"""Owned, serialized coordinator state. Worker output never grants authority.

The owner capability is returned once and only its digest is persisted. This
prevents accidental or unauthenticated API dispatch, not a hostile same-UID
process from inspecting the coordinator. Keep state outside worker workspaces.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from .policy import assess_dispatch, load_policy, reserve_dispatch, _validate_policy
from .usage import IDENTIFIER, validate_receipt_usage
from .packets import prompt_metrics, scopes_conflict
from .controlplane.config import ensure_private_directory, parse_config_bytes
from .controlplane.models import is_valid_model_id, is_valid_reasoning_effort
from .controlplane.plans import Operation, approval_digest, consume_plan, create_plan, load_plan
from .controlplane.profiles import _write_at_locked
from .controlplane.security import (SecurityError, close_private_lock, open_owned_directory_nofollow,
                                    open_parent_directory_nofollow, open_private_lock_at, validate_destination_at)

MAX_STATE_BYTES = 2 * 1024 * 1024
LEDGER = 'session.json'
# Host tool catalog observed on 2026-10-25; compatibility is not account entitlement.
PRESET_EFFORTS = {'gpt-6-luna': {'low', 'medium', 'high', 'xhigh', 'max'},
                  'gpt-6-astra': {'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}}


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_at(parent, name, maximum=MAX_STATE_BYTES, required_mode=0o600):
    metadata = validate_destination_at(parent, name)
    if metadata is None:
        raise FileNotFoundError(name)
    if required_mode is not None and stat.S_IMODE(metadata.st_mode) != required_mode:
        raise SecurityError('controller state file must have mode 0600')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise SecurityError('state changed while opening')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            result = stream.read(maximum + 1)
        if len(result) > maximum:
            raise SecurityError('state exceeds size limit')
        return result
    finally:
        os.close(fd)


@contextmanager
def locked(state):
    fd = open_parent_directory_nofollow(Path(state))
    lock = -1
    try:
        lock = open_private_lock_at(fd)
        yield fd
    finally:
        if lock >= 0:
            close_private_lock(lock)
        os.close(fd)


def workspace_identity(workspace):
    path = Path(workspace)
    fd = open_owned_directory_nofollow(path)
    try:
        info = os.fstat(fd)
        return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino, 'owner': info.st_uid}
    finally:
        os.close(fd)


def _owner(document, token):
    if not isinstance(token, str) or len(token) != 64 or not secrets.compare_digest(document['owner_digest'], digest(token.encode())):
        raise SecurityError('coordinator ownership capability is required')


def _read(fd):
    raw = read_at(fd, LEDGER)
    document = parse_config_bytes(raw)
    fields = {'schema_version', 'session_id', 'owner_digest', 'workspace', 'model', 'effort', 'policy', 'revision', 'calls', 'launches'}
    if not isinstance(document, dict) or set(document) != fields or type(document['schema_version']) is not int or document['schema_version'] != 1:
        raise SecurityError('invalid controller state')
    if type(document['revision']) is not int or document['revision'] < 0:
        raise SecurityError('invalid controller revision')
    _validate_policy(document['policy'])
    if not isinstance(document['calls'], list) or not isinstance(document['launches'], dict):
        raise SecurityError('invalid launch records')
    if {row['id'] for row in document['calls']} != set(document['launches']):
        raise SecurityError('launch records disagree')
    return raw, document


def _write(fd, document):
    document['revision'] += 1
    content = encoded(document)
    if len(content) > MAX_STATE_BYTES:
        raise SecurityError('controller state exceeds size limit')
    _write_at_locked(fd, LEDGER, content)


def initialize(state, workspace, model, effort, policy=None):
    """Create one session; never reuse or replace an existing ownership record."""
    state, workspace = Path(state), Path(workspace)
    if not state.is_absolute() or state.is_relative_to(workspace):
        raise SecurityError('controller state must be absolute and outside the worker workspace')
    if not is_valid_model_id(model) or not is_valid_reasoning_effort(effort):
        raise ValueError('explicit host-selected model and reasoning effort are required')
    policy = deepcopy(load_policy() if policy is None else policy)
    _validate_policy(policy)
    identity = workspace_identity(workspace)
    ensure_private_directory(state)
    token = secrets.token_hex(32)
    document = {'schema_version': 1, 'session_id': secrets.token_hex(16), 'owner_digest': digest(token.encode()),
                'workspace': identity, 'model': model, 'effort': effort, 'policy': policy,
                'revision': 0, 'calls': [], 'launches': {}}
    with locked(state) as fd:
        if validate_destination_at(fd, LEDGER) is not None:
            raise SecurityError('session already exists; ownership cannot be reset')
        _write(fd, document)
    return {'session_id': document['session_id'], 'owner_token': token,
            'state': str(state), 'dispatch_state': 'not_dispatched'}


def status(state):
    with locked(state) as fd:
        _, document = _read(fd)
    result = deepcopy(document)
    del result['owner_digest']
    return result


def _guidance(items):
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError('select at most three relevant skill entrypoints')
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'name', 'text'}:
            raise ValueError('skill guidance requires name and text')
        if not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 100:
            raise ValueError('invalid skill name')
        if not isinstance(item['text'], str) or not item['text'].strip():
            raise ValueError('skill guidance must be text')
        result.append(dict(item, sha256=digest(item['text'].encode())))
    if sum(len(item['text']) for item in result) > 16000:
        raise ValueError('selected skill guidance exceeds context budget')
    return result


def _new_launch(document, proposal, guidance, effort_override=None):
    ledger = {'schema_version': 1, 'calls': document['calls']}
    decision, reserved = reserve_dispatch(proposal, ledger, document['policy'])
    if not decision['allowed']:
        return decision, None
    conflicts = [call['id'] for call in document['calls'] if call['status'] in ('pending', 'running')
                 and scopes_conflict(proposal, document['launches'][call['id']]['proposal'])]
    if conflicts:
        decision.update(allowed=False, conflicting_launches=conflicts)
        decision['stop_reasons'].append('active_scope_conflict')
        return decision, None
    prompt_metrics({'proposal': proposal, 'guidance': guidance})
    request = decision['session_request']
    model = request['model'] or document['model']
    effort = document['effort'] if effort_override is None else effort_override
    if not is_valid_reasoning_effort(effort):
        raise ValueError('invalid worker reasoning effort')
    if request['model'] is not None and effort not in PRESET_EFFORTS.get(model, set()):
        raise ValueError('preset model and reasoning effort are not a verified compatible pair; supply an explicit supported worker effort')
    updated = deepcopy(document)
    updated['calls'] = reserved['calls']
    updated['launches'][proposal['id']] = {
        'phase': 'reserved', 'proposal': deepcopy(proposal), 'guidance': guidance,
        'settings': {'model': model, 'effort': effort, 'sandbox': request['sandbox_mode']},
        'effort_override': effort_override, 'receipt': None, 'verification': None, 'pid': None,
    }
    updated['revision'] += 1
    return decision, updated


def preview(state, owner, proposal, guidance=None, now=None, worker_effort=None):
    """Preview and persist an expiring exact-state reservation, without launch."""
    state = Path(state)
    selected_guidance = _guidance([] if guidance is None else guidance)
    with locked(state) as fd:
        before, document = _read(fd)
        _owner(document, owner)
        if workspace_identity(Path(document['workspace']['path'])) != document['workspace']:
            raise SecurityError('workspace identity changed')
        decision, updated = _new_launch(document, proposal, selected_guidance, worker_effort)
        if not decision['allowed']:
            return decision
        settings = updated['launches'][proposal['id']]['settings']
        after = encoded(updated)
    plans = ensure_private_directory(state / 'plans')
    operation = Operation(str(state / LEDGER), digest(before), digest(after), base64.b64encode(after).decode())
    token = create_plan('dispatch', [operation], plans, now=now)
    plan = load_plan(token, 'dispatch', plans, now=now)
    return dict(decision, token=token, approval_digest=approval_digest(plan),
                model=settings['model'], effort=settings['effort'], sandbox=settings['sandbox'],
                selected_skills=[item['name'] for item in selected_guidance], expires_in_seconds=600,
                context_budget=prompt_metrics({'proposal': proposal, 'guidance': selected_guidance}))


def reserve(state, owner, token, approval, now=None):
    """Consume a reviewed preview once and durably reserve before any process."""
    state = Path(state)
    def apply(plan):
        if len(plan.operations) != 1 or plan.operations[0].path != str(state / LEDGER):
            raise SecurityError('dispatch plan has an unexpected destination')
        operation = plan.operations[0]
        with locked(state) as fd:
            before, current = _read(fd)
            _owner(current, owner)
            if digest(before) != operation.before_sha256:
                raise SecurityError('dispatch preview is stale')
            after = base64.b64decode(operation.content_b64, validate=True)
            if digest(after) != operation.after_sha256:
                raise SecurityError('dispatch plan content mismatch')
            proposed = parse_config_bytes(after)
            added = set(proposed['launches']) - set(current['launches'])
            if len(added) != 1:
                raise SecurityError('dispatch plan must reserve exactly one launch')
            launch_id = added.pop()
            proposed_launch = proposed['launches'][launch_id]
            guidance = _guidance([{'name': item['name'], 'text': item['text']} for item in proposed_launch['guidance']])
            decision, expected = _new_launch(current, proposed_launch['proposal'], guidance, proposed_launch['effort_override'])
            if not decision['allowed'] or encoded(expected) != after:
                raise SecurityError('dispatch policy changed or reservation is invalid')
            if workspace_identity(Path(current['workspace']['path'])) != current['workspace']:
                raise SecurityError('workspace identity changed')
            _write_at_locked(fd, LEDGER, after)
        return {'launch_id': launch_id, 'dispatch_state': 'reserved'}
    # Reject an unrelated caller before consuming the one-use preview.
    with locked(state) as fd:
        _, document = _read(fd)
        _owner(document, owner)
    return consume_plan(token, 'dispatch', state / 'plans', apply, approval=approval, now=now)


def execute_reserved(state, owner, launch_id, runner):
    """Run one reserved worker. Exceptions leave an unresolved, nonretryable slot."""
    state = Path(state)
    _launch_id(launch_id)
    with locked(state) as fd:
        _, document = _read(fd)
        _owner(document, owner)
        launch = document['launches'].get(launch_id)
        if launch is None or launch['phase'] != 'reserved':
            raise SecurityError('launch is absent or has already been attempted')
        if workspace_identity(Path(document['workspace']['path'])) != document['workspace']:
            raise SecurityError('workspace identity changed')
        launch['phase'] = 'launching'
        launch['request_hash'] = digest(encoded({'session': document['session_id'], 'launch': launch_id,
                                                'workspace': document['workspace'], 'request': launch}))
        _write(fd, document)
        request = deepcopy(launch)
        request['workspace_identity'] = deepcopy(document['workspace'])
        workspace = Path(document['workspace']['path'])
    run_root = ensure_private_directory(state / ('run-' + launch_id))
    def started(pid):
        with locked(state) as fd:
            _, latest = _read(fd)
            _owner(latest, owner)
            item = latest['launches'][launch_id]
            if item['phase'] != 'launching':
                raise SecurityError('launch state changed before worker started')
            item['phase'], item['pid'] = 'running', pid
            next(row for row in latest['calls'] if row['id'] == launch_id)['status'] = 'running'
            _write(fd, latest)
    receipt = runner.run(request, workspace, run_root, started)
    _record_receipt(state, owner, launch_id, receipt)
    return receipt


def _record_receipt(state, owner, launch_id, receipt):
    with locked(state) as fd:
        _, document = _read(fd)
        _owner(document, owner)
        item = document['launches'][launch_id]
        if item['phase'] not in ('launching', 'running') or not isinstance(receipt, dict) or receipt.get('request_hash') != item['request_hash']:
            raise SecurityError('receipt does not match the unresolved launch')
        if receipt.get('outcome') not in ('completed', 'failed', 'timeout', 'protocol_error'):
            raise SecurityError('invalid worker outcome')
        item['receipt'], item['phase'] = receipt, 'awaiting_acceptance'
        _write(fd, document)


def reconcile(state, owner, launch_id):
    """Recover a durable supervisor receipt; never resume or relaunch a worker."""
    _launch_id(launch_id)
    with locked(state) as fd:
        _, document = _read(fd)
        _owner(document, owner)
        if launch_id not in document['launches']:
            raise SecurityError('unknown launch')
    with locked(Path(state) / ('run-' + launch_id)) as fd:
        receipt = parse_config_bytes(read_at(fd, 'receipt.json'))
    _record_receipt(state, owner, launch_id, receipt)
    return {'launch_id': launch_id, 'phase': 'awaiting_acceptance', 'relaunched': False}


def finish(state, owner, launch_id, accepted, verification):
    """The coordinator, never a worker completion claim, decides acceptance."""
    _launch_id(launch_id)
    if type(accepted) is not bool or not isinstance(verification, str) or not 1 <= len(verification.strip()) <= 4000:
        raise ValueError('acceptance requires a boolean and bounded verification evidence')
    with locked(state) as fd:
        _, document = _read(fd)
        _owner(document, owner)
        item = document['launches'][launch_id]
        if item['phase'] != 'awaiting_acceptance':
            raise SecurityError('worker has no final receipt to review')
        if accepted and item['receipt']['outcome'] != 'completed':
            raise SecurityError('failed worker cannot be accepted')
        validate_receipt_usage(item['receipt'])
        row = next(row for row in document['calls'] if row['id'] == launch_id)
        row['status'], row['usage'] = ('completed' if accepted else 'failed'), item['receipt'].get('usage')
        # Reuse strict accounting validation before publishing observed usage.
        check = deepcopy(item['proposal'])
        check['id'] = 'validation-only'
        assess_dispatch(check, {'schema_version': 1, 'calls': document['calls']}, document['policy'])
        item['phase'], item['verification'] = ('accepted' if accepted else 'rejected'), verification
        _write(fd, document)
    return {'launch_id': launch_id, 'phase': item['phase'], 'usage': row['usage']}


def _launch_id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('launch id must be a short identifier without path separators')
