from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from orchestrator.packets import packet_prompt, prompt_metrics, scopes_conflict
from orchestrator.policy import assess_dispatch
from test_policy import proposal, ledger, call


def context():
    return {'snapshot': 'reviewed commit abc123; inspect current worktree before use',
            'summary': 'Empty input reaches the decoder before the existing guard.',
            'references': [{'path': 'src/parser.py', 'start_line': 10, 'end_line': 30,
                            'reason': 'Guard and decoder call'}],
            'failed_approaches': ['Changing only the caller leaves another consumer unguarded.']}


class PacketTests(unittest.TestCase):
    def test_original_contract_is_preserved_and_counts_against_worker_budget(self):
        original = 'Preserve bare navigation and existing defaults while adding the requested scoped route.'
        packet = proposal(contract=original)
        self.assertTrue(assess_dispatch(packet, ledger())['allowed'])
        prompt = packet_prompt({'proposal': packet, 'guidance': []})
        self.assertEqual(json.loads(prompt.split('\n\n', 1)[1])['contract'], original)
        with self.assertRaisesRegex(ValueError, 'original contract'):
            assess_dispatch(proposal(contract='x' * 16001), ledger())
        request = {'proposal': proposal(contract='x' * 16000), 'guidance': [{'name': 'guide', 'text': 'y' * 16000}]}
        with self.assertRaisesRegex(ValueError, 'prompt exceeds'):
            prompt_metrics(request)

    def test_context_survives_without_loading_or_executing_references(self):
        request = proposal(context=context(), depends_on=['investigation'])
        with patch('builtins.open', side_effect=AssertionError('unexpected file read')):
            decision = assess_dispatch(request, ledger(call(packet='investigation')))
            prompt = packet_prompt({'proposal': request, 'guidance': []})
        self.assertTrue(decision['allowed'])
        payload = json.loads(prompt.split('\n\n', 1)[1])
        self.assertEqual(payload['context'], context())
        self.assertEqual(payload['depends_on'], ['investigation'])

    def test_prerequisite_requires_coordinator_accepted_ledger_status(self):
        for status in ('pending', 'running', 'failed'):
            with self.subTest(status=status):
                result = assess_dispatch(proposal(depends_on=['research']), ledger(call(packet='research', status=status)))
                self.assertIn('dependencies_not_accepted', result['stop_reasons'])
        result = assess_dispatch(proposal(depends_on=['research']), ledger(call(packet='research')))
        self.assertTrue(result['allowed'])
        self.assertEqual(result['blocked_dependencies'], [])
        result = assess_dispatch(proposal(depends_on=['unknown']), ledger())
        self.assertEqual(result['blocked_dependencies'], ['unknown'])

    def test_invalid_prerequisites_do_not_start_a_loop(self):
        for dependencies in (['parser-fix'], ['a', 'a'], ['../a'], 'a', [1], [str(i) for i in range(17)]):
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                assess_dispatch(proposal(depends_on=dependencies), ledger())

    def test_context_rejects_invalid_ranges_and_unbounded_or_unknown_data(self):
        for start, end in ((True, 20), (0, 20), (30, 20), (1, 10_000_001)):
            value = context()
            value['references'][0].update(start_line=start, end_line=end)
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                assess_dispatch(proposal(context=value), ledger())
        for key, value in (('summary', 'x' * 1501), ('references', [context()['references'][0]] * 9),
                           ('failed_approaches', ['failed'] * 5), ('transcript', 'all messages')):
            data = context()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                assess_dispatch(proposal(context=data), ledger())

    def test_total_context_budget_is_enforced_without_truncation(self):
        value = context()
        value['summary'] = 'x' * 1500
        value['failed_approaches'] = ['x' * 400] * 4
        value['references'] = [dict(value['references'][0], path='x' * 256, reason='x' * 300)] * 8
        with self.assertRaisesRegex(ValueError, 'context exceeds'):
            assess_dispatch(proposal(context=value), ledger())

    def test_prompt_measurement_uses_actual_serialized_characters_not_tokens(self):
        request = {'proposal': proposal(context=context()), 'guidance': [{'name': 'one', 'text': '測試'}]}
        metrics = prompt_metrics(request)
        self.assertEqual(metrics['prompt_characters'], len(packet_prompt(request)))
        self.assertGreater(metrics['prompt_utf8_bytes'], metrics['prompt_characters'])
        self.assertIsNone(metrics['token_count'])
        request['guidance'][0]['text'] = 'x' * 32000
        before = deepcopy(request)
        with self.assertRaisesRegex(ValueError, 'prompt exceeds'):
            prompt_metrics(request)
        self.assertEqual(request, before)

    def test_scope_conflicts_include_ancestors_case_and_unicode_aliases(self):
        for left, right in (('src', 'src/a.py'), ('src/a.py', 'src'), ('.', 'anything'),
                            ('SRC/a.py', 'src/A.py'), ('café/a.py', 'cafe\u0301/a.py')):
            a, b = proposal(), proposal(role='reviewer')
            a['scope']['paths'], b['scope']['paths'] = [left], [right]
            with self.subTest(left=left, right=right):
                self.assertTrue(scopes_conflict(a, b))
                self.assertTrue(scopes_conflict(b, a))
        a, b = proposal(), proposal()
        a['scope']['paths'], b['scope']['paths'] = ['src/a'], ['src/ab']
        self.assertFalse(scopes_conflict(a, b))

    def test_readers_may_share_paths_but_exclusive_resources_serialize(self):
        a, b = proposal(role='investigator'), proposal(role='reviewer')
        self.assertFalse(scopes_conflict(a, b))
        a['scope']['resources'] = ['test-db']
        b['scope']['resources'] = ['test-db']
        self.assertTrue(scopes_conflict(a, b))

    def test_glob_paths_and_malformed_resource_claims_are_rejected(self):
        for path in ('src/*.py', 'src/[ab].py', 'src/?', 'C:/src', '../src'):
            request = proposal()
            request['scope']['paths'] = [path]
            with self.subTest(path=path), self.assertRaises(ValueError):
                assess_dispatch(request, ledger())
        for resources in ('db', [1], ['db', 'db'], ['bad resource']):
            request = proposal()
            request['scope']['resources'] = resources
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                assess_dispatch(request, ledger())
