"""Resumable K1 backup → DingTalk DownKey → personal dev deployment → runtime proof.

prepare is read-only remotely. Each advance does at most one Jenkins POST;
polling/recovery never repeats a recorded submission. Raw keys/logs stay in memory.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse

from jenkins_test_environment import (Jenkins, DeploymentError, require, digest, timestamp,
    read_json, write_new, save_receipt, validate_request, load_config, validate_config, WORKFLOW_FIELDS)

SCRIPTS = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
FIELDS = 'number,url,queueId,timestamp,duration,building,result,actions[parameters[name,value],causes[userId,upstreamProject,upstreamBuild],remoteUrls,lastBuiltRevision[SHA1]]'


def job_path(job):
    return '/job/' + urllib.parse.quote(job, safe='') + '/'



def seconds(value):
    if isinstance(value, (int, float)):
        return value / 1000
    value = re.sub(r'(\.\d{6})\d+', r'\1', value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=CST).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def normalize_request(request, defaults):
    allowed = {'onlineSid', 'targetSid', 'codeBranch', 'configBranch', 'app', 'aliveDays',
               'kvk_keep_servers', 'authorization'}
    require(set(request) <= allowed, 'UNKNOWN_REQUEST_FIELD')
    sid = str(request.get('onlineSid', ''))
    target = str(request.get('targetSid', sid))
    require(all(re.fullmatch(r'[1-9][0-9]*', x) and int(x) <= 2147483647 for x in [sid, target]), 'SID_INVALID')
    app = request.get('app', 'gs' if int(sid) < 10000 else 'battle')
    require(app in {'gs', 'battle'} and all((app == 'gs') == (int(x) < 10000) for x in [sid, target]), 'SID_APP_MISMATCH')
    auth = request.get('authorization')
    require(isinstance(auth, str) and bool(auth.strip()), 'BACKUP_IMPORT_REBUILD_AUTHORIZATION_REQUIRED')
    result = {'onlineSid': sid, 'targetSid': target, 'app': app, 'authorization': auth}
    for key in ['codeBranch', 'configBranch']:
        branch = request.get(key, '')
        require(isinstance(branch, str), 'BRANCH_INVALID')
        if not branch.startswith('origin/'):
            branch = 'origin/' + branch
        require(re.fullmatch(r'origin/[A-Za-z0-9_./-]+', branch) and '..' not in branch, 'BRANCH_INVALID')
        result[key] = branch
    alive = str(request.get('aliveDays', defaults.get('ALIVE', '')))
    require(alive in {'1', '2', '3', '4', '5'}, 'ALIVE_INVALID')
    keep = request.get('kvk_keep_servers')
    if 'kvk_keep_servers' in request:
        require(app == 'battle' and isinstance(keep, list) and len(keep) > 0, 'KVK_LIST_REQUIRED_WHEN_SPECIFIED')
        require(all(isinstance(x, (str, int)) and re.fullmatch(r'[1-9][0-9]*', str(x))
                    and int(x) < 10000 for x in keep), 'KVK_GAME_SID_INVALID')
        keep = ','.join(dict.fromkeys(str(x) for x in keep))
    else:
        keep = ''
    result.update(aliveDays=alive, kvk_keep_servers=keep)
    return result


def deploy_request(plan):
    r = plan['request']
    return {'scope': {'environment': 'dev', 'serverIds': [r['targetSid']],
                      'authorization': r['authorization'], 'dataImportAuthorization': r['authorization']},
            'sources': plan['sources'], 'parameters': {'corebranch': r['codeBranch'], 'branchName': r['configBranch'],
            'app': r['app'], 'server_id': r['targetSid'], 'ALIVE': r['aliveDays'],
            'kvk_keep_servers': r['kvk_keep_servers'], 'DownKey': ''}}


def prepare_plan(request, sources, config):
    validate_config(config, workflow=True)
    r = normalize_request(request, config.get('deploymentDefaults', {}))
    owner = config['username']
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', owner) is not None, 'OWNER_INVALID')
    settings = config['environmentWorkflow']
    plan = {'schemaVersion': 2, 'baseUrl': config['baseUrl'].rstrip('/'), 'owner': owner,
            'request': r, 'sources': sources, 'createdAt': timestamp(),
            'jobs': dict(config['jobs']), **settings}
    # Apply the existing scope/source validation before any external write.
    req = deploy_request(plan)
    validate_request(req, {'buildable': True, 'parameters': {k: {} for k in req['parameters']}})
    plan['planSha256'] = digest(plan)
    return plan


def parameters(build):
    return {p['name']: str(p.get('value', '')) for a in build.get('actions', []) for p in a.get('parameters', [])}


def user_ids(build):
    return {c.get('userId') for a in build.get('actions', []) for c in a.get('causes', []) if c.get('userId')}


def history(client, job, since):
    """Page allBuilds until the time boundary or proven first build, never lastBuild."""
    result, seen, offset = [], set(), 0
    while True:
        data = client.get(client.base + job_path(job) + 'api/json',
                          'firstBuild[number],allBuilds[' + FIELDS + ']{' + str(offset) + ',' + str(offset + 100) + '}')
        require('allBuilds' in data, 'BUILD_HISTORY_UNAVAILABLE')
        items = data['allBuilds']
        if not items:
            require(offset == 0 and not data.get('firstBuild'), 'BUILD_HISTORY_INCOMPLETE')
            return result
        for build in items:
            require(build['number'] not in seen and 'timestamp' in build, 'BUILD_HISTORY_INCOMPLETE')
            seen.add(build['number'])
            if build['timestamp'] / 1000 >= since or build.get('building'):
                result.append(build)
        if min(b['timestamp'] for b in items) / 1000 < since or (data.get('firstBuild') or {}).get('number') in seen:
            return result
        offset += len(items)


def queue_items(client):
    return client.get(client.base + '/queue/api/json',
                      'items[id,inQueueSince,task[name],actions[parameters[name,value],causes[userId]]]')['items']


def submit_once(client, plan, root, job, params, key=None):
    path = root / ('backup.json' if job == plan['jobs']['backup'] else 'deploy.json')
    require(not path.exists(), 'RECEIPT_EXISTS_DO_NOT_RESUBMIT')
    if job == plan['jobs']['backup']:
        meta = client.get(client.base + job_path(plan['jobs']['backup']) + 'api/json', 'name,buildable,property[parameterDefinitions[name]]')
        defs = {p['name'] for prop in meta.get('property', []) for p in prop.get('parameterDefinitions', [])}
        require(meta.get('buildable') is True and defs == {'sid'}, 'BACKUP_JOB_SCHEMA_CHANGED')
    else:
        validate_request(deploy_request(plan), client.inspect())
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    try:
        crumb = client.get(client.base + '/crumbIssuer/api/json')
        field = crumb.get('crumbRequestField', '')
        require(re.fullmatch(r'[A-Za-z0-9-]+', field) and field.lower() not in {'authorization', 'host', 'content-type'}, 'CRUMB_INVALID')
        headers[field] = crumb['crumb']
    except DeploymentError as e:
        if str(e) != 'JENKINS_HTTP_404':
            raise
    receipt = {'job': job, 'baseUrl': client.base, 'planSha256': plan['planSha256'], 'owner': plan['owner'],
               'parameters': {k: v for k, v in params.items() if k != 'DownKey'},
               'submittedAt': timestamp(), 'status': 'submission_pending'}
    # Bound the possible server reception time even if the process dies during
    # POST. Jenkins.request has a 30-second timeout; allow 5 seconds clock skew.
    receipt['submissionWindowEnd'] = datetime.fromtimestamp(seconds(receipt['submittedAt']) + 35, timezone.utc).isoformat()
    if key:
        receipt['downKeySha256'] = hashlib.sha256(key.encode()).hexdigest()
    write_new(path, receipt)  # durable intent before POST; also survives process termination
    try:
        status, response, _ = client.request(client.base + job_path(job) + 'buildWithParameters',
            urllib.parse.urlencode({**params, **({'DownKey': key} if key else {})}).encode(), headers)
        require(status == 201, 'SUBMISSION_OUTCOME_UNKNOWN')
        url = client.safe_url(response.get('Location', ''))
        require(re.fullmatch(re.escape(client.base) + r'/queue/item/([0-9]+)/', url), 'QUEUE_LOCATION_INVALID')
        receipt.update(status='queued', queueUrl=url, queueId=int(url.rstrip('/').split('/')[-1]))
    except Exception as e:
        receipt.update(status='submission_unknown', error=str(e) if isinstance(e, DeploymentError) else type(e).__name__)
        receipt['submissionWindowEnd'] = datetime.fromtimestamp(
            min(seconds(receipt['submissionWindowEnd']), datetime.now(timezone.utc).timestamp() + 5), timezone.utc).isoformat()
        save_receipt(path, receipt)
        raise DeploymentError(receipt['error']) from None
    save_receipt(path, receipt)
    return receipt


def matching_submission(item, receipt):
    p = parameters(item)
    return (all(p.get(k) == v for k, v in receipt['parameters'].items())
            and (not receipt.get('downKeySha256') or hashlib.sha256(p.get('DownKey', '').encode()).hexdigest() == receipt['downKeySha256'])
            and receipt['owner'] in user_ids(item))


def reconcile(client, receipt):
    since = seconds(receipt['submittedAt']) - 5
    builds = history(client, receipt['job'], since)
    if receipt.get('queueId'):
        matches = [b for b in builds if b.get('queueId') == receipt['queueId']]
        queued = []
    else:
        require(receipt.get('submissionWindowEnd'), 'SUBMISSION_WINDOW_UNPROVEN')
        until = seconds(receipt['submissionWindowEnd'])
        matches = []
        for build in builds:
            if not matching_submission(build, receipt):
                continue
            received = build['timestamp'] / 1000
            if build.get('queueId'):
                try:
                    queue = client.get(client.base + '/queue/item/' + str(build['queueId']) + '/api/json')
                    received = queue.get('inQueueSince', build['timestamp']) / 1000
                except DeploymentError as e:
                    if str(e) != 'JENKINS_HTTP_404':
                        raise
            if since <= received <= until:
                matches.append(build)
        queued = [q for q in queue_items(client) if q.get('task', {}).get('name') == receipt['job']
                  and since <= q.get('inQueueSince', 0) / 1000 <= until and matching_submission(q, receipt)
                  and q['id'] not in {b.get('queueId') for b in matches}]
    require(len(matches) + len(queued) == 1, 'SUBMISSION_RECONCILIATION_AMBIGUOUS_OR_NOT_FOUND')
    if matches:
        receipt['buildNumber'] = matches[0]['number']
    else:
        receipt.update(queueId=queued[0]['id'], queueUrl=client.base + '/queue/item/' + str(queued[0]['id']) + '/')
    receipt['reconciledAt'] = timestamp()


def poll_build(client, plan, path):
    receipt = read_json(path)
    require(receipt['planSha256'] == plan['planSha256'] and receipt['baseUrl'] == client.base, 'RECEIPT_TARGET_CHANGED')
    require(receipt['job'] in {plan['jobs']['backup'], plan['jobs']['deploy']}, 'RECEIPT_JOB_INVALID')
    if not receipt.get('buildNumber'):
        if not receipt.get('queueUrl'):
            reconcile(client, receipt)
        if not receipt.get('buildNumber'):
            expected = client.base + '/queue/item/' + str(receipt['queueId']) + '/'
            require(receipt['queueUrl'] == expected, 'QUEUE_LOCATION_INVALID')
            try:
                item = client.get(expected + 'api/json')
                require(not item.get('cancelled'), 'BUILD_CANCELLED')
                if item.get('executable'):
                    exe = item['executable']
                    require(exe['url'] == client.base + job_path(receipt['job']) + str(exe['number']) + '/', 'BUILD_JOB_MISMATCH')
                    receipt['buildNumber'] = exe['number']
            except DeploymentError as e:
                if str(e) != 'JENKINS_HTTP_404':
                    raise
                reconcile(client, receipt)
        save_receipt(path, receipt)
    if not receipt.get('buildNumber'):
        return receipt, None
    number = receipt['buildNumber']
    require(type(number) is int and number > 0, 'BUILD_NUMBER_INVALID')
    url = client.base + job_path(receipt['job']) + str(number) + '/'
    build = client.get(url + 'api/json', FIELDS)
    require(build['number'] == number and matching_submission(build, receipt), 'BUILD_PARAMETERS_OR_OWNER_MISMATCH')
    receipt.update(buildUrl=url, result=build.get('result'), buildTimestamp=build['timestamp'],
                   status='building' if build.get('building') else 'succeeded' if build.get('result') == 'SUCCESS' else 'failed')
    save_receipt(path, receipt)
    require(receipt['status'] != 'failed', 'BUILD_FAILED')
    return receipt, build


def backup_notifications(messages, plan, since, until):
    selected, seen = [], {}
    for m in messages:
        if (m.get('openConversationId') != plan['groupId'] or m.get('sendType') != 'robot'
                or m.get('sender') != plan['sender'] or m.get('senderOpenDingTalkId') != plan['senderId']):
            continue
        content = m.get('content', '')
        if not isinstance(content, str) or not ('k1 Prod DB' in content and '备份数据库' in content):
            continue
        if not int(since) <= seconds(m['createTime']) <= until:
            continue
        ids = re.search(r'备份游戏\s*[:：]\s*\[([^\]]+)\]', content)
        if not ids or re.findall(r'[0-9]+', ids[1]) != [plan['request']['onlineSid']]:
            continue
        status = re.search(r'状态\s*[:：]\s*\*\*(完成|失败|取消|终止)\*\*', content)
        if not status:
            continue
        require(m.get('openMessageId'), 'BACKUP_MESSAGE_MALFORMED')
        mid = m['openMessageId']
        fingerprint = digest(m)
        require(mid not in seen or seen[mid] == fingerprint, 'BACKUP_MESSAGE_ID_CONFLICT')
        if mid not in seen:
            selected.append((m, status[1]))
        seen[mid] = fingerprint
    return selected


def backup_messages(messages, plan, since, until):
    result = []
    for m, status in backup_notifications(messages, plan, since, until):
        require(status == '完成', 'BACKUP_WORKFLOW_FAILED')
        keys = re.findall(r'file-ladder\s+down\s+([A-Za-z0-9_.=+/\-]{16,})', m['content'])
        require(len(keys) == 1, 'BACKUP_MESSAGE_MALFORMED')
        result.append((m, keys[0]))
    return result


def assert_owner(plan, probe):
    require(probe.get('inventoryComplete') is True and probe.get('host') == plan['runtimeHost'], 'OWNER_INVENTORY_UNVERIFIED')
    require(abs(seconds(probe['checkedAt']) - datetime.now(timezone.utc).timestamp()) < 60, 'OWNER_INVENTORY_STALE')
    r = plan['request']
    expected = 'remote-' + plan['owner'] + '-' + r['app'] + '-' + r['targetSid']
    require(isinstance(probe.get('containers'), list), 'OWNER_INVENTORY_UNVERIFIED')
    require(all(name == expected for name in probe['containers']), 'TARGET_SID_OCCUPIED_BY_OTHER_INSTANCE')


def ensure_idle(client, plan, job, parameter, sid):
    queued = [q for q in queue_items(client) if q.get('task', {}).get('name') == job and parameters(q).get(parameter) == sid]
    builds = history(client, job, datetime.now(timezone.utc).timestamp() - 86400)
    require(not queued and not any(b.get('building') and parameters(b).get(parameter) == sid for b in builds), 'SID_HAS_ACTIVE_WORKFLOW')
    return [b for b in builds if parameters(b).get(parameter) == sid]


def backup_preflight(client, plan, services):
    prior = sorted(ensure_idle(client, plan, plan['jobs']['backup'], 'sid', plan['request']['onlineSid']), key=lambda b: b['timestamp'])
    if not prior:
        return
    # A SUCCESS here only dispatches Oops. Prove earlier same-SID jobs completed
    # before a new window starts; otherwise late old notifications could be misbound.
    msgs = services.read_messages(plan['groupId'], prior[0]['timestamp'] / 1000)
    end = datetime.now(timezone.utc).timestamp()
    consumed = set()
    for index, build in enumerate(prior):
        upper = prior[index + 1]['timestamp'] / 1000 if index + 1 < len(prior) else end
        matched = backup_messages(msgs, plan, build['timestamp'] / 1000, upper)
        require(build.get('result') == 'SUCCESS' and len(matched) == 1, 'PREVIOUS_BACKUP_COMPLETION_UNPROVEN')
        message_id = matched[0][0]['openMessageId']
        require(message_id not in consumed, 'PREVIOUS_BACKUP_COMPLETION_UNPROVEN')
        consumed.add(message_id)


def get_downkey(client, plan, root, services, receipt, build):
    since = seconds(receipt['submittedAt']) - 5
    same_sid = [b for b in history(client, plan['jobs']['backup'], since) if parameters(b).get('sid') == plan['request']['onlineSid']]
    require({b['number'] for b in same_sid} == {receipt['buildNumber']}, 'BACKUP_CORRELATION_AMBIGUOUS')
    require(not any(q.get('task', {}).get('name') == plan['jobs']['backup'] and parameters(q).get('sid') == plan['request']['onlineSid']
                    for q in queue_items(client)), 'BACKUP_CORRELATION_AMBIGUOUS')
    messages = services.read_messages(plan['groupId'], build['timestamp'] / 1000)
    end = datetime.now(timezone.utc).timestamp() + 2
    failed = [(m, s) for m, s in backup_notifications(messages, plan, build['timestamp'] / 1000, end) if s != '完成']
    if failed:
        save_receipt(root / 'backup-failure.json', {'status': 'failed', 'backupBuild': receipt['buildUrl'],
            'messages': [{'groupId': plan['groupId'], 'messageId': m['openMessageId'], 'createdAt': m['createTime'],
                          'status': s} for m, s in failed]})
        raise DeploymentError('BACKUP_WORKFLOW_FAILED')
    matches = backup_messages(messages, plan, build['timestamp'] / 1000, end)
    require(len(matches) <= 1, 'BACKUP_MESSAGE_AMBIGUOUS')
    if not matches:
        return None
    message, key = matches[0]
    _, _, raw = client.request(receipt['buildUrl'] + 'consoleText')
    workflows = set(re.findall(re.escape(plan['workflowBaseUrl'].rstrip('/')) + r'/([a-f0-9]{24})(?![a-f0-9])', raw.decode('utf-8', 'replace')))
    require(len(workflows) == 1, 'BACKUP_DISPATCH_ID_UNPROVEN')
    evidence = {'groupId': plan['groupId'], 'messageId': message['openMessageId'], 'createdAt': message['createTime'],
                'senderId': plan['senderId'],
                'downKeySha256': hashlib.sha256(key.encode()).hexdigest(), 'backupBuild': receipt['buildUrl'],
                'workflowId': next(iter(workflows)), 'correlation': 'unique_sid_build_and_completed_robot_message_in_window'}
    path = root / 'downkey-evidence.json'
    if path.exists():
        require(read_json(path) == evidence, 'BACKUP_MESSAGE_BINDING_CHANGED')
    else:
        write_new(path, evidence)
    return key


def verify_builds(client, plan, receipt, build):
    revisions = {}
    for action in build.get('actions', []):
        for remote in action.get('remoteUrls', []):
            for role, repo in [('server', 'game'), ('config', 'dataconfig')]:
                if re.search(r'/' + repo + r'(?:\.git)?$', remote):
                    revisions.setdefault(role, set()).add(action.get('lastBuiltRevision', {}).get('SHA1'))
    require(all(revisions.get(role) == {plan['sources'][role]['sha']} for role in ['server', 'config']), 'ACTUAL_REVISION_MISMATCH_OR_UNPROVEN')
    _, _, raw = client.request(receipt['buildUrl'] + 'consoleText')
    numbers = set(re.findall(re.escape(plan['jobs']['child']) + r'\s+#([0-9]+)', raw.decode('utf-8', 'replace')))
    require(len(numbers) == 1, 'DOWNSTREAM_BUILD_UNPROVEN')
    number = int(next(iter(numbers)))
    child_url = client.base + job_path(plan['jobs']['child']) + str(number) + '/'
    child = client.get(child_url + 'api/json', FIELDS)
    require(child.get('number') == number and not child.get('building') and child.get('result') == 'SUCCESS', 'DOWNSTREAM_NOT_SUCCESSFUL')
    require(any(c.get('upstreamProject') == plan['jobs']['deploy'] and c.get('upstreamBuild') == receipt['buildNumber']
                for a in child.get('actions', []) for c in a.get('causes', [])), 'DOWNSTREAM_CAUSE_MISMATCH')
    p, r = parameters(child), plan['request']
    require(all(p.get(k) == v for k, v in {'APP': r['app'], 'SID': r['targetSid'], 'DB_TAG': 'dev' + r['targetSid'],
            'BRANCH': r['codeBranch'], 'NACOS': 'dev', 'KVK_KEEP_SERVERS': r['kvk_keep_servers']}.items()), 'DOWNSTREAM_PARAMETERS_MISMATCH')
    require(hashlib.sha256(p.get('DownKey', '').encode()).hexdigest() == receipt['downKeySha256'], 'DOWNSTREAM_IMPORT_KEY_MISMATCH')
    _, _, raw = client.request(child_url + 'consoleText')
    restores = re.findall(r'(\d+) document\(s\) restored successfully\.\s*(\d+) document\(s\) failed to restore', raw.decode('utf-8', 'replace'))
    require(restores and all(int(failed) == 0 for _, failed in restores), 'DATABASE_RESTORE_UNPROVEN_OR_FAILED')
    # An old run must not bless a later rebuild using the same image but different
    # configuration, or a pending replacement of the shared dev<SID> database.
    for job, sid_parameter, current in [(plan['jobs']['deploy'], 'server_id', build), (plan['jobs']['child'], 'SID', child)]:
        later = history(client, job, current['timestamp'] / 1000)
        require(not any(b['number'] != current['number'] and parameters(b).get(sid_parameter) == r['targetSid'] for b in later),
                'DEPLOYMENT_SUPERSEDED')
        require(not any(q.get('task', {}).get('name') == job and parameters(q).get(sid_parameter) == r['targetSid'] for q in queue_items(client)),
                'DEPLOYMENT_SUPERSEDED')
    require(type(child.get('duration')) is int and child['duration'] >= 0, 'DOWNSTREAM_COMPLETION_TIME_UNPROVEN')
    return {'parentBuild': receipt['buildUrl'], 'childBuild': child_url, 'startedAt': child['timestamp'] / 1000,
            'finishedAt': (child['timestamp'] + child['duration']) / 1000,
            'serverSha': plan['sources']['server']['sha'], 'configSha': plan['sources']['config']['sha'],
            'restoredDocuments': sum(int(count) for count, _ in restores), 'restoreFailures': 0}


def verify_runtime(plan, probe, started, finished=None):
    assert_owner(plan, probe)
    r, c, hb = plan['request'], probe.get('container', {}), probe.get('heartbeat', {})
    name = 'remote-' + plan['owner'] + '-' + r['app'] + '-' + r['targetSid']
    require(c.get('name') == name and c.get('running') is True and c.get('processRunning') is True, 'RUNTIME_PROCESS_NOT_READY')
    now = datetime.now(timezone.utc).timestamp()
    upper = min(finished + 5, now + 5) if finished is not None else now + 5
    require(started - 5 <= seconds(c['startedAt']) <= upper, 'RUNTIME_CONTAINER_NOT_CURRENT')
    require(c.get('image') == 'k1-dev:' + plan['sources']['server']['sha'][:12] and c.get('imageId'), 'RUNTIME_IMAGE_MISMATCH')
    command = c.get('command', [])
    require(command and command[0] == 'game' and ('--battle' in command) == (r['app'] == 'battle')
            and all(x in command for x in ['--nacos=dev', '--csv=dev' + r['targetSid'], '--sid=' + r['targetSid']]), 'RUNTIME_CONFIGURATION_MISMATCH')
    require(hb.get('type') == ('game' if r['app'] == 'gs' else 'battle') and str(hb.get('sid')) == r['targetSid']
            and hb.get('running') is True and hb.get('codeVer') == r['codeBranch'] + '(' + plan['owner'] + ')', 'RUNTIME_HEARTBEAT_MISMATCH')
    require(abs(now - hb.get('runningTime', 0) / 1000) <= 30 and hb['runningTime'] / 1000 >= started - 5, 'RUNTIME_HEARTBEAT_STALE')
    require(hb.get('ip') == plan['runtimeHost'] and type(hb.get('mainPort')) is int and 0 < hb['mainPort'] < 65536, 'RUNTIME_ENDPOINT_INVALID')
    require(any(p.get('host') == hb['ip'] and p.get('port') == hb['mainPort'] and p.get('reachable') is True
                for p in probe.get('tcp', [])), 'RUNTIME_TCP_UNREACHABLE')
    return probe


def validate_plan_config(plan, config):
    validate_config(config, workflow=True)
    require(plan.get('owner') == config['username'], 'OWNER_CHANGED')
    require(plan.get('baseUrl') == config['baseUrl'].rstrip('/')
            and plan.get('jobs') == config['jobs']
            and all(plan.get(k) == config['environmentWorkflow'][k] for k in WORKFLOW_FIELDS), 'WORKFLOW_TARGET_CHANGED')


def advance(client, plan, root, services):
    root = Path(root)
    validate_plan_config(plan, client.config)
    require(plan.get('planSha256') == digest({k: v for k, v in plan.items() if k != 'planSha256'}), 'PLAN_CHANGED')
    require(plan['baseUrl'] == client.base, 'JENKINS_TARGET_CHANGED')
    r = plan['request']
    backup_path, deploy_path = root / 'backup.json', root / 'deploy.json'
    if not backup_path.exists():
        validate_request(deploy_request(plan), client.inspect())
        assert_owner(plan, services.probe(plan))
        ensure_idle(client, plan, plan['jobs']['deploy'], 'server_id', r['targetSid'])
        backup_preflight(client, plan, services)
        submit_once(client, plan, root, plan['jobs']['backup'], {'sid': r['onlineSid']})
        return {'status': 'backup_submitted'}
    if not deploy_path.exists():
        receipt, build = poll_build(client, plan, backup_path)
        if receipt['status'] != 'succeeded':
            return {'status': 'waiting_backup_build'}
        key = get_downkey(client, plan, root, services, receipt, build)
        if key is None:
            return {'status': 'waiting_backup_message'}
        assert_owner(plan, services.probe(plan))
        ensure_idle(client, plan, plan['jobs']['deploy'], 'server_id', r['targetSid'])
        params = deploy_request(plan)['parameters']
        submit_once(client, plan, root, plan['jobs']['deploy'], params, key)
        return {'status': 'deployment_submitted'}
    receipt, build = poll_build(client, plan, deploy_path)
    if receipt['status'] != 'succeeded':
        return {'status': 'waiting_deployment_build'}
    builds = verify_builds(client, plan, receipt, build)
    probe = verify_runtime(plan, services.probe(plan, runtime=True), builds['startedAt'], builds['finishedAt'])
    evidence = {'schemaVersion': 1, 'status': 'ready', 'featureAcceptance': 'not_performed',
                'checkedAt': timestamp(), 'owner': plan['owner'], 'request': r, 'sources': plan['sources'],
                'backup': read_json(backup_path), 'downKeyEvidence': read_json(root / 'downkey-evidence.json'),
                'deployment': receipt, 'builds': builds, 'runtime': probe,
                'limitations': ['服务端进程、心跳、TCP 就绪；客户端登录、活动时间、功能断言尚未验收。',
                               '钉钉回执无构建 ID，使用固定群/机器人、SID、时间窗内唯一构建与唯一完成回执关联。']}
    save_receipt(root / 'environment-evidence.json', evidence)
    return evidence


def capture_json(argv, code, timeout=60):
    try:
        process = subprocess.run(argv, capture_output=True, encoding='utf-8', errors='replace', timeout=timeout, check=False)
        require(process.returncode == 0, code)
        return json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise DeploymentError(code) from None


class Services:
    def __init__(self):
        self.dws = shutil.which('dws') or str(Path.home() / '.local/bin/dws.exe')
        # Windows npm .cmd wrappers need a shell; prefer the adjacent JS entry
        # with node so JSON arguments never pass through cmd.exe quoting.
        mcporter = shutil.which('mcporter')
        require(mcporter is not None, 'MCPORTER_NOT_INSTALLED')
        self.mcporter = [mcporter]
        if mcporter.lower().endswith(('.cmd', '.ps1')):
            entry = Path(mcporter).parent / 'node_modules/mcporter/dist/cli.js'
            node = shutil.which('node')
            require(entry.is_file() and node, 'MCPORTER_NODE_ENTRY_UNAVAILABLE')
            self.mcporter = [node, str(entry)]

    def read_messages(self, group, since):
        cursor, seen, messages = since, {}, []
        while True:
            date = datetime.fromtimestamp(cursor, CST).strftime('%Y-%m-%d %H:%M:%S')
            data = capture_json([self.dws, 'chat', 'message', 'list', '--group', group,
                '--time', date, '--limit', '100', '--format', 'json'], 'DWS_MESSAGE_READ_FAILED')
            require(data.get('success') is True and isinstance(data.get('result'), dict), 'DWS_MESSAGE_READ_FAILED')
            result = data['result']
            page = result.get('messages')
            require(isinstance(page, list) and isinstance(result.get('hasMore'), bool), 'DWS_PAGE_INVALID')
            for m in page:
                mid = m.get('openMessageId')
                require(mid, 'DWS_MESSAGE_ID_MISSING')
                fingerprint = digest(m)
                require(mid not in seen or seen[mid] == fingerprint, 'DWS_MESSAGE_ID_CONFLICT')
                if mid not in seen:
                    messages.append(m)
                seen[mid] = fingerprint
            if not result['hasMore']:
                return messages
            require(page, 'DWS_PAGINATION_INCOMPLETE')
            boundary = max(seconds(m['createTime']) for m in page)
            # Re-query the whole boundary second. Never advance one second and
            # silently lose messages sharing a timestamp with the last page.
            require(int(boundary) > int(cursor), 'DWS_PAGINATION_STALLED')
            cursor = int(boundary)

    def probe(self, plan, runtime=False):
        payload = {'sid': plan['request']['targetSid'], 'app': plan['request']['app'], 'owner': plan['owner'],
                   'host': plan['runtimeHost'], 'runtime': runtime}
        code = (SCRIPTS / 'k1_environment_probe.py').read_text(encoding='utf-8')
        encoded = base64.b64encode((code + '\nmain(' + repr(payload) + ')\n').encode()).decode()
        command = "python3 -c \"import base64;exec(base64.b64decode('" + encoded + "'))\""
        result = capture_json(self.mcporter + ['call', 'k1-mcp.ssh_exec', '--args',
            json.dumps({'command': command, 'maxLines': 50}), '--output', 'json', '--timeout', '60000'], 'RUNTIME_PROBE_FAILED', 70)
        blocks = result.get('content', []) if isinstance(result, dict) else []
        text = '\n'.join(x.get('text', '') for x in blocks)
        if isinstance(result, dict) and isinstance(result.get('text'), str):
            text += result['text']
        matches = re.findall(r'^K1_ENV_PROBE=(\{.*\})$', text, re.MULTILINE)
        require(len(matches) == 1 and not result.get('isError'), 'RUNTIME_PROBE_OUTPUT_INVALID')
        probe = json.loads(matches[0])
        require(not probe.get('error'), 'RUNTIME_PROBE_FAILED')
        return probe


def freeze_sources(request, root):
    from user_config import load_profile
    repositories = load_profile(required_roles=('server', 'config'))['repositories']
    repos = {role: repositories[role] for role in ('server', 'config')}
    entries = [{'role': role, 'localRepository': path, 'remote': 'origin', 'baseBranch': 'dev',
                'targetBranch': request['codeBranch' if role == 'server' else 'configBranch'].removeprefix('origin/')}
               for role, path in repos.items()]
    request_path, manifest = root / 'source-request.json', root / 'source-manifest.json'
    write_new(request_path, {'repositories': entries})
    try:
        process = subprocess.run(['powershell', '-NoProfile', '-File', str(SCRIPTS / 'prepare-remote-snapshots.ps1'),
            '-RequestPath', str(request_path), '-OutputPath', str(manifest)], capture_output=True, timeout=420)
        require(process.returncode == 0 and manifest.exists(), 'REMOTE_SOURCE_FREEZE_FAILED')
        data = read_json(manifest)
        require(not data.get('errors'), 'REMOTE_SOURCE_FREEZE_FAILED')
        result = {}
        for row in data['repositories']:
            require(row.get('source') == 'remote' and row.get('remote') == 'origin', 'REMOTE_SOURCE_REQUIRED')
            result[row['role']] = {'ref': row['targetRef'].replace('refs/remotes/', ''),
                                  'sha': row['targetSha'], 'evidence': str(manifest) + '#' + row['role']}
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        raise DeploymentError('REMOTE_SOURCE_FREEZE_FAILED') from None


@contextmanager
def exclusive(path):
    """OS lock releases on crashes; the harmless lock file is never a journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DeploymentError('WORKFLOW_ALREADY_RUNNING') from None
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'advance', 'status'])
    parser.add_argument('--config', help='Private JSON path; otherwise K1_JENKINS_CONFIG is required')
    parser.add_argument('--request')
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    try:
        from user_config import workspace_root
        project = workspace_root()
        root = Path(args.run_dir).resolve()
        require(root.is_relative_to(project / 'reports') or root.is_relative_to(project / '.tmp'), 'RUN_DIR_OUTSIDE_PROJECT')
        if args.command == 'prepare':
            require(args.request, 'REQUEST_REQUIRED')
            config = load_config(args.config, workflow=True)
            request = read_json(args.request)
            normalized = normalize_request(request, config.get('deploymentDefaults', {}))
            root.mkdir(parents=True, exist_ok=False)
            sources = freeze_sources(normalized, root)
            plan = prepare_plan(request, sources, config)
            validate_request(deploy_request(plan), Jenkins(config).inspect())
            write_new(root / 'workflow.json', plan)
            result = {'status': 'prepared', 'plan': str(root / 'workflow.json')}
        elif args.command == 'status':
            result = {'runDir': str(root), 'stages': {p.stem: read_json(p).get('status', 'recorded') for p in
                      [root / x for x in ['workflow.json', 'backup.json', 'downkey-evidence.json', 'deploy.json', 'environment-evidence.json', 'progress.json']] if p.exists()}}
        else:
            config = load_config(args.config, workflow=True)
            plan = read_json(root / 'workflow.json')
            validate_plan_config(plan, config)
            lock = project / '.cache/environment-locks' / (digest([plan['baseUrl'], plan['request']['targetSid']]) + '.lock')
            with exclusive(lock), exclusive(root / 'workflow.lock'):
                try:
                    result = advance(Jenkins(config), plan, root, Services())
                except (DeploymentError, OSError, ValueError, KeyError, TypeError) as e:
                    save_receipt(root / 'progress.json', {'status': 'requires_attention', 'checkedAt': timestamp(),
                        'error': str(e) if isinstance(e, DeploymentError) else type(e).__name__})
                    raise
                save_receipt(root / 'progress.json', {'status': result['status'], 'checkedAt': timestamp()})
            if result['status'] == 'ready':
                result = {'status': 'ready', 'evidence': str(root / 'environment-evidence.json'), 'featureAcceptance': 'not_performed'}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (DeploymentError, OSError, ValueError, KeyError, TypeError) as e:
        # Never echo raw service stdout, exception bodies, params, or console logs.
        print(json.dumps({'error': str(e) if isinstance(e, DeploymentError) else type(e).__name__}))
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
