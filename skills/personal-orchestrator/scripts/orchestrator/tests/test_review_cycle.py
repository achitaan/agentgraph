"""Offline integration through real ownership/preview/reserve/finish operations."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from orchestrator import runtime
from orchestrator.review_cycle import ReviewCycle
from orchestrator.controlplane.security import atomic_private_write, platform_mutation_supported

USAGE = {'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 10}
FINDING = {'evidence': 'test_parser.py: failed case empty',
           'expected': 'empty input returns nil', 'observed': 'raises ValueError'}
REVIEW_FINDING = {'evidence': 'Review requests a new parser mode; baseline has no such mode.',
                  'expected': 'Original contract preserves valid parser behavior.',
                  'observed': 'Review blocks on adding an unrequested mode.'}


def proposal(state, role, number):
    records = [state['rejection']] if state.get('rejection') else []
    return {'schema_version': 1, 'id': f'worker-{number}',
            'packet': state['required_packets'][role],
            'requester': {'role': 'coordinator', 'depth': 0},
            'role': role, 'preset': 'standard', 'objective': 'Check bounded parser behavior',
            'scope': {'paths': ['parser.py'], 'bounded': True, 'consequence': 'low', 'uncertainty': 'low'},
            'acceptance_check': 'Run parser regression',
            'evidence': [{'fingerprint': row['fingerprint'], 'summary': 'Observed failure needs repair'}
                         for row in records] or [{'fingerprint': 'a' * 64, 'summary': 'Existing parser regression'}]}


def finish(state, accepted=True, findings=None):
    return {'launch_id': state['pending']['id'], 'generation': state['pending']['generation'],
            'accepted': accepted, 'verification': 'Observed source and parser regression',
            'blocking_findings': [] if accepted else deepcopy(findings or [FINDING])}


class FakeRunner:
    def __init__(self, observed, role, outcome='completed', usage=USAGE, interrupt=False, details=None):
        self.observed, self.role = observed, role
        self.outcome, self.usage, self.interrupt = outcome, usage, interrupt
        self.details = details

    def run(self, request, workspace, run_root, started):
        self.observed.append(deepcopy(request))
        started(os.getpid())
        if self.interrupt:
            raise RuntimeError('recorded interruption')
        receipt = {'request_hash': request['request_hash'], 'outcome': self.outcome,
                   'usage': deepcopy(self.usage), 'handoff': 'Parser regression checked', 'thread_id': 'fake'}
        if self.details is not None:
            receipt['usage_details'] = deepcopy(self.details)
        atomic_private_write(run_root / 'receipt.json', runtime.encoded(receipt))
        atomic_private_write(run_root / 'handoff.txt', b'Parser regression checked')
        return receipt


@unittest.skipUnless(platform_mutation_supported()[0], 'POSIX private runtime required')
class ReviewCycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='review-cycle-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / 'work'
        self.workspace.mkdir(mode=0o700)
        self.observed, self.prompts, self.now = [], [], 100.0

    def make(self, mutate=None, factory=None, **options):
        def coordinator(prompt, remaining):
            self.prompts.append(prompt)
            state = json.loads(prompt.split('\nCurrent state:\n')[1])
            pending = state['pending']
            if pending is None:
                decision = {'next': {'action': 'dispatch', 'proposal': proposal(state, 'implementer', 1)}}
            elif pending['role'] == 'implementer':
                decision = {'finish': finish(state), 'next': {'action': 'dispatch', 'proposal': proposal(state, 'reviewer', len(self.observed) + 1)}}
            else:
                decision = {'finish': finish(state), 'next': {'action': 'complete', 'summary': 'Verified parser behavior'}}
            if mutate:
                decision = mutate(decision, state, len(self.prompts))
            return decision, {'outcome': 'completed', 'usage': deepcopy(USAGE)}
        return ReviewCycle(self.root / 'state', self.workspace, 'parser', 'Preserve valid parser behavior.',
                           deadline=500, coordinator=coordinator,
                           runner_factory=factory or (lambda role, remaining: FakeRunner(self.observed, role)),
                           clock=lambda: self.now, **options)

    def test_happy_path_combines_finish_and_next_in_three_decisions(self):
        cycle = self.make()
        result = cycle.run()
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['coordinator_decisions'], 3)
        self.assertEqual(result['workers'], 2)
        self.assertEqual(result['extra_implementation_passes'], 0)
        self.assertEqual(len(result['calls']), 5)
        self.assertEqual([r['settings']['model'] for r in self.observed], ['gpt-6-luna', 'gpt-6-sol'])
        self.assertEqual([r['settings']['sandbox'] for r in self.observed], ['workspace-write', 'read-only'])
        for state, owner in cycle.lanes.values():
            self.assertNotIn(owner, (cycle.root / 'cycle.json').read_text())
            self.assertEqual(len(runtime.status(state)['calls']), 1)
        self.assertLess(max(map(len, self.prompts)), 5000)
        with self.assertRaisesRegex(ValueError, 'single-use'):
            cycle.run()

    def test_rejected_review_cannot_complete(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['finish'] = finish(state, False)
            return decision
        result = self.make(mutate).run()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('completion lacks', result['reason'])
        self.assertFalse(result['reviewed'])

    def test_repair_invalidates_review_and_counts_extra_pass(self):
        def mutate(decision, state, turn):
            if turn == 3:
                rejection = finish(state, False)
                change = proposal(state, 'implementer', 3)
                change['evidence'] = []
                return {'finish': rejection, 'next': {'action': 'dispatch', 'proposal': change}}
            if turn == 4:
                self.assertEqual(state['generation'], 2)
                self.assertFalse(state['reviewed'])
            return decision
        result = self.make(mutate).run()
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['coordinator_decisions'], 5)
        self.assertEqual(result['workers'], 4)
        self.assertEqual(result['repair_cycles'], 1)
        self.assertEqual(result['extra_implementation_passes'], 1)

    def test_old_generation_acceptance_fails_closed(self):
        def mutate(decision, state, turn):
            if 'finish' in decision:
                decision['finish']['generation'] = 0
            return decision
        result = self.make(mutate).run()
        self.assertIn('pending launch and generation', result['reason'])
        self.assertEqual(len(self.observed), 1)

    def test_wrong_launch_id_fails_closed(self):
        def mutate(decision, state, turn):
            if 'finish' in decision:
                decision['finish']['launch_id'] = 'foreign'
            return decision
        self.assertIn('pending launch and generation', self.make(mutate).run()['reason'])

    def test_gratuitous_implementation_after_review_is_rejected(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['next'] = {'action': 'dispatch', 'proposal': proposal(state, 'implementer', 3)}
            return decision
        result = self.make(mutate).run()
        self.assertIn('only completion', result['reason'])
        self.assertEqual(len(self.observed), 2)

    def test_repair_without_rejection_fails(self):
        def mutate(decision, state, turn):
            if turn == 2:
                decision['next'] = {'action': 'dispatch', 'proposal': proposal(state, 'implementer', 2)}
            return decision
        self.assertIn('implementation then review', self.make(mutate).run()['reason'])

    def test_repair_evidence_is_attached_without_coordinator_hashes(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['finish'] = finish(state, False)
                decision['next'] = {'action': 'dispatch', 'proposal': proposal(state, 'implementer', 3)}
            return decision
        cycle = self.make(mutate)
        self.assertEqual(cycle.run()['outcome'], 'completed')
        records = self.observed[2]['proposal']['evidence']
        self.assertIn(FINDING, [json.loads(item['summary']) for item in records
                              if item['summary'].startswith('{')])

    def test_repair_budget_zero_stops_before_launch(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['finish'] = finish(state, False)
                decision['next'] = {'action': 'dispatch', 'proposal': proposal(state, 'implementer', 3)}
            return decision
        result = self.make(mutate, max_repairs=0).run()
        self.assertIn('repair allowance', result['reason'])
        self.assertEqual(result['workers'], 2)

    def test_worker_limit_checked_before_next_launch(self):
        result = self.make(max_workers=1).run()
        self.assertIn('worker limit', result['reason'])
        self.assertEqual(len(self.observed), 1)

    def test_deadline_checked_before_launch_and_after_callback(self):
        def mutate(decision, state, turn):
            self.now = 501
            return decision
        result = self.make(mutate).run()
        self.assertIn('deadline exhausted', result['reason'])
        self.assertEqual(len(self.observed), 0)
        self.assertEqual(len(result['calls']), 1)

    def test_unknown_worker_usage_stops_and_keeps_pending_evidence(self):
        result = self.make(factory=lambda role, remaining: FakeRunner(self.observed, role, usage=None)).run()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('usage unknown', result['reason'])
        self.assertEqual(result['coordinator_decisions'], 1)
        self.assertIsNotNone(result['pending'])
        self.assertIsNone(result['calls'][1]['receipt']['usage'])

    def test_interrupted_worker_stops_without_relaunch(self):
        result = self.make(factory=lambda role, remaining: FakeRunner(self.observed, role, interrupt=True)).run()
        self.assertEqual(result['workers'], 1)
        self.assertEqual(len(result['calls']), 2)
        self.assertIsNone(result['calls'][1]['receipt']['usage'])
        self.assertIn('recorded interruption', result['reason'])

    def test_incomplete_is_explicit_and_retains_findings(self):
        def mutate(decision, state, turn):
            if turn == 3:
                return {'finish': finish(state, False), 'next': {'action': 'incomplete', 'reason': 'Parser rejection needs investigation'}}
            return decision
        result = self.make(mutate).run()
        self.assertEqual(result['reason'], 'Parser rejection needs investigation')
        self.assertIsNotNone(result['rejection'])

    def test_runtime_recursion_policy_still_applies(self):
        def mutate(decision, state, turn):
            if turn == 1:
                decision['next']['proposal']['requester']['depth'] = 1
            return decision
        result = self.make(mutate).run()
        self.assertIn('nested_dispatch_forbidden', result['reason'])
        self.assertEqual(result['workers'], 0)

    def test_packet_does_not_replay_old_handoffs_or_drop_contract(self):
        cycle = self.make()
        cycle.events = [{'handoff': 'STALE_UNNEEDED_HISTORY' * 1000}]
        prompt = cycle.prompt()
        self.assertIn(cycle.contract, prompt)
        self.assertNotIn('STALE_UNNEEDED_HISTORY', prompt)

    def test_state_and_owner_gates_are_preserved(self):
        with self.assertRaises(ValueError):
            ReviewCycle(self.workspace / 'state', self.workspace, 'x', 'Contract', deadline=500,
                        coordinator=lambda *_: None, runner_factory=lambda *_: None)
        cycle = self.make()
        state, _ = cycle.lanes['implementer']
        cycle.lanes['implementer'] = state, 'f' * 64
        result = cycle.run()
        self.assertIn('ownership capability', result['reason'])
        self.assertEqual(result['workers'], 0)

    def test_two_repairs_are_allowed_but_third_is_not_launched(self):
        def mutate(decision, state, turn):
            if state['pending'] and state['pending']['role'] == 'reviewer':
                rejection = finish(state, False, [dict(FINDING, observed='Failure variant ' + str(turn))])
                packet = proposal(state, 'implementer', len(self.observed) + 1)
                packet['evidence'] = []
                return {'finish': rejection, 'next': {'action': 'dispatch', 'proposal': packet}}
            return decision
        result = self.make(mutate).run()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('repair allowance', result['reason'])
        self.assertEqual(result['repair_cycles'], 2)
        self.assertEqual(result['extra_implementation_passes'], 2)
        self.assertEqual(result['workers'], 6)

    def test_coordinator_unknown_usage_is_preserved_and_stops(self):
        cycle = self.make()
        cycle.coordinator = lambda *_: ({'next': {'action': 'complete', 'summary': 'unsupported'}},
                                         {'outcome': 'completed', 'usage': None})
        result = cycle.run()
        self.assertIn('coordinator failed or usage unknown', result['reason'])
        self.assertEqual(result['workers'], 0)
        self.assertIsNone(result['calls'][0]['receipt']['usage'])

    def test_coordinator_exception_keeps_an_unknown_attempt(self):
        cycle = self.make()
        def interrupted(*_):
            raise RuntimeError('coordinator stream interrupted')
        cycle.coordinator = interrupted
        result = cycle.run()
        self.assertEqual(result['coordinator_decisions'], 1)
        self.assertEqual(len(result['calls']), 1)
        self.assertIsNone(result['calls'][0]['receipt']['usage'])

    def test_pending_worker_cannot_be_skipped(self):
        def mutate(decision, state, turn):
            if turn == 2:
                del decision['finish']
            return decision
        result = self.make(mutate).run()
        self.assertIn('pending worker requires explicit acceptance', result['reason'])
        self.assertEqual(result['workers'], 1)

    def test_selective_guidance_uses_existing_runtime_budget(self):
        cycle = self.make(guidance=[{'name': 'code-assurance', 'text': 'Verify parser behavior.'}])
        self.assertEqual(cycle.run()['outcome'], 'completed')
        self.assertEqual(self.observed[0]['guidance'][0]['name'], 'code-assurance')
        self.assertEqual(self.observed[1]['guidance'][0]['name'], 'code-assurance')
        self.assertNotIn('Verify parser behavior.', self.prompts[0])

    def test_role_guidance_and_original_contract_reach_only_intended_packets(self):
        guidance = {'coordinator': [{'name': 'dispatch', 'text': 'Coordinator-specific evidence.'}],
                    'implementer': [{'name': 'parser', 'text': 'Implementer-specific guidance.'}],
                    'reviewer': [{'name': 'review', 'text': 'Reviewer-specific guidance.'}]}
        cycle = self.make(guidance=guidance)
        guidance['implementer'][0]['text'] = 'LATER MUTATION'
        self.assertEqual(cycle.run()['outcome'], 'completed')
        self.assertIn('Coordinator-specific evidence.', self.prompts[0])
        self.assertNotIn('Implementer-specific guidance.', self.prompts[0])
        self.assertNotIn('Reviewer-specific guidance.', self.prompts[0])
        for request, name, text in zip(self.observed, ('parser', 'review'),
                                       ('Implementer-specific guidance.', 'Reviewer-specific guidance.')):
            self.assertEqual([item['name'] for item in request['guidance']], [name])
            self.assertEqual(request['guidance'][0]['text'], text)
            self.assertEqual(request['proposal']['contract'], cycle.contract)

    def test_invalid_guidance_fails_before_creating_state(self):
        with self.assertRaisesRegex(ValueError, 'role-to-list'):
            self.make(guidance={'all': []})
        self.assertFalse((self.root / 'state').exists())

    def test_raw_json_format_retry_preserves_pending_state_and_meters_both_calls(self):
        def mutate(decision, state, turn):
            if turn == 2:
                return '{"finish":'
            if turn == 3:
                self.assertEqual(state['pending']['id'], 'worker-1')
                self.assertFalse(state['implemented'])
                self.assertIsNotNone(state['format_error'])
                lane, _ = cycle.lanes['implementer']
                self.assertEqual(runtime.status(lane)['launches']['worker-1']['phase'], 'awaiting_acceptance')
            return json.dumps(decision)
        cycle = self.make(mutate)
        result = cycle.run()
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['coordinator_decisions'], 4)
        self.assertEqual(result['format_retries'], 1)
        self.assertEqual(len(result['calls']), 6)
        self.assertEqual(result['calls'][2]['receipt']['usage'], USAGE)
        self.assertEqual((cycle.root / 'coordinator-response-2.txt').read_text(), '{"finish":')
        self.assertEqual(result['workers'], 2)

    def test_format_retry_is_bounded_and_rejects_duplicate_keys(self):
        result = self.make(lambda *_: '{"next":{},"next":{}}').run()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('format retry allowance', result['reason'])
        self.assertEqual(result['coordinator_decisions'], 2)
        self.assertEqual(result['format_retries'], 1)
        self.assertEqual(result['workers'], 0)
        self.assertEqual(len(result['calls']), 2)

    def test_format_retry_uses_existing_decision_and_deadline_limits(self):
        result = self.make(lambda *_: '{', max_decisions=1).run()
        self.assertEqual(result['reason'], 'coordinator decision limit exhausted')
        self.assertEqual(result['format_retries'], 0)
        self.assertEqual(len(result['calls']), 1)

    def test_format_retry_does_not_extend_deadline(self):
        def mutate(*_):
            self.now = 501
            return '{'
        result = self.make(mutate).run()
        self.assertIn('deadline exhausted', result['reason'])
        self.assertEqual(result['format_retries'], 0)
        self.assertEqual(len(result['calls']), 1)

    def test_format_retry_can_be_disabled(self):
        result = self.make(lambda *_: '{', max_format_retries=0).run()
        self.assertIn('format retry allowance', result['reason'])
        self.assertEqual(len(result['calls']), 1)
        self.assertEqual(result['workers'], 0)

    def test_missing_evidence_stops_before_finish_or_dispatch_without_retry(self):
        def mutate(decision, state, turn):
            if turn == 2:
                del decision['next']['proposal']['evidence']
            return decision
        cycle = self.make(mutate)
        result = cycle.run()
        self.assertIn('proposal has invalid fields', result['reason'])
        self.assertEqual(result['format_retries'], 0)
        self.assertEqual(result['workers'], 1)
        self.assertFalse(result['implemented'])
        lane, _ = cycle.lanes['implementer']
        self.assertEqual(runtime.status(lane)['launches']['worker-1']['phase'], 'awaiting_acceptance')

    def test_corrected_review_uses_same_generation_without_implementation(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['finish'] = dict(finish(state, False, [REVIEW_FINDING]), rejection_kind='invalid_review')
                decision['next'] = {'action': 'dispatch', 'proposal': proposal(state, 'reviewer', 3)}
            return decision
        cycle = self.make(mutate)
        result = cycle.run()
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['generation'], 1)
        self.assertEqual(result['implementation_passes'], 1)
        self.assertEqual(result['repair_cycles'], 0)
        self.assertEqual(result['review_corrections'], 1)
        self.assertEqual(result['workers'], 3)
        first, second = self.observed[1:]
        self.assertEqual(first['proposal']['packet'], second['proposal']['packet'])
        self.assertNotEqual(first['proposal']['id'], second['proposal']['id'])
        self.assertEqual(second['settings']['sandbox'], 'read-only')
        self.assertIn(REVIEW_FINDING, [json.loads(row['summary']) for row in second['proposal']['evidence']
                               if row['summary'].startswith('{')])
        lane, _ = cycle.lanes['reviewer']
        self.assertEqual([row['status'] for row in runtime.status(lane)['calls']], ['failed', 'completed'])

    def test_second_review_correction_stops_at_unchanged_worker_budget(self):
        def mutate(decision, state, turn):
            if state['pending'] and state['pending']['role'] == 'reviewer':
                findings = [dict(REVIEW_FINDING, observed='Review error ' + str(turn))]
                return {'finish': dict(finish(state, False, findings), rejection_kind='invalid_review'),
                        'next': {'action': 'dispatch', 'proposal': proposal(state, 'reviewer', turn)}}
            return decision
        result = self.make(mutate).run()
        self.assertIn('review correction allowance', result['reason'])
        self.assertEqual(result['workers'], 3)
        self.assertEqual(result['implementation_passes'], 1)
        self.assertFalse(result['reviewed'])

    def test_invalid_review_cannot_be_accepted_without_fresh_review(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['finish'] = dict(finish(state, False, [REVIEW_FINDING]), rejection_kind='invalid_review')
            return decision
        result = self.make(mutate).run()
        self.assertIn('completion lacks', result['reason'])
        self.assertEqual(result['workers'], 2)
        self.assertFalse(result['reviewed'])

    def test_review_correction_consumes_worker_allowance(self):
        def mutate(decision, state, turn):
            if turn == 3:
                return {'finish': dict(finish(state, False, [REVIEW_FINDING]), rejection_kind='invalid_review'),
                        'next': {'action': 'dispatch', 'proposal': proposal(state, 'reviewer', 3)}}
            return decision
        result = self.make(mutate, max_workers=2).run()
        self.assertIn('worker limit', result['reason'])
        self.assertEqual(result['workers'], 2)
        self.assertEqual(result['review_corrections'], 0)

    def test_corrected_review_with_code_defect_still_requires_code_repair(self):
        def mutate(decision, state, turn):
            if turn == 3:
                return {'finish': dict(finish(state, False, [REVIEW_FINDING]), rejection_kind='invalid_review'),
                        'next': {'action': 'dispatch', 'proposal': proposal(state, 'reviewer', 3)}}
            if turn == 4:
                return {'finish': finish(state, False),
                        'next': {'action': 'dispatch', 'proposal': proposal(state, 'implementer', 4)}}
            return decision
        result = self.make(mutate).run()
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(result['repair_cycles'], 1)
        self.assertEqual(result['review_corrections'], 1)
        self.assertEqual(result['generation'], 2)
        self.assertEqual([row['proposal']['role'] for row in self.observed],
                         ['implementer', 'reviewer', 'reviewer', 'implementer', 'reviewer'])

    def test_code_rejection_cannot_dispatch_review_as_a_correction(self):
        def mutate(decision, state, turn):
            if turn == 3:
                decision['finish'] = finish(state, False)
                decision['next'] = {'action': 'dispatch', 'proposal': proposal(state, 'reviewer', 3)}
            return decision
        result = self.make(mutate).run()
        self.assertIn('implementation then review', result['reason'])
        self.assertEqual(result['workers'], 2)

    def test_implementation_rejection_cannot_be_reclassified_as_invalid_review(self):
        def mutate(decision, state, turn):
            if turn == 2:
                decision['finish'] = dict(finish(state, False), rejection_kind='invalid_review')
            return decision
        result = self.make(mutate).run()
        self.assertIn('only to a rejected reviewer', result['reason'])
        self.assertEqual(result['workers'], 1)

    def test_invalid_extended_usage_blocks_base_receipt_from_acceptance(self):
        result = self.make(factory=lambda role, remaining: FakeRunner(
            self.observed, role, details={'reasoning_output_tokens': 11})).run()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(result['workers'], 1)
        self.assertFalse(result['implemented'])
        self.assertEqual(result['calls'][1]['receipt']['usage'], USAGE)
        self.assertEqual(result['calls'][1]['receipt']['usage_details'], {'reasoning_output_tokens': 11})

    def test_decision_limit_returns_incomplete_without_more_calls(self):
        result = self.make(max_decisions=1).run()
        self.assertEqual(result['reason'], 'coordinator decision limit exhausted')
        self.assertEqual(result['coordinator_decisions'], 1)
        self.assertEqual(result['workers'], 1)

    def test_cancelled_coordinator_keeps_unknown_call_and_stops(self):
        cycle = self.make()
        def cancelled(*_):
            raise KeyboardInterrupt()
        cycle.coordinator = cancelled
        result = cycle.run()
        self.assertIn('cancelled by caller', result['reason'])
        self.assertIsNone(result['calls'][0]['receipt']['usage'])
        self.assertEqual(result['workers'], 0)

    def test_timeout_with_reported_usage_is_not_accepted(self):
        result = self.make(factory=lambda role, remaining: FakeRunner(self.observed, role, outcome='timeout')).run()
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertFalse(result['implemented'])
        self.assertEqual(result['workers'], 1)
        self.assertIsNotNone(result['calls'][1]['receipt']['usage'])

    def test_repeated_identical_failure_stops_without_another_repair(self):
        def mutate(decision, state, turn):
            if state['pending'] and state['pending']['role'] == 'reviewer':
                rejected = finish(state, False)
                packet = proposal(state, 'implementer', len(self.observed) + 1)
                packet['evidence'] = []
                return {'finish': rejected, 'next': {'action': 'dispatch', 'proposal': packet}}
            return decision
        result = self.make(mutate).run()
        self.assertIn('new evidence or an incomplete', result['reason'])
        self.assertEqual(result['workers'], 4)
        self.assertEqual(result['extra_implementation_passes'], 1)

    def test_protocol_documents_complete_proposal_without_private_helpers(self):
        from orchestrator.review_cycle import PROTOCOL
        required = ('schema_version', 'id', 'packet', 'requester', 'depth', 'role',
                    'preset', 'objective', 'scope', 'paths', 'bounded', 'consequence',
                    'uncertainty', 'acceptance_check', 'evidence')
        for key in required:
            self.assertIn('"' + key + '"', PROTOCOL)
        cycle = self.make()
        observed = self.observed
        def plain_json_coordinator(prompt, remaining):
            state = json.loads(prompt.split('\nCurrent state:\n')[1])
            pending = state['pending']
            decision = {}
            if pending:
                decision['finish'] = {'launch_id': pending['id'], 'generation': pending['generation'],
                                      'accepted': True, 'verification': 'Observed parser regression passed.',
                                      'blocking_findings': []}
            if pending and pending['role'] == 'reviewer':
                decision['next'] = {'action': 'complete', 'summary': 'Checked implementation and independent review.'}
            else:
                role = 'reviewer' if pending else 'implementer'
                packet = {'schema_version': 1, 'id': 'launch-' + str(len(observed)),
                          'packet': state['required_packets'][role],
                          'requester': {'role': 'coordinator', 'depth': 0},
                          'role': role, 'preset': 'standard', 'objective': 'Check parser behavior.',
                          'scope': {'paths': ['parser.py'], 'bounded': True, 'consequence': 'low', 'uncertainty': 'low'},
                          'acceptance_check': 'Run parser regression.', 'evidence': []}
                decision['next'] = {'action': 'dispatch', 'proposal': packet}
            return decision, {'outcome': 'completed', 'usage': deepcopy(USAGE)}
        cycle.coordinator = plain_json_coordinator
        self.assertEqual(cycle.run()['outcome'], 'completed')
