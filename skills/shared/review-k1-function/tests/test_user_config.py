import importlib
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def config_module():
    return importlib.import_module('user_config')


def profile(tmp_path, monkeypatch, **updates):
    data = {'schemaVersion': 1, 'workspaceRoot': str(tmp_path / 'work'),
            'repositories': {r: str(tmp_path / ('source-' + r)) for r in ('client', 'server', 'config')}}
    data.update(updates)
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('K1_REVIEW_PROFILE', str(path))
    return data


def test_missing_profile_never_falls_back(monkeypatch):
    monkeypatch.delenv('K1_REVIEW_PROFILE', raising=False)
    with pytest.raises(ValueError, match='USER_PROFILE_REQUIRED'):
        config_module().load_profile()


def test_paths_are_loaded_from_user_profile(tmp_path, monkeypatch):
    data = profile(tmp_path, monkeypatch)
    loaded = config_module().load_profile(required_roles=('server', 'config'))
    assert Path(loaded['repositories']['server']) == Path(data['repositories']['server'])
    assert config_module().workspace_root() == Path(data['workspaceRoot'])


@pytest.mark.parametrize('value', ['', '<WORKSPACE_ROOT>', 'relative/work', 'C:/EXAMPLE/work'])
def test_blank_relative_and_example_paths_rejected(tmp_path, monkeypatch, value):
    profile(tmp_path, monkeypatch, workspaceRoot=value)
    with pytest.raises(ValueError, match='PROFILE_PATH_INVALID'):
        config_module().load_profile()


def test_workspace_and_formal_repo_cannot_overlap(tmp_path, monkeypatch):
    data = profile(tmp_path, monkeypatch)
    data['repositories']['server'] = str(tmp_path / 'work' / 'official')
    profile(tmp_path, monkeypatch, **data)
    with pytest.raises(ValueError, match='WORKSPACE_REPOSITORY_OVERLAP'):
        config_module().load_profile()


def test_writes_reject_configured_source_and_allow_workspace(tmp_path, monkeypatch):
    data = profile(tmp_path, monkeypatch)
    from acceptance.core import atomic_json
    target = Path(data['repositories']['server']) / 'should-not-exist.json'
    with pytest.raises(ValueError, match='FORMAL_REPOSITORY_READ_ONLY'):
        atomic_json(target, {'x': 1})
    assert not target.exists()
    output = Path(data['workspaceRoot']) / 'reports/run/state.json'
    atomic_json(output, {'ok': True})
    assert json.loads(output.read_text()) == {'ok': True}
