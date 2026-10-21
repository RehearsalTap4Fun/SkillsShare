"""Explain changes against explicit dependencies; uncertainty is never 'no impact'."""
from copy import deepcopy
from .core import require, canonical_hash


def baseline_hash(value):
    return canonical_hash({k: v for k, v in value.items() if k != 'evidenceBaselineHash'})


def _matches(kind, change, dependency):
    if kind == 'documents': return change.get('requirementId') in dependency.get('requirements', [])
    if kind == 'environment': return change.get('field') in dependency.get('environment', [])
    if kind == 'code':
        return any(c.get('role') == change.get('role') and c['path'] in {change.get('path'), change.get('previousPath')}
                   and (not c.get('symbol') or c['symbol'] in {change.get('symbol'), change.get('previousSymbol')}) for c in dependency.get('code', []))
    if kind == 'config':
        return any(all(str(c.get(k)) == str(change.get(k)) for k in ['workbook', 'sheet', 'key'])
                   and (not c.get('fields') or bool(set(c['fields']) & set(change.get('fields', [])))) for c in dependency.get('config', []))
    return False


def analyze(old, new, changes, dependencies):
    require(dependencies.get('schemaVersion') == 1 and old.get('schemaVersion') == new.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    deps = dependencies['assertions']; assertions = []; unmapped = []
    all_changes = [(kind, c) for kind in ['documents', 'code', 'config', 'environment'] for c in changes.get(kind, [])]
    for kind, c in all_changes:
        if not any(_matches(kind, c, d) for d in deps): unmapped.append({'kind': kind, 'change': c})
    old_hash = baseline_hash(old); new_hash = baseline_hash(new)
    implicit = []
    unknown_kinds = set(changes) - {'documents', 'code', 'config', 'environment', 'coverage'}
    if unknown_kinds: implicit.append('unknown_change_kinds:' + ','.join(sorted(unknown_kinds)))
    if old.get('documentVersion') != new.get('documentVersion') and not changes.get('documents'): implicit.append('document_diff_missing')
    if old.get('repositories') != new.get('repositories') and not (changes.get('code') or changes.get('config')): implicit.append('repository_diff_missing')
    if old.get('environment') != new.get('environment') and not changes.get('environment'): implicit.append('environment_diff_missing')
    old_repos = {r['role']: r.get('sha') for r in old.get('repositories', [])}
    new_repos = {r['role']: r.get('sha') for r in new.get('repositories', [])}
    changed_roles = {r for r in old_repos.keys() | new_repos.keys() if old_repos.get(r) != new_repos.get(r)}
    coverage = changes.get('coverage', {})
    for role in sorted(changed_roles):
        covered = coverage.get('repositories', {}).get(role, {})
        if not (covered.get('complete') is True and covered.get('oldSha') == old_repos.get(role) and covered.get('newSha') == new_repos.get(role)):
            implicit.append('repository_coverage_missing:' + role)
    old_env, new_env = old.get('environment', {}), new.get('environment', {})
    for field in old_env.keys() | new_env.keys():
        if old_env.get(field) != new_env.get(field) and not any(c.get('field') == field for c in changes.get('environment', [])):
            implicit.append('environment_field_diff_missing:' + field)
    for d in deps:
        matched = [{'kind': k, 'change': c} for k, c in all_changes if _matches(k, c, d)]
        reasons = []; classification = 'reuse_candidate'
        if unmapped or implicit or d.get('coverage') != 'complete' or old.get('evidenceBaselineHash') != old_hash:
            classification = 'unknown'; reasons = ['incomplete_dependency_or_evidence_provenance', *implicit]
        if matched or d.get('dynamicTiming'):
            classification = 'must_rerun'; reasons = ['dependency_changed' if matched else 'dynamic_timing_requires_fresh_execution']
        assertions.append({'assertionId': d['assertionId'], 'classification': classification, 'reasons': reasons or ['explicit_dependencies_unchanged_candidate_only'],
            'sourceRefs': [m['change'].get('ref', m['change']) for m in matched], 'dependencyPaths': matched,
            'dynamicTiming': bool(d.get('dynamicTiming')), 'oldBaselineHash': old_hash, 'newBaselineHash': new_hash})
    return {'schemaVersion': 1, 'assertions': assertions, 'unmappedChanges': unmapped, 'implicitGaps': implicit,
            'oldBaseline': deepcopy(old), 'newBaseline': deepcopy(new),
            'inputHashes': {k: canonical_hash(v) for k, v in [('old', old), ('new', new), ('changes', changes), ('dependencies', dependencies)]}}
