"""Runtime evidence ledger, continuation queue, phase gate and final publisher.

This validates recorded evidence, not its truth. The agent executes returned
actions with Unity/GM/Jenkins, then reads authoritative state into this ledger.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import sys


class RuntimeContractError(ValueError):
    pass


KINDS = {'ui', 'protocol', 'server', 'runtime', 'platform', 'isolated', 'static', 'gm'}
STATUSES = {'pending', 'partial', 'passed', 'failed', 'blocked', 'excluded', 'missed'}


def require(condition, code):
    if not condition:
        raise RuntimeContractError(code)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, value):
    from user_config import check_write_path
    check_write_path(path)
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def validate_plan(plan, root):
    require(plan.get('schemaVersion') == 1 and nonempty(plan.get('runId')), 'PLAN_INVALID')
    source = root / plan['baselineFile']
    require(hashlib.sha256(source.read_bytes()).hexdigest() == plan.get('baselineSha256'), 'BASELINE_CHANGED')
    baseline = read_json(source)
    metadata = baseline['metadata']
    require(nonempty(metadata.get('documentVersion')) and metadata['documentVersion'] == metadata.get('testcaseDocumentVersion'), 'DOCUMENT_VERSION_MISMATCH')
    cases = baseline.get('testcases', [])
    case_ids = {c['id'] for c in cases}
    require(bool(case_ids) and len(case_ids) == len(cases), 'BASELINE_CASES_INVALID')
    assertions = plan.get('assertions', [])
    ids = {a['id'] for a in assertions}
    require(bool(ids) and len(ids) == len(assertions), 'ASSERTION_IDS_INVALID')
    require({a['testcaseId'] for a in assertions} == case_ids, 'CASE_COVERAGE')
    for a in assertions:
        require(all(nonempty(a.get(k)) for k in ('id', 'expected', 'action')), 'ASSERTION_INVALID')
        require(type(a.get('phase')) is int and a['phase'] >= 0, 'PHASE_INVALID')
        require(isinstance(a.get('requiredEvidence'), list) and bool(a['requiredEvidence']) and set(a['requiredEvidence']) <= KINDS, 'EVIDENCE_SCOPE_INVALID')
        require(isinstance(a.get('dependsOn'), list) and set(a['dependsOn']) <= ids, 'DEPENDENCY_INVALID')
    by_id = {a['id']: a for a in assertions}
    visited, visiting = set(), set()

    def visit(aid):
        require(aid not in visiting, 'DEPENDENCY_CYCLE')
        if aid in visited:
            return
        visiting.add(aid)
        for dep in by_id[aid]['dependsOn']:
            require(by_id[dep]['phase'] <= by_id[aid]['phase'], 'DEPENDENCY_PHASE_INVALID')
            visit(dep)
        visiting.remove(aid)
        visited.add(aid)

    for aid in by_id:
        visit(aid)
    return baseline


def initialize(plan, root):
    validate_plan(plan, root)
    return {'schemaVersion': 1, 'planSha256': digest(plan), 'currentPhase': 0,
            'environmentEvidence': [], 'cleanup': {},
            'results': {a['id']: {'status': 'pending', 'nextAction': a['action']} for a in plan['assertions']}}


def evidence_kinds(entries, root, required=False):
    require(isinstance(entries, list) and (bool(entries) or not required), 'EVIDENCE_REQUIRED')
    kinds = set()
    for e in entries:
        require(isinstance(e, dict) and e.get('kind') in KINDS and nonempty(e.get('path')), 'EVIDENCE_INVALID')
        path = (root / e['path']).resolve()
        require(path.is_file() and path.stat().st_size > 0, 'EVIDENCE_FILE_MISSING')
        kinds.add(e['kind'])
    return kinds


def evaluate(plan, state, root):
    require(state.get('schemaVersion') == 1 and state.get('planSha256') == digest(plan), 'PLAN_CHANGED')
    baseline = validate_plan(plan, root)
    assertions = plan['assertions']
    results = state.get('results', {})
    require(set(results) == {a['id'] for a in assertions}, 'ASSERTION_COVERAGE')
    require(type(state.get('currentPhase')) is int and state['currentPhase'] >= 0, 'PHASE_INVALID')
    next_actions, waiting, unfinished = [], [], []
    for a in sorted(assertions, key=lambda x: (x['phase'], x['id'])):
        r = results[a['id']]
        status = r.get('status')
        require(status in STATUSES, 'STATUS_INVALID')
        kinds = evidence_kinds(r.get('evidence', []), root)
        actionable = status in {'pending', 'partial'}
        action = r.get('nextAction') or a['action']
        if status in {'passed', 'failed', 'partial'}:
            require(nonempty(r.get('observation')) and bool(kinds), 'OBSERVATION_REQUIRED')
        if status == 'passed':
            require(set(a['requiredEvidence']) <= kinds, 'EVIDENCE_SCOPE_MISMATCH')
        if status == 'failed':
            require(bool(set(a['requiredEvidence']) & kinds), 'EVIDENCE_SCOPE_MISMATCH')
        if status == 'blocked':
            b = r.get('blocker', {})
            require(b.get('kind') in {'environment', 'dependency', 'permission', 'information', 'tool'}, 'BLOCKER_INVALID')
            require(nonempty(b.get('reason')) and nonempty(b.get('nextAction')), 'BLOCKER_INVALID')
            attempts = b.get('attempts', [])
            require(bool(attempts) and all(nonempty(x.get('action')) and nonempty(x.get('outcome')) for x in attempts), 'BLOCKER_ATTEMPTS_REQUIRED')
            options = b.get('options', {})
            require(all(v in {'available', 'exhausted', 'unavailable', 'out_of_scope'} for v in options.values()), 'BLOCKER_OPTIONS_INVALID')
            if b['kind'] == 'environment':
                require({'gm', 'jenkins'} <= set(options), 'ENVIRONMENT_RECOVERY_NOT_ASSESSED')
            if b['kind'] == 'dependency':
                require(b.get('assertionId') in results, 'BLOCKER_DEPENDENCY_INVALID')
                require(b['assertionId'] in a['dependsOn'], 'BLOCKER_DEPENDENCY_INVALID')
            actionable = 'available' in options.values()
            if b['kind'] == 'dependency' and results[b['assertionId']]['status'] in {'passed', 'pending', 'partial'}:
                actionable = True
            action = b['nextAction']
        if status == 'excluded':
            require(nonempty(r.get('reason')) and nonempty(r.get('userDecision')), 'USER_DECISION_REQUIRED')
        if status == 'missed':
            require(nonempty(r.get('reason')) and nonempty(r.get('nextAction')) and nonempty(r.get('responsibility')), 'MISSED_WINDOW_RECORD_REQUIRED')
        if actionable:
            unfinished.append(a['id'])
            deps = [d for d in a['dependsOn'] if results[d]['status'] != 'passed']
            item = {'id': a['id'], 'testcaseId': a['testcaseId'], 'action': action, 'phase': a['phase']}
            if deps:
                waiting.append({**item, 'waitingFor': deps})
            else:
                next_actions.append(item)
    case_results = {}
    for case in baseline['testcases']:
        statuses = [results[a['id']]['status'] for a in assertions if a['testcaseId'] == case['id']]
        case_results[case['id']] = (
            '失败' if 'failed' in statuses else '通过' if all(s == 'passed' for s in statuses)
            else '部分验证' if any(s in {'passed', 'partial'} for s in statuses)
            else '阻塞' if 'blocked' in statuses else '执行遗漏' if 'missed' in statuses
            else '已排除' if all(s == 'excluded' for s in statuses) else '未验证')
    env = evidence_kinds(state.get('environmentEvidence', []), root)
    cleanup = state.get('cleanup', {})
    cleanup_ready = cleanup.get('verified') is True and nonempty(cleanup.get('retainedChanges'))
    if cleanup_ready:
        evidence_kinds(cleanup.get('evidence', []), root, required=True)
    ready = not unfinished and bool(env) and cleanup_ready
    return {'runId': plan['runId'], 'nextActions': next_actions, 'waiting': waiting,
            'unfinished': unfinished, 'canFinalize': ready,
            'fullyAccepted': ready and all(r['status'] == 'passed' for r in results.values()),
            'caseResults': case_results, 'counts': dict(Counter(case_results.values())),
            'missingClosure': [k for k, ok in [('environmentEvidence', bool(env)), ('cleanup', cleanup_ready)] if not ok]}


def check_advance(plan, state, root, target_phase):
    require(type(target_phase) is int and target_phase > state['currentPhase'], 'TIME_MUST_ADVANCE')
    evaluate(plan, state, root)
    expiring = [a['id'] for a in plan['assertions'] if a['phase'] < target_phase
                and state['results'][a['id']]['status'] not in {'passed', 'failed', 'excluded'}]
    require(not expiring, 'PHASE_INCOMPLETE:' + ','.join(expiring))
    return {'allowed': True, 'targetPhase': target_phase, 'note': '检查不修改游戏时间；推进后回读逐服时间再更新 currentPhase'}


def publish(plan, state, root, output):
    from user_config import check_write_path
    check_write_path(output)
    result = evaluate(plan, state, root)
    require(not result['unfinished'], 'RUNNABLE_OR_UNFINISHED')
    require(result['canFinalize'], 'CLOSURE_EVIDENCE_REQUIRED')
    h = html.escape
    rows = []
    for a in plan['assertions']:
        r = state['results'][a['id']]
        links = ' '.join('<a href="' + h((root / e['path']).resolve().as_uri(), quote=True) + '">' + h(e['kind']) + '</a>' for e in r.get('evidence', []))
        detail = r.get('observation') or r.get('reason') or r.get('blocker', {}).get('reason', '')
        next_step = r.get('nextAction') or r.get('blocker', {}).get('nextAction', '')
        rows.append('<tr>' + ''.join('<td>' + h(str(v)) + '</td>' for v in [a['testcaseId'], a['id'], a['expected'], r['status'], detail, next_step]) + '<td>' + links + '</td></tr>')
    verdict = '运行验收通过' if result['fullyAccepted'] else '本轮结束，未完整验收通过'
    document = '<!doctype html><meta charset="utf-8"><title>运行验收</title><style>body{font:16px sans-serif;margin:32px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:10px;text-align:left}</style>'
    document += '<h1>' + verdict + '</h1><p>' + h(json.dumps(result['counts'], ensure_ascii=False)) + '</p><p>用例与断言分别统计；隔离、GM、静态证据不能替代所需运行证据。台账校验不证明证据真实性。</p>'
    document += '<table><tr><th>用例</th><th>断言</th><th>预期</th><th>状态</th><th>观察/原因</th><th>补验动作</th><th>证据</th></tr>' + ''.join(rows) + '</table><h2>留存变化</h2><p>' + h(state['cleanup']['retainedChanges']) + '</p>'
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / 'runtime-result.json', {'summary': result, 'plan': plan, 'state': state})
    (output / 'summary.html').write_text(document, encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'next', 'check-advance', 'publish'])
    parser.add_argument('--plan', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--target-phase', type=int)
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        path = Path(args.plan).resolve()
        root, plan = path.parent, read_json(path)
        if args.command == 'init':
            state = initialize(plan, root)
            write_new(args.state, state)
            result = evaluate(plan, state, root)
        else:
            state = read_json(args.state)
            if args.command == 'check-advance':
                result = check_advance(plan, state, root, args.target_phase)
            elif args.command == 'publish':
                require(bool(args.output), 'OUTPUT_REQUIRED')
                result = publish(plan, state, root, Path(args.output))
            else:
                result = evaluate(plan, state, root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RuntimeContractError, OSError, ValueError, KeyError, TypeError) as e:
        print(json.dumps({'error': str(e) if isinstance(e, RuntimeContractError) else type(e).__name__}))
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
