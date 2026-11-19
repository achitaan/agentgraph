import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from orchestrator.__main__ import main
from orchestrator.policy import assess_dispatch, load_json, load_policy, reserve_dispatch


def proposal(**changes):
    value = {
        'schema_version': 1, 'id': 'attempt-1', 'packet': 'parser-fix',
        'requester': {'role': 'coordinator', 'depth': 0}, 'role': 'implementer',
        'objective': 'Fix the reproduced parser failure.',
        'scope': {'paths': ['src/parser.py'], 'bounded': True, 'consequence': 'low', 'uncertainty': 'low'},
        'acceptance_check': 'python -m unittest tests.test_parser', 'evidence': [],
    }
    value.update(changes)
    return value


def ledger(*calls):
    return {'schema_version': 1, 'calls': list(calls)}


def evidence(character='a'):
    return {'fingerprint': character * 64, 'summary': 'A recorded regression result.'}


def call(identifier='prior-1', packet='prior-task', status='completed', usage=None, records=None):
    return {'id': identifier, 'packet': packet, 'agent': 'implementer', 'status': status,
            'usage': usage, 'preset': 'standard', 'evidence': records or []}


class PolicyTests(unittest.TestCase):
    def test_standard_roles_inherit_model_and_request_worker_restrictions(self):
        for role in ('implementer', 'investigator', 'reviewer'):
            with self.subTest(role=role):
                result = assess_dispatch(proposal(role=role), ledger())
                self.assertTrue(result['allowed'])
                self.assertEqual(result['dispatch_state'], 'not_dispatched')
                self.assertEqual(result['host_enforcement'], 'unverified')
                self.assertIsNone(result['session_request']['model'])
                self.assertIsNone(result['session_request']['reasoning_effort'])
                self.assertEqual(result['session_request']['subagent_tools'], 'disabled')
                expected = 'workspace-write' if role == 'implementer' else 'read-only'
                self.assertEqual(result['session_request']['sandbox_mode'], expected)

    def test_only_root_coordinator_can_propose_a_worker(self):
        for role in ('implementer', 'investigator', 'reviewer'):
            result = assess_dispatch(proposal(requester={'role': role, 'depth': 1}), ledger())
            self.assertFalse(result['allowed'])
            self.assertIn('requester_not_coordinator', result['stop_reasons'])
        result = assess_dispatch(proposal(requester={'role': 'coordinator', 'depth': 1}), ledger())
        self.assertIn('nested_dispatch_forbidden', result['stop_reasons'])
        result = assess_dispatch(proposal(role='coordinator'), ledger())
        self.assertIn('coordinator_cannot_be_worker', result['stop_reasons'])
        with self.assertRaises(ValueError):
            assess_dispatch(proposal(role='fast-worker'), ledger())

    def test_named_presets_are_explicit_and_role_scoped(self):
        fast = assess_dispatch(proposal(preset='fast'), ledger())
        self.assertEqual(fast['session_request']['model'], 'gpt-6-luna')
        astra = assess_dispatch(proposal(role='investigator', preset='astra'), ledger())
        self.assertEqual(astra['session_request']['model'], 'gpt-6-astra')
        for role, preset in (('reviewer', 'astra'), ('implementer', 'astra'), ('investigator', 'fast')):
            with self.subTest(role=role, preset=preset), self.assertRaises(ValueError):
                assess_dispatch(proposal(role=role, preset=preset), ledger())

    def test_fast_rejects_unbounded_scope_or_uncertain_consequences_even_for_ui(self):
        for field, value in (('bounded', False), ('consequence', 'high'), ('uncertainty', 'high'), ('uncertainty', 'medium')):
            request = proposal(preset='fast', objective='A small UI polish change')
            request['scope'][field] = value
            result = assess_dispatch(request, ledger())
            self.assertFalse(result['allowed'])
            self.assertIn('fast_eligibility_not_met', result['stop_reasons'])
        with self.assertRaises(ValueError):
            assess_dispatch(proposal(preset='fast', acceptance_check=' '), ledger())

    def test_pending_reservation_blocks_another_dispatch_without_mutating_input(self):
        request, state = proposal(), ledger()
        before = deepcopy((request, state))
        decision, reserved = reserve_dispatch(request, state)
        self.assertTrue(decision['allowed'])
        self.assertEqual((request, state), before)
        self.assertEqual(reserved['calls'][0]['status'], 'pending')
        denied, unchanged = reserve_dispatch(proposal(id='attempt-2', packet='another-task'), reserved)
        self.assertFalse(denied['allowed'])
        self.assertIn('pending_dispatch_unresolved', denied['stop_reasons'])
        self.assertEqual(unchanged, reserved)

    def test_running_worker_counts_against_active_limit(self):
        state = ledger(call(status='running'))
        self.assertIn('active_worker_limit_reached', assess_dispatch(proposal(), state)['stop_reasons'])
        policy = load_policy()
        policy['limits']['max_active_workers'] = 2
        self.assertTrue(assess_dispatch(proposal(), state, policy)['allowed'])
        pending = ledger(call(status='pending'))
        self.assertIn('pending_dispatch_unresolved', assess_dispatch(proposal(), pending, policy)['stop_reasons'])

    def test_failed_retry_requires_new_evidence_fingerprint(self):
        state = ledger(call(packet='parser-fix', status='failed', records=[evidence()]))
        for records in ([], [evidence()], [dict(evidence(), summary='Different wording, same evidence')]):
            result = assess_dispatch(proposal(evidence=records), state)
            self.assertIn('retry_requires_new_evidence', result['stop_reasons'])
        self.assertTrue(assess_dispatch(proposal(evidence=[evidence('b')]), state)['allowed'])

    def test_attempt_limit_counts_failures_and_new_evidence_does_not_reset_it(self):
        state = ledger(*(call('prior-' + str(index), 'parser-fix', 'failed') for index in range(3)))
        result = assess_dispatch(proposal(evidence=[evidence()]), state)
        self.assertIn('packet_attempt_limit_reached', result['stop_reasons'])

    def test_global_limit_survives_a_different_packet_id(self):
        state = ledger(*(call('prior-' + str(index)) for index in range(8)))
        result = assess_dispatch(proposal(packet='renamed-task'), state)
        self.assertIn('call_limit_reached', result['stop_reasons'])

    def test_completed_or_in_progress_packet_cannot_be_relaunched(self):
        for status, reason in (('completed', 'packet_already_completed'), ('running', 'packet_in_progress')):
            result = assess_dispatch(proposal(evidence=[evidence()]), ledger(call(packet='parser-fix', status=status)))
            self.assertIn(reason, result['stop_reasons'])

    def test_unknown_usage_fails_closed_only_when_token_budget_is_set(self):
        state = ledger(call(status='failed'))
        self.assertTrue(assess_dispatch(proposal(), state)['allowed'])
        policy = load_policy()
        policy['limits']['max_tokens'] = 100
        result = assess_dispatch(proposal(), state, policy)
        self.assertFalse(result['allowed'])
        self.assertIn('token_usage_unknown', result['stop_reasons'])

    def test_cached_tokens_are_not_counted_twice(self):
        state = ledger(call(usage={'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 80}))
        policy = load_policy()
        policy['limits']['max_tokens'] = 121
        self.assertTrue(assess_dispatch(proposal(), state, policy)['allowed'])
        policy['limits']['max_tokens'] = 120
        result = assess_dispatch(proposal(), state, policy)
        self.assertIn('observed_token_limit_reached', result['stop_reasons'])
        self.assertEqual(result['usage']['observed_tokens'], 120)

    def test_duplicate_launch_ids_and_invalid_accounting_are_rejected(self):
        with self.assertRaises(ValueError):
            assess_dispatch(proposal(), ledger(call(), call()))
        result = assess_dispatch(proposal(id='prior-1'), ledger(call()))
        self.assertIn('launch_id_already_used', result['stop_reasons'])
        for value in (-1, True, 1.5, '100'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assess_dispatch(proposal(), ledger(call(usage={'input_tokens': value, 'output_tokens': 0, 'cached_input_tokens': 0})))
        for status in ('pending', 'running'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                assess_dispatch(proposal(), ledger(call(status=status, usage={'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0})))

    def test_policy_cannot_grant_worker_dispatch_or_relax_read_only_roles(self):
        changes = [('worker_subagent_tools', 'enabled'), ('role_dispatch', True), ('reviewer_sandbox', 'workspace-write'), ('retries', 3), ('default_model', 'gpt-6-luna')]
        for key, value in changes:
            policy = load_policy()
            if key == 'worker_subagent_tools':
                policy[key] = value
            elif key == 'role_dispatch':
                policy['roles']['implementer']['can_dispatch'] = value
            elif key == 'reviewer_sandbox':
                policy['roles']['reviewer']['sandbox'] = value
            elif key == 'retries':
                policy['limits']['max_retries'] = value
            else:
                policy['roles']['implementer']['presets']['standard']['model'] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                assess_dispatch(proposal(), ledger(), policy)

    def test_fast_requires_an_explicit_model(self):
        policy = load_policy()
        policy['roles']['implementer']['presets']['fast']['model'] = None
        with self.assertRaisesRegex(ValueError, 'fast preset must select an explicit model'):
            assess_dispatch(proposal(preset='fast'), ledger(), policy)

    def test_malformed_nested_values_are_input_errors(self):
        requests = [proposal(requester=[]), proposal(scope=[]), proposal(role={}),
                    proposal(preset={}), proposal(role='coordinator', preset=[])]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                assess_dispatch(request, ledger())
        invalid_call = call()
        invalid_call['agent'] = []
        with self.assertRaises(ValueError):
            assess_dispatch(proposal(), ledger(invalid_call))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ledger.json').write_text(json.dumps(ledger()))
            for request in requests:
                (root / 'proposal.json').write_text(json.dumps(request))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = main(['--proposal', str(root / 'proposal.json'), '--ledger', str(root / 'ledger.json')])
                self.assertEqual(status, 2)
                self.assertEqual(json.loads(output.getvalue())['error'], 'invalid_input')

    def test_strict_shapes_and_paths(self):
        for bad in (proposal(extra='unexpected'), proposal(schema_version=True), proposal(evidence=[{'fingerprint': 'x'}])):
            with self.assertRaises(ValueError):
                assess_dispatch(bad, ledger())
        for path in ('../outside', '/absolute', 'C:\\outside', 'src/../../outside', ''):
            request = proposal()
            request['scope']['paths'] = [path]
            with self.subTest(path=path), self.assertRaises(ValueError):
                assess_dispatch(request, ledger())

    def test_json_loader_rejects_ambiguous_or_oversized_data(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'input.json'
            for raw in ('{"value":1,"value":2}', '{"value":NaN}', '{"value":Infinity}', '[' * 2000 + ']' * 2000):
                source.write_text(raw)
                with self.assertRaises(ValueError):
                    load_json(source)
            source.write_text('"' + 'x' * 100 + '"')
            with self.assertRaises(ValueError):
                load_json(source, maximum=16)

    def test_cli_is_a_read_only_not_dispatched_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = proposal(acceptance_check='echo unexpected > "' + str(root / 'should-not-exist') + '"')
            (root / 'proposal.json').write_text(json.dumps(request))
            (root / 'ledger.json').write_text(json.dumps(ledger()))
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(['--proposal', str(root / 'proposal.json'), '--ledger', str(root / 'ledger.json')])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())['dispatch_state'], 'not_dispatched')
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})


if __name__ == '__main__':
    unittest.main()
