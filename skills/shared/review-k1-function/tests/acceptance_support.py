"""Fixtures for acceptance modules; no live services are contacted."""
import importlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def module(name):
    assert (SCRIPTS / 'acceptance' / (name + '.py')).is_file(), f'{name} capability is not implemented'
    return importlib.import_module('acceptance.' + name)


def metadata():
    return {'schemaVersion': 1, 'runId': 'example-run', 'baseline': {
        'documentVersion': 'r1', 'testcaseDocumentVersion': 'r1',
        'repositories': [{'role': r, 'sha': c * 40} for r, c in [('client', 'a'), ('server', 'b'), ('config', 'c')]]},
        'environment': {'serverId': '1234', 'accountId': 'test-player', 'activityId': 'test-activity', 'clientIdentity': 'a' * 40}}


def new_store(tmp_path):
    store = module('core').RunStore(tmp_path / 'run')
    store.create(metadata())
    return store
