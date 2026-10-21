"""Content-addressed local evidence, with separate human/agent image review."""
from copy import deepcopy
from pathlib import Path
import os
import shutil
import uuid
from .core import canonical_hash, file_hash, now, owned_path, require, reject_formal_write

KINDS = {'ui', 'protocol', 'server', 'runtime', 'platform', 'isolated', 'static', 'gm', 'original_image', 'document'}


def register(store, source: Path, metadata: dict) -> dict:
    reject_formal_write(store.root)
    source = Path(source).resolve()
    require(source.is_file() and source.stat().st_size > 0, 'EVIDENCE_FILE_MISSING')
    require(metadata.get('kind') in KINDS and bool(metadata.get('assertionId')), 'EVIDENCE_METADATA')
    sha = file_hash(source)
    state = store.read()
    eid = canonical_hash([state['runId'], metadata['assertionId'], metadata.get('feedbackId'), sha])[:24]
    if eid in state['evidence']:
        return state['evidence'][eid]
    extension = source.suffix.lower()
    require(len(extension) < 15 and all(c.isalnum() or c == '.' for c in extension), 'EVIDENCE_EXTENSION')
    relative = 'evidence/' + sha + extension
    target = owned_path(store.root, relative)
    reject_formal_write(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = owned_path(store.root, 'evidence/.' + uuid.uuid4().hex)
        try:
            shutil.copyfile(source, temporary)
            require(file_hash(temporary) == sha, 'EVIDENCE_CHANGED_DURING_COPY')
            os.replace(temporary, target)
        finally:
            if temporary.exists(): temporary.unlink()
    require(file_hash(target) == sha, 'EVIDENCE_HASH_MISMATCH')
    item = {**deepcopy(metadata), 'schemaVersion': 1, 'evidenceId': eid, 'runId': state['runId'],
            'path': relative, 'sha256': sha, 'capturedAt': metadata.get('capturedAt', now()),
            'sourceIdentity': metadata.get('sourceIdentity', deepcopy(state['environment'])),
            'review': {'status': 'unreviewed', 'observation': '', 'reviewedAt': None}}
    def change(data):
        data['evidence'][eid] = item
        return data
    store.update(state['revision'], change)
    return item


def record_review(store, evidence_id, observation, reviewer):
    require(bool(str(observation).strip()) and bool(str(reviewer).strip()), 'REVIEW_REQUIRED')
    state = store.read()
    require(evidence_id in state['evidence'], 'EVIDENCE_NOT_FOUND')
    def change(data):
        data['evidence'][evidence_id]['review'] = {'status': 'reviewed', 'observation': observation, 'reviewer': reviewer, 'reviewedAt': now()}
        return data
    return store.update(state['revision'], change)['evidence'][evidence_id]


def verify_package(store):
    differences = []
    for eid, item in store.read()['evidence'].items():
        path = owned_path(store.root, item['path'])
        if not path.is_file() or file_hash(path) != item['sha256']:
            differences.append({'evidenceId': eid, 'reason': 'missing_or_changed'})
    return {'status': 'mismatch' if differences else 'verified', 'differences': differences,
            'evidenceIds': list(store.read()['evidence'])}
