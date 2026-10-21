"""Conservative, pure feedback patch planning and read-back comparison."""
from copy import deepcopy
from .core import canonical_hash, require

STATUS = {'pending': '待验收', 'partial': '验收中', 'blocked': '验收中', 'excluded': '验收中',
          'missed': '验收中', 'failed': '未通过', 'passed': '已通过'}


def _row(snapshot, ident):
    rows = [r for r in snapshot['rows'] if r['id'] == ident]
    return rows[0] if len(rows) == 1 else None


def _hashes(row):
    return [i['sha256'] for i in row.get('images', [])]


def make_patch(before, proposals):
    require(before.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    fm = before['fieldMap']
    require(len(set(fm.values())) == len(fm), 'OVERLAPPING_FIELDS')
    patch = {'schemaVersion': 1, 'baselineHash': canonical_hash(before), 'before': deepcopy(before),
             'proposals': deepcopy(proposals), 'operations': [], 'conflicts': []}
    seen = set()
    for p in proposals:
        fid = p['feedbackId']; row = _row(before, fid); reasons = []
        if fid in seen: reasons.append('duplicate_proposal')
        seen.add(fid)
        if row is None: reasons.append('ambiguous_or_missing_row')
        if not before.get('imagesReadComplete'): reasons.append('image_coverage_incomplete')
        if STATUS.get(p.get('status')) != p.get('acceptance'): reasons.append('status_mapping')
        if p.get('acceptance') not in [v for rule in before['dataValidations'] for v in rule.get('conditionValues', [])]:
            reasons.append('acceptance_not_in_validation')
        if row:
            if row['values'].get(fm['problem']) != row['identity']['problemText']: reasons.append('problem_identity_changed')
            if not set(row['identity']['originalImages']).issubset(_hashes(row)): reasons.append('original_missing')
            if p.get('status') == 'passed':
                if not p.get('evidenceIds'): reasons.append('missing_pass_evidence')
                if any(i.get('review', {}).get('status') != 'reviewed' for i in row['images'] if i['sha256'] in row['identity']['originalImages']):
                    reasons.append('original_unreviewed')
        if reasons:
            patch['conflicts'].append({'feedbackId': fid, 'reasons': reasons}); continue
        common = {'feedbackId': fid}
        values = row['values']
        details = str(values.get(fm['details'], '') or '')
        note = str(p.get('details', '') or '').strip()
        if note and note not in details.split('\n'): details = '\n'.join(filter(None, [details, note]))
        for col, value in [(fm['acceptance'], p['acceptance']), (fm['details'], details)]:
            if value != str(values.get(col, '') or ''):
                patch['operations'].append({**common, 'kind': 'set_value', 'column': col, 'cell': col + str(row['row']), 'value': value})
        known = set(_hashes(row))
        for image in p.get('images', []):
            if image['sha256'] not in known:
                patch['operations'].append({**common, 'kind': 'append_image', 'column': fm['images'],
                                            'cell': fm['images'] + str(row['row']), 'image': deepcopy(image)})
                known.add(image['sha256'])
    patch['patchId'] = canonical_hash([patch['baselineHash'], proposals])[:24]
    return patch


def rebase_patch(patch, current):
    before = patch['before']; conflicts = []
    for key in ['nodeId', 'sheetId', 'fieldMap', 'dataValidations']:
        if before[key] != current.get(key): conflicts.append({'field': key, 'reason': 'concurrent_change'})
    for p in patch['proposals']:
        old = _row(before, p['feedbackId']); new = _row(current, p['feedbackId'])
        if not old or not new or old['identity'] != new['identity']:
            conflicts.append({'feedbackId': p['feedbackId'], 'reason': 'row_identity_conflict'}); continue
        expected = _expected(patch, old)
        if (set(new['values']) != set(old['values']) or any(v not in [old['values'].get(k), expected['values'].get(k)] for k, v in new['values'].items())
                or not set(_hashes(old)).issubset(_hashes(new))):
            conflicts.append({'feedbackId': p['feedbackId'], 'reason': 'concurrent_row_change'})
    if conflicts:
        return {**deepcopy(patch), 'operations': [], 'conflicts': conflicts}
    return make_patch(current, patch['proposals'])


def _expected(patch, row):
    expected = deepcopy(row)
    for op in patch['operations']:
        if op['feedbackId'] != row['id']: continue
        if op['kind'] == 'append_image': expected['images'].append(op['image'])
        else: expected['values'][op['column']] = op['value']
    return expected


def verify_patch(patch, after):
    differences = list(patch['conflicts']); before = patch['before']
    for key in ['nodeId', 'sheetId', 'fieldMap', 'dataValidations']:
        if before[key] != after.get(key): differences.append({'field': key, 'reason': 'changed'})
    for p in patch['proposals']:
        old = _row(before, p['feedbackId']); new = _row(after, p['feedbackId'])
        if old is None or new is None:
            differences.append({'feedbackId': p['feedbackId'], 'reason': 'missing_or_ambiguous'}); continue
        expected = _expected(patch, old)
        if new['identity'] != old['identity'] or new['values'] != expected['values'] or _hashes(new) != _hashes(expected):
            differences.append({'feedbackId': p['feedbackId'], 'reason': 'row_mismatch'})
    status = 'mismatch' if differences else ('verified' if after.get('imagesReadComplete') else 'incomplete')
    return {'status': status, 'differences': differences, 'evidenceIds': after.get('evidenceIds', [])}
