"""Compile fixed, source-bound recipes into MCP actions for the current session."""
import base64
from pathlib import Path
import json
import re
from .core import require, canonical_hash, file_hash
from .fixtures import validate_parameters

ASSETS = Path(__file__).resolve().parents[2] / 'assets/acceptance/unity'
SCHEMAS = {
    'inspect': {}, 'stamina': {'amount': {'type': 'integer', 'minimum': -100, 'maximum': 100}},
    'level': {'level': {'type': 'integer', 'minimum': 1, 'maximum': 10000}},
    'talent': {'group': {'type': 'integer', 'minimum': 1, 'maximum': 10000}, 'level': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
    'heartbeat_timeout': {}, 'connection_close': {},
}


def validate_adapter(adapter, source_identity, capabilities):
    require(adapter.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    binding = adapter.get('sourceIdentity', {})
    require(bound(binding.get('sha')) and bool(binding.get('files'))
            and all(bound(v) for v in binding['files'].values()), 'SOURCE_BINDING_REQUIRED')
    require(adapter['sourceIdentity'] == source_identity, 'SOURCE_IDENTITY_MISMATCH')
    require(set(adapter['requiredTypes']).issubset(capabilities.get('types', [])), 'RUNTIME_TYPE_MISMATCH')
    return {'status': 'verified', 'differences': [], 'evidenceIds': capabilities.get('evidenceIds', [])}


def bound(value):
    return (isinstance(value, (str, int)) and bool(str(value).strip())
            and str(value).strip().lower() not in {'unknown', 'todo', 'replace_me', 'none', '0'}
            and not re.search(r'[<>]|\$\{|YOUR[_-]|EXAMPLE', str(value), re.I))


def source_identity(repository, files):
    import subprocess
    sha = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
    return {'sha': sha, 'files': {name: file_hash(Path(repository) / name) for name in files}}


def build_unity_action(adapter, action, parameters, environment):
    identity = adapter.get('identity', {})
    require(all(bound(identity.get(k)) for k in ('accountId', 'serverId', 'activityId', 'clientIdentity', 'sessionId')),
            'ADAPTER_IDENTITY_REQUIRED')
    validate_adapter(adapter, environment.get('sourceIdentity', {}), {'types': environment.get('types', [])})
    require(action in SCHEMAS, 'ACTION_NOT_SUPPORTED')
    if action != 'inspect':
        require(bound(adapter.get('runtimeAssembly')), 'RUNTIME_ASSEMBLY_REQUIRED')
    validate_parameters(SCHEMAS[action], parameters)
    require(environment['identity'] == adapter['identity'], 'IDENTITY_MISMATCH')
    require(environment['adapterVersion'] == adapter['version'], 'ADAPTER_VERSION')
    require(environment.get('instanceId') in environment.get('instances', []), 'UNITY_INSTANCE_REQUIRED')
    payload = {**parameters, 'action': action, 'identity': environment['identity'], 'runtimeAssembly': adapter.get('runtimeAssembly')}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    template = 'gold-miner-inspect.cs' if action == 'inspect' else 'gold-miner-apply.cs'
    code = (ASSETS / template).read_text(encoding='utf-8').replace('__PARAMETERS_BASE64__', encoded)
    return {'schemaVersion': 1, 'actionId': canonical_hash([adapter['version'], payload])[:24], 'adapter': 'unity',
            'action': action, 'parameters': parameters, 'identity': environment['identity'], 'instanceId': environment['instanceId'],
            'preconditions': {'sourceIdentity': adapter['sourceIdentity'], 'inspectImmediatelyBeforeDispatch': True},
            'mcp': {'tool': 'execute_code', 'arguments': {'action': 'execute', 'code': code, 'compiler': 'auto', 'safety_checks': True}},
            'expectedEvidence': ['protocol', 'state_after'], 'mutationScope': action}


def parse_unity_receipt(action, result):
    data = result.get('data', {}).get('result', result)
    identity = data.get('identity')
    return {'status': 'verification_pending' if result.get('success') and identity == action['identity'] else 'incomplete',
            'actionId': action['actionId'], 'identityMatches': identity == action['identity'],
            'acknowledged': bool(result.get('success')), 'requires': 'independent state and business-response verification'}
