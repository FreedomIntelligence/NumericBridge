"""Exercise formal acceptance and verdict routing without a model or accelerator."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from tools.workflow import init_plan, inspect_workflow, run_workflow


COMPARE = Path(__file__).resolve().parents[1] / 'skills' / 'precision-compare' / 'scripts'


def stages(plan):
    return {stage['id']: stage for stage in plan['stages']}


def report_command(document):
    return [sys.executable, '-c', 'from pathlib import Path; import sys; '
            'Path(sys.argv[1]).write_text(sys.argv[2])', '{run}/report.json', json.dumps(document)]


def scores(mae, sides=('A', 'B')):
    return dict(kind='target-scores', status='no_thresholds', sides=dict(zip(('side_a', 'side_b'), sides)), metrics=dict(
        mae_nat=mae, p99_nat=mae, nll_abs_diff_nat_per_token=0,
        side_a_nll_nat_per_token=1, side_b_nll_nat_per_token=1))


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False))
    return path


def gradient_packets(root, *, task='sft', on_b=(1., 2.), b_status='completed'):
    """Actual v2 comparator inputs with separate arrays and native-rank records."""
    common = dict(model='fixture-linear', inputs={'batch': 'matched-fixture'},
                  numerics={'dtype': 'fp32'}, scope=f'{task}-gradients', state='matched-start')
    modes = {'A': {'mode': 'reference'}, 'B': {'mode': 'candidate'}, 'A-repeat': {'mode': 'reference'}}
    tensor = dict(name='weight', shape=[2], present=True)
    contract = write_json(root / 'gradients.json', dict(schema_version=2, kind='tensors', stage='gradients',
                          thresholds=None, common=common, side_conditions=modes,
                          expected_ranks={side: [0] for side in modes}, tensors=[tensor]))
    manifests = {}
    for side, values, status in [('A', (1., 2.), 'completed'), ('B', on_b, b_status),
                                 ('A-repeat', (1., 2.), 'completed')]:
        folder = root / side
        folder.mkdir()
        np.save(folder / 'gradient.npy', np.array(values, dtype=np.float32), allow_pickle=False)
        write_json(folder / 'environment.json', dict(rank=0, versions={'numpy': np.__version__}, actual=common | modes[side]))
        write_json(folder / 'status.json', dict(rank=0, status=status))
        manifests[side] = write_json(folder / 'tensors.json', dict(schema_version=2, kind='tensors', side=side,
                                     common=common, stage='gradients', expected_ranks=[0],
                                     ranks=[dict(rank=0, environment='environment.json', status='status.json')],
                                     tensors=[dict(tensor, shards=[dict(offset=0, value={'path': 'gradient.npy'})])]))
    return contract, manifests


def compare_gradients(contract, left, right, output):
    subprocess.run([sys.executable, '-B', str(COMPARE / 'compare_tensors.py'), '--contract', str(contract),
                    '--side-a', str(left), '--side-b', str(right), '--output', str(output), '--exit-zero'],
                   check=True, capture_output=True)
    return output


def judge(primary, repeat, exact, output, *extra):
    subprocess.run([sys.executable, '-B', str(COMPARE / 'acceptance.py'), '--report', str(primary),
                    '--noise-for', str(primary), str(repeat), '--exact-report', str(exact),
                    '--exit-zero', '--output', str(output), *extra], check=True, capture_output=True)
    return json.loads(output.read_text())


class WorkflowAcceptanceTests(unittest.TestCase):
    def test_real_sft_and_rl_tensor_packets_reach_acceptance(self):
        for task, values, status, verdict in [('sft', (1., 2.), 'completed', 'pass'),
                                              ('rl', (-1., -2.), 'completed', 'fail'),
                                              ('rl', (1., 2.), 'failed', 'incomplete')]:
            with self.subTest(task=task, status=status, verdict=verdict), TemporaryDirectory() as directory:
                root = Path(directory)
                contract, captures = gradient_packets(root, task=task, on_b=values, b_status=status)
                primary = compare_gradients(contract, captures['A'], captures['B'], root / 'primary.json')
                repeat = compare_gradients(contract, captures['A'], captures['A-repeat'], root / 'repeat.json')
                self.assertEqual(json.loads(primary.read_text())['kind'], 'tensors')
                exact = write_json(root / 'exact.json', dict(kind='exact-checks', checks={'loss-mask': True}, missing=[]))
                report = judge(primary, repeat, exact, root / 'acceptance.json', '--sides', 'A', 'B')
                self.assertEqual(report['verdict'], verdict, report)
                if status == 'completed':
                    same = compare_gradients(contract, captures['A'], captures['A'], root / 'same.json')
                    report = judge(same, repeat, exact, root / 'self-check.json', '--sides', 'A', 'B')
                    self.assertEqual(report['verdict'], 'incomplete')
                    self.assertTrue(any('self-comparison' in gap['reason'] for gap in report['unavailable']))

    def test_native_repeat_cannot_change_contract_or_shared_capture(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            contract, captures = gradient_packets(root)
            primary = compare_gradients(contract, captures['A'], captures['B'], root / 'paired.json')
            exact = write_json(root / 'exact.json', dict(kind='exact-checks', checks={'loss-mask': True}, missing=[]))
            second_contract = write_json(root / 'other-contract.json', json.loads(contract.read_text()))
            other = compare_gradients(second_contract, captures['A'], captures['A-repeat'], root / 'other-repeat.json')
            result = judge(primary, other, exact, root / 'judged.json', '--dtype', 'fp32', '--sides', 'A', 'B')
            self.assertEqual(result['verdict'], 'incomplete')
            self.assertTrue(any('different comparison contract' in row['reason'] for row in result['unavailable']), result)
            independent = root / 'second-capture'
            independent.mkdir()
            _, independent_captures = gradient_packets(independent)
            other = compare_gradients(contract, independent_captures['A'], captures['A-repeat'], root / 'wrong-anchor.json')
            result = judge(primary, other, exact, root / 'rejected-anchor.json', '--dtype', 'fp32', '--sides', 'A', 'B')
            self.assertEqual(result['verdict'], 'incomplete')
            self.assertTrue(any('matching primary capture' in row['reason'] for row in result['unavailable']), result)

    def test_repeat_noise_matches_report_and_objective(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exact = write_json(root / 'exact.json', dict(kind='exact-checks', checks={'loss-mask': True}, missing=[]))
            primary = write_json(root / 'primary.json', dict(kind='scalar-metrics', objective_pairs={'actor': [1, 1], 'critic': [1, 1.2]}))
            unrelated = write_json(root / 'actor-repeat.json', dict(kind='scalar-metrics', objective_pairs={'actor': [1, 1.2], 'critic': [1, 1]}))
            matched = write_json(root / 'critic-repeat.json', dict(kind='scalar-metrics', objective_pairs={'actor': [1, 1], 'critic': [1, 1.2]}))
            report = judge(primary, unrelated, exact, root / 'actual-fail.json')
            self.assertEqual(report['verdict'], 'fail', report)
            self.assertEqual(report['checks'][1]['noise'], 0)
            report = judge(primary, matched, exact, root / 'matched-noise.json')
            self.assertEqual(report['verdict'], 'noise_limited', report)
            second = write_json(root / 'other-objective-report.json', dict(kind='scalar-metrics', objective_pairs={'critic': [1, 1]}))
            report = judge(primary, unrelated, exact, root / 'separate-report.json', '--report', str(second),
                           '--noise-for', str(second), str(matched))
            self.assertEqual(report['verdict'], 'fail', report)

    def test_template_requires_explicit_dtype_and_binds_exact_report(self):
        for task in ('inference', 'sft', 'rl'):
            with self.subTest(task=task):
                plan = stages(init_plan(task, ['A', 'B']))
                acceptance = plan['acceptance']
                self.assertIn('TODO-comparison-dtype', acceptance['argv'])
                self.assertIn('check-exact', acceptance['depends_on'])
                self.assertEqual(plan['check-exact']['depends_on'], ['capture-A', 'capture-B'])
                self.assertIn('--exit-zero', plan['compare-scores']['argv'])
        with self.assertRaisesRegex(ValueError, 'formal acceptance dtype'):
            init_plan('sft', ['A', 'B'], dtype='fp8')

    def test_actual_acceptance_receipt_routes_each_result(self):
        exact = dict(kind='exact-checks', checks={'token-ids': True}, missing=[])
        cases = [
            ('pass', scores(0.0), scores(0.0), exact, 'bf16', 'deliver', None),
            ('gap', scores(0.05), scores(0.0), exact, 'bf16', 'optimize_optional', None),
            ('fail', scores(0.05), scores(0.0), exact, 'fp32', 'diagnose', 'precision-repair'),
            ('noise_limited', scores(0.2), scores(0.2), exact, 'bf16', 'stabilize', 'precision-compare'),
            ('incomplete', scores(0.0), scores(0.0), exact | {'missing': ['loss-mask']},
             'bf16', 'recover_capture', 'precision-compare'),
            ('incomplete', dict(kind='tensors', per_tensor={}, status='not_covered'), scores(0.0), exact,
             'bf16', 'recover_capture', 'precision-compare'),
            ('incomplete', scores(0.0, ('A', 'A')) | {'self_comparison': True}, scores(0.0), exact,
             'bf16', 'recover_capture', 'precision-compare'),
            ('incomplete', scores(0.0, ('A', 'C')), scores(0.0), exact,
             'bf16', 'recover_capture', 'precision-compare'),
            ('incomplete', scores(0.0), dict(kind='target-scores', status='not_covered'), exact,
             'bf16', 'recover_capture', 'precision-compare'),
            ('incomplete', dict(kind='scalar-metrics', metrics={}), scores(0.0), exact,
             'bf16', 'recover_capture', 'precision-compare'),
            ('fail', scores(0.2), scores(0.2), exact | {'checks': {'token-ids': False}, 'missing': ['loss-mask']},
             'bf16', 'diagnose', 'precision-repair'),
        ]
        for verdict, score, noise, check, dtype, route, skill in cases:
            with self.subTest(verdict=verdict, dtype=dtype, score=score, noise=noise, exact=check), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                plan = init_plan('inference', ['A', 'B'], dtype=dtype)
                acceptance = stages(plan)['acceptance']
                for name in ('compare-distributions', 'compare-distributions-noise'):
                    acceptance['depends_on'].remove(name)
                commands = acceptance['argv']
                index = commands.index('{artifact:compare-distributions:report}')
                self.assertEqual(commands[index - 1], '--report')
                del commands[index - 1:index + 1]
                dist_repeat = commands.index('{artifact:compare-distributions-noise:report}')
                self.assertEqual(commands[dist_repeat - 2], '--noise-for')
                del commands[dist_repeat - 2:dist_repeat + 1]
                noise.update(sides={'side_a': 'A', 'side_b': 'A-repeat'})
                sources = {'compare-scores': score, 'compare-scores-noise': noise, 'check-exact': check}
                plan['stages'] = [dict(id=name, skill='precision-compare', timeout=10,
                                       argv=report_command(doc), outputs={'report': 'report.json'})
                                  for name, doc in sources.items()] + [acceptance]
                path = root / 'plan.json'
                path.write_text(json.dumps(plan))
                workspace = root / 'work'
                for result in (run_workflow(path, workspace), inspect_workflow(path, workspace)):
                    if skill:
                        self.assertEqual(result['status'], 'needs_action')
                        row = result['next_action']
                        self.assertEqual(row['skill'], skill)
                    else:
                        self.assertEqual(result['status'], 'completed')
                        row = next(stage for stage in result['stages'] if stage['id'] == 'acceptance')
                    self.assertEqual((row['verdict'], row['route']), (verdict, route))
                    report = json.loads(Path(row['outputs']['acceptance']['path']).read_text())
                    self.assertEqual(report['dtype'], dtype)
                    self.assertEqual(report['profile'], 'nb-default-v1')

    def test_comparators_only_suppress_nonzero_when_report_was_written(self):
        scripts = Path(__file__).resolve().parents[1] / 'skills' / 'precision-compare' / 'scripts'
        import subprocess

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for script in ('compare_target_scores.py', 'compare_distributions.py', 'compare_tensors.py'):
                with self.subTest(script=script):
                    output = root / (script + '.json')
                    args = [sys.executable, '-B', str(scripts / script), '--contract', str(root / 'missing'),
                            '--side-a', str(root / 'a'), '--side-b', str(root / 'b'), '--output', str(output)]
                    for extra, path, code in (([], output, 1), (['--exit-zero'], root / ('plan-' + output.name), 0)):
                        args[-1] = str(path)
                        result = subprocess.run(args + extra, capture_output=True, text=True, check=False)
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertNotEqual(json.loads(path.read_text())['status'], 'no_thresholds')
                    result = subprocess.run(args + ['--exit-zero'], capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
