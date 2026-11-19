"""Owned CLI. All mutations name their state roots explicitly."""
import argparse
import json
from pathlib import Path
import sys

from . import __version__, progress, runtime
from .policy import assess_dispatch, load_json, load_policy
from .controlplane.config import apply_config, load_config, preview_config, preview_restore
from .controlplane.diagnostics import CommandResult, command_result, render_json
from .controlplane.profiles import apply_profiles, preview_profiles
from .controlplane.security import read_regular_nofollow


def parser(*, progress_only=False):
    root = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('version')
    progress.add_parser(commands)
    if progress_only:
        return root
    diagnostic = commands.add_parser('assess')
    diagnostic.add_argument('--proposal', required=True, type=Path)
    diagnostic.add_argument('--ledger', required=True, type=Path)
    diagnostic.add_argument('--policy', type=Path)
    route = commands.add_parser('route')
    route.add_argument('--objective', required=True)
    route.add_argument('--known-area', action='store_true')
    route.add_argument('--acceptance-criteria', action='store_true')
    route.add_argument('--files', type=int, default=2)
    route.add_argument('--risk', choices=('low', 'normal', 'high', 'unknown'), default='unknown')
    route.add_argument('--read-only', action='store_true')
    route.add_argument('--review', action='store_true')
    discovery = commands.add_parser('discover')
    discovery.add_argument('--query', required=True)
    discovery.add_argument('--root', action='append', type=Path, required=True)
    discovery.add_argument('--limit', type=int, default=5)
    doctor = commands.add_parser('doctor')
    doctor.add_argument('--codex', type=Path)
    for name in ('config-preview', 'config-restore-preview', 'config-apply', 'profile-preview', 'profile-apply', 'init', 'status',
                 'preview', 'reserve', 'launch', 'reconcile', 'finish'):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument('--state', type=Path, required=True)
        if name in ('preview', 'reserve', 'launch', 'reconcile', 'finish'):
            sub.add_argument('--owner-stdin', action='store_true', required=True,
                             help='Read the coordinator capability from stdin; never pass it in argv')
        if name in ('config-apply', 'profile-apply', 'reserve'):
            sub.add_argument('--token', required=True)
            sub.add_argument('--approval', required=True)
        if name.startswith('profile-'):
            sub.add_argument('--profiles-root', required=True, type=Path)
            sub.add_argument('--action', choices=('install', 'update', 'uninstall'), required=True)
        if name == 'config-preview':
            sub.add_argument('--set', action='append', required=True, dest='settings')
        if name == 'config-restore-preview':
            sub.add_argument('--backup', required=True)
        if name == 'init':
            sub.add_argument('--workspace', type=Path, required=True)
            sub.add_argument('--model', required=True)
            sub.add_argument('--effort', required=True)
            sub.add_argument('--policy', type=Path)
        if name == 'preview':
            sub.add_argument('--proposal', type=Path, required=True)
            sub.add_argument('--skill', type=Path, action='append', default=[])
            sub.add_argument('--worker-effort', help='Explicit effort for a compatible preset; never silently downgraded')
        if name in ('launch', 'reconcile', 'finish'):
            sub.add_argument('--id', required=True)
        if name == 'launch':
            sub.add_argument('--codex', type=Path, required=True)
            sub.add_argument('--node', type=Path, help='Explicit trusted node executable for Codex wrappers that require it')
            sub.add_argument('--account-home', type=Path, required=True)
            sub.add_argument('--timeout', type=int, default=300)
        if name == 'finish':
            sub.add_argument('--accepted', choices=('true', 'false'), required=True)
            sub.add_argument('--verification', required=True)
    return root


def _owner(args):
    if not args.owner_stdin:
        raise ValueError('coordinator capability must be supplied through stdin')
    token = sys.stdin.read(4097).strip()
    if len(token) != 64:
        raise ValueError('stdin must contain exactly one coordinator capability')
    return token


def dispatch(args):
    name = args.command
    if name == 'progress':
        return progress.dispatch(args)
    if name == 'version':
        return {'name': 'personal-orchestrator', 'version': __version__, 'package': 'self-contained-skill',
                'live_execution_verified': False, 'marketplace_dependency': False}
    if name == 'assess':
        return assess_dispatch(load_json(args.proposal), load_json(args.ledger), load_policy(args.policy))
    if name == 'route':
        from .controlplane.routing import RouteFacts, recommend_route
        return recommend_route(RouteFacts(args.objective, args.known_area, args.acceptance_criteria,
                                          args.files, args.risk, read_only=args.read_only, require_review=args.review))
    if name == 'discover':
        from .controlplane.discovery import DiscoveryRequest, discover
        return discover(DiscoveryRequest(args.query, args.root, (), args.limit))
    if name == 'doctor':
        from .controlplane.doctor import check_codex_cli, check_manifests, check_python_and_platform
        diagnostics = list(check_python_and_platform()) + list(check_manifests(Path(__file__).parent))
        if args.codex is not None:
            if args.codex.name not in ('codex', 'codex.exe'):
                raise ValueError('doctor requires an explicit codex executable path')
            diagnostics.append(check_codex_cli({'PATH': str(args.codex.parent)}))
        return command_result('doctor', diagnostics=diagnostics, data={'live_execution_verified': False,
                              'native_windows_mutation': False, 'profile_files_are_not_permission_evidence': True})
    if name == 'config-preview':
        settings = {}
        for item in args.settings:
            key, separator, value = item.partition('=')
            if not separator or key in settings:
                raise ValueError('configuration settings must be unique KEY=VALUE pairs')
            settings[key] = value
        return preview_config(settings, args.state)[1]
    if name == 'config-apply':
        return apply_config(args.token, args.state, args.approval)
    if name == 'config-restore-preview':
        return preview_restore(args.backup, args.state)[1]
    if name == 'profile-preview':
        return preview_profiles(args.action, load_config(args.state), args.profiles_root, args.state)[1]
    if name == 'profile-apply':
        return apply_profiles(args.action, args.token, args.profiles_root, args.state, args.approval)
    if name == 'init':
        return runtime.initialize(args.state, args.workspace, args.model, args.effort, load_policy(args.policy))
    if name == 'status':
        state = runtime.status(args.state)
        return {key: state[key] for key in ('session_id', 'workspace', 'model', 'effort', 'revision', 'calls')} | {
            'launches': {identifier: {'phase': item['phase'], 'pid': item['pid'], 'receipt': item['receipt']}
                         for identifier, item in state['launches'].items()}}
    owner = _owner(args)
    if name == 'preview':
        guidance = [{'name': path.parent.name, 'text': read_regular_nofollow(path, 16000).decode()}
                    for path in args.skill]
        return runtime.preview(args.state, owner, load_json(args.proposal), guidance, worker_effort=args.worker_effort)
    if name == 'reserve':
        return runtime.reserve(args.state, owner, args.token, args.approval)
    if name == 'launch':
        from .execution import CodexRunner
        return runtime.execute_reserved(args.state, owner, args.id,
                                        CodexRunner(args.codex, args.account_home, args.timeout, node=args.node))
    if name == 'reconcile':
        return runtime.reconcile(args.state, owner, args.id)
    if name == 'finish':
        return runtime.finish(args.state, owner, args.id, args.accepted == 'true', args.verification)
    raise ValueError('unknown command')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parser(progress_only=bool(argv and argv[0] == 'progress')).parse_args(argv)
    try:
        result = dispatch(args)
        if isinstance(result, CommandResult):
            print(render_json(result))
            return 0 if result.ok else 1
        compact = args.command == 'progress' and args.compact
        if compact:
            result = progress.compact_summary(result)
        print(json.dumps(result, indent=None if compact else 2,
                         separators=(',', ':') if compact else None, allow_nan=False))
        return 1 if result.get('allowed') is False else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__, 'message': str(error),
                          'dispatch_state': 'not_confirmed'}))
        return 2
