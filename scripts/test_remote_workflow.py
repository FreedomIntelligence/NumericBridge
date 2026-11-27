"""Synthetic durable-stage tests without SSH, accelerators or mutable cases."""
import json
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest

from tools.remote_stage import identity, profile, worker
from tools.workflow import bind_plan, inspect_workflow, run_workflow, watch_workflow
from tools.workflow_controller import start


REPO = Path(__file__).resolve().parents[1]


class RemoteWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workspace = self.root / 'state'
        self.spec = {'schema_version': 1, 'host': None, 'python': sys.executable, 'checkout': str(REPO),
                     'state_dir': str(self.root / 'worker'), 'timeout_seconds': 20,
                     'argv': [sys.executable, '-c', '''
from pathlib import Path
import sys, time
r, go, count = map(Path, sys.argv[1:])
with count.open('a') as f: f.write('one\\n')
for i in range(200):
    if go.exists(): break
    time.sleep(.05)
else: raise SystemExit(10)
(r / 'ready.json').write_text('{"verified_shards":16,"ready":true}')
''', '{run}', str(self.root / 'go'), str(self.root / 'A-count')],
                     'readiness': {'path': 'ready.json', 'equals': {'ready': True, 'verified_shards': 16}}}
        self.plan = self.root / 'plan.json'

    def make_plan(self):
        (self.root / 'remote.json').write_text(json.dumps(self.spec))
        remote = dict(id='download-A', skill='framework-adapt', side='A', timeout=10,
                      remote_profile='remote.json', outputs={'receipt': 'remote.json'},
                      checks=[{'output': 'receipt', 'pointer': '/status', 'equals': 'completed'}])
        def local(name, side, depends):
            code = ('from pathlib import Path; import sys; '
                    'Path(sys.argv[1]).open("a").write("ran\\n"); Path(sys.argv[2]).touch()')
            return dict(id=name, skill='precision-compare', timeout=5, side=side, depends_on=depends,
                        argv=[sys.executable, '-c', code, str(self.root / (name + '-count')), '{run}/ok.txt'],
                        outputs={'ok': 'ok.txt'})
        self.plan.write_text(json.dumps(dict(schema_version=1, id='synthetic', budget_seconds=60,
                                              stages=[remote, local('capture-B', 'B', []),
                                                      local('preflight-A', 'A', ['download-A'])])))

    def test_new_controller_reuses_live_remote_stage_and_finished_B(self):
        self.addCleanup((self.root / 'go').touch)
        self.make_plan()
        first = run_workflow(self.plan, self.workspace, jobs=2)
        self.assertEqual(first['next_action']['state'], 'observe_remote')
        self.assertFalse((self.root / 'preflight-A-count').exists())
        self.assertEqual((self.root / 'capture-B-count').read_text(), 'ran\n')
        self.assertEqual(inspect_workflow(self.plan, self.workspace)['next_action']['state'], 'observe_remote')
        with self.assertRaisesRegex(ValueError, 'cannot refresh'):
            run_workflow(self.plan, self.workspace, refresh=['download-A'])
        # Recreate the on-disk window left when a controller disappears after dispatch.
        state_path = self.workspace / 'state.json'
        state = json.loads(state_path.read_bytes())
        waiting = next(row for row in state['history'] if row['stage'] == 'download-A')
        waiting['status'] = 'running'
        state_path.write_text(json.dumps(state))
        (self.root / 'go').touch()
        report = watch_workflow(self.plan, self.workspace, seconds=8, interval=1, jobs=2)
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual((self.root / 'A-count').read_text(), 'one\n')
        self.assertEqual((self.root / 'preflight-A-count').read_text(), 'ran\n')
        self.assertEqual((self.root / 'capture-B-count').read_text(), 'ran\n')
        self.assertTrue(next(stage for stage in report['stages'] if stage['id'] == 'capture-B')['reused'])

    def test_detached_watch_survives_dispatcher_exit(self):
        self.addCleanup((self.root / 'go').touch)
        self.make_plan()
        result = subprocess.run([sys.executable, '-B', '-m', 'tools.workflow_controller', 'start',
                                 '--plan', str(self.plan), '--workspace', str(self.workspace),
                                 '--seconds', '10', '--interval', '1', '--jobs', '2'],
                                cwd=REPO, capture_output=True, check=True, timeout=5)
        launched = json.loads(result.stdout)
        self.assertEqual(launched['status'], 'controller_started')
        self.assertEqual(start(self.plan, self.workspace, seconds=10)['status'], 'controller_active')
        started = time.monotonic() + 6
        a_count, b_count = self.root / 'A-count', self.root / 'capture-B-count'
        while not (a_count.is_file() and b_count.is_file()) and time.monotonic() < started:
            time.sleep(.05)
        self.assertTrue(a_count.is_file() and b_count.is_file())
        self.assertEqual(worker(profile(self.spec), '0' * 64)['status'], 'unknown')
        self.assertFalse((self.root / 'preflight-A-count').exists())
        (self.root / 'go').touch()
        report = Path(launched['report'])
        limit = time.monotonic() + 10
        while not report.is_file() and time.monotonic() < limit:
            time.sleep(.05)
        self.assertEqual(json.loads(report.read_bytes())['status'], 'completed')
        self.assertEqual((self.root / 'A-count').read_text(), 'one\n')
        self.assertEqual((self.root / 'capture-B-count').read_text(), 'ran\n')
        self.assertEqual((self.root / 'preflight-A-count').read_text(), 'ran\n')

    def test_bind_does_not_execute_or_accept_placeholders(self):
        self.make_plan()
        original = json.loads(self.plan.read_bytes())
        command = original['stages'][1]['argv']
        original['stages'][1]['argv'] = ['TODO-capture-command']
        self.plan.write_text(json.dumps(original))
        bindings, output = self.root / 'bindings.json', self.root / 'bound.json'
        bindings.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'TODO'):
            bind_plan(self.plan, bindings, output)
        self.assertFalse(output.exists())
        bindings.write_text(json.dumps({'capture-B': {'argv': command}}))
        self.assertEqual(bind_plan(self.plan, bindings, output), output)
        self.assertFalse(self.workspace.exists())
        self.assertFalse((self.root / 'worker').exists())

    def test_unfenced_crash_is_unknown_not_a_retry(self):
        spec, key = profile(self.spec), 'a' * 64
        scope = self.root / 'worker'
        scope.mkdir(mode=0o700)
        lost = scope / identity(key, spec) / '1'
        lost.mkdir(parents=True)
        self.assertEqual(worker(spec, key)['status'], 'unknown')
        self.assertFalse((self.root / 'A-count').exists())

    def test_profile_rejects_ssh_argument_injection(self):
        with self.assertRaisesRegex(ValueError, 'Invalid SSH host'):
            profile(dict(self.spec, host='-oProxyCommand=echo'))


if __name__ == '__main__':
    unittest.main()
