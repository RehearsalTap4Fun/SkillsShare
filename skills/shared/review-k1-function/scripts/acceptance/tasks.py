"""Persistent local tasks and explicit triggers. No remote side effects here."""
from pathlib import Path
import re
from .core import RunStore, require, read_json, atomic_json, exclusive, owned_path, canonical_hash, redact

COMMANDS = {'next', 'feedback-plan', 'fixture-plan', 'replay-plan', 'impact-analyze', 'scenario-prepare', 'publish'}


def _root(root, run_id):
    require(isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', run_id), 'RUN_ID')
    return owned_path(root, 'runs/' + run_id)


def _registry(root):
    path = owned_path(root, 'registry.json')
    return read_json(path) if path.exists() else {'schemaVersion': 1, 'runs': {}, 'events': {}}


def _register(root, request, registry):
    store = RunStore(_root(root, request['runId'])); sha = canonical_hash(request)
    old = registry['runs'].get(request['runId'])
    if old:
        require(old['requestHash'] == sha, 'RUN_CONFLICT'); return old
    if store.path.exists():
        require(store.read().get('registrationHash') == sha, 'ORPHAN_RUN_CONFLICT')
    else:
        store.create({**request, 'registrationHash': sha, 'pendingActions': [], 'commandResults': {},
                      'validationLayers': {'codeValidation': 'unknown', 'exampleIntegration': 'unknown', 'liveIntegration': 'needs_environment', 'businessAcceptance': 'pending'}})
    record = {'runId': request['runId'], 'requestHash': sha}
    registry['runs'][request['runId']] = record
    return record


def register(root, request):
    with exclusive(root):
        registry = _registry(root); result = _register(root, request, registry)
        atomic_json(owned_path(root, 'registry.json'), registry)
    return result


def trigger(root, event):
    require(event.get('schemaVersion') == 1 and event.get('eventId') and event.get('kind') in {'document', 'manifest'}, 'EVENT_INVALID')
    with exclusive(root):
        registry = _registry(root); sha = canonical_hash(event); old = registry['events'].get(event['eventId'])
        if old:
            require(old['sha256'] == sha, 'EVENT_CONFLICT'); return old
        run = _register(root, event['run'], registry)
        result = {'runId': run['runId'], 'eventId': event['eventId'], 'sha256': sha}
        registry['events'][event['eventId']] = result; atomic_json(owned_path(root, 'registry.json'), registry)
    return result


def view(root, run_id):
    require(run_id in _registry(root)['runs'], 'RUN_NOT_REGISTERED')
    return redact(RunStore(_root(root, run_id)).read())


def list_runs(root):
    return [{'runId': key, 'revision': view(root, key)['revision'], 'validationLayers': view(root, key)['validationLayers']} for key in _registry(root)['runs']]


def command(root, run_id, name, payload):
    require(name in COMMANDS, 'COMMAND_NOT_SUPPORTED')
    view(root, run_id); store = RunStore(_root(root, run_id)); state = store.read(); key = canonical_hash([name, payload])
    if name != 'next' and key in state['commandResults']: return state['commandResults'][key]
    if name == 'next':
        from .operations import next_actions
        return {'operations': next_actions(store), 'pendingActions': state['pendingActions']}
    if name == 'feedback-plan':
        from .feedback import make_patch
        result = make_patch(payload['before'], payload['proposals'])
    elif name == 'fixture-plan':
        from .fixtures import plan_fixture
        result = plan_fixture(payload['definition'], payload['parameters'], payload['snapshot'], payload['scope'], payload.get('budget'))
    elif name == 'replay-plan':
        from .replay import plan
        result = plan(payload['recipe'], payload['environment'], payload['adapter'])
    elif name == 'impact-analyze':
        from .impact import analyze
        result = analyze(payload['old'], payload['new'], payload['changes'], payload['dependencies'])
    elif name == 'scenario-prepare':
        from .scenarios import prepare
        result = prepare(payload['definition'], payload['bundle'], payload['environment'], payload['scope'])
    else:
        from .runtime_bridge import publish_run
        result = publish_run(owned_path(store.root, 'runtime'), owned_path(store.root, 'published'))
    answer = {'actionId': key[:24], 'command': name, 'result': result, 'status': 'prepared' if name in {'feedback-plan', 'fixture-plan', 'replay-plan', 'scenario-prepare'} else 'local_complete'}
    def change(s):
        s['commandResults'][key] = answer
        if answer['status'] == 'prepared': s['pendingActions'].append(answer)
        return s
    store.update(state['revision'], change)
    return answer
