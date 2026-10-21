import json
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def isolated_user_profile(tmp_path, monkeypatch):
    profile = {'schemaVersion': 1, 'workspaceRoot': str(tmp_path),
               'repositories': {r: str(tmp_path.parent / (tmp_path.name + '-source-' + r)) for r in ('client', 'server', 'config')}}
    path = tmp_path.parent / (tmp_path.name + '-profile.json')
    path.write_text(json.dumps(profile), encoding='utf-8')
    monkeypatch.setenv('K1_REVIEW_PROFILE', str(path))
    return path
