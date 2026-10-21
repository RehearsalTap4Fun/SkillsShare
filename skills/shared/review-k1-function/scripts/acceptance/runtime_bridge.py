"""New-batch adapter to the existing runtime acceptance contract."""
from pathlib import Path
import shutil
import uuid
import runtime_acceptance as runtime
from .core import WorkflowError, require, atomic_json, read_json, file_hash, owned_path, reject_formal_write, exclusive, canonical_hash
from .impact import baseline_hash


def validate_impact_binding(baseline, impact):
    if not impact.get('assertions'): return  # Empty analysis permits pending execution only, never reuse.
    target = impact.get('newBaseline', {})
    require(target.get('runtimeBaselineSha256') == canonical_hash(baseline)
            and target.get('documentVersion') == baseline['metadata']['documentVersion']
            and all(a.get('newBaselineHash') == baseline_hash(target) for a in impact['assertions']), 'IMPACT_BASELINE_MISMATCH')


def _call(fn, *args):
    try: return fn(*args)
    except runtime.RuntimeContractError as e: raise WorkflowError(str(e)) from e


def create_batch(baseline, assertions, impact, output, planning=None):
    output = Path(output).resolve(); reject_formal_write(output)
    require(not output.exists(), 'BATCH_EXISTS')
    metadata = baseline['metadata']
    require(metadata.get('documentVersion') and metadata['documentVersion'] == metadata.get('testcaseDocumentVersion'), 'BASELINE_VERSION')
    require({a['testcaseId'] for a in assertions} == {c['id'] for c in baseline['testcases']}, 'CASE_COVERAGE')
    validate_impact_binding(baseline, impact)
    if planning is not None:
        require(isinstance(planning, dict) and set(planning) == {'acceptanceMode', 'documentBaseline', 'executionGroups'}, 'PLANNING_FIELDS_INVALID')
        _call(runtime.validate_planning, {**planning, 'assertions': assertions}, baseline, output)
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / 'baseline.json', baseline)
    plan = {'schemaVersion': 1, 'runId': output.name, 'baselineFile': 'baseline.json', 'baselineSha256': file_hash(output / 'baseline.json'), 'assertions': assertions}
    if planning is not None:
        plan.update(planning, schemaVersion=2)
    state = _call(runtime.initialize, plan, output)
    atomic_json(output / 'plan.json', plan); atomic_json(output / 'state.json', state); atomic_json(output / 'impact.json', impact)
    return {'runId': plan['runId'], 'path': str(output), 'status': 'pending', 'impact': impact}


def evaluate_run(run_dir):
    root = Path(run_dir).resolve()
    return _call(runtime.evaluate, read_json(root / 'plan.json'), read_json(root / 'state.json'), root)


def record_reuse(run_dir, decision):
    root = Path(run_dir).resolve()
    require(decision.get('equivalenceEvidence') and decision.get('sourceEvidence') and decision.get('confirmedBy') and decision.get('sourceEvidenceId'), 'REUSE_EVIDENCE_REQUIRED')
    with exclusive(root):
        plan = read_json(root / 'plan.json'); state = read_json(root / 'state.json'); impact = read_json(root / 'impact.json')
        baseline = read_json(owned_path(root, plan['baselineFile']))
        require(file_hash(owned_path(root, plan['baselineFile'])) == plan['baselineSha256'], 'IMPACT_BASELINE_MISMATCH')
        validate_impact_binding(baseline, impact)
        items = [i for i in impact['assertions'] if i['assertionId'] == decision['assertionId']]
        require(len(items) == 1 and items[0]['classification'] == 'reuse_candidate' and not items[0].get('dynamicTiming'), 'REUSE_NOT_ELIGIBLE')
        item = items[0]
        require(item['oldBaselineHash'] == decision.get('oldBaselineHash') and item['newBaselineHash'] == decision.get('newBaselineHash'), 'REUSE_BASELINE_MISMATCH')
        require(state['results'][decision['assertionId']]['status'] == 'pending', 'REUSE_STATUS_CONFLICT')
        entries = []
        for sources, expected_hash in [(decision['sourceEvidence'], item['oldBaselineHash']), (decision['equivalenceEvidence'], item['newBaselineHash'])]:
            require(all(e.get('assertionId') == decision['assertionId'] and e.get('baselineHash') == expected_hash for e in sources), 'REUSE_EVIDENCE_PROVENANCE')
        for source in decision['sourceEvidence'] + decision['equivalenceEvidence']:
            path = Path(source['path'])
            require(path.is_file() and file_hash(path) == source['sha256'], 'REUSE_EVIDENCE_HASH')
            require(source['kind'] in runtime.KINDS, 'REUSE_EVIDENCE_KIND')
            dest = owned_path(root, 'inherited/' + source['sha256'] + path.suffix)
            dest.parent.mkdir(exist_ok=True); shutil.copyfile(path, dest)
            entries.append({'kind': source['kind'], 'path': str(dest.relative_to(root))})
        state['results'][decision['assertionId']] = {'status': 'passed', 'observation': '继承旧证据，非本轮重跑', 'evidence': entries, 'inheritance': decision}
        _call(runtime.evaluate, plan, state, root)
        atomic_json(root / 'state.json', state)
        return state['results'][decision['assertionId']]


def publish_run(run_dir, output):
    root = Path(run_dir).resolve(); output = Path(output).resolve(); reject_formal_write(output)
    result = _call(runtime.publish, read_json(root / 'plan.json'), read_json(root / 'state.json'), root, output)
    atomic_json(output / 'inheritance.json', {k: v['inheritance'] for k, v in read_json(root / 'state.json')['results'].items() if 'inheritance' in v})
    return result
