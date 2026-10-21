"""DWS value writes and session-owned browser handoffs; never replace cell images."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import uuid
from .core import WorkflowError, require, atomic_json, owned_path, file_hash, canonical_hash, reject_formal_write
from . import feedback, operations, evidence
from .workbook_images import inspect_xlsx, column


class DwsClient:
    def __init__(self, executable): self.executable = str(executable)

    def invoke(self, arguments):
        require(isinstance(arguments, list) and all(isinstance(a, str) for a in arguments), 'DWS_ARGUMENTS')
        try:
            proc = subprocess.run([self.executable, *arguments, '--format', 'json'], capture_output=True, encoding='utf-8', timeout=360, shell=False)
            require(proc.returncode == 0, 'DWS_EXIT_ERROR')
            result = json.loads(proc.stdout)
            require(result.get('success') is True, 'DWS_RESULT_ERROR')
            return result
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            raise WorkflowError('DWS_OUTCOME_UNKNOWN') from e


def validate_browser_export(receipt, target):
    require(receipt.get('source') == 'browser_download' and receipt.get('nodeId') == target['nodeId'], 'EXPORT_IDENTITY')
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(receipt['exportedAt'])).total_seconds()
    except (KeyError, ValueError, TypeError):
        raise WorkflowError('EXPORT_TIMESTAMP') from None
    require(0 <= age <= 600, 'EXPORT_STALE')
    path = Path(receipt['path'])
    require(path.is_file() and file_hash(path) == receipt.get('sha256'), 'EXPORT_HASH')
    return path


def read_snapshot(client, target, field_map, export_receipt=None):
    """target binds a real sheet name, bounded range, sidecar identities and export directory."""
    if export_receipt is None:
        reject_formal_write(Path(target['exportDirectory']))
    common = ['--node', target['nodeId'], '--sheet-id', target['sheetId']]
    listing = client.invoke(['sheet', 'list', '--node', target['nodeId']])
    sheet = next((s for s in listing['sheets'] if s['sheetId'] == target['sheetId']), None)
    require(sheet is not None, 'SHEET_NOT_FOUND')
    values = client.invoke(['sheet', 'range', 'read', *common, '--range', target['range']])
    require(not values.get('hasMore') and not values.get('truncationReasons'), 'SHEET_TRUNCATED')
    validation = client.invoke(['sheet', 'get-dropdown', *common, '--range', target['validationRange']])
    if export_receipt is not None:
        export = validate_browser_export(export_receipt, target)
    else:
        root = Path(target['exportDirectory']).resolve()
        export = owned_path(root, uuid.uuid4().hex + '.xlsx')
        reject_formal_write(export)
        export.parent.mkdir(parents=True, exist_ok=True)
        client.invoke(['sheet', 'export', '--node', target['nodeId'], '--output', str(export)])
    require(export.is_file(), 'EXPORT_MISSING')
    images = inspect_xlsx(export)
    sheetmatches = [s for s in images['sheets'] if s['name'] == sheet['name']]
    require(len(sheetmatches) == 1, 'EXPORTED_SHEET_AMBIGUOUS')
    image_sid = sheetmatches[0]['sheetId']
    match = re.fullmatch(r'A(\d+):[A-Z]+\d+', target['range'])
    require(match is not None, 'SNAPSHOT_RANGE_MUST_START_COLUMN_A')
    rows = []
    problem_index = next(i for i in range(1, 1000) if column(i) == field_map['problem']) - 1
    problem_texts = [str(row[problem_index]) for row in values['values'] if len(row) > problem_index and row[problem_index]]
    duplicates = sorted({v for v in problem_texts if problem_texts.count(v) > 1})
    for offset, cells in enumerate(values['values']):
        number = int(match[1]) + offset
        vals = {column(i + 1): v for i, v in enumerate(cells)}
        pics = [dict(i) for i in images['images'] if i['sheetId'] == image_sid and i['cell'] == field_map['images'] + str(number)]
        identities = [s for s in target['identities'] if s['identity']['problemText'] == vals.get(field_map['problem'])
                      and set(s['identity']['originalImages']).issubset(i['sha256'] for i in pics)]
        if not identities: continue
        for sidecar in identities:
            for pic in pics: pic['review'] = sidecar.get('reviews', {}).get(pic['sha256'], {'status': 'unreviewed'})
            rows.append({'id': sidecar['id'], 'row': number, 'identity': deepcopy(sidecar['identity']), 'values': vals, 'images': pics})
    return {'schemaVersion': 1, 'nodeId': target['nodeId'], 'sheetId': target['sheetId'], 'fieldMap': field_map,
            'rows': rows, 'imagesReadComplete': not images['unsupported'], 'dataValidations': validation['dataValidations'],
            'export': {'path': str(export), 'sha256': file_hash(export)}, 'target': deepcopy(target),
            'ambiguousProblemTexts': duplicates, 'writeMode': 'browser_identity_check' if export_receipt is not None else 'fresh_dws'}


def _read(client, patch, export_receipt=None):
    b = patch['before']
    return read_snapshot(client, b.get('target', b), b['fieldMap'], export_receipt)


def _operation(store, patch, op):
    # Physical row is intentionally excluded: stable feedback ID owns deduplication.
    intent = {k: v for k, v in op.items() if k not in {'cell'}}
    return operations.prepare(store, 'feedback', {k: patch['before'][k] for k in ['nodeId', 'sheetId']},
                              {'baselineHash': patch['baselineHash'], 'intent': intent})


def execute_patch(client, store, patch, capabilities, export_receipt=None):
    current = _read(client, patch, export_receipt)
    rebased = feedback.rebase_patch(patch, current)
    result = {'completed': [], 'pendingActions': [], 'conflicts': rebased['conflicts']}
    if current.get('ambiguousProblemTexts'):
        result['conflicts'].append({'reason': 'same_text_requires_live_image_identity', 'problemTexts': current['ambiguousProblemTexts']})
    if result['conflicts']: return result
    for op in rebased['operations']:
        record = _operation(store, patch, op)
        if record['status'] == 'verified': continue
        if record['status'] != 'prepared':
            result['pendingActions'].append({'operationId': record['operationId'], 'adapter': 'dws', 'action': 'reconcile'}); continue
        if op['kind'] == 'append_image':
            result['pendingActions'].insert(0, {'actionId': record['operationId'], 'operationId': record['operationId'],
                'adapter': 'browser', 'action': 'append_cell_images', 'parameters': {**op, 'nodeId': current['nodeId'], 'sheetId': current['sheetId']},
                'preconditions': {'baselineHash': canonical_hash(current), 'imageWriteMode': capabilities.get('imageWriteMode', 'unknown')},
                'expectedEvidence': ['online_open_per_image', 'export_snapshot'], 'mutationScope': 'one_cell_append'})
            continue
        if export_receipt is not None:
            result['pendingActions'].append({'actionId': record['operationId'], 'operationId': record['operationId'],
                'adapter': 'browser', 'action': 'verify_identity_and_set_cell',
                'parameters': {**op, 'nodeId': current['nodeId'], 'sheetId': current['sheetId']},
                'preconditions': {'currentRowIdentityRequired': True, 'originalImagesMustBeOpened': True,
                                  'identity': next(r['identity'] for r in current['rows'] if r['id'] == op['feedbackId'])},
                'expectedEvidence': ['live_identity_before_write', 'export_snapshot'], 'mutationScope': 'one_cell_value'})
            continue
        # Re-read before EACH write: a sort after H must not send I to the old physical row.
        current = _read(client, patch)
        fresh = feedback.rebase_patch(patch, current)
        if fresh['conflicts'] or current.get('ambiguousProblemTexts'):
            result['conflicts'].extend(fresh['conflicts'] or [{'reason': 'same_text_requires_live_image_identity'}]); break
        matches = [x for x in fresh['operations'] if x['feedbackId'] == op['feedbackId'] and x['kind'] == op['kind'] and x['column'] == op['column']]
        if not matches: continue
        require(len(matches) == 1, 'AMBIGUOUS_CELL_WRITE')
        op = matches[0]
        operations.dispatch(store, record['operationId'])
        try:
            receipt = client.invoke(['sheet', 'range', 'update', '--node', current['nodeId'], '--sheet-id', current['sheetId'],
                                     '--range', op['cell'], '--values', json.dumps([[op['value']]], ensure_ascii=False)])
            operations.record_receipt(store, record['operationId'], {'success': receipt['success'], 'logId': receipt.get('logId')})
        except (WorkflowError, OSError):
            operations.mark_unknown(store, record['operationId'], 'write_outcome_unknown')
            result['pendingActions'].append({'operationId': record['operationId'], 'adapter': 'dws', 'action': 'reconcile'})
            break
    return result


def reconcile_patch(client, store, patch, browser_receipts, export_receipt=None):
    after = _read(client, patch, export_receipt)
    path = owned_path(store.root, 'snapshots/' + uuid.uuid4().hex + '.json'); atomic_json(path, after)
    proof = evidence.register(store, path, {'kind': 'document', 'assertionId': 'feedback/' + patch['patchId']})
    validation = feedback.verify_patch(patch, after)
    validation['evidenceIds'] = [proof['evidenceId']]
    for op in patch['operations']:
        if op['kind'] != 'append_image': continue
        record = _operation(store, patch, op)
        receipts = [r for r in browser_receipts if r.get('operationId') == record['operationId'] and r.get('sha256') == op['image']['sha256']
                    and r.get('nodeId') == after['nodeId'] and r.get('sheetId') == after['sheetId'] and r.get('feedbackId') == op['feedbackId']]
        valid = False
        for r in receipts:
            e = store.read()['evidence'].get(r.get('openedEvidenceId'), {})
            valid |= (e.get('kind') == 'ui' and e.get('review', {}).get('status') == 'reviewed' and e.get('feedbackId') == op['feedbackId']
                      and bool(after.get('export')) and r.get('exportSha256') == after['export']['sha256'])
        if not valid:
            if validation['status'] != 'mismatch': validation['status'] = 'incomplete'
            validation['differences'].append({'feedbackId': op['feedbackId'], 'reason': 'online_open_evidence_missing'})
    if validation['status'] == 'verified':
        require(evidence.verify_package(store)['status'] == 'verified', 'EVIDENCE_CORRUPT')
        for op in patch['operations']:
            record = _operation(store, patch, op)
            if record['status'] in {'prepared', 'verified'}: continue  # Never invent a dispatch receipt.
            operations.complete_verification(store, record['operationId'], validation)
    return validation
