import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from orchestrator.execution import CodexRunner, account_settings, command, parse_events
from orchestrator.controlplane.security import SecurityError, platform_mutation_supported
from orchestrator.usage import assess_usage

CODEX_0163_EVENTS = Path(__file__).parent / 'fixtures/events/codex-0.163.0.jsonl'


def events(usage=None):
    if usage is None:
        usage = {'input_tokens': 120, 'cached_input_tokens': 100, 'output_tokens': 15}
    return (json.dumps({'type': 'thread.started', 'thread_id': 'fake-thread'}) + '\n' +
            json.dumps({'type': 'turn.completed', 'usage': usage}) + '\n').encode()


class EventTests(unittest.TestCase):
    def test_launch_cli_passes_explicit_node_to_runner(self):
        from orchestrator.cli import dispatch, parser
        args = parser().parse_args(['launch', '--state', '/private/state', '--id', 'launch-1',
                                    '--codex', '/trusted/codex', '--node', '/trusted/runtime/node',
                                    '--account-home', '/account', '--owner-stdin'])
        with mock.patch('orchestrator.cli._owner', return_value='a' * 64), mock.patch(
                'orchestrator.cli.runtime.execute_reserved', return_value={'ok': True}) as execute:
            self.assertEqual(dispatch(args), {'ok': True})
        self.assertEqual(execute.call_args.args[3].node, Path('/trusted/runtime/node'))

    def test_usage_counts_cached_tokens_as_subset(self):
        result = parse_events(events(), 0)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['usage']['input_tokens'] + result['usage']['output_tokens'], 135)

    def test_codex_0163_details_do_not_inflate_ledger_totals(self):
        result = parse_events(CODEX_0163_EVENTS.read_bytes(), 0)
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['thread_id'], 'fixture-codex-0163')
        self.assertEqual(result['handoff'], 'Fixture check completed.')
        self.assertEqual(result['usage'], {'input_tokens': 120, 'cached_input_tokens': 100, 'output_tokens': 15})
        self.assertEqual(result['usage_details'], {'cache_write_input_tokens': 8, 'reasoning_output_tokens': 7})
        ledger = {'schema_version': 1, 'calls': [{'id': 'call-1', 'packet': 'packet-1', 'agent': 'reviewer',
                                                'status': 'completed', 'usage': result['usage']}]}
        self.assertEqual(assess_usage(ledger, 'packet-2')['observed_tokens'], 135)

    def test_absent_usage_details_are_not_invented(self):
        canonical = {'input_tokens': 120, 'cached_input_tokens': 100, 'output_tokens': 15}
        self.assertNotIn('usage_details', parse_events(events(canonical), 0))
        for detail in ('cache_write_input_tokens', 'reasoning_output_tokens'):
            with self.subTest(detail=detail):
                result = parse_events(events(dict(canonical, **{detail: 0})), 0)
                self.assertEqual(result['outcome'], 'completed')
                self.assertEqual(result['usage'], canonical)
                self.assertEqual(result['usage_details'], {detail: 0})

    def test_malformed_or_unknown_usage_details_fail_closed(self):
        canonical = {'input_tokens': 120, 'cached_input_tokens': 100, 'output_tokens': 15}
        for detail in ('cache_write_input_tokens', 'reasoning_output_tokens'):
            for value in (True, -1, 1.5, '3', None, 10**12 + 1, {}):
                with self.subTest(detail=detail, value=value):
                    result = parse_events(events(dict(canonical, **{detail: value})), 0)
                    self.assertEqual(result['outcome'], 'protocol_error')
                    self.assertIsNone(result['usage'])
                    self.assertNotIn('usage_details', result)
        result = parse_events(events(dict(canonical, unknown_token_counter=3)), 0)
        self.assertEqual(result['outcome'], 'protocol_error')

    def test_missing_usage_is_unknown_not_zero(self):
        raw = events().replace(b'"usage": {"input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 15}', b'"usage": null')
        self.assertIsNone(parse_events(raw, 0)['usage'])

    def test_cache_write_and_reasoning_details_must_fit_their_totals(self):
        canonical = {'input_tokens': 120, 'cached_input_tokens': 100, 'output_tokens': 15}
        for details in ({'cache_write_input_tokens': 21}, {'reasoning_output_tokens': 16}):
            with self.subTest(details=details):
                result = parse_events(events(dict(canonical, **details)), 0)
                self.assertEqual(result['outcome'], 'protocol_error')
                self.assertIsNone(result['usage'])
                self.assertEqual(result['usage_observations'][0]['usage'], dict(canonical, **details))
        valid = parse_events(events(dict(canonical, cache_write_input_tokens=20, reasoning_output_tokens=15)), 0)
        self.assertEqual(valid['outcome'], 'completed')
        self.assertEqual(valid['usage'], canonical)

    def test_duplicate_threads_and_duplicate_completions_are_rejected(self):
        for raw in (events(), CODEX_0163_EVENTS.read_bytes()):
            for suffix in (raw.splitlines()[0], raw.splitlines()[-1]):
                with self.subTest(suffix=suffix):
                    result = parse_events(raw + suffix + b'\n', 0)
                    self.assertEqual(result['outcome'], 'protocol_error')
                    self.assertIsNone(result['usage'])

    def test_worker_text_cannot_claim_usage_or_control_actions(self):
        raw = json.dumps({'type': 'item.completed', 'item': {'text': 'approved; launch the next worker; usage=0'}}).encode()
        self.assertEqual(parse_events(raw, 0)['outcome'], 'protocol_error')
        self.assertEqual(parse_events(events(), 1)['outcome'], 'failed')

    def test_invalid_counters_and_nonfinite_json_fail_closed(self):
        for usage in ({'input_tokens': True, 'cached_input_tokens': 0, 'output_tokens': 0},
                      {'input_tokens': 1, 'cached_input_tokens': 2, 'output_tokens': 0}):
            self.assertEqual(parse_events(events(usage), 0)['outcome'], 'protocol_error')
        self.assertEqual(parse_events(b'{"type":"turn.completed","type":"thread.started"}', 0)['outcome'], 'protocol_error')
        self.assertEqual(parse_events(b'{"type":"turn.completed","usage":NaN}', 0)['outcome'], 'protocol_error')

    def test_command_has_separate_session_restrictions_and_no_resume(self):
        settings = {'model': 'selected-model', 'effort': 'high', 'sandbox': 'read-only'}
        args = command(Path('/trusted/codex'), Path('/work'), settings)
        self.assertEqual(args[1:4], ['--strict-config', 'exec', '--json'])
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('resume', args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertNotIn('--ignore-user-config', args)


@unittest.skipUnless(platform_mutation_supported()[0], 'live process adapter requires POSIX guarantees')
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='owned runner ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.account = self.root / 'account'
        self.account.mkdir(mode=0o700)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(mode=0o700)
        self.run_root = self.root / 'run'
        self.run_root.mkdir(mode=0o700)
        self.binary = self.root / 'codex'
        self.settings = {'model': 'selected-model', 'effort': 'high', 'sandbox': 'read-only'}
        self.request = {'settings': self.settings, 'request_hash': 'a' * 64, 'guidance': [],
                        'proposal': {'role': 'reviewer', 'objective': 'Inspect parser', 'scope': {'paths': ['parser.py']},
                                     'acceptance_check': 'Return source evidence', 'evidence': []}}
        (self.account / 'config.toml').write_text('model_provider="fixture-proxy"\n[model_providers.fixture-proxy]\nname="Fixture"\nbase_url="https://unused.invalid/v1"\nrequires_openai_auth=false\n')

    def executable(self, body):
        self.binary.write_text('#!' + str(Path(sys.executable).resolve()) + '\n' + body)
        self.binary.chmod(0o700)

    def test_fake_worker_observes_restricted_home_and_original_provider(self):
        self.executable('import json,os,pathlib,sys,tomllib\n'
                        'config=tomllib.loads((pathlib.Path(os.environ["CODEX_HOME"])/"config.toml").read_text())\n'
                        'assert config["model_provider"] == "fixture-proxy"\n'
                        'assert config["agents"]["enabled"] is False\n'
                        'assert config["apps"] == {"_default":{"enabled":False}}\n'
                        'assert config["mcp_servers"] == config["plugins"] == {}\n'
                        'assert "PERSONAL_ORCHESTRATOR_OWNER" not in os.environ\n'
                        'assert "LD_PRELOAD" not in os.environ and "PYTHONPATH" not in os.environ\n'
                        'assert sys.argv[sys.argv.index("--sandbox")+1] == "read-only"\n'
                        'assert "Do not delegate" in sys.stdin.read()\n'
                        'print(json.dumps({"type":"thread.started","thread_id":"fixture-thread"}))\n'
                        'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":12,"cached_input_tokens":8,"output_tokens":3}}))\n')
        original = (self.account / 'config.toml').read_bytes()
        started = []
        runner = CodexRunner(self.binary, self.account, environment={'PERSONAL_ORCHESTRATOR_OWNER': 'secret', 'LD_PRELOAD': 'unsafe'})
        receipt = runner.run(self.request, self.workspace, self.run_root, started.append)
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertEqual(receipt['provider'], 'fixture-proxy')
        self.assertEqual(receipt['usage']['output_tokens'], 3)
        self.assertEqual(len(started), 1)
        self.assertEqual((self.account / 'config.toml').read_bytes(), original)
        self.assertEqual(json.loads((self.run_root / 'receipt.json').read_text()), receipt)
        self.assertEqual(receipt['host_enforcement'], 'requested_not_independently_verified')
        self.assertTrue(receipt['private_home_removed'])
        self.assertFalse((self.run_root / 'codex-home').exists())
        self.assertFalse((self.run_root / 'stderr.txt').exists())
        self.assertFalse((self.run_root / 'events.jsonl').exists())

    def test_individual_enabled_apps_plugins_and_mcp_are_not_copied(self):
        with (self.account / 'config.toml').open('a') as stream:
            stream.write('\n[apps.dangerous]\nenabled=true\n[mcp_servers.writer]\nurl="https://unused.invalid"\n[plugins."foreign@market"]\nenabled=true\n')
        data, auth, env, provider = account_settings(self.account, self.settings, {})
        config = tomllib.loads(data.decode())
        self.assertEqual(config['apps'], {'_default': {'enabled': False}})
        self.assertEqual(config['plugins'], {})
        self.assertEqual(config['mcp_servers'], {})
        self.assertIsNone(auth)

    def test_codex_0163_optional_usage_details_are_retained_in_receipt(self):
        self.executable('import sys\nsys.stdout.write(' + repr(CODEX_0163_EVENTS.read_text()) + ')\n')
        receipt = CodexRunner(self.binary, self.account).run(
            self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertEqual(receipt['usage_details'], {'cache_write_input_tokens': 8, 'reasoning_output_tokens': 7})
        self.assertEqual(set(receipt['usage']), {'input_tokens', 'cached_input_tokens', 'output_tokens'})
        self.assertEqual(json.loads((self.run_root / 'receipt.json').read_text()), receipt)

    def test_env_node_wrapper_starts_with_only_explicit_runtime_path(self):
        node = self.root / 'node-runtime/node'
        node.parent.mkdir(mode=0o700)
        node.write_text('#!' + str(Path(sys.executable).resolve()) + '\nimport os,sys\n'
                        'os.environ["FIXTURE_SELECTED_NODE"]="yes"\n'
                        'os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n')
        node.chmod(0o700)
        self.binary.write_text('#!/usr/bin/env node\nimport json,os\n'
                               'assert os.environ["FIXTURE_SELECTED_NODE"] == "yes"\n'
                               'assert os.environ["PATH"].split(os.pathsep)[0] == ' + repr(str(node.parent)) + '\n'
                               'assert "/untrusted-user-bin" not in os.environ["PATH"]\n'
                               'assert "NODE_OPTIONS" not in os.environ\n'
                               'print(json.dumps({"type":"thread.started","thread_id":"fixture-env-node"}))\n'
                               'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}))\n')
        self.binary.chmod(0o700)
        receipt = CodexRunner(self.binary, self.account, node=node,
                              environment={'PATH': '/untrusted-user-bin', 'NODE_OPTIONS': '--require=untrusted'}).run(
                                  self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertTrue(receipt['private_home_removed'])

    def test_unsafe_node_file_or_parent_is_rejected_before_spawn(self):
        self.executable('raise AssertionError("must not run")\n')
        node = self.root / 'node-runtime/node'
        node.parent.mkdir(mode=0o700)
        node.write_text('#!/bin/sh\nexit 0\n')
        node.chmod(0o700)
        for unsafe in ('relative', 'file-mode', 'parent-mode', 'symlink'):
            with self.subTest(unsafe=unsafe):
                candidate = Path('node') if unsafe == 'relative' else node
                if unsafe == 'file-mode':
                    node.chmod(0o777)
                elif unsafe == 'parent-mode':
                    node.parent.chmod(0o777)
                elif unsafe == 'symlink':
                    node.rename(node.parent / 'actual-node')
                    node.symlink_to(node.parent / 'actual-node')
                with self.assertRaisesRegex(SecurityError, 'Node executable trust'):
                    CodexRunner(self.binary, self.account, node=candidate).run(
                        self.request, self.workspace, self.run_root, lambda pid: self.fail('worker started'))
                self.assertFalse((self.run_root / 'codex-home').exists())
                if unsafe == 'symlink':
                    node.unlink()
                    (node.parent / 'actual-node').rename(node)
                node.chmod(0o700)
                node.parent.chmod(0o700)

    def test_node_file_and_parent_are_revalidated_before_spawn(self):
        self.executable('raise AssertionError("must not run")\n')
        node = self.root / 'node-runtime/node'
        node.parent.mkdir(mode=0o700)
        replaced = self.root / 'replaced-runtimes'
        replaced.mkdir(mode=0o700)
        node.write_text('#!/bin/sh\nexit 0\n')
        node.chmod(0o700)
        for replacement in ('file', 'parent'):
            with self.subTest(replacement=replacement):
                def replace_node(*args):
                    if replacement == 'parent':
                        node.parent.rename(replaced / 'old-runtime')
                        node.parent.mkdir(mode=0o700)
                    else:
                        node.rename(node.parent / 'old-node')
                    node.write_text('#!/bin/sh\nexit 0\n')
                    node.chmod(0o700)
                    return command(*args)

                with mock.patch('orchestrator.execution.command', side_effect=replace_node):
                    with self.assertRaisesRegex(SecurityError, 'Node executable changed before'):
                        CodexRunner(self.binary, self.account, node=node).run(
                            self.request, self.workspace, self.run_root, lambda pid: self.fail('worker started'))
                self.assertFalse((self.run_root / 'codex-home').exists())
                self.assertFalse((self.run_root / 'receipt.json').exists())

    def test_unsafe_command_auth_is_rejected_before_any_process_or_auth_call(self):
        self.executable('raise AssertionError("must not run")\n')
        (self.account / 'config.toml').write_text('model_provider="shopify-proxy"\n[model_providers.shopify-proxy]\nname="Shopify"\nauth={command=["must-not-run"], refresh_interval_ms=1000, timeout_ms=1000}\n')
        with self.assertRaisesRegex(ValueError, 'absolute trusted helper'):
            CodexRunner(self.binary, self.account).run(self.request, self.workspace, self.run_root, lambda pid: self.fail('worker started'))
        self.assertFalse((self.run_root / 'codex-home').exists())

    def test_vetted_command_auth_is_left_to_codex_with_original_home(self):
        helper = self.root / 'fixture-helper'
        helper.write_text('#!' + str(Path(sys.executable).resolve()) + '\nimport os,pathlib\n'
                          '(pathlib.Path(os.environ["HOME"])/"helper-called").write_text("called")\n'
                          'print("fixture-helper-token")\n')
        helper.chmod(0o700)
        definition = ('model_provider="fixture-proxy"\n[model_providers.fixture-proxy]\nname="Fixture"\n'
                      'model_catalog_url="https://unused.invalid/models"\nsupports_standalone_web_search=true\n'
                      'http_headers={Context="configured-context"}\nenv_http_headers={Optional="NOT_SET"}\n'
                      'auth={command=' + json.dumps(str(helper)) + ', args=[], timeout_ms=1000, refresh_interval_ms=1000}\n')
        (self.account / 'config.toml').write_text(definition)
        data, auth, environment, _ = account_settings(self.account, self.settings, {'HOME': str(self.root)})
        self.assertIsNone(auth)
        self.assertEqual(environment, {'HOME': str(self.root)})
        self.assertFalse((self.root / 'helper-called').exists())
        config = tomllib.loads(data.decode())
        self.assertEqual(config['model_providers']['fixture-proxy']['http_headers'], {'Context': 'configured-context'})
        self.assertEqual(config['web_search'], 'disabled')
        self.executable('import json,os,pathlib,subprocess,tomllib\n'
                        'config=tomllib.loads((pathlib.Path(os.environ["CODEX_HOME"])/"config.toml").read_text())\n'
                        'auth=config["model_providers"]["fixture-proxy"]["auth"]\n'
                        'result=subprocess.run([auth["command"],*auth["args"]],capture_output=True,text=True,check=True)\n'
                        'assert result.stdout.strip() == "fixture-helper-token"\n'
                        'print(json.dumps({"type":"thread.started","thread_id":"fixture-command-auth"}))\n'
                        'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}))\n')
        receipt = CodexRunner(self.binary, self.account, environment={'HOME': str(self.root)}).run(
            self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertTrue((self.root / 'helper-called').is_file())
        self.assertEqual((self.account / 'config.toml').read_text(), definition)
        self.assertFalse((self.run_root / 'codex-home').exists())
        for path in self.run_root.rglob('*'):
            if path.is_file():
                self.assertNotIn('fixture-helper-token', path.read_text())

    def test_command_auth_rejects_competing_auth_sources_before_launch(self):
        self.executable('raise AssertionError("must not run")\n')
        for conflict in ('env_key="FIXTURE_TOKEN"', 'requires_openai_auth=true'):
            with self.subTest(conflict=conflict):
                (self.account / 'config.toml').write_text(
                    'model_provider="fixture-proxy"\n[model_providers.fixture-proxy]\n'
                    'name="Fixture"\n' + conflict + '\n'
                    'auth={command=' + json.dumps(str(self.binary)) + '}\n')
                with self.assertRaisesRegex(ValueError, 'cannot be combined'):
                    CodexRunner(self.binary, self.account).run(
                        self.request, self.workspace, self.run_root, lambda pid: self.fail('worker started'))
                self.assertFalse((self.run_root / 'codex-home').exists())

    def test_command_auth_helper_is_rechecked_before_spawn(self):
        helper = self.root / 'fixture-helper'
        helper.write_text('#!/bin/sh\nexit 0\n')
        helper.chmod(0o700)
        (self.account / 'config.toml').write_text(
            'model_provider="fixture-proxy"\n[model_providers.fixture-proxy]\n'
            'name="Fixture"\nauth={command=' + json.dumps(str(helper)) + '}\n')
        self.executable('raise AssertionError("must not run")\n')

        def change_helper(*args):
            helper.chmod(0o777)
            return command(*args)

        with mock.patch('orchestrator.execution.command', side_effect=change_helper):
            with self.assertRaisesRegex(SecurityError, 'helper changed before'):
                CodexRunner(self.binary, self.account, environment={'HOME': str(self.root)}).run(
                    self.request, self.workspace, self.run_root, lambda pid: self.fail('worker started'))
        self.assertFalse((self.run_root / 'codex-home').exists())
        self.assertFalse((self.run_root / 'receipt.json').exists())

    def test_missing_custom_provider_never_substitutes_openai(self):
        (self.account / 'config.toml').write_text('model_provider="missing-custom"\n')
        with self.assertRaisesRegex(ValueError, 'definition is missing'):
            account_settings(self.account, self.settings, {})

    def test_only_required_provider_environment_is_preserved(self):
        (self.account / 'config.toml').write_text('model_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nenv_key="FIXTURE_TOKEN"\nenv_http_headers={Context="FIXTURE_CONTEXT"}\n')
        _, _, environment, _ = account_settings(self.account, self.settings,
                                               {'FIXTURE_TOKEN': 'fake', 'FIXTURE_CONTEXT': 'fake-context', 'OTHER_SECRET': 'exclude'})
        self.assertEqual(environment, {'FIXTURE_TOKEN': 'fake', 'FIXTURE_CONTEXT': 'fake-context'})
        _, _, optional_absent, _ = account_settings(self.account, self.settings, {'FIXTURE_TOKEN': 'fake'})
        self.assertEqual(optional_absent, {'FIXTURE_TOKEN': 'fake'})
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            account_settings(self.account, self.settings, {})

    def test_unsafe_cli_and_symlinked_account_are_rejected(self):
        self.executable('raise AssertionError("must not run")\n')
        self.binary.chmod(0o777)
        with self.assertRaisesRegex(SecurityError, 'trust'):
            CodexRunner(self.binary, self.account).run(self.request, self.workspace, self.run_root, lambda pid: None)
        link = self.root / 'account-link'
        link.symlink_to(self.account, target_is_directory=True)
        with self.assertRaises(SecurityError):
            account_settings(link, self.settings, {})

    def test_project_and_ancestor_config_cannot_reenable_external_tools(self):
        self.executable('raise AssertionError("must not run")\n')
        for directory in (self.workspace / '.codex', self.root / '.codex'):
            with self.subTest(directory=directory):
                directory.mkdir(mode=0o700)
                config = directory / 'config.toml'
                config.write_text('[agents]\nenabled=true\n[apps.dangerous]\nenabled=true\n[mcp_servers.writer]\ncommand="must-not-run"\n')
                with self.assertRaisesRegex(SecurityError, 'could override'):
                    CodexRunner(self.binary, self.account).run(self.request, self.workspace, self.run_root, lambda pid: self.fail('worker started'))
                self.assertFalse((self.run_root / 'codex-home').exists())
                config.unlink()
                directory.rmdir()

    def test_file_credentials_and_raw_stderr_are_removed_on_failure(self):
        (self.account / 'config.toml').write_text('model_provider="openai"\n')
        auth = self.account / 'auth.json'
        auth.write_text('{"token":"fixture-sensitive-token"}')
        auth.chmod(0o600)
        self.executable('import json,sys\nprint("fixture-sensitive-token",file=sys.stderr)\n'
                        'print(json.dumps({"type":"thread.started","thread_id":"fixture"}))\n'
                        'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"Report fixture-sensitive-token"}}))\n'
                        'sys.exit(1)\n')
        receipt = CodexRunner(self.binary, self.account).run(self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(receipt['outcome'], 'failed')
        self.assertFalse((self.run_root / 'codex-home').exists())
        self.assertEqual(auth.read_text(), '{"token":"fixture-sensitive-token"}')
        for path in self.run_root.rglob('*'):
            if path.is_file():
                self.assertNotIn('fixture-sensitive-token', path.read_text())
        self.assertIn('[redacted credential]', (self.run_root / 'handoff.txt').read_text())

    def test_timeout_has_terminal_receipt_and_unknown_usage(self):
        self.executable('import time\ntime.sleep(30)\n')
        result = CodexRunner(self.binary, self.account, timeout=1).run(self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(result['outcome'], 'timeout')
        self.assertIsNone(result['usage'])
        self.assertLess(result['elapsed_seconds'], 5)
        self.assertFalse((self.run_root / 'codex-home').exists())

    def test_opt_in_redacted_diagnostics_survive_protocol_error(self):
        self.executable('import json,sys\nprint(json.dumps({"type":"thread.started","thread_id":"fixture"}))\n'
                        'print("not-json")\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":7,"cached_input_tokens":0,"output_tokens":2}}))\n'
                        'print("Bearer fixture-token",file=sys.stderr)\n')
        result = CodexRunner(self.binary, self.account, retain_diagnostics=True).run(
            self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(result['outcome'], 'protocol_error')
        self.assertIsNone(result['usage'])
        self.assertEqual(result['usage_observations'][0]['usage']['input_tokens'], 7)
        self.assertNotIn('fixture-token', (self.run_root / 'stderr.redacted.txt').read_text())
        for line in (self.run_root / 'events.redacted.jsonl').read_text().splitlines():
            json.loads(line)
        self.assertGreaterEqual(result['elapsed_seconds'], result['execution_seconds'])
        self.assertGreater(result['preparation_seconds'], 0)

    def test_deadline_includes_launch_preparation(self):
        import time
        class SlowPreparation(CodexRunner):
            def prepare_configuration(self, configuration, worker_home, request):
                time.sleep(1.05)
                return configuration
        self.executable('raise AssertionError("must not start")\n')
        with self.assertRaises(TimeoutError):
            SlowPreparation(self.binary, self.account, timeout=1).run(
                self.request, self.workspace, self.run_root, lambda pid: self.fail('spawned after deadline'))
        self.assertFalse((self.run_root / 'codex-home').exists())

    def test_timeout_kills_descendant_in_same_process_group(self):
        import time
        marker = self.workspace / 'descendant-finished'
        child = 'import time;time.sleep(2);open(' + repr(str(marker)) + ',"w").write("orphan")'
        self.executable('import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",' + repr(child) + '])\ntime.sleep(30)\n')
        result = CodexRunner(self.binary, self.account, timeout=1, retain_diagnostics=True).run(
            self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(result['outcome'], 'timeout')
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_excessive_output_is_bounded_and_not_success(self):
        self.executable('import sys\nsys.stdout.write("x" * (2*1024*1024))\nsys.stdout.flush()\n')
        result = CodexRunner(self.binary, self.account, timeout=5).run(self.request, self.workspace, self.run_root, lambda pid: None)
        self.assertEqual(result['outcome'], 'protocol_error')
        self.assertFalse((self.run_root / 'events.jsonl').exists())
        self.assertFalse((self.run_root / 'codex-home').exists())


if __name__ == '__main__':
    unittest.main()
