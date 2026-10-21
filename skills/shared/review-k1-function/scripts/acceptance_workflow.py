"""Local acceptance workflow CLI; external actions require their connected tools."""
import argparse
import json
from pathlib import Path
import sys
from acceptance.core import RunStore, WorkflowError, read_json, require, reject_formal_write
from acceptance import evidence, operations


def execute(domain, action, request, root):
    reject_formal_write(root)
    store = RunStore(root)
    if domain == 'run':
        if action == 'init': return store.create(request)
        if action == 'show': return store.read()
    if domain == 'operation':
        if action == 'prepare': return operations.prepare(store, request['kind'], request['target'], request['inputs'])
        if action == 'next': return operations.next_actions(store)
        if action == 'dispatch': return operations.dispatch(store, request['operationId'])
        if action == 'receipt': return operations.record_receipt(store, request['operationId'], request['receipt'])
    if domain == 'evidence':
        if action == 'add': return evidence.register(store, Path(request['source']), request['metadata'])
        if action == 'verify': return evidence.verify_package(store)
        if action == 'review': return evidence.record_review(store, request['evidenceId'], request['observation'], request['reviewer'])
    if domain == 'feedback':
        from acceptance import feedback, dingtalk
        if action == 'plan': return feedback.make_patch(request['before'], request['proposals'])
        if action == 'verify': return feedback.verify_patch(request['patch'], request['after'])
        client = dingtalk.DwsClient(request['executable'])
        if action == 'snapshot': return dingtalk.read_snapshot(client, request['target'], request['fieldMap'], request.get('exportReceipt'))
        if action == 'apply': return dingtalk.execute_patch(client, store, request['patch'], request.get('capabilities', {}), request.get('exportReceipt'))
        if action == 'reconcile': return dingtalk.reconcile_patch(client, store, request['patch'], request.get('browserReceipts', []), request.get('exportReceipt'))
    if domain == 'unity':
        from acceptance import unity
        if action == 'build': return unity.build_unity_action(request['adapter'], request['action'], request['parameters'], request['environment'])
        if action == 'receipt': return unity.parse_unity_receipt(request['action'], request['result'])
    if domain == 'fixture':
        from acceptance import fixtures
        if action == 'list': return read_json(Path(__file__).parent.parent / 'assets/acceptance/gm-fixtures.json')
        if action == 'plan': return fixtures.plan_fixture(request['definition'], request['parameters'], request['snapshot'], request['scope'], request.get('budget'))
        if action == 'verify': return fixtures.verify_fixture(request['plan'], request['before'], request['after'])
        if action == 'cleanup-plan': return fixtures.plan_cleanup(request['plan'], request['applied'], request['current'])
    if domain == 'recording':
        from acceptance import recording
        if action == 'start': return recording.start(store, request['context'])
        if action == 'import': return recording.append_events(store, request['recordingId'], request['events'])
        if action == 'finish': return recording.finish(store, request['recordingId'], request['cleanupReceipt'])
        if action == 'report': return str(recording.render_timeline(store, request['recordingId'], store.root / ('timeline-' + request['recordingId'] + '.html')))
    if domain == 'replay':
        from acceptance import replay
        if action == 'plan': return replay.register(store, request['recipe'], request['environment'], request['adapter'])
        if action in {'next', 'receipt'}: return replay.advance(store, request['planId'], request['observation'])
    if domain == 'impact':
        from acceptance import impact
        if action == 'analyze': return impact.analyze(request['old'], request['new'], request['changes'], request['dependencies'])
    if domain == 'runtime':
        from acceptance import runtime_bridge
        if action == 'create': return runtime_bridge.create_batch(request['baseline'], request['assertions'], request['impact'], root)
        if action == 'next': return runtime_bridge.evaluate_run(root)
        if action == 'reuse': return runtime_bridge.record_reuse(root, request)
        if action == 'publish': return runtime_bridge.publish_run(root, Path(request['output']))
    if domain == 'scenario':
        from acceptance import scenarios, config_variants
        if action == 'list': return read_json(Path(__file__).parent.parent / 'assets/acceptance/scenarios/gold-miner.json')
        if action == 'generate': return config_variants.generate(request['recipe'], request['sources'], request['target'], Path(request['output']))
        if action == 'prepare': return scenarios.prepare(request['definition'], request['bundle'], request['environment'], request['scope'])
        if action == 'verify': return scenarios.verify_loaded(request['plan'], request['observed'])
        if action == 'deploy':
            require(request.get('execute') is True, 'EXPLICIT_EXECUTE_REQUIRED')
            from jenkins_test_environment import Jenkins
            return scenarios.submit_deployment(request['plan'], Jenkins(read_json(request['config'])), Path(request['receiptPath']))
    if domain == 'task':
        from acceptance import tasks
        if action == 'register': return tasks.register(root, request)
        if action == 'show': return tasks.view(root, request['runId'])
        if action == 'trigger': return tasks.trigger(root, request)
        if action == 'command': return tasks.command(root, request['runId'], request['name'], request.get('payload', {}))
    raise WorkflowError('COMMAND_NOT_SUPPORTED')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        parser = argparse.ArgumentParser(description='Local acceptance workbench')
        parser.add_argument('--root', required=True); parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', type=int, default=0)
        args = parser.parse_args(sys.argv[2:])
        from acceptance.workbench import serve
        serve(Path(args.root), args.host, args.port)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', help='run | operation | evidence | feedback | fixture | unity | recording | replay | impact | runtime | scenario | task')
    parser.add_argument('action', help='Use the workflow reference for action request schemas')
    parser.add_argument('--request')
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    try:
        result = execute(args.domain, args.action, read_json(args.request) if args.request else {}, Path(args.run_dir))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (WorkflowError, OSError, ValueError, KeyError, TypeError) as e:
        print(json.dumps({'error': e.code if isinstance(e, WorkflowError) else type(e).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
