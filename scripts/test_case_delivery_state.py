"""Offline contract tests for versioned case delivery checkpoints."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tools.case_state import template, validate


CLI = Path(__file__).resolve().parents[1] / 'skills' / 'precision-compare' / 'scripts' / 'case_state.py'
DESTINATION = 'https://numericbridge.com/v1/public-submissions'


def observed(verdict, report):
    return dict(verdict=verdict, verification='supported_native', evidence=[report])


def checkpoint():
    candidates = [dict(id=f'candidate-{i}', focused='pass', original_relation='independent',
                       evidence=[f'evidence/candidate-{i}-focused.json'], target_commit='a' * 40,
                       patch_sha256=str(i) * 64)
                  for i in range(1, 5)]

    def receipt(n, rows):
        digest = str(n) * 64
        versions = [dict(candidate_id=row['id'], target_commit=row['target_commit'],
                         patch_sha256=row['patch_sha256'], tested_commit='b' * 40,
                         tested_patch_sha256=row['patch_sha256']) for row in rows]
        return dict(review_state='pending_review',
                    receipt_binding=dict(schema_version=1, destination=DESTINATION, draft_sha256=digest,
                                         receipt_id=f'submission-{digest}', candidates=versions))

    summary = dict(schema_version=1, original=observed('fail', 'evidence/original-acceptance.json'),
                   delivery=dict(outcome='bounded_attribution', evidence=['evidence/attribution.json']),
                   candidates=candidates, community=[receipt(5, candidates[:1]), receipt(6, candidates[1:])])
    return dict(template(), status='done', stage='delivery', next_action='none',
                delivery='report.md', delivery_summary=summary)


class CaseDeliveryTests(unittest.TestCase):
    def assertInvalid(self, state, message):
        result = validate(state)
        self.assertEqual(result['status'], 'invalid', result)
        self.assertTrue(any(message in p for p in result['problems']), result)

    def test_legacy_checkpoint_unchanged(self):
        expected = dict(status='in_progress', stage='request', next_action='community retrieval',
                        running_jobs=[], sessions=1)
        self.assertEqual(template(), expected)
        self.assertEqual(validate(expected), dict(status='ok', problems=[]))
        restored = dict(expected, status='done', delivery='report.md', extra_historical_field=True)
        self.assertEqual(validate(restored), dict(status='ok', problems=[]))

    def test_fail_bounded_delivery_four_focused_validations_and_two_pending_receipts(self):
        state = checkpoint()
        before = deepcopy(state)
        self.assertEqual(validate(state), dict(status='ok', problems=[]))
        self.assertEqual(state, before)
        summary = state['delivery_summary']
        self.assertEqual(summary['original']['verdict'], 'fail')
        self.assertEqual(len(summary['candidates']), 4)
        self.assertTrue(all(row['focused'] == 'pass' and 'original_workload' not in row
                            for row in summary['candidates']))
        self.assertTrue(all(row['review_state'] == 'pending_review' for row in summary['community']))

    def test_receipt_cannot_restore_acceptance_or_claim_untested_native_result(self):
        state = checkpoint()
        summary = state['delivery_summary']
        summary['delivery']['outcome'] = 'acceptance_restored'
        self.assertInvalid(state, 'acceptance_restored requires original pass or gap')
        summary['original']['verdict'] = 'pass'
        self.assertInvalid(state, 'earlier nonpassing original.baseline')
        summary['original']['baseline'] = observed('fail', 'evidence/baseline.json')
        summary['original']['verification'] = 'not_run'
        self.assertInvalid(state, 'observed verdict requires supported_native')
        summary['original']['verification'] = 'supported_native'
        summary['original']['verdict'] = 'gap'
        self.assertEqual(validate(state), dict(status='ok', problems=[]))
        summary['original']['baseline']['evidence'] = list(summary['original']['evidence'])
        self.assertInvalid(state, 'baseline and current verdict need separate evidence')

    def test_focused_regression_is_not_causation_or_a_verified_fix(self):
        state = checkpoint()
        candidate = state['delivery_summary']['candidates'][0]
        candidate['original_relation'] = 'contributing_cause'
        self.assertInvalid(state, 'relationship_evidence must include evidence')
        candidate['relationship_evidence'] = list(candidate['evidence'])
        self.assertInvalid(state, 'must not reuse focused regression evidence')
        candidate['relationship_evidence'] = ['evidence/matched-operands.json']
        self.assertEqual(validate(state), dict(status='ok', problems=[]))
        candidate['original_relation'] = 'verified_fix'
        self.assertInvalid(state, 'verified_fix requires focused pass')
        candidate['original_workload'] = observed('pass', candidate['evidence'][0])
        self.assertInvalid(state, 'original_workload must not reuse focused regression evidence')
        candidate['original_workload'] = observed('pass', 'evidence/candidate-native-original-acceptance.json')
        candidate['original_relation'] = 'independent'
        self.assertInvalid(state, 'focused-only relationship cannot claim an original_workload pass')
        candidate['original_relation'] = 'verified_fix'
        self.assertEqual(validate(state), dict(status='ok', problems=[]))
        self.assertEqual(state['delivery_summary']['original']['verdict'], 'fail')

    def test_receipts_are_bound_to_draft_and_current_candidate_version(self):
        for key, value, expected in (
            ('patch_sha256', 'f' * 64, 'must match the current candidate'),
            ('target_commit', 'f' * 40, 'must match the current candidate'),
            ('candidate_id', 'other-candidate', 'must identify a current candidate'),
            ('tested_patch_sha256', None, 'requires both tested_commit and tested_patch_sha256'),
        ):
            with self.subTest(key=key):
                state = checkpoint()
                binding = state['delivery_summary']['community'][0]['receipt_binding']
                binding['candidates'][0][key] = value
                self.assertInvalid(state, expected)
        for key, value, expected in (
            ('receipt_id', 'submission-' + 'c' * 64, 'receipt_id must match draft_sha256'),
            ('candidates', [], 'bind at least one current candidate version'),
            ('destination', 'https://github.com/example/project/pulls', 'must be a NumericBridge intake URL'),
        ):
            with self.subTest(key=key):
                state = checkpoint()
                state['delivery_summary']['community'][0]['receipt_binding'][key] = value
                self.assertInvalid(state, expected)

    def test_unknown_schema_and_not_run_are_not_evidence(self):
        state = template(with_delivery_summary=True)
        self.assertEqual(validate(state), dict(status='ok', problems=[]))
        summary = state['delivery_summary']
        summary['schema_version'] = True
        self.assertInvalid(state, 'schema_version must be 1')
        summary['schema_version'] = 1
        summary['delivery'] = dict(outcome='bounded_attribution', evidence=['evidence/diagnostic.json'])
        self.assertInvalid(state, 'bounded_attribution requires original fail or noise_limited')
        summary['original']['verification'] = 'supported_native'
        summary['original']['evidence'] = ['evidence/unrun.json']
        self.assertInvalid(state, 'not_run cannot claim verification or acceptance evidence')

    def test_existing_cli_checks_summary_and_template_is_opt_in(self):
        def cli(command, target, *extra):
            return subprocess.run([sys.executable, '-B', str(CLI), command, '--state', str(target), *extra],
                                  text=True, capture_output=True, check=False)

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.json'
            created = cli('template', path, '--with-delivery-summary')
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertIn('delivery_summary', json.loads(path.read_text()))
            checked = cli('check', path)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout), dict(status='ok', problems=[]))
            state = checkpoint()
            state['delivery_summary']['original']['evidence'] = []
            path.write_text(json.dumps(state))
            rejected = cli('check', path)
            self.assertEqual(rejected.returncode, 1, rejected.stderr)
            self.assertEqual(json.loads(rejected.stdout)['status'], 'invalid')
            legacy = Path(directory) / 'legacy.json'
            self.assertEqual(cli('template', legacy).returncode, 0)
            self.assertNotIn('delivery_summary', json.loads(legacy.read_text()))


if __name__ == '__main__':
    unittest.main()
