"""Semantic replay progression. Returned state must be persisted before dispatch."""
from copy import deepcopy
from .core import require, canonical_hash

ACTIONS = {'fire', 'bomb', 'resume', 'pause'}


def plan(recipe, environment, adapter):
    require(recipe.get('schemaVersion') == 1, 'SCHEMA_VERSION')
    require(recipe['adapterVersion'] == adapter['version'], 'ADAPTER_VERSION')
    require(recipe.get('steps'), 'REPLAY_EMPTY')
    for step in recipe['steps']:
        require(step['action'] in ACTIONS, 'ACTION_NOT_SUPPORTED')
        require(type(step['timeoutMs']) is int and 0 < step['timeoutMs'] <= 300000, 'STEP_TIMEOUT')
        require(step['selector'].get('component') == 'GoldMinerBattleView', 'SELECTOR_UNSUPPORTED')
    p = {**deepcopy(recipe), 'identity': deepcopy(environment['identity'])}
    p['planId'] = canonical_hash(p)[:24]
    return p


def next_step(plan, state, observation):
    state = deepcopy(state)
    def result(status, reason=None, **kw): return {'status': status, 'reason': reason, 'state': state, **kw}
    if observation['identity'] != plan['identity']: return result('failed', 'identity_changed', preserveObservation=True)
    index = state.setdefault('index', 0)
    if index >= len(plan['steps']): return result('complete')
    step = plan['steps'][index]; clock = observation['monotonicTime']
    state.setdefault('startedAt', clock)
    if clock < state['startedAt']: return result('failed', 'clock_reset', preserveObservation=True)
    if (clock - state['startedAt']) * 1000 > step['timeoutMs']: return result('failed', 'step_timeout', preserveObservation=True)
    matches = lambda expected: all(observation.get('state', {}).get(k) == v for k, v in expected.items())
    if state.get('dispatched'):
        if matches(step['until']) and observation.get('actionId') == state['actionId'] and observation.get('evidenceIds'):
            state = {'index': index + 1}
            return result('complete' if index + 1 == len(plan['steps']) else 'waiting')
        return result('waiting', 'await_postcondition_no_redispatch')
    if not matches(step['preconditions']): return result('waiting', 'await_precondition')
    targets = [t for t in observation['targets'] if all(t.get(k) == v for k, v in step['selector'].items())]
    if len(targets) != 1: return result('failed', 'target_ambiguous_or_missing', preserveObservation=True)
    aid = canonical_hash([plan['planId'], index])[:24]
    state.update(dispatched=True, actionId=aid)
    return result('action', action={'actionId': aid, 'adapter': 'unity', 'action': step['action'], 'parameters': {'targetId': targets[0]['id']}}, expectedEvidence=step['expectedEvidence'])


def advance(store, plan_id, observation):
    current = store.read(); saved = current['replays'][plan_id]
    result = next_step(saved['plan'], saved['state'], observation)
    def change(s): s['replays'][plan_id]['state'] = result['state']; s['replays'][plan_id]['lastResult'] = result; return s
    store.update(current['revision'], change)
    return result


def register(store, recipe, environment, adapter):
    p = plan(recipe, environment, adapter); state = store.read()
    if p['planId'] not in state.get('replays', {}):
        def change(s): s.setdefault('replays', {})[p['planId']] = {'plan': p, 'state': {}}; return s
        store.update(state['revision'], change)
    return p
