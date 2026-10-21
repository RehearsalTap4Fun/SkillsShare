"""Versioned, atomic local records. No external services are invoked here."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import uuid


class WorkflowError(ValueError):
    def __init__(self, code: str, detail: dict | None = None):
        self.code, self.detail = code, detail or {}
        super().__init__(code)


def require(condition, code, detail=None):
    if not condition:
        raise WorkflowError(code, detail)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
        return result.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def owned_path(root, relative):
    root = Path(root).resolve()
    require(isinstance(relative, str) and relative and '\x00' not in relative, 'PATH_OUTSIDE_RUN')
    rel = Path(relative)
    require(not rel.is_absolute() and not PureWindowsPath(relative).drive, 'PATH_OUTSIDE_RUN')
    target = (root / rel).resolve()
    require(target != root and target.is_relative_to(root), 'PATH_OUTSIDE_RUN')
    return target


def reject_formal_write(path):
    from user_config import check_write_path, UserConfigError
    try:
        check_write_path(path)
    except UserConfigError as error:
        raise WorkflowError(str(error)) from None


def atomic_json(path, value):
    path = Path(path)
    reject_formal_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def exclusive(root, name='.lock'):
    root = Path(root)
    reject_formal_write(root)
    root.mkdir(parents=True, exist_ok=True)
    path = owned_path(root, name)
    try:
        stream = path.open('x', encoding='utf-8')
    except FileExistsError as e:
        raise WorkflowError('STORE_BUSY') from e
    try:
        with stream:
            stream.write(json.dumps({'pid': os.getpid(), 'createdAt': now()})); stream.flush()
            yield
    finally:
        path.unlink()


def redact(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if re.search(r'password|token|secret|authorization|cookie|downkey|api.?key', k, re.I) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


def validate_metadata(data):
    require(data.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    require(isinstance(data.get('runId'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', data['runId']), 'RUN_ID')
    b = data.get('baseline', {})
    require(bool(b.get('documentVersion')) and b['documentVersion'] == b.get('testcaseDocumentVersion'), 'BASELINE_VERSION')
    repos = b.get('repositories', [])
    require({r.get('role') for r in repos} == {'client', 'server', 'config'} and len(repos) == 3, 'BASELINE_REPOSITORIES')
    require(all(re.fullmatch('[a-fA-F0-9]{40}', r.get('sha', '')) for r in repos), 'BASELINE_SHA')
    require(isinstance(data.get('environment'), dict), 'ENVIRONMENT_REQUIRED')


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = owned_path(self.root, 'run.json')

    def create(self, metadata: dict) -> dict:
        validate_metadata(metadata)
        with exclusive(self.root):
            require(not self.path.exists(), 'RUN_EXISTS')
            data = {**deepcopy(metadata), 'revision': 0, 'createdAt': now(), 'evidence': {}, 'operations': {}}
            atomic_json(self.path, data)
        return data

    def read(self) -> dict:
        data = read_json(owned_path(self.root, 'run.json'))
        validate_metadata(data)
        require(type(data.get('revision')) is int and data['revision'] >= 0, 'REVISION_INVALID')
        return data

    def update(self, expected_revision: int, transform) -> dict:
        with exclusive(self.root):
            before = self.read()
            require(before['revision'] == expected_revision, 'REVISION_CONFLICT')
            result = transform(deepcopy(before))
            validate_metadata(result)
            require(result['runId'] == before['runId'] and result['baseline'] == before['baseline'], 'RUN_IDENTITY_IMMUTABLE')
            result['revision'] = before['revision'] + 1
            result['updatedAt'] = now()
            atomic_json(owned_path(self.root, 'run.json'), result)
            return result
