"""Strict, side-effect-free assessment of host-supplied dispatch proposals.

Claims in JSON are not authenticated identities. A trusted host must own task
identity, evidence, ledger persistence, locking, and actual session enforcement.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

from .usage import IDENTIFIER, _integer, assess_usage
from .packets import relative_path, validate_context

ROLES = ('coordinator', 'implementer', 'investigator', 'reviewer')
PRESETS = {'coordinator': (), 'implementer': ('standard', 'fast'),
           'investigator': ('standard', 'astra'), 'reviewer': ('standard',)}
CALL_FIELDS = {'id', 'packet', 'agent', 'status', 'usage', 'preset', 'evidence'}
USAGE_FIELDS = ('id', 'packet', 'agent', 'status', 'usage')


def _exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(label + ' has invalid fields')


def _text(value, label, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError(label + ' must be bounded nonempty text')


def _identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(label + ' must be a short identifier')


def _evidence(items):
    if not isinstance(items, list) or len(items) > 16:
        raise ValueError('evidence must contain at most 16 records')
    seen = set()
    for item in items:
        _exact(item, ('fingerprint', 'summary'), 'evidence record')
        fingerprint = item['fingerprint']
        if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise ValueError('evidence fingerprint must be a SHA256 digest')
        if fingerprint in seen:
            raise ValueError('duplicate evidence fingerprint')
        seen.add(fingerprint)
        _text(item['summary'], 'evidence summary', 1000)
    return seen


def load_json(path, maximum=512 * 1024):
    """Read bounded JSON, rejecting duplicate keys and non-finite constants."""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError('non-finite JSON number')

    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('JSON file exceeds size limit')
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid JSON document') from error
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 16:
            raise ValueError('JSON nesting exceeds depth limit')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def load_policy(path=None):
    policy = load_json(path or Path(__file__).with_name('policy.json'))
    _validate_policy(policy)
    return policy


def _validate_policy(policy):
    _exact(policy, ('schema_version', 'limits', 'worker_subagent_tools', 'roles'), 'policy')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 1:
        raise ValueError('unsupported policy schema')
    _exact(policy['limits'], ('max_calls', 'max_retries', 'max_active_workers', 'max_tokens'), 'limits')
    limits = policy['limits']
    _integer(limits['max_calls'], 'max_calls', 1000)
    _integer(limits['max_retries'], 'max_retries', 2)
    _integer(limits['max_active_workers'], 'max_active_workers', 8)
    if limits['max_active_workers'] < 1:
        raise ValueError('at least one worker slot is required')
    if limits['max_tokens'] is not None:
        _integer(limits['max_tokens'], 'max_tokens')
    if policy['worker_subagent_tools'] != 'disabled':
        raise ValueError('worker subagent tools must be disabled')
    _exact(policy['roles'], ROLES, 'roles')
    for name, role in policy['roles'].items():
        _exact(role, ('can_dispatch', 'sandbox', 'presets'), 'role policy')
        if type(role['can_dispatch']) is not bool or role['can_dispatch'] != (name == 'coordinator'):
            raise ValueError('only the coordinator may propose dispatch')
        sandbox = None if name == 'coordinator' else 'workspace-write' if name == 'implementer' else 'read-only'
        if role['sandbox'] != sandbox:
            raise ValueError('role sandbox request violates the policy contract')
        _exact(role['presets'], PRESETS[name], 'role presets')
        for preset, settings in role['presets'].items():
            _exact(settings, ('model',), 'model preset')
            model = settings['model']
            if preset == 'standard' and model is not None:
                raise ValueError('standard preset must inherit the selected model')
            if preset == 'fast' and model is None:
                raise ValueError('fast preset must select an explicit model')
            if model is not None:
                _identifier(model, 'preset model')
            if preset == 'astra' and model != 'gpt-6-astra':
                raise ValueError('investigator Astra preset must select gpt-6-astra')
            if name != 'investigator' and model is not None and 'astra' in model.lower():
                raise ValueError('Astra override is restricted to the investigator preset')


def _validate_proposal(proposal):
    fields = {'schema_version', 'id', 'packet', 'requester', 'role', 'objective', 'scope', 'acceptance_check', 'evidence'}
    optional = {'preset', 'context', 'depends_on', 'contract'}
    if not isinstance(proposal, dict) or not fields <= set(proposal) or set(proposal) - fields - optional:
        raise ValueError('proposal has invalid fields')
    if type(proposal['schema_version']) is not int or proposal['schema_version'] != 1:
        raise ValueError('unsupported proposal schema')
    _identifier(proposal['id'], 'launch id')
    _identifier(proposal['packet'], 'packet')
    _exact(proposal['requester'], ('role', 'depth'), 'requester')
    if proposal['requester']['role'] not in ROLES or proposal['role'] not in ROLES:
        raise ValueError('unknown role')
    _integer(proposal['requester']['depth'], 'requester depth', 32)
    _text(proposal['objective'], 'objective')
    _text(proposal['acceptance_check'], 'acceptance check')
    if 'contract' in proposal:
        _text(proposal['contract'], 'original contract', 16000)
    scope = proposal['scope']
    scope_fields = {'paths', 'bounded', 'consequence', 'uncertainty'}
    if not isinstance(scope, dict) or set(scope) not in (scope_fields, scope_fields | {'resources'}):
        raise ValueError('scope has invalid fields')
    if type(scope['bounded']) is not bool:
        raise ValueError('scope bounded must be a boolean')
    for key in ('consequence', 'uncertainty'):
        if scope[key] not in ('low', 'medium', 'high'):
            raise ValueError('invalid scope assessment')
    paths = scope['paths']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
        raise ValueError('scope requires 1 to 64 paths')
    for path in paths:
        _text(path, 'scope path', 256)
        relative_path(path)
    if len(paths) != len(set(paths)):
        raise ValueError('duplicate scope path')
    _evidence(proposal['evidence'])
    resources = scope.get('resources', [])
    if not isinstance(resources, list) or len(resources) > 16:
        raise ValueError('scope resources must contain at most 16 identifiers')
    for resource in resources:
        _identifier(resource, 'shared resource')
    if len(resources) != len(set(resources)):
        raise ValueError('duplicate shared resource')
    dependencies = proposal.get('depends_on', [])
    if not isinstance(dependencies, list) or len(dependencies) > 16:
        raise ValueError('depends_on must contain at most 16 packet identifiers')
    for dependency in dependencies:
        _identifier(dependency, 'dependency')
    if len(dependencies) != len(set(dependencies)) or proposal['packet'] in dependencies:
        raise ValueError('dependencies must be unique and cannot include this packet')
    if 'context' in proposal:
        validate_context(proposal['context'])
    preset = proposal.get('preset', 'standard')
    _identifier(preset, 'preset')
    if proposal['role'] != 'coordinator' and preset not in PRESETS[proposal['role']]:
        raise ValueError('unknown role preset')


def assess_dispatch(proposal, ledger, policy=None):
    """Assess claimed inputs only; allowed does not mean dispatched or authorized."""
    policy = load_policy() if policy is None else policy
    _validate_policy(policy)
    _validate_proposal(proposal)
    _exact(ledger, ('schema_version', 'calls'), 'ledger')
    if type(ledger['schema_version']) is not int or ledger['schema_version'] != 1:
        raise ValueError('unsupported ledger schema')
    calls = ledger['calls']
    if not isinstance(calls, list) or len(calls) > 1000:
        raise ValueError('ledger must contain at most 1000 calls')
    for call in calls:
        _exact(call, CALL_FIELDS, 'call')
        if call['agent'] not in ROLES[1:] or call['preset'] not in PRESETS[call['agent']]:
            raise ValueError('ledger contains an unknown worker role or preset')
        _evidence(call['evidence'])
    usage_ledger = {'schema_version': 1, 'calls': [{key: row[key] for key in USAGE_FIELDS} for row in calls]}
    limits = policy['limits']
    usage = assess_usage(usage_ledger, proposal['packet'], limits['max_calls'], limits['max_retries'], limits['max_tokens'])
    reasons = list(usage['stop_reasons'])
    accepted = {call['packet'] for call in calls if call['status'] == 'completed'}
    blocked_dependencies = [name for name in proposal.get('depends_on', []) if name not in accepted]
    if blocked_dependencies:
        reasons.append('dependencies_not_accepted')
    if proposal['requester']['role'] != 'coordinator':
        reasons.append('requester_not_coordinator')
    if proposal['requester']['depth'] != 0:
        reasons.append('nested_dispatch_forbidden')
    if proposal['role'] == 'coordinator':
        reasons.append('coordinator_cannot_be_worker')
    if proposal['id'] in {call['id'] for call in calls}:
        reasons.append('launch_id_already_used')
    if any(call['status'] == 'pending' for call in calls):
        reasons.append('pending_dispatch_unresolved')
    if sum(call['status'] == 'running' for call in calls) >= limits['max_active_workers']:
        reasons.append('active_worker_limit_reached')
    prior = [call for call in calls if call['packet'] == proposal['packet']]
    if any(call['status'] in ('pending', 'running') for call in prior):
        reasons.append('packet_in_progress')
    if any(call['status'] == 'completed' for call in prior):
        reasons.append('packet_already_completed')
    previous_evidence = {item['fingerprint'] for call in prior for item in call['evidence']}
    if prior and not (_evidence(proposal['evidence']) - previous_evidence):
        reasons.append('retry_requires_new_evidence')
    preset = proposal.get('preset', 'standard')
    scope = proposal['scope']
    if preset == 'fast' and not (scope['bounded'] and scope['consequence'] == 'low' and scope['uncertainty'] == 'low'):
        reasons.append('fast_eligibility_not_met')
    role = policy['roles'][proposal['role']]
    session = None if proposal['role'] == 'coordinator' else {
        'role': proposal['role'], 'preset': preset, 'sandbox_mode': role['sandbox'],
        'subagent_tools': policy['worker_subagent_tools'], 'model': role['presets'][preset]['model'],
        'reasoning_effort': None, 'reasoning_effort_resolution': 'host_must_validate_for_selected_model',
    }
    return {'allowed': not reasons, 'stop_reasons': reasons, 'dispatch_state': 'not_dispatched',
            'decision_scope': 'policy_only', 'host_enforcement': 'unverified',
            'session_request': session, 'usage': usage, 'blocked_dependencies': blocked_dependencies}


def reserve_dispatch(proposal, ledger, policy=None):
    """Return a pending reservation; the host must atomically persist it before launch."""
    decision = assess_dispatch(proposal, ledger, policy)
    updated = deepcopy(ledger)
    if decision['allowed']:
        updated['calls'].append({'id': proposal['id'], 'packet': proposal['packet'], 'agent': proposal['role'],
                                 'status': 'pending', 'usage': None, 'preset': proposal.get('preset', 'standard'),
                                 'evidence': deepcopy(proposal['evidence'])})
    return decision, updated
