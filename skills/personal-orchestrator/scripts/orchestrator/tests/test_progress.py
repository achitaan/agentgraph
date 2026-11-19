import concurrent.futures
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orchestrator import progress
from orchestrator.cli import main, parser
from orchestrator.controlplane.security import SecurityError, platform_mutation_supported


@unittest.skipUnless(platform_mutation_supported()[0], 'private progress mutation requires POSIX guarantees')
class ProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='chat progress ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(mode=0o700)
        self.artifact = self.workspace / 'result.py'
        self.artifact.write_text('value = 1\n')
        self.state = self.root / 'state'
        self.thread = 'chat-1'
        self.clock = 1000
        self.initialize()

    def initialize(self, **kwargs):
        return progress.initialize(self.state, self.workspace, self.thread, 'Deliver checked output',
                                   'Engineer', ['result.py'], now=self.clock, **kwargs)

    def tick(self):
        self.clock += 1
        return self.clock

    def begin(self, identifier, phase='work'):
        return progress.begin(self.state, self.thread, identifier, phase, 'Test a bounded hypothesis', now=self.tick())

    def record(self, identifier, outcome='pass', **kwargs):
        evidence = self.root / (identifier + '.txt')
        if not evidence.exists():
            evidence.write_text('Saved observations for ' + identifier)
        return progress.record(self.state, self.thread, identifier, outcome, evidence,
                               'Observed ' + outcome, now=self.tick(), **kwargs)

    def step(self, identifier, phase, outcome='pass', **kwargs):
        self.begin(identifier, phase)
        return self.record(identifier, outcome, **kwargs)

    def status(self):
        return progress.status(self.state, self.thread, now=self.clock)

    def transition(self, action):
        return progress.transition(self.state, self.thread, action, 'Requested ' + action, now=self.tick())

    def ready(self):
        self.step('check-1', 'check')
        self.step('review-1', 'review')

    def test_complete_self_review_is_explicit_and_private(self):
        self.step('work-1', 'work')
        self.ready()
        result = self.transition('complete')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['review_mode'], 'self')
        self.assertEqual(result['passes_used'], 1)
        self.assertTrue(result['checks_current'] and result['review_current'])
        self.assertIsNone(result['cost_usd'])
        self.assertIsNone(result['model_usage'])
        self.assertFalse(result['launches_models'] or result['controls_native_goal'])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        for filename in (progress.LEDGER, progress.VIEW):
            self.assertEqual((self.state / filename).stat().st_mode & 0o777, 0o600)
        self.assertIn('(self)', (self.state / progress.VIEW).read_text())

    def test_independent_review_rejects_owner_and_recorded_contributors(self):
        self.state = self.root / 'independent'
        self.initialize(review_mode='independent')
        self.step('research-1', 'research', actor='researcher-1')
        self.step('work-1', 'work', actor='worker-1')
        self.step('check-1', 'check')
        self.begin('review-1', 'review')
        for actor in (self.thread, 'worker-1', 'researcher-1'):
            with self.assertRaisesRegex(ValueError, 'separate reviewer'):
                self.record('review-1', actor=actor)
        self.record('review-1', actor='reviewer-1')
        self.assertEqual(self.transition('complete')['status'], 'complete')

    def test_missing_or_failed_checks_block_review_and_completion(self):
        with self.assertRaisesRegex(ValueError, 'passing checks'):
            self.begin('review-1', 'review')
        self.step('check-1', 'check', 'fail')
        with self.assertRaisesRegex(ValueError, 'passing checks'):
            self.begin('review-2', 'review')
        with self.assertRaisesRegex(ValueError, 'passing checks and review'):
            self.transition('complete')

    def test_review_failure_blocks_completion(self):
        self.step('check-1', 'check')
        self.step('review-1', 'review', 'fail')
        with self.assertRaisesRegex(ValueError, 'passing checks and review'):
            self.transition('complete')

    def test_stale_source_requires_new_checks_and_review(self):
        self.ready()
        self.artifact.write_text('value = 2\n')
        self.assertFalse(self.status()['checks_current'])
        self.assertFalse(self.status()['review_current'])
        with self.assertRaises(ValueError):
            self.transition('complete')
        self.step('check-2', 'check')
        self.assertFalse(self.status()['review_current'])
        self.step('review-2', 'review')
        self.transition('complete')

    def test_later_check_invalidates_previous_review_even_without_source_changes(self):
        self.ready()
        self.step('check-2', 'check')
        self.assertFalse(self.status()['review_current'])
        with self.assertRaises(ValueError):
            self.transition('complete')

    def test_changed_evidence_invalidates_approval(self):
        self.ready()
        (self.root / 'review-1.txt').write_text('different findings')
        self.assertFalse(self.status()['review_current'])
        with self.assertRaises(ValueError):
            self.transition('complete')
        (self.root / 'check-1.txt').unlink()
        self.assertFalse(self.status()['checks_current'])

    def test_check_evidence_change_during_review_prevents_approval(self):
        self.step('check-1', 'check')
        self.begin('review-1', 'review')
        (self.root / 'check-1.txt').write_text('changed check evidence')
        with self.assertRaisesRegex(ValueError, 'cannot waive'):
            self.record('review-1')

    def test_source_change_during_check_or_review_cannot_pass(self):
        self.begin('check-1', 'check')
        self.artifact.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'artifacts changed'):
            self.record('check-1')
        self.record('check-1', 'interrupted')
        self.step('check-2', 'check')
        self.begin('review-1', 'review')
        self.artifact.write_text('changed again')
        with self.assertRaisesRegex(ValueError, 'artifacts changed'):
            self.record('review-1')

    def test_missing_artifact_can_be_created_but_not_approved_missing(self):
        self.artifact.unlink()
        self.begin('check-1', 'check')
        with self.assertRaises(ValueError):
            self.record('check-1')
        self.record('check-1', 'fail')
        self.artifact.write_text('created')
        self.step('check-2', 'check')
        self.step('review-1', 'review')

    def test_pass_is_consumed_at_begin_and_interruption_never_refunds_it(self):
        self.state = self.root / 'one-pass'
        self.initialize(max_passes=1)
        self.assertEqual(self.begin('work-1')['passes_remaining'], 0)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.begin('work-2')
        self.record('work-1', 'interrupted')
        for phase in ('work', 'research'):
            with self.assertRaisesRegex(ValueError, 'pass limit'):
                self.begin('work-2', phase)
        self.ready()
        self.transition('complete')

    def test_pause_resume_keeps_deadline_passes_and_active_step(self):
        self.begin('work-1')
        before = json.loads((self.state / progress.LEDGER).read_text())
        self.transition('pause')
        with self.assertRaises(ValueError):
            self.begin('work-2')
        self.transition('resume')
        after = json.loads((self.state / progress.LEDGER).read_text())
        self.assertEqual(before['deadline'], after['deadline'])
        self.assertEqual(self.status()['passes_used'], 1)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.begin('work-2')
        self.record('work-1', 'interrupted')

    def test_deadline_retains_late_result_but_does_not_admit_more_work(self):
        self.begin('work-1')
        self.clock = 2200
        self.record('work-1')
        self.assertTrue(self.status()['deadline_exceeded'])
        with self.assertRaises(ValueError):
            self.begin('check-1', 'check')
        with self.assertRaises(ValueError):
            self.transition('complete')
        self.transition('stop')

    def test_pause_does_not_extend_deadline(self):
        self.transition('pause')
        self.clock = 2200
        with self.assertRaisesRegex(ValueError, 'original deadline'):
            self.transition('resume')

    def test_backwards_clock_refused(self):
        self.begin('work-1')
        with self.assertRaisesRegex(ValueError, 'backwards'):
            progress.status(self.state, self.thread, now=999)

    def test_terminal_runs_cannot_restart_and_late_observation_is_retained(self):
        self.begin('work-1')
        self.transition('stop')
        self.record('work-1', 'interrupted')
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.transition('resume')
        with self.assertRaises(ValueError):
            self.begin('work-2')

    def test_completion_stops_extra_work(self):
        self.ready()
        for phase in ('work', 'research'):
            with self.assertRaisesRegex(ValueError, 'complete instead'):
                self.begin('extra', phase)
        self.transition('complete')
        with self.assertRaises(ValueError):
            self.begin('extra')

    def test_completed_view_marks_subsequent_source_changes(self):
        self.ready()
        self.transition('complete')
        self.artifact.write_text('new task changes')
        self.assertEqual(self.status()['status'], 'complete')
        self.assertIn('historical completion', (self.state / progress.VIEW).read_text())

    def test_thread_binding_and_reinitialization(self):
        with self.assertRaisesRegex(SecurityError, 'another chat'):
            progress.status(self.state, 'different-chat', now=self.clock)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.initialize()

    def test_idempotent_record_and_duplicate_ids(self):
        self.step('work-1', 'work')
        before = (self.state / progress.LEDGER).read_bytes()
        self.record('work-1')
        self.assertEqual(before, (self.state / progress.LEDGER).read_bytes())
        with self.assertRaisesRegex(ValueError, 'conflicting result'):
            self.record('work-1', 'fail')
        with self.assertRaisesRegex(ValueError, 'identifier already used'):
            self.begin('work-1')

    def test_concurrent_begins_allow_only_one_active_step(self):
        def attempt(identifier):
            try:
                progress.begin(self.state, self.thread, identifier, 'work', 'Bounded work', now=1001)
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ['first', 'second']))
        self.clock = 1001
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.status()['passes_used'], 1)

    def test_invalid_artifact_scope(self):
        for name in ('../escape', '/absolute', '.git/config', 'a/../b', 'a\\b', './x'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                progress.initialize(self.root / 'invalid', self.workspace, self.thread,
                                    'Task', 'Engineer', [name], now=self.clock)

    def test_symlinked_artifact_evidence_and_state_rejected(self):
        target = self.root / 'target'
        target.write_text('real data')
        self.artifact.unlink()
        self.artifact.symlink_to(target)
        with self.assertRaises(SecurityError):
            self.status()
        self.artifact.unlink()
        self.artifact.write_text('safe')
        self.begin('work-1')
        (self.root / 'work-1.txt').symlink_to(target)
        with self.assertRaises(SecurityError):
            self.record('work-1')
        link = self.root / 'state-link'
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(SecurityError):
            progress.status(link, self.thread, now=self.clock)

    def test_empty_or_missing_evidence_cannot_be_recorded(self):
        self.begin('work-1')
        (self.root / 'work-1.txt').write_text('')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.record('work-1')
        with self.assertRaises(OSError):
            progress.record(self.state, self.thread, 'work-1', 'pass', self.root / 'missing',
                            'Missing evidence', now=self.clock)

    def test_corrupt_state_is_never_overwritten(self):
        original = (self.state / progress.LEDGER).read_bytes()
        for raw in (b'{', b'null', b'{"schema_version":100}'):
            (self.state / progress.LEDGER).write_bytes(raw)
            with self.assertRaises(ValueError):
                self.status()
            self.assertEqual((self.state / progress.LEDGER).read_bytes(), raw)
        (self.state / progress.LEDGER).write_bytes(original)
        document = json.loads(original)
        document['steps'] = [42]
        (self.state / progress.LEDGER).write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            self.status()

    def test_unsafe_permissions_rejected(self):
        (self.state / progress.LEDGER).chmod(0o644)
        with self.assertRaises(SecurityError):
            self.status()

    def test_failed_view_write_retains_authoritative_ledger(self):
        write = progress._write_at_locked

        def fail_view(fd, filename, data):
            if filename == progress.VIEW:
                raise OSError('simulated display failure')
            return write(fd, filename, data)

        with patch.object(progress, '_write_at_locked', side_effect=fail_view):
            result = self.begin('work-1')
        self.assertIn('view_warning', result)
        self.assertEqual(self.status()['active_step']['id'], 'work-1')
        with self.assertRaises(ValueError):
            self.begin('work-2')

    def test_snapshot_failure_does_not_commit_partial_transition(self):
        before = (self.state / progress.LEDGER).read_bytes()
        with patch.object(progress, '_snapshot', side_effect=OSError('unreadable source')):
            with self.assertRaises(OSError):
                self.transition('pause')
        self.assertEqual((self.state / progress.LEDGER).read_bytes(), before)

    def test_status_does_not_rewrite_ledger(self):
        before = (self.state / progress.LEDGER).read_bytes()
        (self.state / progress.VIEW).unlink()
        self.status()
        self.assertEqual((self.state / progress.LEDGER).read_bytes(), before)
        self.assertTrue((self.state / progress.VIEW).exists())

    def test_compact_cli_keeps_ledger_and_full_status_compatible(self):
        self.begin('work-1')
        before = (self.state / progress.LEDGER).read_bytes()
        def invoke(*extra):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(progress.time, 'time', return_value=self.clock):
                code = main(['progress', 'status', '--state', str(self.state), '--thread', self.thread, *extra])
            self.assertEqual(code, 0)
            return output.getvalue(), json.loads(output.getvalue())
        full_text, full = invoke()
        compact_text, compact = invoke('--compact')
        self.assertEqual(compact, progress.compact_summary(full))
        self.assertEqual(compact['active_step'], {'id': 'work-1', 'phase': 'work'})
        self.assertNotIn('snapshot', compact['active_step'])
        self.assertIn('snapshot', full['active_step'])
        self.assertLess(len(compact_text), len(full_text) / 2)
        self.assertEqual((self.state / progress.LEDGER).read_bytes(), before)
        self.assertIn('work-1', (self.state / progress.VIEW).read_text())

    def test_compact_keeps_view_warning_and_completion_freshness(self):
        self.ready()
        result = self.transition('complete')
        compact = progress.compact_summary(result | {'view_warning': 'Display unavailable'})
        self.assertEqual(compact['view_warning'], 'Display unavailable')
        self.assertEqual(compact['status'], 'complete')
        self.assertTrue(compact['review_current'])
        self.assertIsNone(compact['model_usage'])
        self.artifact.write_text('later change')
        compact = progress.compact_summary(self.status())
        self.assertFalse(compact['checks_current'] or compact['review_current'])

    def test_compact_errors_keep_nonzero_exit_and_diagnostic(self):
        self.step('check-1', 'check', 'fail')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(progress.time, 'time', return_value=self.clock):
            code = main(['progress', 'begin', '--state', str(self.state), '--thread', self.thread,
                         '--id', 'review-1', '--phase', 'review', '--note', 'Must refuse', '--compact'])
        self.assertEqual(code, 2)
        self.assertIn('passing checks', json.loads(output.getvalue())['message'])
        self.assertIsNone(self.status()['active_step'])

    def test_progress_only_parser_matches_default_arguments(self):
        for action, extra in [('status', []), ('begin', ['--id', 'c', '--phase', 'check', '--note', 'Check'])]:
            args = ['progress', action, '--state', str(self.state), '--thread', self.thread, '--compact', *extra]
            self.assertEqual(vars(parser().parse_args(args)), vars(parser(progress_only=True).parse_args(args)))

    def test_compact_rounding_does_not_change_deadline_enforcement(self):
        self.clock = 2199.999
        compact = progress.compact_summary(self.status())
        self.assertEqual(compact['seconds_remaining'], 0)
        self.assertFalse(compact['deadline_exceeded'])
        self.clock = 2200
        self.assertTrue(progress.compact_summary(self.status())['deadline_exceeded'])
        with self.assertRaises(ValueError):
            self.begin('late')

    def test_progress_startup_does_not_import_dispatch_only_modules(self):
        code = ('import sys; from orchestrator.cli import parser; '
                'parser().parse_args(["progress", "status", "--state", "unused", "--thread", "chat", "--compact"]); '
                'assert not ({"orchestrator.execution", "orchestrator.controlplane.discovery", '
                '"orchestrator.controlplane.doctor", "orchestrator.controlplane.routing"} & sys.modules.keys())')
        result = subprocess.run([sys.executable, '-B', '-c', code],
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_copied_skill_cli_needs_no_model_account_or_installed_skill(self):
        source = Path(__file__).resolve().parents[3]
        copied = self.root / 'copied-skill'
        shutil.copytree(source, copied)
        script = copied / 'scripts/controller.py'
        environment = {'PATH': '', 'HOME': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1'}

        def invoke(*args):
            result = subprocess.run([sys.executable, '-B', str(script), *args], env=environment,
                                    cwd=self.root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        self.assertEqual(invoke('version')['name'], 'personal-orchestrator')
        common = ('--state', str(self.root / 'cli-state'), '--thread', self.thread)
        initialized = invoke('progress', 'init', *common, '--workspace', str(self.workspace),
                             '--objective', 'Local CLI check', '--profile', 'Engineer',
                             '--artifact', 'result.py', '--review-mode', 'self')
        self.assertFalse(initialized['launches_models'])
        self.assertEqual(invoke('progress', 'status', *common)['status'], 'active')
        self.assertEqual(invoke('progress', 'stop', *common, '--note', 'Check done')['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
