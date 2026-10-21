from copy import deepcopy
import pytest
from acceptance_support import module


def batch():
    b={'metadata':{'documentVersion':'r1','testcaseDocumentVersion':'r1'},'testcases':[{'id':'R1','expected':'works'}]}
    a=[{'id':'R1/check','testcaseId':'R1','expected':'works','action':'test normally','phase':0,'dependsOn':[], 'requiredEvidence':['runtime']}]
    c=module('core'); target={'schemaVersion':1,'documentVersion':'r1','runtimeBaselineSha256':c.canonical_hash(b)}
    i={'schemaVersion':1,'newBaseline':target,'assertions':[{'assertionId':'R1/check','classification':'reuse_candidate','dynamicTiming':False,'oldBaselineHash':'old','newBaselineHash':module('impact').baseline_hash(target)}]}
    return b,a,i


def test_candidates_start_pending_and_case_coverage_preserved(tmp_path):
    r=module('runtime_bridge');b,a,i=batch();result=r.create_batch(b,a,i,tmp_path/'batch')
    from acceptance.core import read_json
    state=read_json(tmp_path/'batch/state.json');plan=read_json(tmp_path/'batch/plan.json')
    assert {x['status'] for x in state['results'].values()}=={'pending'}
    assert {x['testcaseId'] for x in plan['assertions']}=={x['id'] for x in b['testcases']}
    assert r.evaluate_run(tmp_path/'batch')['fullyAccepted'] is False
    with pytest.raises(module('core').WorkflowError):r.create_batch(b,a,i,tmp_path/'batch')


def test_version_conflict_missing_case_and_unfinished_publish_rejected(tmp_path):
    r=module('runtime_bridge');b,a,i=batch();wrong=deepcopy(b);wrong['metadata']['documentVersion']='r2'
    with pytest.raises(module('core').WorkflowError):r.create_batch(wrong,a,i,tmp_path/'bad')
    wrong=deepcopy(b);wrong['testcases'].append({'id':'R2'})
    with pytest.raises(module('core').WorkflowError):r.create_batch(wrong,a,i,tmp_path/'missing')
    r.create_batch(b,a,i,tmp_path/'good')
    with pytest.raises(module('core').WorkflowError):r.publish_run(tmp_path/'good',tmp_path/'published')


def test_reuse_requires_equivalence_evidence(tmp_path):
    r=module('runtime_bridge');b,a,i=batch();r.create_batch(b,a,i,tmp_path/'batch')
    with pytest.raises(module('core').WorkflowError,match='REUSE_EVIDENCE_REQUIRED'):
        r.record_reuse(tmp_path/'batch',{'assertionId':'R1/check','confirmedBy':'human'})


def test_workflow_create_preserves_mode_and_schedule(tmp_path):
    import acceptance_workflow
    b, a, i = batch()
    planning = {
        'acceptanceMode': 'mainline_regression',
        'documentBaseline': {'status': 'needs_review', 'source': 'design-doc',
                             'checkedAt': '2026-10-21T10:00:00+08:00', 'reason': '尚未读取当前版本'},
        'executionGroups': [{'id': 'play', 'batch': 0, 'owner': 'tester-a', 'setup': '正常玩家账号',
                             'assertionIds': ['R1/check'], 'resources': [{'id': 'unity-a', 'access': 'write'}]}]}
    acceptance_workflow.execute('runtime', 'create', {'baseline': b, 'assertions': a, 'impact': i, 'planning': planning}, tmp_path / 'batch')
    plan = module('core').read_json(tmp_path / 'batch/plan.json')
    assert plan['schemaVersion'] == 2
    assert plan['acceptanceMode'] == 'mainline_regression'
    assert plan['executionGroups'] == planning['executionGroups']
    result = module('runtime_bridge').evaluate_run(tmp_path / 'batch')
    assert result['dispatchGroups'][0]['owner'] == 'tester-a'
    assert result['documentStatus'] == '待复核'
