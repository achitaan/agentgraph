import concurrent.futures
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from orchestrator import runtime
from orchestrator.controlplane.security import SecurityError, atomic_private_write, platform_mutation_supported
from orchestrator.controlplane.plans import Operation, approval_digest, create_plan, load_plan
from orchestrator.policy import load_policy


def proposal(identifier='launch-1', packet='task-1', role='implementer', preset='standard'):
    return {'schema_version': 1, 'id': identifier, 'packet': packet,
            'requester': {'role': 'coordinator', 'depth': 0}, 'role': role, 'preset': preset,
            'objective': 'Fix the parser for an empty input',
            'scope': {'paths': ['parser.py'], 'bounded': True, 'consequence': 'low', 'uncertainty': 'low'},
            'acceptance_check': 'Run the existing empty-input parser test',
            'evidence': [{'fingerprint': 'a' * 64, 'summary': 'Inspected parser and failing assertion'}]}


class FakeRunner:
    def __init__(self, outcome='completed', usage=True, interrupted=False, details=None):
        self.outcome, self.usage, self.interrupted = outcome, usage, interrupted
        self.calls = 0
        self.details = details

    def run(self, request, workspace, run_root, started):
        self.calls += 1
        started(os.getpid())
        receipt = {'request_hash': request['request_hash'], 'outcome': self.outcome,
                   'usage': {'input_tokens': 100, 'output_tokens': 30, 'cached_input_tokens': 80} if self.usage else None,
                   'thread_id': 'fake-worker'}
        if self.details is not None:
            receipt['usage_details'] = self.details
        atomic_private_write(run_root / 'receipt.json', runtime.encoded(receipt))
        if self.interrupted:
            raise RuntimeError('supervisor interrupted after durable completion')
        return receipt


@unittest.skipUnless(platform_mutation_supported()[0], 'controller mutation requires POSIX guarantees')
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='owned controller ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / 'work'
        self.workspace.mkdir(mode=0o700)
        self.state = self.root / 'state'
        self.owner = runtime.initialize(self.state, self.workspace, 'selected-model', 'high')['owner_token']

    def reserve(self, packet=None):
        packet = proposal() if packet is None else packet
        preview = runtime.preview(self.state, self.owner, packet)
        self.assertTrue(preview['allowed'])
        return runtime.reserve(self.state, self.owner, preview['token'], 'approve:' + preview['approval_digest'])

    def test_owner_capability_is_not_persisted_and_cannot_be_reset(self):
        self.assertNotIn(self.owner, (self.state / 'session.json').read_text())
        self.assertNotIn('owner_digest', runtime.status(self.state))
        with self.assertRaisesRegex(SecurityError, 'ownership'):
            runtime.preview(self.state, 'b' * 64, proposal())
        with self.assertRaisesRegex(SecurityError, 'already exists'):
            runtime.initialize(self.state, self.workspace, 'selected-model', 'high')

    def test_state_cannot_be_in_workspace_or_symlinked(self):
        with self.assertRaisesRegex(SecurityError, 'outside'):
            runtime.initialize(self.workspace / 'state', self.workspace, 'selected-model', 'high')
        link = self.root / 'link'
        link.symlink_to(self.workspace, target_is_directory=True)
        with self.assertRaises(SecurityError):
            runtime.initialize(self.root / 'other-state', link, 'selected-model', 'high')

    def test_preview_preserves_selected_model_and_does_not_reserve(self):
        before = (self.state / 'session.json').read_bytes()
        preview = runtime.preview(self.state, self.owner, proposal(), [{'name': 'code-assurance', 'text': 'Inspect the failing contract.'}])
        self.assertEqual(preview['model'], 'selected-model')
        self.assertEqual(preview['selected_skills'], ['code-assurance'])
        self.assertEqual((self.state / 'session.json').read_bytes(), before)
        self.assertEqual(runtime.status(self.state)['calls'], [])

    def test_preview_measures_full_packet_and_rejects_oversized_evidence(self):
        measured = runtime.preview(self.state, self.owner, proposal())
        self.assertGreater(measured['context_budget']['prompt_characters'], 100)
        self.assertIsNone(measured['context_budget']['token_count'])
        packet = proposal()
        packet['evidence'] = [{'fingerprint': format(i, '064x'), 'summary': 'x' * 1000} for i in range(16)]
        before = (self.state / 'session.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'prompt exceeds'):
            runtime.preview(self.state, self.owner, packet, [{'name': 'large', 'text': 'x' * 16000}])
        self.assertEqual((self.state / 'session.json').read_bytes(), before)

    def test_opt_in_parallelism_blocks_conflicts_until_acceptance(self):
        policy = load_policy()
        policy['limits']['max_active_workers'] = 2
        state = self.root / 'parallel-state'
        owner = runtime.initialize(state, self.workspace, 'selected-model', 'high', policy)['owner_token']
        first = proposal()
        first['scope']['resources'] = ['test-db']
        preview = runtime.preview(state, owner, first)
        runtime.reserve(state, owner, preview['token'], 'approve:' + preview['approval_digest'])
        runtime.execute_reserved(state, owner, first['id'], FakeRunner())
        second = proposal('launch-2', 'task-2')
        conflict = runtime.preview(state, owner, second)
        self.assertIn('active_scope_conflict', conflict['stop_reasons'])
        second['scope']['paths'] = ['different.py']
        self.assertTrue(runtime.preview(state, owner, second)['allowed'])
        second['scope']['resources'] = ['test-db']
        self.assertIn('active_scope_conflict', runtime.preview(state, owner, second)['stop_reasons'])
        runtime.finish(state, owner, first['id'], True, 'Verified the fixture result.')
        second['depends_on'] = ['task-1']
        self.assertTrue(runtime.preview(state, owner, second)['allowed'])

    def test_approval_and_owner_are_required_without_consuming_good_preview(self):
        preview = runtime.preview(self.state, self.owner, proposal())
        with self.assertRaises(ValueError):
            runtime.reserve(self.state, self.owner, preview['token'], 'approve:wrong')
        with self.assertRaises(SecurityError):
            runtime.reserve(self.state, 'b' * 64, preview['token'], 'approve:' + preview['approval_digest'])
        runtime.reserve(self.state, self.owner, preview['token'], 'approve:' + preview['approval_digest'])
        with self.assertRaises(ValueError):
            runtime.reserve(self.state, self.owner, preview['token'], 'approve:' + preview['approval_digest'])

    def test_expired_preview_does_not_reserve(self):
        preview = runtime.preview(self.state, self.owner, proposal(), now=100)
        with self.assertRaisesRegex(ValueError, 'expired'):
            runtime.reserve(self.state, self.owner, preview['token'], 'approve:' + preview['approval_digest'], now=701)
        self.assertEqual(runtime.status(self.state)['calls'], [])

    def test_concurrent_previews_can_reserve_only_one_worker(self):
        previews = [runtime.preview(self.state, self.owner, proposal('launch-' + str(i), 'packet-' + str(i))) for i in range(2)]
        def apply(item):
            try:
                runtime.reserve(self.state, self.owner, item['token'], 'approve:' + item['approval_digest'])
                return True
            except SecurityError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(apply, previews))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(len(runtime.status(self.state)['calls']), 1)

    def test_worker_completion_requires_coordinator_acceptance(self):
        self.reserve()
        runner = FakeRunner()
        runtime.execute_reserved(self.state, self.owner, 'launch-1', runner)
        self.assertEqual(runtime.status(self.state)['launches']['launch-1']['phase'], 'awaiting_acceptance')
        blocked = runtime.preview(self.state, self.owner, proposal('launch-2', 'task-2'))
        self.assertFalse(blocked['allowed'])
        self.assertIn('active_worker_limit_reached', blocked['stop_reasons'])
        with self.assertRaises(SecurityError):
            runtime.execute_reserved(self.state, self.owner, 'launch-1', runner)
        self.assertEqual(runner.calls, 1)
        result = runtime.finish(self.state, self.owner, 'launch-1', True, 'Coordinator ran the parser regression; passed.')
        self.assertEqual(result['usage']['input_tokens'], 100)
        self.assertEqual(runtime.status(self.state)['calls'][0]['status'], 'completed')
        self.assertTrue(runtime.preview(self.state, self.owner, proposal('launch-2', 'task-2'))['allowed'])

    def test_unknown_usage_blocks_a_token_budget_after_review(self):
        other = self.root / 'budget-state'
        policy = load_policy()
        policy['limits']['max_tokens'] = 1000
        owner = runtime.initialize(other, self.workspace, 'selected-model', 'high', policy)['owner_token']
        preview = runtime.preview(other, owner, proposal())
        runtime.reserve(other, owner, preview['token'], 'approve:' + preview['approval_digest'])
        runtime.execute_reserved(other, owner, 'launch-1', FakeRunner(usage=False))
        runtime.finish(other, owner, 'launch-1', False, 'Missing usage; retained as unknown.')
        next_proposal = proposal('launch-2', 'task-2')
        decision = runtime.preview(other, owner, next_proposal)
        self.assertIn('token_usage_unknown', decision['stop_reasons'])

    def test_inconsistent_extended_receipt_cannot_accept_valid_base_usage(self):
        self.reserve()
        receipt = runtime.execute_reserved(self.state, self.owner, 'launch-1',
                                           FakeRunner(details={'cache_write_input_tokens': 21}))
        self.assertIsNotNone(receipt['usage'])
        with self.assertRaisesRegex(ValueError, 'token subset'):
            runtime.finish(self.state, self.owner, 'launch-1', True, 'Source check passed.')
        status = runtime.status(self.state)
        self.assertEqual(status['launches']['launch-1']['phase'], 'awaiting_acceptance')
        self.assertEqual(status['launches']['launch-1']['receipt'], receipt)
        self.assertIsNone(status['calls'][0]['usage'])

    def test_valid_extended_receipt_does_not_inflate_base_ledger_totals(self):
        self.reserve()
        runtime.execute_reserved(self.state, self.owner, 'launch-1',
                                 FakeRunner(details={'cache_write_input_tokens': 20, 'reasoning_output_tokens': 30}))
        runtime.finish(self.state, self.owner, 'launch-1', True, 'Source check passed.')
        self.assertEqual(runtime.status(self.state)['calls'][0]['usage'],
                         {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 30})

    def test_failure_retry_requires_new_evidence_and_counts_all_attempts(self):
        packet = proposal()
        for attempt in range(3):
            packet['id'] = 'launch-' + str(attempt)
            packet['evidence'] = [{'fingerprint': str(attempt + 1) * 64, 'summary': 'New measured failure evidence'}]
            self.reserve(packet)
            runtime.execute_reserved(self.state, self.owner, packet['id'], FakeRunner(outcome='failed'))
            with self.assertRaises(SecurityError):
                runtime.finish(self.state, self.owner, packet['id'], True, 'Unjustified worker success')
            runtime.finish(self.state, self.owner, packet['id'], False, 'Regression still fails.')
            retry = deepcopy(packet)
            retry['id'] = 'another-id'
            decision = runtime.preview(self.state, self.owner, retry)
            self.assertIn('retry_requires_new_evidence', decision['stop_reasons'])
        packet['id'], packet['evidence'] = 'launch-final', [{'fingerprint': 'f' * 64, 'summary': 'More evidence'}]
        self.assertIn('packet_attempt_limit_reached', runtime.preview(self.state, self.owner, packet)['stop_reasons'])
        self.assertEqual(len(runtime.status(self.state)['calls']), 3)

    def test_interrupted_execution_requires_receipt_recovery_never_relaunches(self):
        self.reserve()
        runner = FakeRunner(interrupted=True)
        with self.assertRaises(RuntimeError):
            runtime.execute_reserved(self.state, self.owner, 'launch-1', runner)
        with self.assertRaises(SecurityError):
            runtime.execute_reserved(self.state, self.owner, 'launch-1', runner)
        self.assertEqual(runner.calls, 1)
        self.assertFalse(runtime.reconcile(self.state, self.owner, 'launch-1')['relaunched'])
        runtime.finish(self.state, self.owner, 'launch-1', True, 'Recovered receipt and independently checked the parser.')

    def test_missing_or_foreign_recovery_receipt_fails_closed(self):
        self.reserve()
        class Interrupted:
            def run(_self, request, workspace, run_root, started):
                started(os.getpid())
                raise RuntimeError('No terminal receipt')
        with self.assertRaises(RuntimeError):
            runtime.execute_reserved(self.state, self.owner, 'launch-1', Interrupted())
        with self.assertRaises(FileNotFoundError):
            runtime.reconcile(self.state, self.owner, 'launch-1')
        atomic_private_write(self.state / 'run-launch-1/receipt.json', runtime.encoded({'request_hash': 'foreign', 'outcome': 'completed'}))
        with self.assertRaises(SecurityError):
            runtime.reconcile(self.state, self.owner, 'launch-1')
        self.assertEqual(runtime.status(self.state)['calls'][0]['status'], 'running')

    def test_workers_cannot_claim_dispatch_authority_or_expand_skill_context(self):
        packet = proposal()
        packet['requester']['role'] = 'implementer'
        self.assertIn('requester_not_coordinator', runtime.preview(self.state, self.owner, packet)['stop_reasons'])
        with self.assertRaisesRegex(ValueError, 'three'):
            runtime.preview(self.state, self.owner, proposal(), [{'name': str(i), 'text': 'guide'} for i in range(4)])
        with self.assertRaisesRegex(ValueError, 'context budget'):
            runtime.preview(self.state, self.owner, proposal(), [{'name': 'large', 'text': 'x' * 16001}])

    def test_explicit_fast_preset_and_read_only_roles(self):
        fast = runtime.preview(self.state, self.owner, proposal(preset='fast'))
        self.assertEqual(fast['model'], 'gpt-6-luna')
        for role in ('investigator', 'reviewer'):
            request = runtime.preview(self.state, self.owner, proposal(role=role))
            self.assertEqual(request['sandbox'], 'read-only')
            self.assertEqual(request['model'], 'selected-model')

    def test_preset_effort_is_validated_without_silent_downgrade(self):
        other = self.root / 'ultra-state'
        owner = runtime.initialize(other, self.workspace, 'selected-model', 'ultra')['owner_token']
        with self.assertRaisesRegex(ValueError, 'compatible pair'):
            runtime.preview(other, owner, proposal(preset='fast'))
        requested = runtime.preview(other, owner, proposal(preset='fast'), worker_effort='high')
        self.assertEqual(requested['effort'], 'high')
        self.assertEqual(runtime.status(other)['effort'], 'ultra')
        standard = runtime.preview(other, owner, proposal())
        self.assertEqual(standard['effort'], 'ultra')

    def test_recovery_rejects_path_traversal_before_creating_any_lock(self):
        before = sorted(self.root.rglob('*'))
        with self.assertRaises(ValueError):
            runtime.reconcile(self.state, self.owner, '../../foreign')
        self.assertEqual(sorted(self.root.rglob('*')), before)

    def test_reservation_reconstructs_every_field_from_trusted_state(self):
        mutations = (
            lambda doc: doc['launches']['launch-1']['settings'].update(sandbox='workspace-write'),
            lambda doc: doc.update(model='unrequested-model'),
            lambda doc: doc.update(owner_digest='b' * 64),
            lambda doc: doc['policy']['limits'].update(max_calls=999),
            lambda doc: doc['workspace'].update(inode=0),
            lambda doc: doc['launches']['launch-1'].update(phase='running'),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                preview = runtime.preview(self.state, self.owner, proposal(role='investigator'))
                plan = load_plan(preview['token'], 'dispatch', self.state / 'plans')
                original = plan.operations[0]
                after = json.loads(base64.b64decode(original.content_b64))
                mutate(after)
                content = runtime.encoded(after)
                operation = Operation(original.path, original.before_sha256, runtime.digest(content), base64.b64encode(content).decode())
                token = create_plan('dispatch', [operation], self.state / 'plans')
                altered = load_plan(token, 'dispatch', self.state / 'plans')
                with self.assertRaisesRegex(SecurityError, 'reservation is invalid'):
                    runtime.reserve(self.state, self.owner, token, 'approve:' + approval_digest(altered))
                self.assertEqual(runtime.status(self.state)['calls'], [])


if __name__ == '__main__':
    unittest.main()
