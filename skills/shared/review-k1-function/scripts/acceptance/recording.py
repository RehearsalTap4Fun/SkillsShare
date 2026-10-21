"""Append-only, idempotent event ingestion with explicit coverage and cleanup gaps."""
from copy import deepcopy
import html
import json
import uuid
from .core import require, now, canonical_hash, owned_path, reject_formal_write
from . import evidence

KEYS = {'eventId', 'sequence', 'wallTime', 'monotonicTime', 'gameTime', 'actionId', 'kind', 'requestCorrelation', 'targetIdentity', 'evidenceIds', 'errorCode', 'messageType'}
PAYLOAD = {'income', 'level', 'slot', 'instanceId', 'hookState', 'propType', 'errorCode', 'connected', 'callbackCount'}


def start(store, context):
    require(context['identity'] == store.read()['environment'], 'IDENTITY_MISMATCH')
    rid = uuid.uuid4().hex
    rec = {'recordingId': rid, 'context': {'identity': context['identity']}, 'startedAt': now(), 'events': [], 'status': 'recording'}
    state = store.read()
    def change(s): s.setdefault('recordings', {})[rid] = rec; return s
    store.update(state['revision'], change)
    return rec


def append_events(store, recording_id, events):
    clean = []
    for raw in events:
        e = {k: deepcopy(v) for k, v in raw.items() if k in KEYS}
        e['payload'] = {k: v for k, v in raw.get('payload', {}).items() if k in PAYLOAD and type(v) in [str, int, float, bool, type(None)]}
        require(all(k in e for k in ['eventId', 'sequence', 'wallTime', 'monotonicTime', 'gameTime', 'kind']), 'EVENT_FIELDS')
        require(type(e['sequence']) is int and e['sequence'] >= 0, 'EVENT_SEQUENCE')
        require(e['kind'] in {'action', 'request', 'response', 'state', 'frame', 'error', 'lifecycle'}, 'EVENT_KIND')
        # Identity is structural; arbitrary nested protocol objects never enter the record.
        e['targetIdentity'] = {k: v for k, v in e.get('targetIdentity', {}).items() if k in {'slot', 'instanceId', 'sessionId'}}
        clean.append(e)
    state = store.read()
    def change(s):
        rec = s['recordings'][recording_id]; require(rec['status'] == 'recording', 'RECORDING_FINISHED')
        for e in clean:
            old = next((x for x in rec['events'] if x['eventId'] == e['eventId']), None)
            if old is not None:
                require(old == e, 'EVENT_ID_CONFLICT'); continue
            if rec['events']:
                require(e['sequence'] > rec['events'][-1]['sequence'] and e['monotonicTime'] >= rec['events'][-1]['monotonicTime'], 'EVENT_ORDER')
            rec['events'].append(e)
        return s
    return store.update(state['revision'], change)['recordings'][recording_id]


def finish(store, recording_id, cleanup_receipt):
    state = store.read(); rec = state['recordings'][recording_id]; gaps = ['unobserved_manual_inputs']; errors = {}
    events = rec['events']
    if not any(e['kind'] == 'frame' and e.get('evidenceIds') for e in events): gaps.append('missing_frames')
    if any(e['kind'] in {'request', 'response'} and not e.get('requestCorrelation') for e in events): gaps.append('missing_request_correlation')
    if any(b['sequence'] != a['sequence'] + 1 for a, b in zip(events, events[1:])): gaps.append('sequence_gap')
    for e in events:
        if e.get('errorCode') not in [None, '', '0', 0]: errors.setdefault(str(e['errorCode']), []).append(e['eventId'])
    ids = cleanup_receipt.get('evidenceIds', [])
    cleanup = (cleanup_receipt.get('callbacksRemaining') == 0 and cleanup_receipt.get('recordingId') == recording_id
               and bool(ids) and all(i in state['evidence'] for i in ids) and evidence.verify_package(store)['status'] == 'verified')
    if not cleanup: gaps.append('cleanup_unverified')
    report = {'status': 'complete' if cleanup and set(gaps) <= {'unobserved_manual_inputs'} else 'incomplete',
              'coverageGaps': gaps, 'errorGroups': errors, 'cleanupVerification': {'status': 'verified' if cleanup else 'incomplete', 'evidenceIds': ids}}
    def change(s): s['recordings'][recording_id]['report'] = report; s['recordings'][recording_id]['status'] = report['status']; return s
    store.update(state['revision'], change)
    return report


def render_timeline(store, recording_id, output):
    reject_formal_write(output); rec = store.read()['recordings'][recording_id]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('<!doctype html><meta charset="utf-8"><title>验收录制时间线</title><h1>操作与证据时间线</h1><pre>' +
                      html.escape(json.dumps(rec, ensure_ascii=False, indent=2)) + '</pre>', encoding='utf-8')
    return output
