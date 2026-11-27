"""Follow the nine-stage workflow on a CPU fixture with an in-process inbox."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree

import numpy as np
from starlette.testclient import TestClient

from community.intake import Inbox, create_app
from community.records.catalog import Catalog
from community.records.submission import Submissions, encode
from community.contribution_input import compile_contribution
from community.test_contribution_input import PUBLIC, TOKEN, git, git_fixture, packet, snapshot
from tools.contribution_contract import receipt_binding
from tools.operator_optimization import scaffold
from tools.workflow import init_plan, run_workflow, inspect_workflow


SKILLS = Path(__file__).resolve().parents[1] / 'skills'
COMPARE = SKILLS / 'precision-compare/scripts'
REPAIR = SKILLS / 'precision-repair/scripts'
OPTIMIZE = SKILLS / 'operator-optimize/scripts'
ADAPTER = '''
import json, os, sys
from hashlib import sha256
from pathlib import Path
import numpy as np

get = lambda p: json.loads(Path(p).read_text())
def write(p, value):
    Path(p).write_text(json.dumps(value, allow_nan=False))

action, *args = sys.argv[1:]
if action == 'exact':
    a, b, output = args
    targets = lambda p: [{k: row[k] for k in ('sample', 'position', 'token')} for row in get(p)['scores']]
    write(output, dict(kind='exact-checks', checks={'selected-targets': targets(a) == targets(b)}, missing=[]))
else:
    side, settings, output, receipt = args
    output = Path(output)
    output.mkdir()
    spec, fields = get('workload.json'), get(settings)
    assert os.environ['NB_FIXTURE_DEVICE'] == 'cpu'
    x = np.asarray(spec['inputs']['operands'], dtype=np.float32)
    w = np.asarray(spec['state']['weight'], dtype=np.float32)
    sign = fields['sign']
    # Native objective is softplus(-sign * dot(w, x)); differentiate analytically on CPU.
    z = sign * float(np.dot(w, x))
    loss = float(np.logaddexp(0, -z))
    np.save(output / 'grad.npy', (-sign * x / (1 + np.exp(z))).astype(np.float32))
    write(output / 'environment.json', dict(rank=0, versions={'numpy': np.__version__}, actual=spec))
    write(output / 'status.json', dict(rank=0, status='completed'))
    manifest = dict(side=side, common=spec, expected_ranks=[0],
                    ranks=[dict(rank=0, environment='environment.json', status='status.json')])
    write(output / 'scores.json', dict(manifest, kind='target-scores', schema_version=1,
          scores=[dict(sample='item', position=0, token=7, logprob=-loss)]))
    write(output / 'tensors.json', dict(manifest, kind='tensors', schema_version=2, stage='gradients',
          tensors=[dict(name='weight', shape=[2], present=True, shards=[dict(offset=0, value={'path':'grad.npy'})])]))
    digest = lambda p: sha256(Path(p).read_bytes()).hexdigest()
    write(output / 'identity.json', dict(workload_sha256=digest('workload.json'), base_sha256=digest('base.json'),
                    change_sha256=None if fields['stock'] else digest(settings)))
    write(receipt, dict(ready=True))
'''


class WorkflowEndToEndTests(unittest.TestCase):
    def test_cpu_fail_diagnose_repair_and_share_evaluation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            visited = []

            def json_file(name, data):
                path = root / name
                path.parent.mkdir(exist_ok=True, parents=True)
                with path.open('x') as stream:
                    json.dump(data, stream, allow_nan=False)
                return path

            def tool(path, *args, code=0):
                proc = subprocess.run([sys.executable, '-B', str(path), *map(str, args)], cwd=root,
                                      text=True, capture_output=True, timeout=30)
                self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
                return proc.stdout

            case = root / 'case-state.json'
            tool(COMPARE / 'case_state.py', 'template', '--state', case)
            state = json.loads(case.read_text())
            base = json_file('base.json', dict(runtime='fixture-numpy-on-cpu', adapter='cpu-v1'))
            workload = json_file('workload.json', dict(model='fixture-linear', inputs={'operands': [1, 2]},
                                        numerics={'dtype':'fp32'}, scope='sft-gradients', state={'weight':[0.5, 0.5]}))
            criterion = json.loads(tool(COMPARE / 'acceptance.py', '--criterion-only', '--dtype', 'fp32'))
            goal = 'repair_defect_and_restore_acceptance'
            request = json_file('request.json', dict(completion_goal=goal, defect_id='gradient-sign',
                        original_contract=dict(criterion, base_sha256=sha256(base.read_bytes()).hexdigest(),
                                               workload_sha256=sha256(workload.read_bytes()).hexdigest()),
                        community_contribution={'consent':'share'}))
            state['completion_goal'] = goal

            def checkpoint(stage, *, done=False, code=0):
                if not visited or stage != visited[-1]:
                    visited.append(stage)
                state.update(stage=stage, status='done' if done else 'in_progress',
                             next_action=None if done else f'Continue {stage}')
                case.write_text(json.dumps(state))
                return json.loads(tool(COMPARE / 'case_state.py', 'check', '--state', case, '--request', request, code=code))

            checkpoint('request')
            checkpoint('community_lookup')
            site = root / 'catalog'
            snapshot(site)
            query = dict(hardware='CPU', library='Fixture engine', version='1.0', model='Synthetic model', task='sft')
            self.assertEqual(Catalog(site).search(query)['status'], 'no-records')

            checkpoint('environment')
            adapter = root / 'cpu.py'
            adapter.write_text(ADAPTER)
            for side in 'AB':
                json_file(f'environments/{side}.json', dict(env={'NB_FIXTURE_DEVICE':'cpu'}))
            stock = {'A': json_file('stock-a.json', dict(stock=True, sign=1)),
                     'B': json_file('stock-b.json', dict(stock=True, sign=-1))}
            common = dict(thresholds=None, common=json.loads(workload.read_text()),
                          expected_ranks={'A':[0], 'B':[0], 'A-repeat':[0]})
            json_file('contracts/scores.json', dict(common, schema_version=1, kind='target-scores',
                                                    targets=[dict(sample='item', position=0, token=7)]))
            json_file('contracts/gradients.json', dict(common, schema_version=2, kind='tensors', stage='gradients',
                                                       tensors=[dict(name='weight', shape=[2], present=True)]))

            def plan_for(name, side_b):
                plan = init_plan('sft', ['A', 'B'], dtype='fp32')
                for stage in plan['stages']:
                    stage['timeout'] = 20
                    if stage['id'].startswith('capture-'):
                        side = stage['id'].removeprefix('capture-')
                        settings = side_b if side == 'B' else stock['A']
                        stage['argv'] = [sys.executable, str(adapter), 'capture', side, str(settings),
                                         '{run}/capture', '{run}/receipt.json']
                        stage['inputs'] = [adapter.name, settings.name, workload.name, base.name]
                        stage['outputs']['identity'] = 'capture/identity.json'
                    if stage['id'] == 'check-exact':
                        stage['argv'] = [sys.executable, str(adapter), 'exact', '{artifact:capture-A:scores}',
                                         '{artifact:capture-B:scores}', '{run}/report.json']
                        stage['inputs'] = [adapter.name]
                    if stage['id'] == 'acceptance':
                        stage['depends_on'].append('capture-B')
                        stage['argv'] += ['--tested-identity', '{artifact:capture-B:identity}']
                return json_file(name, plan)

            checkpoint('comparison')
            workspace = root / 'plan-work'
            baseline_plan = plan_for('baseline-plan.json', stock['B'])
            baseline_run = run_workflow(baseline_plan, workspace, jobs=2)
            self.assertEqual(baseline_run['status'], 'needs_action', baseline_run)
            self.assertEqual(baseline_run['next_action']['route'], 'diagnose')
            baseline = Path(baseline_run['next_action']['outputs']['acceptance']['path'])
            self.assertEqual(inspect_workflow(baseline_plan, workspace)['next_action']['outputs']['acceptance']['path'], str(baseline))
            baseline_bytes = baseline.read_bytes()

            checkpoint('diagnosis')
            diagnosis = REPAIR / 'diagnosis_state.py'
            args = ('--state', 'diagnosis.json')
            tool(diagnosis, 'init', *args, '--acceptance', baseline)
            h = tool(diagnosis, 'add', *args, '--statement', 'A sign error reverses the gradient.',
                     '--check', 'Compare native signed gradient to the reference.', '--cost', 'cpu').strip()
            self.assertEqual(json.loads(tool(diagnosis, 'next', *args))['action'], 'check')
            baseline_stages = {row['id']: row for row in baseline_run['stages']}
            before = Path(baseline_stages['compare-gradients']['outputs']['report']['path'])
            self.assertLess(json.loads(before.read_text())['global']['cosine']['value'], -0.99)
            tool(diagnosis, 'record', *args, '--hypothesis', h, '--label', 'primary_cause_confirmed',
                 '--finding', 'Sign reversal against the analytic objective.', '--evidence', before)
            self.assertEqual(json.loads(tool(diagnosis, 'next', *args))['action'], 'repair')

            checkpoint('repair')
            changed = json_file('fix-sign.json', dict(stock=False, sign=1))
            fixed_plan = plan_for('fixed-plan.json', changed)
            fixed = run_workflow(fixed_plan, workspace, jobs=2)
            self.assertEqual(fixed['status'], 'completed', fixed)
            results = {row['id']: row for row in fixed['stages']}
            accepted = Path(results['acceptance']['outputs']['acceptance']['path'])
            self.assertEqual(results['acceptance']['verdict'], 'pass')
            self.assertEqual(baseline.read_bytes(), baseline_bytes)
            self.assertEqual(results['capture-A']['run'], baseline_stages['capture-A']['run'])
            after = Path(results['compare-gradients']['outputs']['report']['path'])
            self.assertGreater(json.loads(after.read_text())['global']['cosine']['value'], 0.999)
            focus = json_file('focused.json', dict(kind='focused-verification', schema_version=1,
                 defect_id='gradient-sign', base_sha256=sha256(base.read_bytes()).hexdigest(),
                 change_sha256=sha256(changed.read_bytes()).hexdigest(),
                 before=dict(verdict='fail', evidence=str(before)), after=dict(verdict='pass', evidence=str(after))))

            checkpoint('operator_optimization')
            def grad(stage):
                return str(Path(stage['outputs']['packet']['path']) / 'grad.npy')
            x = np.array(json.loads(workload.read_text())['inputs']['operands'], dtype=np.float64)
            w = np.array(json.loads(workload.read_text())['state']['weight'], dtype=np.float64)
            np.save(root / 'fp64.npy', -x / (1 + np.exp(np.dot(w, x))))
            spec = json_file('operator.json', dict(operator='signed-gradient', dtype='fp32', reduction=2,
                cases=[dict(id='captured', shape_class='captured', reference='fp64.npy',
                 variants={'baseline':grad(baseline_stages['capture-B']), 'fixed':grad(results['capture-B'])})]))
            tool(OPTIMIZE / 'kernel_check.py', '--spec', spec, '--output', 'kernel-check.json')
            measured = json.loads((root / 'kernel-check.json').read_text())
            self.assertFalse(measured['variants']['baseline']['within_bound'])
            self.assertTrue(measured['variants']['fixed']['within_bound'])
            ledger = scaffold(dict(issue=h, operator='signed-gradient', reading='b_deviates', entry='required'),
                              kernel_report=measured)
            self.assertEqual(ledger['round_status'], 'handoff_open')
            json_file('operator-ledger.json', ledger)

            tool(diagnosis, 'decide', *args, '--hypothesis', h, '--ownership', 'adapter',
                 '--decision', 'record_only', '--reason', 'adapter_only')
            tool(diagnosis, 'conclude', *args, '--classification', 'supported_defect', '--summary', 'Sign fixed.')
            tool(diagnosis, 'audit', *args)
            tool(diagnosis, 'decisions', *args, '--output', 'contribution-decisions.json')
            (root / 'report.md').write_text('CPU SFT comparison: original FAIL, corrected PASS. Operator coverage remains open.\n')
            original = dict(verdict='pass', verification='supported_native', evidence=[str(accepted)])
            candidate = dict(id='fix-sign', focused='pass', focused_report=focus.name, evidence=[focus.name],
                             original_relation='verified_fix', original_workload=original,
                             base_sha256=sha256(base.read_bytes()).hexdigest(), change_sha256=sha256(changed.read_bytes()).hexdigest())
            state.update(delivery='report.md', delivery_summary=dict(schema_version=1, candidates=[candidate],
                  original=dict(original, baseline=dict(verdict='fail', verification='supported_native', evidence=[str(baseline)])),
                  delivery=dict(outcome='acceptance_restored', candidate_id='fix-sign', evidence=['report.md', 'contribution-decisions.json'])))
            checkpoint('delivery')
            checkpoint('community_contribution')

            code = root / 'git-fixture'
            commit = git_fixture(code)
            git(code, 'update-ref', 'refs/remotes/origin/main', commit)
            drafts = Submissions(code, root / 'drafts', destination=PUBLIC, catalog_site=site)
            public = packet(commit)
            evaluation = public['items'][0]
            public.update(id='cpu-repair', title='CPU repair verification', items=[evaluation])
            score_report = json.loads(Path(results['compare-scores']['outputs']['report']['path']).read_text())
            final_report = json.loads(accepted.read_text())
            evaluation.update(task='sft', workload='Compare the CPU gradients for a fixed signed objective.',
                metrics=[dict(id='target-logprob-mae', value=score_report['metrics']['mae_nat'], unit='nat')],
                coverage=dict(observed=1, expected=1, unit='target'),
                acceptance=dict(verdict=final_report['verdict'], policy=final_report['profile'], criterion='Paired FP32 SFT.'))
            additions, review = compile_contribution(public, {'mode':'anonymous'})
            prepared = drafts.prepare(public['title'], additions, review_evidence=review)
            digest = prepared['draft_sha256']
            state['community_draft_sha256'] = digest
            missing = checkpoint('community_contribution', done=True, code=1)
            self.assertTrue(any('receipt for the current draft' in p for p in missing['problems']), missing)
            reviewed = drafts.reviewed(prepared['draft_id'], digest)
            self.assertEqual(reviewed['schema_version'], 5)
            self.assertEqual(reviewed['review_evidence'], {})
            with TestClient(create_app(Inbox(code, root / 'inbox', catalog_site=site), TOKEN,
                                       public_url=PUBLIC, public_status_key='fixture-status-key')) as app:
                response = app.post('/v1/public-submissions', content=encode(reviewed, compact=True),
                                    headers={'content-type':'application/json', 'x-draft-sha256':digest})
            self.assertEqual(response.status_code, 201, response.text)
            receipt = response.json()
            self.assertEqual((receipt['status'], receipt['review_state']), ('received', 'pending_review'))
            binding = receipt_binding(reviewed, digest, receipt['receipt_id'])
            self.assertEqual(binding['candidates'], [])
            state['delivery_summary']['community'] = [dict(review_state=receipt['review_state'], receipt_binding=binding)]
            checkpoint('community_contribution', done=True)
            self.assertEqual(Catalog(site).search(query)['status'], 'no-records')

            # Perturb a disposable captured array after closure, leaving its manifest
            # untouched. The plan must detect the changed packet without executing.
            np.save(grad(results['capture-B']), np.array([0, 0], dtype=np.float32))
            damaged = inspect_workflow(fixed_plan, workspace)['next_action']
            self.assertEqual((damaged['stage'], damaged['reason']), ('capture-B', 'cached outputs unavailable'))

            # The integration must exercise each published stage, in order.
            source = (SKILLS / 'numericbridge/references/workflow.md').read_text()
            snippets = source.split('```xml\n')[1:]
            stage_ids = [stage.attrib['id'] for snippet in snippets
                         for stage in ElementTree.fromstring('<workflow>' + snippet.split('```', 1)[0] + '</workflow>').findall('stage')]
            self.assertEqual(visited, stage_ids)


if __name__ == '__main__':
    unittest.main()
