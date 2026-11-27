"""Exercise the evidence boundary between an open handoff and a delivered round."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from community.normalize import assess_operator_optimization
from tools.operator_optimization import check, scaffold


SKILLS = Path(__file__).resolve().parents[1] / 'skills'
CLI = SKILLS / 'precision-compare' / 'scripts' / 'operator_optimization.py'
KERNEL_CHECK = SKILLS / 'operator-optimize' / 'scripts' / 'kernel_check.py'
HANDOFF = dict(issue='H1', operator='normalization', reading='a_deviates', entry='required')


def measurements():
    shapes = ('captured', 'random', 'edge')
    report = dict(kind='kernel-check', schema_version=1, dtype='bf16', baseline='stock', bound=0.01,
                  rounded_reference={shape: {'shape_class': shape} for shape in shapes})
    report['variants'] = {name: dict(within_bound=name != 'stock', reproducible=True, missing_cases=[],
                                    cases={shape: dict(error=error, repeats=2, reproducible=True, within_bound=name != 'stock',
                                                        repeat_errors=[dict(error=error, within_bound=name != 'stock') for _ in range(2)])
                                            for shape in shapes})
                          for name, error in (('stock', 0.025), ('wide', 0.002))}
    report['performance'] = dict(status='measured', conditions=dict(host='target-a', session='timing-a', warmup=3),
                                 variants={'stock': dict(samples=12, median=1), 'wide': dict(samples=12, median=1.2)})
    # An accurately measured endpoint failure must not be converted to missing evidence or to a pass.
    outcome = dict(status='measured', verdict='fail', evidence='native-wide-acceptance', metrics={'target_logprob_mae': 0.4})
    variants = [dict(name='stock', candidate_id='stock'), dict(name='wide', candidate_id='wide', end_to_end=outcome)]
    return report, {'verdict': 'fail'}, variants


class OperatorRoundTests(unittest.TestCase):
    def test_only_local_evidence_stays_open_and_is_not_discarded(self):
        report, _, variants = measurements()
        variants[1]['end_to_end'] = dict(status='not_measured', reason='no native slot')
        report['performance'] = dict(status='not_measured', reason='no timing')
        report['rounded_reference'].pop('edge')
        for row in report['variants'].values():
            row['cases'].pop('edge')
        report['variants']['wide']['cases']['captured'].update(repeats=1, reproducible='not_measured')
        payload = scaffold(HANDOFF, open_state=dict(round_status='round_delivered', entry='optional'),
                           kernel_report=report, end_to_end='not_measured', variants=variants)
        self.assertEqual(payload['round_status'], 'handoff_open')
        self.assertEqual(payload['entry'], 'required')
        self.assertEqual(payload['kernel_check'], report)
        result = check(payload)
        self.assertEqual(result['status'], 'ok', result)
        for gap in ('target shape classes: edge', 'wide: repeated kernel executions',
                    'wide: paired timing samples', 'wide: measured original-workload result'):
            self.assertIn(gap, result['missing_checks'])
        self.assertEqual(payload['open_items']['missing_checks'], result['missing_checks'])
        self.assertTrue(payload['open_items']['next_step'])

        payload['round_status'] = 'round_delivered'
        self.assertEqual(check(payload)['status'], 'problems')
        before = deepcopy(payload)
        self.assertEqual(assess_operator_optimization(payload)['round_status'], 'handoff_open')
        self.assertEqual(payload, before)

    def test_delivered_measurements_do_not_claim_original_acceptance(self):
        report, e2e, variants = measurements()
        payload = scaffold(HANDOFF, kernel_report=report, end_to_end=e2e, variants=variants)
        self.assertEqual(payload['round_status'], 'round_delivered')
        entities = [dict(kind='candidate', id=name) for name in ('stock', 'wide')]
        result = check(payload, entities=entities)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['missing_checks'], [])
        self.assertEqual(assess_operator_optimization(payload)['end_to_end']['verdict'], 'fail')
        self.assertEqual(check(payload, entities=[])['status'], 'problems')
        payload['entry'] = 'optional'
        self.assertIn('entry must match the local FP64 reading', check(payload)['problems'])

    def test_missing_baseline_pair_or_unmatched_case_cannot_be_delivered(self):
        report, e2e, variants = measurements()
        del report['performance']['variants']['stock']
        report['variants']['wide']['missing_cases'] = ['random']
        del report['variants']['wide']['cases']['random']
        payload = scaffold(HANDOFF, kernel_report=report, end_to_end=e2e, variants=variants[1:])
        self.assertEqual(payload['round_status'], 'handoff_open')
        gaps = payload['open_items']['missing_checks']
        self.assertIn('kept baseline', gaps)
        self.assertIn('wide: matched kernel cases with FP64 error', gaps)
        self.assertIn('wide: paired timing samples', gaps)

    def test_kernel_report_preserves_observed_nonfinite_failures_in_strict_json(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            spec, report_path = root / 'spec.json', root / 'kernel-report.json'
            cases = []
            for name, inputs in [('captured', [1, 2]), ('random', [1.1, 0.3, 1.4]), ('edge', [-1, 0, 32, 0, 1])]:
                reference = root / f'{name}-ref.npy'
                np.save(reference, np.square(np.array(inputs, dtype=np.float64)))
                outputs = {'stock': [], 'wide': []}
                for variant in outputs:
                    for repeat in range(2):
                        output = np.square(np.array(inputs, dtype=np.float32))
                        if variant == 'wide' and name == 'edge' and repeat == 1:
                            output[0] = np.nan
                        path = root / f'{name}-{variant}-{repeat}.npy'
                        np.save(path, output)
                        outputs[variant].append(path.name)
                cases.append(dict(id=name, shape_class=name, reference=reference.name, variants=outputs))
            spec.write_text(json.dumps(dict(dtype='fp32', reduction=5, baseline='stock', cases=cases,
                                            timing=dict(conditions=dict(host='target-a', session='benchmark-a', warmup=3),
                                                        variants={'stock': [1.0, 1.1], 'wide': [1.0, 1.2]}))))
            subprocess.run([sys.executable, '-B', str(KERNEL_CHECK), '--spec', str(spec),
                            '--output', str(report_path)], check=True, capture_output=True)
            report = json.loads(report_path.read_text())
            _, e2e, variants = measurements()
            payload = scaffold(HANDOFF, kernel_report=report, end_to_end=e2e, variants=variants)
            self.assertEqual(payload['round_status'], 'round_delivered', check(payload))
            json.dumps(payload, allow_nan=False)
            edge = payload['kernel_check']['variants']['wide']['cases']['edge']
            self.assertEqual(edge['nonfinite'], 1)
            self.assertFalse(edge['within_bound'])
            self.assertIsNone(edge['error'])
            self.assertIsNone(report['variants']['wide']['cases']['edge']['error'])
            self.assertNotIn('Infinity', report_path.read_text())
            self.assertFalse(report['variants']['wide']['within_bound'])
            self.assertTrue(edge['repeat_errors'][0]['within_bound'])
            self.assertIsNone(edge['repeat_errors'][1]['error'])
            self.assertFalse(edge['repeat_errors'][1]['within_bound'])

            # Two paths to the same output do not count as independent repeats;
            # a slow fix measured on only one shape cannot cover the others.
            cases[0]['variants']['slow_fix'] = cases[0]['variants']['stock']
            duplicate = root / 'alias.npy'
            duplicate.symlink_to(root / cases[1]['variants']['wide'][0])
            cases[1]['variants']['wide'] = [cases[1]['variants']['wide'][0], duplicate.name]
            new_spec = json.loads(spec.read_text()) | {'cases': cases}
            next_spec, next_report = root / 'next-spec.json', root / 'next-report.json'
            next_spec.write_text(json.dumps(new_spec))
            subprocess.run([sys.executable, '-B', str(KERNEL_CHECK), '--spec', str(next_spec),
                            '--output', str(next_report)], check=True, capture_output=True)
            report = json.loads(next_report.read_text())
            wide = report['variants']['wide']
            self.assertEqual((wide['cases']['random']['repeats'], wide['cases']['random']['duplicate_outputs']), (1, 1))
            self.assertEqual(wide['vs_slow_fix'], 'not_measured')
            self.assertEqual(wide['missing_slow_fix_cases'], ['edge', 'random'])
            payload = scaffold(HANDOFF, kernel_report=report, end_to_end=e2e, variants=variants)
            self.assertEqual(payload['round_status'], 'handoff_open')
            self.assertIn('wide: repeated kernel executions', check(payload)['missing_checks'])
            self.assertIn('wide: matched slow-fix cases', check(payload)['missing_checks'])

    def test_unmeasured_or_failing_candidates_are_never_recommended(self):
        report, e2e, variants = measurements()
        failing = scaffold(HANDOFF, kernel_report=report, end_to_end=e2e, variants=variants)
        self.assertIsNone(assess_operator_optimization(failing)['recommendation']['recommended_variant'])
        variants[1]['end_to_end']['verdict'] = 'gap'
        measured = scaffold(HANDOFF, kernel_report=report, end_to_end={'verdict': 'gap'}, variants=variants)
        self.assertEqual(assess_operator_optimization(measured)['recommendation']['recommended_variant'], 'wide')
        del report['variants']['wide']
        variants[1]['within_bound'] = True
        open_case = scaffold(HANDOFF, kernel_report=report, end_to_end=e2e, variants=variants)
        result = assess_operator_optimization(open_case)
        self.assertEqual(result['round_status'], 'handoff_open')
        self.assertEqual(result['variants'][1]['tier'], 'unmeasured')
        self.assertIsNone(result['variants'][1]['within_bound'])
        self.assertIsNone(result['recommendation']['recommended_variant'])

    def test_cli_writes_a_resumable_handoff_even_without_a_report(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            handoff, out = root / 'handoff.json', root / 'round.json'
            handoff.write_text(json.dumps(HANDOFF))
            raw = subprocess.check_output([sys.executable, '-B', str(CLI), 'scaffold',
                                           '--handoff', str(handoff), '--output', str(out)], text=True)
            self.assertEqual(json.loads(raw)['round_status'], 'handoff_open')
            document = json.loads(out.read_text())
            self.assertEqual(document['open_items']['missing_checks'], ['kernel_check report'])
            self.assertEqual(check(document)['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
