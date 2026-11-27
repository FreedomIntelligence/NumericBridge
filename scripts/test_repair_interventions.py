"""Check that intervention reports separate measured effects from repair claims."""

from importlib import import_module
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


replay = import_module('skills.precision-repair.scripts.replay')


class RepairInterventionsTests(unittest.TestCase):
    def test_worse_single_can_be_part_of_an_improved_combination(self):
        report = replay.interventions(dict(metric='endpoint error', baseline=10, noise=0.1,
                                           runs={'A': 12, 'B': 9, 'A+B': 5}))
        self.assertEqual(report['schema_version'], 2)
        self.assertEqual((report['factors']['A']['direction'], report['factors']['A']['effect']),
                         ('worsened', -2))
        combo = report['combinations']['A+B']
        self.assertEqual((combo['direction'], combo['effect'], combo['interaction']), ('improved', 5, 6))
        self.assertTrue(combo['interaction_beyond_repeat_band'])
        for row in [*report['factors'].values(), combo]:
            self.assertNotIn('confirmed', row)
            self.assertNotIn('label', row)
        self.assertNotIn('verdict', report)

    def test_controls_are_reported_without_substituting_for_acceptance(self):
        report = replay.interventions(dict(baseline=4, noise=0.25, target=10, lower_is_better=False,
                                           runs={'A': 6, 'B': 2, 'C': 4.5, 'revert:A': 4.5,
                                                 'revert:B': 2.5, 'extra:A': 3}))
        factors = report['factors']
        self.assertEqual(factors['A']['direction'], 'improved')
        self.assertEqual(factors['A']['revert_status'], 'matches_baseline')
        self.assertEqual(factors['A']['extra_value'], 3)
        self.assertNotIn('extra_improves', factors['A'])
        self.assertEqual(factors['B']['direction'], 'worsened')
        self.assertEqual(factors['B']['revert_status'], 'differs_from_baseline')
        self.assertEqual(factors['C']['direction'], 'within_repeat_band')
        self.assertEqual(factors['C']['revert_status'], 'not_run')
        self.assertEqual(report['target'], 10)

    def test_cli_keeps_partial_combination_measurements_without_assuming_zero_effect(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / 'interventions.json'
            output = root / 'effects.json'
            spec.write_text(json.dumps(dict(baseline=10, noise=0, runs={'A': 8, 'A+B': 5})))
            raw = subprocess.check_output([sys.executable, '-IB', str(Path(replay.__file__).resolve()),
                                           'interventions', '--spec', str(spec), '--output', str(output)],
                                          cwd=root, text=True)
            self.assertEqual(json.loads(raw), {'factors': {'A': 'improved'}, 'combinations': {'A+B': 'improved'}})
            combo = json.loads(output.read_text())['combinations']['A+B']
            self.assertEqual(combo['effect'], 5)
            self.assertEqual(combo['missing_factors'], ['B'])
            self.assertIsNone(combo['interaction'])
            self.assertIsNone(combo['interaction_beyond_repeat_band'])

    def test_reject_invalid_noise_or_combinations(self):
        for changes, error in (({'noise': -1}, 'nonnegative'), ({'noise': float('nan')}, 'finite number'),
                               ({'runs': {'A+A': 0}}, 'distinct change names')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, error):
                replay.interventions(dict(baseline=1, noise=0, runs={'A': 0}) | changes)


if __name__ == '__main__':
    unittest.main()
