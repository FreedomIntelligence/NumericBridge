"""Readiness checks share the capture launch unless a side requests preflight."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.workflow import init_plan, inspect_workflow, main, run_workflow


def stages(plan):
    return {stage['id']: stage for stage in plan['stages']}


class WorkflowReadinessTests(unittest.TestCase):
    def test_default_has_no_standalone_preflight(self):
        plan = stages(init_plan('inference', ['A', 'B']))
        self.assertFalse(any(name.startswith('preflight-') for name in plan))
        for name in ('capture-A', 'capture-B', 'capture-A-repeat'):
            capture = plan[name]
            self.assertIn('--receipt', capture['argv'])
            self.assertEqual(capture['outputs']['receipt'], 'receipt.json')
            self.assertEqual(capture['checks'], [{'output': 'receipt', 'pointer': '/ready', 'equals': True}])
        self.assertEqual(plan['capture-A-repeat']['depends_on'], ['capture-A'])
        self.assertEqual(plan['compare-scores']['depends_on'], ['capture-A', 'capture-B'])
        self.assertEqual(plan['compare-scores-noise']['depends_on'], ['capture-A', 'capture-A-repeat'])

    def test_cli_can_add_only_the_side_that_needs_preflight(self):
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / 'plan.json'
            status = main(['init', '--task', 'rl', '--sides', 'A,B', '--preflight', 'B', '--output', str(path)])
            self.assertEqual(status, 0)
            plan = stages(json.loads(path.read_text()))
        self.assertNotIn('preflight-A', plan)
        self.assertEqual(plan['capture-B']['depends_on'], ['preflight-B'])
        self.assertEqual(plan['preflight-B']['execution'], plan['capture-B']['execution'])
        self.assertNotIn('--receipt', plan['capture-B']['argv'])
        self.assertIn('--receipt', plan['capture-A']['argv'])
        for invalid in (['C'], ['A', 'A']):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'preflight sides'):
                init_plan('rl', ['A', 'B'], preflight_sides=invalid)

    def test_failed_readiness_blocks_comparison_not_the_other_side(self):
        for separate in (False, True):
            with self.subTest(separate=separate), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / 'inputs').mkdir()
                (root / 'environments').mkdir()
                for side in ('A', 'B'):
                    (root / 'environments' / f'{side}.json').write_text('{}')
                plan = init_plan('inference', ['A', 'B'], preflight_sides=['A'] if separate else [])
                by_id = stages(plan)
                for stage in plan['stages']:
                    stage['timeout'] = 10
                    # All commands are local. A exits successfully but its receipt
                    # shows that it used the wrong path or missed required records.
                    if stage['id'] in ('capture-A', 'capture-B', 'preflight-A'):
                        command = ('from pathlib import Path; import sys\n'
                                   'for name in sys.argv[2:]:\n'
                                   '    path = Path(name)\n'
                                   '    path.parent.mkdir(parents=True, exist_ok=True)\n'
                                   '    path.write_text(sys.argv[1])\n')
                        stage['argv'] = [sys.executable, '-c', command,
                                         json.dumps({'ready': stage['side'] != 'A'}),
                                         *(f'{{run}}/{path}' for key, path in stage['outputs'].items() if key != 'packet')]
                    else:
                        stage['argv'] = [sys.executable, '-c', 'raise SystemExit("blocked stage ran")']
                plan_path = root / 'plan.json'
                plan_path.write_text(json.dumps(plan))
                result = run_workflow(plan_path, root / 'work', jobs=2)
                failed = 'preflight-A' if separate else 'capture-A'
                self.assertEqual({row['id'] for row in result['stages']}, {'capture-B'})
                blocked = {row['stage']: row for row in result['blocked_actions']}
                self.assertEqual(blocked[failed]['skill'], 'framework-adapt')
                self.assertIn('receipt/ready', blocked[failed]['reason'])
                for name in by_id.keys() - {failed, 'capture-B'}:
                    self.assertEqual(blocked[name]['state'], 'dependency')


class WorkflowEngineTests(unittest.TestCase):
    def test_observe_live_child_without_blocking_independent_capture(self):
        def stage(name, side, depends_on=()):
            return dict(id=name, side=side, skill='precision-compare', depends_on=list(depends_on),
                        timeout=5, outputs={'out': 'out'}, argv=[sys.executable, '-c',
                            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("complete")',
                            '{run}/out'])

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            plan_path = root / 'plan.json'
            plan = dict(schema_version=1, id='recovery', budget_seconds=30, stages=[
                stage('prepare-A', 'A'), stage('capture-A', 'A', ('prepare-A',)), stage('capture-B', 'B'),
                stage('compare', 'compare', ('capture-A', 'capture-B'))])
            plan_path.write_text(json.dumps(plan))
            workspace = root / 'work'
            prefix = 'A-recovery-capture-A-attempt1-'
            folder = workspace / 'runs' / (prefix + '20260101T000000Z')
            folder.mkdir(parents=True)
            # The current test process guarantees a live PID without spawning an orphan.
            command = folder / 'command.json'
            command.write_text(json.dumps(dict(status='running', child_pid=os.getpid(), returncode=None)))
            for name in ('stdout.bin', 'stderr.bin'):
                (folder / name).touch()
            state_path = workspace / 'state.json'
            receipt = dict(stage='capture-A', skill='precision-compare', status='running', run_prefix=prefix,
                           timeout=5, wall_seconds=0, outputs={})
            original = json.dumps(dict(schema_version=1, id='recovery', root=str(root), history=[receipt]))
            state_path.write_text(original)
            snapshot = inspect_workflow(plan_path, workspace)
            self.assertEqual({row['stage'] for row in snapshot['runnable_actions']}, {'capture-B'})
            self.assertEqual(state_path.read_text(), original)
            self.assertEqual(snapshot['blocked_actions'][0]['child_pid'], os.getpid())

            for refreshed in ('capture-A', 'prepare-A'):
                with self.subTest(refresh=refreshed), self.assertRaisesRegex(ValueError, 'cannot refresh.*capture-A'):
                    run_workflow(plan_path, workspace, refresh=[refreshed], jobs=2)
            self.assertEqual(state_path.read_text(), original)

            plan['budget_seconds'] = 5
            plan_path.write_text(json.dumps(plan))
            budgeted = run_workflow(plan_path, workspace, jobs=2)
            self.assertEqual(budgeted['attempts'], 1)
            self.assertEqual({row['state'] for row in budgeted['blocked_actions'] if row['stage'] == 'capture-B'}, {'budget'})
            plan['budget_seconds'] = 30
            plan_path.write_text(json.dumps(plan))
            result = run_workflow(plan_path, workspace, jobs=2)
            self.assertEqual({row['id'] for row in result['stages']}, {'capture-B'})
            self.assertEqual(result['attempts'], 2)
            blocked = {row['stage']: row for row in result['blocked_actions']}
            self.assertEqual(blocked['prepare-A']['state'], 'dependency')
            self.assertIn('waiting for live dependents', blocked['prepare-A']['reason'])
            self.assertEqual(blocked['capture-A']['state'], 'observe')
            self.assertEqual(Path(blocked['capture-A']['run']), folder)
            self.assertEqual(blocked['compare']['state'], 'dependency')
            self.assertEqual(json.loads(command.read_text())['child_pid'], os.getpid())
            repeated = run_workflow(plan_path, workspace)
            self.assertEqual(repeated['attempts'], 2)
            self.assertTrue(repeated['stages'][0]['reused'])

    def test_recovers_terminal_outputs_before_unblocking_dependents(self):
        def stage(name, side, deps):
            return dict(id=name, side=side, skill='precision-compare', depends_on=deps, timeout=5,
                        outputs={'out': 'out'}, argv=[sys.executable, '-c',
                            'from pathlib import Path; import json, sys; Path(sys.argv[1]).write_text(json.dumps("verified"))',
                            '{run}/out'], checks=[dict(output='out', pointer='', equals='verified')])

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            plan_path, workspace = root / 'plan.json', root / 'work'
            first = stage('capture-A', 'A', [])
            plan = dict(schema_version=1, id='terminal', budget_seconds=30, stages=[first])
            plan_path.write_text(json.dumps(plan))
            completed = run_workflow(plan_path, workspace)['stages'][0]
            recorded_run = Path(completed['run'])
            state_path = workspace / 'state.json'
            state = json.loads(state_path.read_text())
            # Emulate a crash after record_run saved its verified terminal command
            # receipt, but before the workflow finished output validation.
            state['history'][0].update(status='running', outputs={}, wall_seconds=0)
            state_path.write_text(json.dumps(state))
            plan['stages'].append(stage('compare', 'compare', ['capture-A']))
            plan_path.write_text(json.dumps(plan))
            snapshot = inspect_workflow(plan_path, workspace)
            self.assertEqual(snapshot['runnable_actions'][0]['reason'], 'validate terminal outputs')
            resumed = run_workflow(plan_path, workspace)
            self.assertEqual(resumed['status'], 'completed', resumed)
            self.assertEqual(resumed['attempts'], 2)
            self.assertTrue(resumed['stages'][0]['reused'])
            self.assertEqual(Path(resumed['stages'][0]['run']), recorded_run)

    def test_default_plan_hashes_interpreted_comparison_and_acceptance_sources(self):
        for task in ('inference', 'sft', 'rl'):
            with self.subTest(task=task):
                by_id = stages(init_plan(task, ['A', 'B'], dtype='bf16'))
                for stage in by_id.values():
                    if not (stage['id'].startswith('compare-') or stage['id'] == 'acceptance'):
                        continue
                    self.assertIn(stage['argv'][1], stage['inputs'])
                    self.assertTrue(Path(stage['argv'][1]).is_file())
                argv = by_id['acceptance']['argv']
                self.assertEqual(argv[argv.index('--sides') + 1:argv.index('--sides') + 3], ['A', 'B'])
                self.assertEqual(argv[argv.index('--noise-for') + 1:argv.index('--noise-for') + 3],
                                 ['{artifact:compare-scores:report}', '{artifact:compare-scores-noise:report}'])

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            script = root / 'acceptance.py'
            script.write_text('from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text("first")\n')
            with patch('tools.workflow._compare_scripts', return_value=root):
                generated = init_plan('inference', ['A', 'B'], dtype='bf16')
            # Retain the generated interpreter argv and its inputs; isolate the cache
            # behavior from the numerical acceptance fixture and upstream artifacts.
            stage = stages(generated)['acceptance']
            stage.pop('acceptance_output')
            stage.update(depends_on=[], argv=stage['argv'][:2] + ['{run}/acceptance.json'])
            generated['stages'] = [stage]
            plan_path = root / 'plan.json'
            plan_path.write_text(json.dumps(generated))
            workspace = root / 'work'
            initial = run_workflow(plan_path, workspace)
            self.assertEqual(initial['attempts'], 1)
            self.assertTrue(run_workflow(plan_path, workspace)['stages'][0]['reused'])
            script.write_text(script.read_text().replace('first', 'second'))
            rerun = run_workflow(plan_path, workspace)
            self.assertEqual(rerun['attempts'], 2)
            self.assertFalse(rerun['stages'][0]['reused'])
            self.assertEqual(Path(rerun['stages'][0]['outputs']['acceptance']['path']).read_text(), 'second')
            self.assertEqual(Path(initial['stages'][0]['outputs']['acceptance']['path']).read_text(), 'first')


if __name__ == '__main__':
    unittest.main()
