"""Scoped GM preparation. Plans and acknowledgements never pass business assertions."""
from copy import deepcopy
from pathlib import Path
from .core import require, WorkflowError, canonical_hash, exclusive, atomic_json, owned_path, read_json


def validate_parameters(schema, parameters):
    require(set(parameters) == set(schema), 'PARAMETER_KEYS')
    for name, rule in schema.items():
        value = parameters[name]
        require(rule['type'] == 'integer' and type(value) is int, 'PARAMETER_TYPE')
        require(rule['minimum'] <= value <= rule['maximum'], 'PARAMETER_RANGE')


def plan_fixture(definition, parameters, snapshot, scope, budget):
    require(definition.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    require(definition.get('available'), 'FIXTURE_UNAVAILABLE')
    validate_parameters(definition['parameterSchema'], parameters)
    require(snapshot['identity'] == scope['identity'] and all(v not in [None, '', 'unknown'] for v in scope['identity'].values()), 'IDENTITY_MISMATCH')
    require(snapshot['adapterVersion'] == definition['adapterVersion'], 'ADAPTER_VERSION')
    require(scope.get('reference') and definition['fixtureId'] in scope['capabilities'], 'SCOPE_MISMATCH')
    require(set(definition.get('irreversibleEffects', [])).issubset(scope.get('acceptedEffects', [])), 'IRREVERSIBLE_EFFECT_NOT_ACCEPTED')
    expected = {}
    for effect in definition['effects']:
        field = effect['field']; require(field in definition['ownedFields'], 'FIELD_NOT_OWNED')
        require(effect['mode'] in {'add', 'set'}, 'EFFECT_MODE')
        value = parameters[effect['parameter']]
        expected[field] = snapshot['state'][field] + value if effect['mode'] == 'add' else value
    cost = definition.get('cost', 0)
    require(type(cost) is int and cost >= 0, 'COST_INVALID')
    if cost:
        require(budget and budget.get('scopeId') == scope['scopeId'], 'BUDGET_REQUIRED')
        require(budget['spent'] + budget['reserved'] + cost <= budget['limit'], 'BUDGET_EXCEEDED')
    result = {'schemaVersion': 1, 'fixtureId': definition['fixtureId'], 'adapterId': definition['adapterId'],
        'adapterVersion': definition['adapterVersion'], 'identity': deepcopy(snapshot['identity']),
        'beforeHash': canonical_hash(snapshot), 'before': deepcopy(snapshot), 'expectedState': expected,
        'ownedFields': list(expected), 'irreversibleEffects': definition.get('irreversibleEffects', []),
        'cleanup': definition['cleanup'], 'scopeId': scope['scopeId'], 'cost': cost,
        'action': {'adapter': 'unity', 'action': definition['fixtureId'], 'parameters': parameters}}
    result['planId'] = canonical_hash(result)[:24]
    return result


def verify_fixture(plan, before, after):
    differences = []
    if canonical_hash(before) != plan['beforeHash']: differences.append({'reason': 'before_changed'})
    if after['identity'] != plan['identity']: differences.append({'reason': 'identity_changed'})
    for field, value in plan['expectedState'].items():
        if after['state'].get(field) != value:
            differences.append({'field': field, 'expected': value, 'actual': after['state'].get(field)})
    return {'status': 'mismatch' if differences else 'verified', 'differences': differences, 'evidenceIds': after.get('evidenceIds', [])}


def plan_cleanup(plan, applied, current):
    conflicts = []; actions = []
    if current['identity'] != plan['identity']: conflicts.append({'reason': 'identity_changed'})
    for field in plan['ownedFields']:
        if applied['state'].get(field) != plan['expectedState'][field] or current['state'].get(field) != applied['state'].get(field):
            conflicts.append({'field': field, 'reason': 'subsequent_business_change'})
        elif plan['cleanup'] == 'restore':
            actions.append({'field': field, 'value': plan['before']['state'][field]})
    return {'status': 'conflict' if conflicts else 'ready', 'actions': [] if conflicts else actions, 'conflicts': conflicts}


class BudgetStore:
    def __init__(self, root, scope_id):
        self.root = owned_path(Path(root), scope_id)
        self.path = owned_path(self.root, 'budget.json')
        self.scope_id = scope_id

    def initialize(self, limit, authorization_reference):
        require(type(limit) is int and limit >= 0 and authorization_reference, 'BUDGET_INVALID')
        with exclusive(self.root):
            require(not self.path.exists(), 'BUDGET_ALREADY_EXISTS')
            atomic_json(self.path, {'schemaVersion': 1, 'scopeId': self.scope_id, 'reference': authorization_reference,
                        'limit': limit, 'spent': 0, 'reserved': 0, 'revision': 0, 'operations': {}})
        return self.read()

    def read(self):
        require(self.path.is_file(), 'BUDGET_REQUIRED')
        data = read_json(self.path)
        require(data['scopeId'] == self.scope_id and data['schemaVersion'] == 1, 'BUDGET_IDENTITY')
        return data

    def reserve(self, operation_id, amount, expected_revision):
        require(type(amount) is int and amount >= 0, 'BUDGET_AMOUNT')
        with exclusive(self.root):
            data = self.read()
            require(data['revision'] == expected_revision, 'REVISION_CONFLICT')
            if operation_id in data['operations']:
                require(data['operations'][operation_id]['amount'] == amount, 'RESERVATION_CONFLICT'); return data
            require(data['spent'] + data['reserved'] + amount <= data['limit'], 'BUDGET_EXCEEDED')
            data['operations'][operation_id] = {'amount': amount, 'status': 'reserved'}
            data['reserved'] += amount; data['revision'] += 1; atomic_json(self.path, data)
        return data

    def reconcile(self, operation_id, observed_spend, confirmed_not_applied=False):
        with exclusive(self.root):
            data = self.read(); op = data['operations'][operation_id]
            if observed_spend is None and not confirmed_not_applied: return data
            if confirmed_not_applied:
                require(observed_spend in [None, 0], 'SPEND_CONFLICT'); observed_spend = 0
            require(type(observed_spend) is int and 0 <= observed_spend <= op['amount'], 'SPEND_INVALID')
            if op['status'] == 'reconciled':
                require(op['spend'] == observed_spend, 'SPEND_CONFLICT'); return data
            data['reserved'] -= op['amount']; data['spent'] += observed_spend
            op.update(status='reconciled', spend=observed_spend)
            data['revision'] += 1; atomic_json(self.path, data)
        return data


def prepare_operation(store, plan, budget_store=None):
    from . import operations
    op = operations.prepare(store, 'fixture', plan['identity'], plan)
    if plan['cost']:
        require(budget_store is not None and budget_store.scope_id == plan['scopeId'], 'BUDGET_REQUIRED')
        budget_store.reserve(op['operationId'], plan['cost'], budget_store.read()['revision'])
        op = operations.bind_budget_reservation(store, op['operationId'], budget_store)
    # Reserve before dispatch. A crash keeps the reservation; only reconciled observations release it.
    return op
