"""Durable progress for the current chat; never launches commands or models.

Uses the controller's private, locked, atomic file operations. Thread binding
prevents accidental cross-chat updates; it is not authentication against a
same-user process. Outcomes and actor identities are supplied by the coordinator.
"""
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import time

from .runtime import MAX_STATE_BYTES, digest, encoded, locked, read_at, workspace_identity
from .controlplane.config import ensure_private_directory, parse_config_bytes
from .controlplane.profiles import _write_at_locked
from .controlplane.security import SecurityError, read_regular_nofollow, validate_destination_at

LEDGER = 'loop-progress.json'
VIEW = 'PROGRESS.md'
PROFILES = ('Engineer', 'Optimizer', 'Interface Builder', 'Researcher')
PHASES = ('work', 'research', 'check', 'review')
OUTCOMES = ('pass', 'fail', 'blocked', 'interrupted')
IDENTIFIER = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')


def _text(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(name + ' must be nonempty bounded text')
    return value


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid thread or step identifier')
    return value


def _number(value):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError('invalid clock value')
    return value


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('artifact must be a workspace-relative file')
    path = Path(value)
    if path.is_absolute() or any(part in ('', '.', '..', '.git') for part in value.split('/')):
        raise ValueError('artifact must be a safe workspace-relative file')
    return value


def _file(path, *, missing=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('evidence path must be absolute without traversal')
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise SecurityError('artifact and evidence paths cannot contain symlinks')
    try:
        before = path.stat()
        content = read_regular_nofollow(path, 16 * 1024 * 1024)
        after = path.stat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
            after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
        raise SecurityError('evidence changed while reading')
    return {'path': str(path), 'sha256': digest(content), 'bytes': len(content)}


def _snapshot(document):
    root = Path(document['workspace']['path'])
    if workspace_identity(root) != document['workspace']:
        raise SecurityError('workspace identity changed')
    return {name: (_file(root / _relative(name), missing=True) or {}).get('sha256')
            for name in document['artifacts']}


def _read(fd, thread):
    document = parse_config_bytes(read_at(fd, LEDGER))
    if not isinstance(document, dict) or type(document.get('schema_version')) is not int or document['schema_version'] != 1:
        raise ValueError('unsupported progress state; never reset it automatically')
    if document.get('thread') != _identifier(thread):
        raise SecurityError('progress belongs to another chat')
    if document.get('profile') not in PROFILES or document.get('review_mode') not in ('self', 'independent'):
        raise ValueError('invalid progress profile or review mode')
    if document.get('status') not in ('active', 'paused', 'stopped', 'complete'):
        raise ValueError('invalid progress status')
    if type(document.get('revision')) is not int or document['revision'] < 1:
        raise ValueError('invalid progress revision')
    _text(document['objective'], 'objective')
    _text(document['note'], 'note')
    if type(document.get('max_passes')) is not int or not 1 <= document['max_passes'] <= 1000:
        raise ValueError('invalid pass allowance')
    for name in ('started_at', 'last_seen', 'deadline'):
        _number(document[name])
    if not document['started_at'] <= document['last_seen'] or document['deadline'] <= document['started_at']:
        raise ValueError('invalid progress clock ordering')
    if not isinstance(document.get('artifacts'), list) or not 1 <= len(document['artifacts']) <= 256:
        raise ValueError('invalid artifact scope')
    for name in document['artifacts']:
        _relative(name)
    if len(set(document['artifacts'])) != len(document['artifacts']):
        raise ValueError('duplicate artifacts')

    def validate_snapshot(snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != set(document['artifacts']):
            raise ValueError('invalid artifact snapshot')
        if any(value is not None and (not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value))
               for value in snapshot.values()):
            raise ValueError('invalid artifact hash')

    steps = document.get('steps')
    if not isinstance(steps, list) or len(steps) > 1000:
        raise ValueError('invalid progress steps')
    ids = set()
    previous_end = document['started_at']
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('invalid progress step')
        identifier = _identifier(step['id'])
        if identifier in ids or step['phase'] not in PHASES:
            raise ValueError('duplicate or invalid progress step')
        ids.add(identifier)
        _text(step['note'], 'step note')
        validate_snapshot(step['snapshot'])
        started = _number(step['started_at'])
        if not previous_end <= started <= document['last_seen']:
            raise ValueError('invalid step ordering')
        result = step['result']
        if result is None:
            if step is not steps[-1]:
                raise ValueError('unresolved step must be last')
            continue
        if not isinstance(result, dict) or result.get('outcome') not in OUTCOMES:
            raise ValueError('invalid progress outcome')
        _identifier(result['actor'])
        _text(result['summary'], 'result summary')
        validate_snapshot(result['snapshot'])
        previous_end = _number(result['finished_at'])
        if not started <= previous_end <= document['last_seen'] or result['duration_seconds'] != previous_end - started:
            raise ValueError('invalid result timing')
        evidence = result['evidence']
        if (not isinstance(evidence, dict) or not isinstance(evidence.get('path'), str)
                or not Path(evidence['path']).is_absolute() or '..' in Path(evidence['path']).parts
                or not isinstance(evidence.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', evidence['sha256'])
                or type(evidence.get('bytes')) is not int or not 0 < evidence['bytes'] <= 16 * 1024 * 1024):
            raise ValueError('invalid evidence receipt')
    if sum(step['result'] is None for step in steps) > 1:
        raise ValueError('multiple active progress steps')
    if sum(step['phase'] == 'work' for step in steps) > document['max_passes']:
        raise ValueError('recorded passes exceed allowance')
    if document['status'] == 'complete' and _active(document):
        raise ValueError('completed progress has unresolved work')
    return document


def _clock(document, now=None):
    now = _number(time.time() if now is None else now)
    if now < document['last_seen']:
        raise ValueError('clock moved backwards; refusing to extend the deadline')
    return now


def _active(document):
    return next((step for step in document['steps'] if step['result'] is None), None)


def _current(document, phase, snapshot):
    step = next((item for item in reversed(document['steps']) if item['phase'] == phase), None)
    if not step or not step['result'] or step['result']['outcome'] != 'pass':
        return False
    result = step['result']
    if not all(snapshot.values()) or result['snapshot'] != snapshot:
        return False
    if phase == 'review':
        check = next((item for item in reversed(document['steps']) if item['phase'] == 'check'), None)
        if check is None or result.get('check_id') != check['id']:
            return False
    try:
        return _file(result['evidence']['path']) == result['evidence']
    except (OSError, ValueError):
        return False


def _summary(document, state, now):
    snapshot = _snapshot(document)
    active = _active(document)
    used = sum(step['phase'] == 'work' for step in document['steps'])
    checks_current = _current(document, 'check', snapshot)
    return {'thread': document['thread'], 'objective': document['objective'],
            'profile': document['profile'], 'status': document['status'],
            'revision': document['revision'], 'active_step': active,
            'passes_used': used, 'passes_remaining': document['max_passes'] - used,
            'seconds_remaining': max(0, document['deadline'] - now),
            'elapsed_seconds': now - document['started_at'],
            'deadline_exceeded': now >= document['deadline'],
            'checks_current': checks_current,
            'review_current': checks_current and _current(document, 'review', snapshot),
            'review_mode': document['review_mode'], 'note': document['note'],
            'model_usage': None, 'cost_usd': None,
            'progress_path': str(Path(state) / VIEW),
            'launches_models': False, 'controls_native_goal': False}


def _plain(value):
    return re.sub(r'([\\`*_{}\[\]<>()#|])', r'\\\1', ' '.join(str(value).split()))


def compact_summary(summary):
    """Project routine CLI output only; keep full state and view unchanged."""
    keys = ('status', 'revision', 'passes_used', 'passes_remaining', 'seconds_remaining',
            'deadline_exceeded', 'checks_current', 'review_current', 'review_mode',
            'progress_path', 'model_usage', 'cost_usd')
    result = {key: summary[key] for key in keys}
    result['seconds_remaining'] = round(summary['seconds_remaining'], 1)
    step = summary['active_step']
    result['active_step'] = {key: step[key] for key in ('id', 'phase')} if step else None
    if 'view_warning' in summary:
        result['view_warning'] = summary['view_warning']
    return result


def _render(document, summary, now):
    phase = summary['active_step']['phase'] if summary['active_step'] else 'waiting for next decision'
    status = summary['status']
    if summary['deadline_exceeded'] and status not in ('complete', 'stopped'):
        status += ' — deadline reached; no new work admitted'
    if status == 'complete' and not (summary['checks_current'] and summary['review_current']):
        status += ' — historical completion; current files or evidence changed'
    lines = ['# Loop progress', '', _plain(document['objective']), '',
             f"**Status:** {_plain(status)} · **Profile:** {_plain(document['profile'])} · **Phase:** {phase}",
             f"**Passes:** {summary['passes_used']}/{document['max_passes']} · "
             f"**Time remaining at update:** {int(summary['seconds_remaining'])} seconds", '',
             f"Checks current: **{summary['checks_current']}** · Review current: **{summary['review_current']}** "
             f"({document['review_mode']})", '', f"Next action / blocker: {_plain(document['note'])}", '',
             '| Step | Phase | Outcome | Evidence / finding |', '|---|---|---|---|']
    for step in document['steps']:
        result = step['result']
        lines.append('| ' + ' | '.join(_plain(value) for value in (
            step['id'], step['phase'], result['outcome'] if result else 'in progress',
            result['summary'] if result else step['note'])) + ' |')
    lines += ['', 'Model tokens and cost: **unknown**; this helper does not observe native account usage.',
              'No model sessions, commands or agents are launched by this tracker.',
              'Evidence hashes detect stale files; reported outcomes and reviewer identity are coordinator-supplied.',
              'The ledger is authoritative. This file is a snapshot; refresh status to regenerate it.', '',
              f"Revision {document['revision']} · updated {datetime.fromtimestamp(now, timezone.utc).isoformat()} · "
              f"chat {_plain(document['thread'])}", '']
    return '\n'.join(lines).encode()


def _save(fd, document, state, now, *, mutate=True):
    if mutate:
        document['revision'] += 1
        document['last_seen'] = now
        content = encoded(document)
        if len(content) > MAX_STATE_BYTES:
            raise ValueError('progress ledger is full; retain evidence and stop')
    result = _summary(document, state, now)
    if mutate:
        _write_at_locked(fd, LEDGER, content)
    try:
        _write_at_locked(fd, VIEW, _render(document, result, now))
    except (OSError, ValueError):
        result['view_warning'] = 'Ledger retained; progress view could not be refreshed. Inspect status; do not reinitialize.'
    return result


def initialize(state, workspace, thread, objective, profile, artifacts, *, max_passes=3,
               minutes=20, review_mode='self', now=None):
    _identifier(thread)
    _text(objective, 'objective')
    if profile not in PROFILES or review_mode not in ('self', 'independent'):
        raise ValueError('unknown profile or review mode')
    if type(max_passes) is not int or not 1 <= max_passes <= 1000:
        raise ValueError('max passes must be between 1 and 1000')
    if type(minutes) is not int or not 1 <= minutes <= 10080:
        raise ValueError('minutes must be finite, between 1 and 10080')
    artifacts = list(artifacts)
    if not 1 <= len(artifacts) <= 256 or len(set(artifacts)) != len(artifacts):
        raise ValueError('supply 1–256 distinct artifact paths')
    for name in artifacts:
        _relative(name)
        if (Path(workspace) / name).is_relative_to(Path(state)):
            raise ValueError('progress files cannot be tracked artifacts')
    now = _number(time.time() if now is None else now)
    document = {'schema_version': 1, 'revision': 0, 'thread': thread, 'objective': objective,
                'profile': profile, 'review_mode': review_mode, 'workspace': workspace_identity(workspace),
                'artifacts': artifacts, 'max_passes': max_passes, 'started_at': now,
                'last_seen': now, 'deadline': now + minutes * 60, 'status': 'active',
                'steps': [], 'note': 'Inspect the baseline and choose the next bounded action.'}
    _snapshot(document)
    ensure_private_directory(Path(state))
    with locked(state) as fd:
        if validate_destination_at(fd, LEDGER) is not None:
            raise ValueError('progress already exists; use status/resume without resetting counters')
        return _save(fd, document, state, now)


def status(state, thread, *, now=None):
    with locked(state) as fd:
        document = _read(fd, thread)
        return _save(fd, document, state, _clock(document, now), mutate=False)


def begin(state, thread, identifier, phase, note, *, now=None):
    _identifier(identifier)
    _text(note, 'next hypothesis')
    if phase not in PHASES:
        raise ValueError('invalid phase')
    with locked(state) as fd:
        document = _read(fd, thread)
        now = _clock(document, now)
        if document['status'] != 'active' or now >= document['deadline']:
            raise ValueError('run paused, stopped, complete or past its deadline')
        if _active(document):
            raise ValueError('a step is unresolved; record its outcome before continuing')
        if any(step['id'] == identifier for step in document['steps']):
            raise ValueError('step identifier already used; inspect its saved outcome')
        if len(document['steps']) >= 1000:
            raise ValueError('step limit reached')
        snapshot = _snapshot(document)
        if phase in ('work', 'research'):
            used = sum(step['phase'] == 'work' for step in document['steps'])
            if used >= document['max_passes']:
                raise ValueError('work-pass limit reached; switching profiles cannot reset it')
            if _current(document, 'check', snapshot) and _current(document, 'review', snapshot):
                raise ValueError('current evidence already passes; complete instead of using remaining passes')
        if phase == 'review' and not _current(document, 'check', snapshot):
            raise ValueError('review requires passing checks on current artifacts')
        document['steps'].append({'id': identifier, 'phase': phase, 'note': note,
                                  'started_at': now, 'snapshot': snapshot, 'result': None})
        document['note'] = note
        return _save(fd, document, state, now)


def record(state, thread, identifier, outcome, evidence, summary, *, actor=None, now=None):
    _text(summary, 'result summary')
    if outcome not in OUTCOMES:
        raise ValueError('invalid outcome')
    with locked(state) as fd:
        document = _read(fd, thread)
        now = _clock(document, now)
        step = next((item for item in document['steps'] if item['id'] == identifier), None)
        if step is None:
            raise ValueError('unknown step')
        receipt = _file(Path(evidence))
        if receipt['bytes'] == 0:
            raise ValueError('evidence must be a nonempty saved file')
        actor = _identifier(actor or thread)
        supplied = {'outcome': outcome, 'evidence': receipt, 'summary': summary, 'actor': actor}
        if step['result'] is not None:
            if any(step['result'][key] != value for key, value in supplied.items()):
                raise ValueError('conflicting result for an already recorded step')
            return _save(fd, document, state, now, mutate=False)
        snapshot = _snapshot(document)
        if outcome == 'pass' and step['phase'] in ('check', 'review'):
            if not all(snapshot.values()) or snapshot != step['snapshot']:
                raise ValueError('artifacts changed or are missing; record a failure and verify again')
            if step['phase'] == 'review':
                if not _current(document, 'check', snapshot):
                    raise ValueError('review cannot waive missing or failed checks')
                contributors = {thread} | {item['result']['actor'] for item in document['steps']
                    if item['phase'] in ('work', 'research') and item['result']}
                if document['review_mode'] == 'independent' and actor in contributors:
                    raise ValueError('independent review requires a separate reviewer identity')
        step['result'] = supplied | {'snapshot': snapshot, 'finished_at': now,
                                     'duration_seconds': now - step['started_at']}
        if step['phase'] == 'review':
            check = next((item for item in reversed(document['steps']) if item['phase'] == 'check'), None)
            step['result']['check_id'] = check['id'] if check else None
        document['note'] = summary
        return _save(fd, document, state, now)


def transition(state, thread, action, note, *, now=None):
    _text(note, 'transition reason')
    with locked(state) as fd:
        document = _read(fd, thread)
        now = _clock(document, now)
        if document['status'] in ('complete', 'stopped'):
            raise ValueError('terminal progress cannot be restarted')
        if action == 'pause':
            document['status'] = 'paused'
        elif action == 'resume':
            if document['status'] != 'paused' or now >= document['deadline']:
                raise ValueError('resume requires a paused run within its original deadline')
            document['status'] = 'active'
        elif action == 'stop':
            document['status'] = 'stopped'
        elif action == 'complete':
            snapshot = _snapshot(document)
            if document['status'] != 'active' or now >= document['deadline'] or _active(document):
                raise ValueError('completion requires an active, resolved run within deadline')
            if not _current(document, 'check', snapshot) or not _current(document, 'review', snapshot):
                raise ValueError('completion requires current passing checks and review evidence')
            document['status'] = 'complete'
        else:
            raise ValueError('invalid transition')
        document['note'] = note
        return _save(fd, document, state, now)


def add_parser(commands):
    root = commands.add_parser('progress', help='Track this chat without launching another session', allow_abbrev=False)
    actions = root.add_subparsers(dest='progress_action', required=True)
    for name in ('init', 'status', 'begin', 'record', 'pause', 'resume', 'stop', 'complete'):
        sub = actions.add_parser(name, allow_abbrev=False)
        sub.add_argument('--state', type=Path, required=True)
        sub.add_argument('--thread', required=True)
        sub.add_argument('--compact', action='store_true',
                         help='Return concise JSON; full ledger, view and validation are unchanged')
        if name == 'init':
            sub.add_argument('--workspace', type=Path, required=True)
            sub.add_argument('--objective', required=True)
            sub.add_argument('--profile', choices=PROFILES, required=True)
            sub.add_argument('--artifact', action='append', required=True)
            sub.add_argument('--max-passes', type=int, default=3)
            sub.add_argument('--minutes', type=int, default=20)
            sub.add_argument('--review-mode', choices=('self', 'independent'), required=True)
        if name in ('begin', 'record'):
            sub.add_argument('--id', required=True)
        if name == 'begin':
            sub.add_argument('--phase', choices=PHASES, required=True)
            sub.add_argument('--note', required=True)
        if name == 'record':
            sub.add_argument('--outcome', choices=OUTCOMES, required=True)
            sub.add_argument('--evidence', type=Path, required=True)
            sub.add_argument('--summary', required=True)
            sub.add_argument('--actor')
        if name in ('pause', 'resume', 'stop', 'complete'):
            sub.add_argument('--note', required=True)


def dispatch(args):
    common = (args.state, args.thread)
    if args.progress_action == 'init':
        return initialize(args.state, args.workspace, args.thread, args.objective, args.profile,
                          args.artifact, max_passes=args.max_passes, minutes=args.minutes,
                          review_mode=args.review_mode)
    if args.progress_action == 'status':
        return status(*common)
    if args.progress_action == 'begin':
        return begin(*common, args.id, args.phase, args.note)
    if args.progress_action == 'record':
        return record(*common, args.id, args.outcome, args.evidence, args.summary, actor=args.actor)
    return transition(*common, args.progress_action, args.note)
