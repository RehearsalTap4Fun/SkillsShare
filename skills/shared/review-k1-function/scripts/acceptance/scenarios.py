"""Preparation is distinct from deployment and observed configuration loading."""
from copy import deepcopy
from pathlib import Path
from .core import require, read_json, canonical_hash


def prepare(definition, bundle, environment, scope):
    require(definition.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    missing = [c for c in definition.get('requiredConditions', []) if not environment.get(c)]
    if not environment.get('serverId'): missing.append('serverId')
    if not environment.get('accountId'): missing.append('accountId')
    authorized = (scope.get('mode') == 'isolated' and bool(scope.get('authorization')) and str(environment.get('serverId')) in scope.get('serverIds', [])
                  and str(environment.get('accountId')) in scope.get('accountIds', []))
    if not authorized: missing.append('authorized_isolated_environment')
    if definition.get('naturalExpiry') and environment.get('expiryMethod') != 'natural': missing.append('natural_expiry_not_forced_end')
    p = {'schemaVersion': 1, 'assertionId': definition['assertionId'], 'definition': deepcopy(definition), 'bundle': deepcopy(bundle),
         'environment': deepcopy(environment), 'scope': deepcopy(scope), 'missingConditions': sorted(set(missing)),
         'liveValidation': 'needs_authorized_environment' if not authorized else ('needs_conditions' if missing else 'ready_for_deployment'),
         'expectedLoaded': deepcopy(environment.get('expectedLoaded', {}))}
    p['planId'] = canonical_hash(p)[:24]
    return p


def _authorized(plan):
    fresh = prepare(plan['definition'], plan['bundle'], plan['environment'], plan['scope'])
    return fresh['liveValidation'] == 'ready_for_deployment'


def prepare_deployment(plan, client):
    require(_authorized(plan), 'DEPLOYMENT_NOT_AUTHORIZED')
    request = plan['deploymentRequest']; scope = plan['scope']; sid = str(plan['environment']['serverId'])
    require(request.get('parameters', {}).get('server_id') == sid
            and set(request.get('scope', {}).get('serverIds', [])) <= set(scope['serverIds'])
            and request.get('scope', {}).get('authorization') == scope['authorization'], 'DEPLOYMENT_TARGET_MISMATCH')
    import jenkins_test_environment as jenkins
    return jenkins.prepare(client, plan['deploymentRequest'])


def submit_deployment(plan, client, receipt_path):
    if not _authorized(plan): return {'liveValidation': 'needs_authorized_environment', 'missingConditions': plan['missingConditions']}
    path = Path(receipt_path)
    if path.exists(): return {'liveValidation': 'reconcile_existing_receipt', 'receipt': read_json(path)}
    import jenkins_test_environment as jenkins
    prepared = prepare_deployment(plan, client)
    return jenkins.submit(client, prepared, path)


def verify_loaded(plan, observed):
    differences = [{'field': k, 'expected': v, 'actual': observed.get(k)} for k, v in plan.get('expectedLoaded', {}).items() if observed.get(k) != v]
    for key in ['serverId', 'accountId']:
        expected = plan.get('environment', {}).get(key)
        if key in observed and expected is not None and str(observed[key]) != str(expected):
            differences.append({'field': key, 'expected': expected, 'actual': observed[key]})
    status = 'mismatch' if differences else 'verified'
    if not differences and (observed.get('source') != 'live' or not observed.get('evidenceIds') or not plan.get('expectedLoaded')
                           or any(not observed.get(k) or not plan.get('environment', {}).get(k) for k in ['serverId', 'accountId'])): status = 'incomplete'
    return {'status': status, 'differences': differences, 'evidenceIds': observed.get('evidenceIds', [])}
