"""Separate Codex exec sessions with private configuration and bounded receipts.

This requests CLI sandbox enforcement; fake-worker tests verify the command
contract, not live sandbox behavior. Resume is deliberately unsupported. Native
agent tools are disabled, but a general shell is not a hard recursive-CLI jail.
"""
import json
import os
import re
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import tomllib

from .controlplane.config import ensure_private_directory, parse_config_bytes
from .controlplane.doctor import _codex_executable_inspection, _codex_probe_environment, _kill_probe
from .controlplane.security import SecurityError, atomic_private_write, open_owned_directory_nofollow, open_parent_directory_nofollow
from .runtime import digest, encoded, read_at, workspace_identity
from .packets import packet_prompt, prompt_metrics

MAX_OUTPUT = 1024 * 1024
PROVIDER_FIELDS = {'name', 'base_url', 'env_key', 'env_key_instructions', 'wire_api', 'http_headers',
                   'env_http_headers', 'requires_openai_auth', 'request_max_retries',
                   'stream_max_retries', 'stream_idle_timeout_ms', 'supports_websockets',
                   'model_catalog_url', 'supports_standalone_web_search', 'auth'}


def _toml(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) is int:
        return str(value)
    if isinstance(value, dict):
        return '{' + ', '.join(_toml(key) + ' = ' + _toml(item) for key, item in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_toml(item) for item in value) + ']'
    raise ValueError('unsupported account configuration value')


def account_settings(account_home, settings, environment):
    """Preserve the selected provider, not user tools, plugins or permission grants."""
    account_home = Path(account_home)
    fd = open_owned_directory_nofollow(account_home)
    try:
        try:
            source = tomllib.loads(read_at(fd, 'config.toml', 1024 * 1024, required_mode=None).decode('utf-8-sig'))
        except FileNotFoundError:
            source = {}
    finally:
        os.close(fd)
    provider = source.get('model_provider', 'openai')
    if not isinstance(provider, str) or not provider:
        raise ValueError('invalid selected model provider')
    config = {
        'model': settings['model'], 'model_reasoning_effort': settings['effort'], 'model_provider': provider,
        'approval_policy': 'never', 'sandbox_mode': settings['sandbox'], 'web_search': 'disabled',
        'agents': {'enabled': False}, 'apps': {'_default': {'enabled': False}},
        'plugins': {}, 'mcp_servers': {}, 'sandbox_workspace_write': {'network_access': False},
        'cli_auth_credentials_store': 'file',
    }
    provider_env = {}
    definition = source.get('model_providers', {}).get(provider)
    requires_auth = provider == 'openai'
    if definition is not None:
        if not isinstance(definition, dict) or set(definition) - PROVIDER_FIELDS:
            raise ValueError('selected provider has unsupported settings; review its adapter before launch')
        config['model_providers'] = {provider: definition}
        requires_auth = definition.get('requires_openai_auth', False)
        if 'auth' in definition:
            if requires_auth or definition.get('env_key') is not None:
                raise ValueError('command auth cannot be combined with env_key or OpenAI account auth')
            auth_config = definition['auth']
            if (not isinstance(auth_config, dict) or 'command' not in auth_config
                    or set(auth_config) - {'command', 'args', 'timeout_ms', 'refresh_interval_ms'}):
                raise ValueError('unsupported command-auth configuration')
            helper = auth_config['command']
            if not isinstance(helper, str) or not Path(helper).is_absolute():
                raise ValueError('command-auth requires an absolute trusted helper path')
            helper_state, _ = _codex_executable_inspection(Path(helper))
            if helper_state != 'found':
                raise SecurityError('auth helper trust check failed: ' + helper_state)
            args = auth_config.get('args', [])
            if not isinstance(args, list) or len(args) > 64 or not all(isinstance(arg, str) and '\x00' not in arg and len(arg) <= 4096 for arg in args):
                raise ValueError('invalid command-auth arguments')
            for key in ('timeout_ms', 'refresh_interval_ms'):
                if key in auth_config and (type(auth_config[key]) is not int or auth_config[key] < (1 if key == 'timeout_ms' else 0)):
                    raise ValueError('invalid command-auth timing')
            home = environment.get('HOME')
            if not isinstance(home, str) or not Path(home).is_absolute():
                raise ValueError('command-auth requires the original absolute account HOME')
            home_fd = open_owned_directory_nofollow(Path(home))
            os.close(home_fd)
            provider_env['HOME'] = home
            requires_auth = False
        required_key = definition.get('env_key')
        if required_key and (required_key not in environment or not environment[required_key]):
            raise ValueError('selected provider requires an unavailable environment credential')
        header_keys = list(definition.get('env_http_headers', {}).values())
        for key in ([required_key] if required_key else []) + header_keys:
            if not isinstance(key, str) or not key:
                raise ValueError('invalid provider environment name')
            if key not in environment:
                # Codex env_http_headers are optional when a variable is absent.
                continue
            if not isinstance(environment[key], str) or '\x00' in environment[key]:
                raise ValueError('selected provider requires an unavailable environment credential')
            if key in {'PATH', 'HOME', 'CODEX_HOME', 'PYTHONPATH', 'NODE_OPTIONS'} or key.startswith(('LD_', 'DYLD_')):
                raise ValueError('provider environment key conflicts with runtime isolation')
            provider_env[key] = environment[key]
    elif provider != 'openai':
        raise ValueError('selected provider definition is missing')
    if 'service_tier' in source:
        config['service_tier'] = source['service_tier']
    auth = None
    if requires_auth:
        fd = open_owned_directory_nofollow(account_home)
        try:
            try:
                auth = read_at(fd, 'auth.json', 1024 * 1024)
            except FileNotFoundError as error:
                raise ValueError('file-based account credentials are unavailable; keyring migration requires separate review') from error
        finally:
            os.close(fd)
        if not isinstance(parse_config_bytes(auth), dict):
            raise ValueError('invalid account credential document')
    text = ''.join(_toml(key) + ' = ' + _toml(value) + '\n' for key, value in config.items())
    if tomllib.loads(text) != config:
        raise ValueError('worker configuration did not round-trip')
    return text.encode(), auth, provider_env, provider


def command(executable, workspace, settings):
    if settings['sandbox'] not in ('read-only', 'workspace-write'):
        raise ValueError('unsupported worker sandbox')
    return [str(executable), '--strict-config', 'exec', '--json', '--sandbox', settings['sandbox'],
            '--cd', str(workspace), '--model', settings['model'], '-']


def parse_events(raw, returncode):
    """Validate one exec turn; retain observations even after a corrupt event.

    Completion usage is a turn aggregate, not an incremental counter. Duplicate
    terminal events are ambiguous even when equal: retain them, never sum them.
    """
    threads, terminals, observations, diagnostics = [], [], [], []
    handoff, failure = '', False
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            if len(line) > 256 * 1024:
                raise ValueError('event too large')
            event = parse_config_bytes(line)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('invalid event')
            kind = event['type']
            if kind == 'thread.started':
                thread = event.get('thread_id')
                if not isinstance(thread, str) or not 1 <= len(thread) <= 128:
                    raise ValueError('invalid thread identity')
                threads.append(thread)
            elif kind in ('turn.completed', 'turn.failed'):
                terminals.append(event)
                observations.append({'source': kind, 'usage': event.get('usage')})
                if len(threads) != 1:
                    diagnostics.append('terminal event requires one preceding thread identity')
                failure |= kind == 'turn.failed'
            elif kind == 'error':
                failure = True
            elif kind == 'item.completed':
                item = event.get('item')
                if isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                    handoff = item['text'][:8000]
        except (ValueError, UnicodeError, RecursionError) as error:
            diagnostics.append(str(error))
    if len(threads) != 1 or len(terminals) > 1:
        diagnostics.append('execution must identify one fresh thread and at most one terminal event')
    usage, details = None, {}
    if len(terminals) == 1 and terminals[0].get('usage') is not None:
        reported = terminals[0]['usage']
        required = {'input_tokens', 'cached_input_tokens', 'output_tokens'}
        optional = {'cache_write_input_tokens', 'reasoning_output_tokens'}
        if (not isinstance(reported, dict) or not required <= set(reported)
                or set(reported) - required - optional
                or any(type(value) is not int or not 0 <= value <= 10**12 for value in reported.values())):
            diagnostics.append('invalid reported token usage')
        elif (reported['cached_input_tokens'] + reported.get('cache_write_input_tokens', 0) > reported['input_tokens']
                or reported.get('reasoning_output_tokens', 0) > reported['output_tokens']):
            diagnostics.append('invalid token subset')
        else:
            usage = {key: reported[key] for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')}
            details = {key: reported[key] for key in sorted(optional) if key in reported}
    outcome = 'completed' if returncode == 0 and len(terminals) == 1 and not failure else 'failed'
    if diagnostics:
        outcome, usage, details = 'protocol_error', None, {}
    result = {'thread_id': threads[0] if len(threads) == 1 else None, 'usage': usage,
              'outcome': outcome, 'handoff': handoff, 'usage_observations': observations,
              'diagnostics': diagnostics}
    if details:
        result['usage_details'] = details
    return result


def reject_project_configuration(workspace, account_home):
    """Do not assume strict-config or a fresh home blocks project overrides."""
    workspace, account_home = Path(workspace), Path(account_home)
    for ancestor in (workspace, *workspace.parents):
        directory = ancestor / '.codex'
        # The explicitly supplied account config is sanitized separately.
        if directory == account_home:
            continue
        if directory.is_symlink():
            raise SecurityError('project or ancestor .codex directory is a link')
        if directory.is_dir():
            fd = open_owned_directory_nofollow(directory)
            os.close(fd)
        config = directory / 'config.toml'
        if config.exists() or config.is_symlink():
            raise SecurityError('project or ancestor Codex configuration could override worker isolation; separate-session launch is blocked')


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _redact(text, configuration, auth, environment):
    config = tomllib.loads(configuration.decode())
    values = [value for key, value in environment.items() if key != 'HOME']
    for provider in config.get('model_providers', {}).values():
        values.extend(_strings(provider.get('http_headers', {})))
    if auth is not None:
        values.extend(_strings(parse_config_bytes(auth)))
    for secret in sorted(set(values), key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[redacted credential]')
    return text


def redact_diagnostics(raw, configuration, auth, environment, jsonl=False):
    """Keep valid JSONL valid while removing known values and common credentials.

    This is best-effort redaction, not permission to publish private task output.
    """
    sensitive = re.compile(r'(?i)^(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie|set-cookie)$')

    def clean(value):
        if isinstance(value, dict):
            return {key: '[redacted]' if sensitive.match(key) else clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if not isinstance(value, str):
            return value
        value = _redact(value, configuration, auth, environment)
        value = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]', value)
        value = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+', r'\1[redacted]', value)
        return value

    text = raw.decode('utf-8', errors='replace')
    if not jsonl:
        return clean(text).encode()
    lines = []
    for line in text.splitlines():
        try:
            lines.append(json.dumps(clean(parse_config_bytes(line.encode('utf-8'))), ensure_ascii=False))
        except (ValueError, RecursionError):
            lines.append(json.dumps({'type': 'diagnostic.invalid_json', 'text': clean(line)}))
    return ('\n'.join(lines) + ('\n' if lines else '')).encode()


class CodexRunner:
    """A foreground, bounded subprocess adapter; no implicit retry or resume."""
    def __init__(self, executable, account_home, timeout=300, environment=None, node=None, retain_diagnostics=False):
        if type(timeout) is not int or not 1 <= timeout <= 2400:
            raise ValueError('worker timeout must be between 1 and 2400 seconds')
        self.executable, self.account_home = Path(executable), Path(account_home)
        self.node = Path(node) if node is not None else None
        self.timeout = timeout
        self.environment = dict(os.environ if environment is None else environment)
        self.retain_diagnostics = retain_diagnostics
        self.spawn_attempted = False

    def render_prompt(self, request):
        prompt_metrics(request)
        return packet_prompt(request).encode()

    def prepare_configuration(self, configuration, worker_home, request):
        return configuration

    def build_command(self, workspace, settings):
        return command(self.executable, workspace, settings)

    def run(self, request, workspace, run_root, started):
        outer_start = time.monotonic()
        if Path(run_root).is_relative_to(Path(workspace)):
            raise SecurityError('worker private home must be outside its workspace')
        worker_home = Path(run_root) / 'codex-home'
        if worker_home.exists() or worker_home.is_symlink():
            raise SecurityError('worker home already exists; do not reuse an interrupted session')
        try:
            receipt = self._run(request, workspace, run_root, started, outer_start)
        finally:
            # Remove copied credentials, private config and CLI session history.
            # fd-relative rmtree refuses symlink replacement of the managed leaf.
            if worker_home.exists() or worker_home.is_symlink():
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise SecurityError('safe worker-home cleanup is unavailable')
                fd = open_parent_directory_nofollow(Path(run_root))
                try:
                    shutil.rmtree('codex-home', dir_fd=fd)
                    os.fsync(fd)
                finally:
                    os.close(fd)
        receipt['private_home_removed'] = True
        receipt['elapsed_seconds'] = round(time.monotonic() - outer_start, 6)
        atomic_private_write(Path(run_root) / 'receipt.json', encoded(receipt))
        return receipt

    def _run(self, request, workspace, run_root, started, outer_start):
        prompt = self.render_prompt(request)
        reject_project_configuration(workspace, self.account_home)
        state, snapshot = _codex_executable_inspection(self.executable)
        if state != 'found':
            raise SecurityError('Codex executable trust check failed: ' + state)
        node_snapshot = None
        if self.node is not None:
            if self.node.name != 'node' or os.pathsep in str(self.node.parent):
                raise ValueError('Node must name an absolute node executable with an unambiguous PATH directory')
            node_snapshot = _codex_executable_inspection(self.node)
            if node_snapshot[0] != 'found':
                raise SecurityError('Node executable trust check failed: ' + node_snapshot[0])
        configuration, auth, provider_env, provider = account_settings(self.account_home, request['settings'], self.environment)
        auth_settings = tomllib.loads(configuration.decode()).get('model_providers', {}).get(provider, {}).get('auth')
        helper = Path(auth_settings['command']) if auth_settings else None
        helper_snapshot = _codex_executable_inspection(helper) if helper is not None else None
        if helper_snapshot is not None and helper_snapshot[0] != 'found':
            raise SecurityError('auth helper changed after account inspection')
        worker_home = ensure_private_directory(run_root / 'codex-home')
        configuration = self.prepare_configuration(configuration, worker_home, request)
        atomic_private_write(worker_home / 'config.toml', configuration)
        if auth is not None:
            atomic_private_write(worker_home / 'auth.json', auth)
        atomic_private_write(run_root / 'packet.txt', prompt)
        environment = dict(_codex_probe_environment(self.executable), HOME=str(worker_home), CODEX_HOME=str(worker_home),
                           PYTHONDONTWRITEBYTECODE='1')
        if self.node is not None:
            node_directory = str(self.node.parent)
            environment['PATH'] = os.pathsep.join([node_directory] + [entry for entry in environment['PATH'].split(os.pathsep)
                                                                     if entry != node_directory])
        environment.update(provider_env)
        argv = self.build_command(workspace, request['settings'])
        reject_project_configuration(workspace, self.account_home)
        expected_workspace = request.get('workspace_identity')
        if expected_workspace is not None and workspace_identity(workspace) != expected_workspace:
            raise SecurityError('workspace changed before process creation')
        # Revalidate immediately adjacent to spawn, retaining the upstream trust rule.
        if _codex_executable_inspection(self.executable) != ('found', snapshot):
            raise SecurityError('Codex executable changed after inspection')
        if helper is not None and _codex_executable_inspection(helper) != helper_snapshot:
            raise SecurityError('auth helper changed before process creation')
        if self.node is not None and _codex_executable_inspection(self.node) != node_snapshot:
            raise SecurityError('Node executable changed before process creation')
        with os.fdopen(os.open(run_root / 'packet.txt', os.O_RDONLY | os.O_NOFOLLOW), 'rb') as packet:
            if time.monotonic() - outer_start >= self.timeout:
                raise TimeoutError('deadline exhausted during launch preparation')
            self.spawn_attempted = True
            process = subprocess.Popen(argv, stdin=packet, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=workspace, env=environment, shell=False, start_new_session=True)
        output, errors = bytearray(), bytearray()
        started_at = time.monotonic()
        timed_out = limited = False
        selector = selectors.DefaultSelector()
        try:
            started(process.pid)
            for pipe, target in ((process.stdout, output), (process.stderr, errors)):
                selector.register(pipe, selectors.EVENT_READ, target)
            while selector.get_map():
                if time.monotonic() - outer_start >= self.timeout:
                    timed_out = True
                    break
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk)
                    if len(output) + len(errors) > MAX_OUTPUT:
                        limited = True
                        break
                if limited:
                    break
            if timed_out or limited:
                _kill_probe(process)
            else:
                try:
                    process.wait(timeout=max(0.01, self.timeout - (time.monotonic() - outer_start)))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _kill_probe(process)
        except BaseException:
            _kill_probe(process)
            raise
        finally:
            selector.close()
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            if self.retain_diagnostics:
                atomic_private_write(run_root / 'events.redacted.jsonl',
                                     redact_diagnostics(bytes(output), configuration, auth, provider_env, jsonl=True))
                atomic_private_write(run_root / 'stderr.redacted.txt',
                                     redact_diagnostics(bytes(errors), configuration, auth, provider_env))
        result = parse_events(bytes(output), process.returncode)
        result = json.loads(redact_diagnostics(json.dumps(result).encode(), configuration, auth, provider_env, jsonl=True))
        handoff = result.pop('handoff')
        if timed_out or limited:
            result['outcome'] = 'timeout' if timed_out else 'protocol_error'
        receipt = dict(result, request_hash=request['request_hash'], provider=provider,
                       model_requested=request['settings']['model'], sandbox_requested=request['settings']['sandbox'],
                       exit_code=process.returncode, elapsed_seconds=round(time.monotonic() - outer_start, 6),
                       preparation_seconds=round(started_at - outer_start, 6),
                       execution_seconds=round(time.monotonic() - started_at, 6),
                       diagnostics_retained=self.retain_diagnostics, output_limited=limited,
                       pid=process.pid, process_group_id=process.pid,
                       output_sha256=digest(bytes(output)), stderr_sha256=digest(bytes(errors)),
                       configuration_sha256=digest(configuration), handoff_sha256=digest(handoff.encode()),
                       host_enforcement='requested_not_independently_verified', recursive_shell_containment='not_enforced')
        atomic_private_write(run_root / 'handoff.txt', handoff.encode())
        return receipt
