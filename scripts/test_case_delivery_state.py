"""Offline contract tests for versioned case delivery checkpoints."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tools.case_state import template, validate
from tools.contribution_contract import receipt_binding


CLI = Path(__file__).resolve().parents[1] / 'skills' / 'precision-compare' / 'scripts' / 'case_state.py'
ACCEPTANCE = CLI.with_name('acceptance.py')
DESTINATION = 'https://numericbridge.com/v1/public-submissions'
REPAIR_GOAL = 'restore_original_acceptance'


def observed(verdict, report):
    return dict(verdict=verdict, verification='supported_native', evidence=[report])


def acceptance(root, name, objective, change=None, *, workload='f', dtype='bf16'):
    metrics = root / f'{name}-metrics.json'
    identity = root / f'{name}-tested.json'
    report = root / f'{name}.json'
    metrics.write_text(json.dumps(dict(kind='scalar-metrics', objective_pairs={'loss': [1, objective]})))
    identity.write_text(json.dumps(dict(workload_sha256=workload * 64, base_sha256='e' * 64, change_sha256=change)))
    exact = root / f'{name}-exact.json'
    exact.write_text(json.dumps(dict(kind='exact-checks', checks={'loss-mask': True}, missing=[])))
    subprocess.run([sys.executable, '-B', str(ACCEPTANCE), '--report', str(metrics), '--tested-identity', str(identity),
                    '--exact-report', str(exact), '--dtype', dtype, '--exit-zero', '--output', str(report)],
                   capture_output=True, check=True)
    return observed(json.loads(report.read_text())['verdict'], report.name)


def repair_request(goal=REPAIR_GOAL, *, defect_id=None):
    criterion = json.loads(subprocess.check_output([sys.executable, '-B', str(ACCEPTANCE), '--criterion-only']))
    request = dict(completion_goal=goal, original_contract=dict(workload_sha256='f' * 64, base_sha256='e' * 64, **criterion))
    if defect_id is not None:
        request['defect_id'] = defect_id
    return request


def focused_report(root, change, *, defect='fixture-defect'):
    before, after, report = (root / name for name in ('focused-before.log', 'focused-after.log', 'focused.json'))
    before.write_text('Specific assertion failed\n')
    after.write_text('Specific assertion passed\n')
    report.write_text(json.dumps(dict(kind='focused-verification', schema_version=1, defect_id=defect,
                                      base_sha256='e' * 64, change_sha256=change,
                                      before=dict(verdict='fail', evidence=before.name),
                                      after=dict(verdict='pass', evidence=after.name))))
    return report.name


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
    return dict(template(), status='done', stage='community_contribution', next_action=None,
                delivery='report.md', delivery_summary=summary, community_draft_sha256='6' * 64)


class CaseDeliveryTests(unittest.TestCase):
    def assertInvalid(self, state, message, **kwargs):
        result = validate(state, **kwargs)
        self.assertEqual(result['status'], 'invalid', result)
        self.assertTrue(any(message in p for p in result['problems']), result)

    def test_checkpoint_requires_the_current_delivery_summary_from_creation(self):
        state = template()
        self.assertEqual(state['delivery_summary']['original']['verdict'], 'not_run')
        self.assertEqual(state['delivery_summary']['delivery']['outcome'], 'partial')
        self.assertEqual(validate(state), dict(status='ok', problems=[]))
        state.pop('delivery_summary')
        self.assertInvalid(state, 'missing field: delivery_summary')
        state.update(status='done', next_action=None, delivery='report.md')
        self.assertInvalid(state, 'missing field: delivery_summary')

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

    def test_default_diagnosis_cannot_be_replaced_by_a_submission_receipt(self):
        state = dict(checkpoint(), failure_policy='diagnose')
        for status, next_action in (('done', None), ('in_progress', 'Prepare the draft')):
            state.update(status=status, next_action=next_action)
            self.assertInvalid(state, 'fail requires evidence of completed diagnosis', request={'failure_policy': 'diagnose'})
        state.update(stage='delivery', status='partial', next_action=None,
                     open_items=['No discriminating cause established'], resume_when='Native layer probes available')
        state['delivery_summary'].pop('community')
        state['delivery_summary']['delivery']['outcome'] = 'partial'
        self.assertEqual(validate(state, request={'failure_policy': 'diagnose'})['status'], 'ok')

    def test_confirmed_measure_only_is_allowed_but_cannot_replace_diagnose(self):
        state = dict(checkpoint(), failure_policy='measure_only')
        self.assertInvalid(state, 'requires an explicit community review request', request={'failure_policy': 'measure_only'})
        request = {'failure_policy': 'measure_only', 'community_contribution': {'review_requested': True}}
        self.assertEqual(validate(state, request=request)['status'], 'ok')
        self.assertInvalid(state, 'checkpoint failure_policy must match', request={'failure_policy': 'diagnose'})
        self.assertInvalid(dict(state, completion_goal=REPAIR_GOAL), 'measure_only is not compatible')
        self.assertInvalid(dict(state, failure_policy='guess'), 'failure_policy must be diagnose or measure_only')

    def test_diagnosed_failure_checks_local_evidence_not_just_a_reference(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request = {'failure_policy': 'diagnose'}
            state = dict(template(), status='done', stage='delivery', next_action=None,
                         failure_policy='diagnose', delivery='report.md')
            (root / 'report.md').write_text('Report')
            summary = state['delivery_summary']
            summary['original'] = acceptance(root, 'baseline', 2)
            self.assertEqual(summary['original']['verdict'], 'fail')
            delivery = summary['delivery'] = dict(outcome='bounded_attribution', evidence=['diagnosis.json'],
                                                  diagnosis_evidence='diagnosis.json')
            self.assertInvalid(state, 'cannot read diagnosis evidence', request=request, case_dir=root)
            diagnosis = {'kind': 'diagnosis', 'schema_version': 1, 'trigger': {'verdict': 'fail'},
                         'conclusion': {'classification': 'unresolved', 'summary': 'No cause established'},
                         'checks': [{'finding': 'Controlled operator A/B', 'evidence': ['probe.json']}]}
            saved = root / 'diagnosis.json'
            saved.write_text(json.dumps(diagnosis))
            self.assertInvalid(state, 'unresolved cases stay partial', request=request, case_dir=root)
            diagnosis['conclusion'].update(classification='expected_policy', summary='Confirmed different reduction policy')
            saved.write_text(json.dumps(diagnosis))
            self.assertEqual(validate(state, request=request, case_dir=root)['status'], 'ok')
            delivery['evidence'].clear()
            self.assertInvalid(state, 'listed in delivery.evidence', request=request, case_dir=root)

    def test_terminal_delivery_rejects_partial_outcome_and_active_jobs(self):
        state = dict(template(), status='done', stage='delivery', next_action=None,
                     delivery='report.md', running_jobs=[dict(host='a', job_or_pid='job-1', check_command='status job-1')])
        self.assertInvalid(state, 'outcome partial is not a done delivery')
        self.assertInvalid(state, 'case cannot be delivered with running jobs')

        state.update(status='partial', open_items=['Missing side B observations'])
        self.assertInvalid(state, 'partial case requires resume_when')
        state['resume_when'] = 'Side B target access is available'
        self.assertInvalid(state, 'case cannot be delivered with running jobs')
        state['running_jobs'].clear()
        self.assertEqual(validate(state, request={}), dict(status='ok', problems=[]))

    def test_pass_and_gap_deliver_without_diagnosis_or_optional_drafts(self):
        for verdict in ('pass', 'gap'):
            with self.subTest(verdict=verdict):
                state = dict(template(), status='done', stage='delivery', next_action=None,
                             delivery='report.md', incomplete_drafts=['optional-upstream-draft'])
                state['delivery_summary'].update(original=observed(verdict, 'evidence/acceptance.json'),
                                                 delivery=dict(outcome='acceptance_passed', evidence=['report.md']))
                self.assertEqual(validate(state, request={}), dict(status='ok', problems=[]))
                state['open_items'] = ['Promised verification of the pinned revision is missing']
                self.assertInvalid(state, 'local completion cannot have required open_items')
                state.update(status='partial', resume_when='Pinned revision is available')
                self.assertEqual(validate(state, request={}), dict(status='ok', problems=[]))
                self.assertEqual(state['delivery_summary']['original']['verdict'], verdict)

    def test_local_pass_can_deliver_report_without_ending_authorized_contribution(self):
        request = {'community_contribution': {'consent': 'share', 'attribution_mode': 'anonymous'}}
        state = dict(template(), status='done', stage='delivery', delivery='report.md',
                     next_action='Submit the reviewed draft')
        state['delivery_summary'].update(original=observed('pass', 'evidence/acceptance.json'),
                                         delivery=dict(outcome='acceptance_passed', evidence=['report.md']))
        self.assertIn('done requires next_action=null; finish or resolve the pending action first',
                      validate(state, request=request)['problems'])
        state['next_action'] = None
        self.assertIn('done with recorded share consent requires resolving the community_contribution stage',
                      validate(state, request=request)['problems'])
        self.assertEqual(validate(state, request={'community_contribution': {'consent': 'decline'}}),
                         dict(status='ok', problems=[]))

        state.update(status='in_progress', stage='community_contribution', next_action='Submit the reviewed draft')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, request_path = root / 'case-state.json', root / 'request.json'
            baseline_report = acceptance(root, 'accepted', 1)
            (root / state['delivery']).write_text('Local result: PASS.\n')
            state['delivery_summary']['original'] = baseline_report
            request_path.write_text(json.dumps(request))
            command = [sys.executable, '-B', str(CLI), 'check', '--state', str(state_path), '--request', str(request_path)]

            def check():
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertIn(result.returncode, (0, 1), result.stderr)
                return json.loads(result.stdout)

            state_path.write_text(json.dumps(state))
            self.assertEqual(check(), dict(status='ok', problems=[]))
            # A fresh invocation reads only the persisted checkpoint and request.
            resumed = json.loads(state_path.read_text())
            self.assertEqual(resumed['delivery'], 'report.md')
            digest = 'a' * 64
            resumed.update(status='done', next_action=None, community_draft_sha256=digest)
            summary = resumed['delivery_summary']
            summary.update(original=observed('pass', 'missing-acceptance.json'), community=[],
                           community_local_only=dict(draft_sha256=digest, reason='  '))
            resumed['delivery'] = 'missing-report.md'
            state_path.write_text(json.dumps(resumed))
            problems = check()['problems']
            self.assertIn('community completion requires a receipt for the current draft or a matching community_local_only reason', problems)
            self.assertTrue(any('missing or empty local file' in problem for problem in problems), problems)
            self.assertTrue(any('cannot read acceptance report' in problem for problem in problems), problems)
            resumed['delivery'] = state['delivery']
            summary['original'] = baseline_report
            summary['community_local_only']['reason'] = 'Intake definitively rejected the reviewed draft.'
            state_path.write_text(json.dumps(resumed))
            self.assertEqual(check(), dict(status='ok', problems=[]))

            summary['community_local_only']['draft_sha256'] = 'b' * 64
            state_path.write_text(json.dumps(resumed))
            self.assertEqual(check()['status'], 'invalid')
            del summary['community_local_only']
            draft = dict(schema_version=5, destination=DESTINATION, review_evidence={})
            old_digest = 'b' * 64
            summary['community'] = [dict(review_state='pending_review',
                                         receipt_binding=receipt_binding(draft, old_digest, f'submission-{old_digest}'))]
            state_path.write_text(json.dumps(resumed))
            self.assertEqual(check()['status'], 'invalid')
            summary['community'].append(dict(review_state='pending_review',
                                             receipt_binding=receipt_binding(draft, digest, f'submission-{digest}')))
            state_path.write_text(json.dumps(resumed))
            self.assertEqual(check(), dict(status='ok', problems=[]))
            self.assertEqual(summary['original']['verdict'], 'pass')

    def test_in_progress_needs_a_next_action_and_checkable_jobs(self):
        state = dict(template(), stage='comparison', next_action='',
                     running_jobs=[dict(host='', job_or_pid='', check_command='')])
        self.assertInvalid(state, 'in_progress requires a concrete next_action')
        self.assertInvalid(state, 'running_jobs entries need a non-empty host and check_command')
        state['running_jobs'] = [dict(host='worker-a', job_or_pid=182, check_command='ps -p 182')]
        self.assertInvalid(state, 'in_progress requires a concrete next_action')
        state['next_action'] = 'Reconcile process 182 before collecting side A outputs'
        self.assertEqual(validate(state, request={}), dict(status='ok', problems=[]))
        for field, value in (('host', '   '), ('job_or_pid', False), ('job_or_pid', 0), ('check_command', [])):
            with self.subTest(field=field, value=value):
                self.assertInvalid(dict(state, running_jobs=[dict(state['running_jobs'][0], **{field: value})]),
                                   'running_jobs entries need a non-empty host and check_command')

    def test_reproduced_native_failure_delivers_bounded_diagnosis_without_acceptance(self):
        state = dict(template(), status='done', stage='delivery', next_action=None, delivery='report.md')
        summary = state['delivery_summary']
        failure = dict(kind='native_failure', evidence='evidence/native-failure.json',
                       reproduction='evidence/native-reproduction.json')
        summary['original']['native_failure'] = failure
        summary['delivery'] = dict(outcome='bounded_attribution', evidence=['diagnosis.json'])
        before = deepcopy(state)
        self.assertEqual(validate(state, request={}), dict(status='ok', problems=[]))
        self.assertEqual(state, before)
        self.assertEqual(summary['original']['verdict'], 'not_run')
        self.assertEqual(summary['original']['evidence'], [])

        for change, error in (
            ({'reproduction': ' evidence/native-failure.json '}, 'requires separate first-failure and reproduction evidence'),
            ({'reproduction': ''}, 'requires separate first-failure and reproduction evidence'),
            ({'verdict': 'fail'}, 'must be a native_failure trigger without an acceptance verdict'),
        ):
            with self.subTest(change=change):
                summary['original']['native_failure'] = dict(failure, **change)
                self.assertInvalid(state, error)
        summary['original']['native_failure'] = failure
        summary['original'].update(observed('fail', 'evidence/numerical.json'))
        self.assertInvalid(state, 'native_failure requires acceptance verdict not_run')
        summary['original'].update(verdict='not_run', verification='not_run', evidence=[])
        state['completion_goal'] = REPAIR_GOAL
        self.assertInvalid(state, 'repair done requires an accepted original outcome')
        state.update(status='partial', open_items=['Native execution is not restored'], resume_when='A fixed runtime is available')
        self.assertEqual(validate(state, request={'completion_goal': REPAIR_GOAL}), dict(status='ok', problems=[]))

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

    def test_local_fix_with_worse_endpoint_is_not_a_completed_repair(self):
        state = checkpoint()
        state['completion_goal'] = REPAIR_GOAL
        request = {'completion_goal': REPAIR_GOAL}
        summary = state['delivery_summary']
        candidate = summary['candidates'][0]
        baseline = observed('fail', 'evidence/baseline.json')
        worse = observed('fail', 'evidence/candidate-a-native-worse.json')
        summary['original'] = dict(worse, baseline=baseline)
        candidate['original_workload'] = dict(worse, metrics=[dict(id='score-mae', before=0.2, after=0.3, unit='nat')])
        summary['delivery']['candidate_id'] = candidate['id']
        self.assertInvalid(state, 'repair done requires an accepted original outcome')
        self.assertInvalid(state, 'final candidate to match the accepted original verdict')

        state.update(status='in_progress', stage='repair',
                     next_action='Check whether the unpatched reduction masks the remaining divergence')
        self.assertEqual(validate(state, request=request), dict(status='ok', problems=[]))
        self.assertEqual(candidate['focused'], 'pass')
        self.assertEqual(candidate['original_workload']['verdict'], 'fail')
        lost_goal = {k: v for k, v in state.items() if k != 'completion_goal'}
        self.assertIn('checkpoint completion_goal must match the confirmed request',
                      validate(lost_goal, request=request)['problems'])

        state.update(status='partial', stage='delivery', open_items=['Original acceptance still fails'])
        self.assertInvalid(state, 'partial repair requires resume_when')
        state['resume_when'] = 'The matched-operands capture becomes available'
        self.assertEqual(validate(state, request=request), dict(status='ok', problems=[]))

    def test_repair_done_matches_final_combination_and_its_own_acceptance(self):
        state = checkpoint()
        state['completion_goal'] = REPAIR_GOAL
        summary = state['delivery_summary']
        request = repair_request()
        state['stage'] = 'delivery'
        del summary['community']
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report.md').write_text('Final combination meets acceptance.\n')
            focus = focused_report(root, '5' * 64)
            baseline = acceptance(root, 'baseline', 1.1)
            earlier = acceptance(root, 'old-candidate', 1.003, '4' * 64)
            accepted = acceptance(root, 'final-combination', 1.003, '5' * 64)
            summary['original'] = dict(accepted, baseline=baseline)
            summary['delivery'] = dict(outcome='acceptance_restored', evidence=['report.md'], candidate_id='combined-fix')
            final = dict(id='combined-fix', focused='pass', original_relation='verified_fix',
                         evidence=[focus], focused_report=focus, base_sha256='e' * 64,
                         change_sha256='5' * 64, original_workload=earlier)
            summary['candidates'].append(final)
            self.assertInvalid(state, 'requires the same original-workload evidence', case_dir=root)
            summary['original'] = dict(earlier, baseline=baseline)
            self.assertInvalid(state, 'does not match the base and change measured', case_dir=root)

            summary['original'] = dict(accepted, baseline=baseline)
            final['original_workload'] = accepted
            self.assertEqual(validate(state, request=request, case_dir=root), dict(status='ok', problems=[]))
            state_path, request_path = root / 'case-state.json', root / 'request.json'
            state_path.write_text(json.dumps(state))
            request_path.write_text(json.dumps(request))
            command = [sys.executable, '-B', str(CLI), 'check', '--state', str(state_path), '--request', str(request_path)]
            self.assertEqual(json.loads(subprocess.check_output(command)), dict(status='ok', problems=[]))
            state['running_jobs'] = [dict(host='host-a', job_or_pid='job-1', check_command='job-status job-1')]
            self.assertInvalid(state, 'repair cannot be delivered with running jobs', case_dir=root)
        state.update(status='in_progress', next_action='')
        self.assertInvalid(state, 'in_progress requires a concrete next_action even with running jobs')
        state['next_action'] = 'Reconcile job-1 before retrying capture'
        self.assertEqual(validate(state), dict(status='ok', problems=[]))

    def test_unchanged_acceptance_can_close_only_an_acceptance_only_goal(self):
        state = dict(template(), status='done', stage='delivery', next_action=None,
                     delivery='report.md', completion_goal=REPAIR_GOAL)
        request = repair_request()
        summary = state['delivery_summary']
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report.md').write_text('Matched acceptance retest.\n')
            baseline = acceptance(root, 'failed-baseline', 1.1)
            accepted = acceptance(root, 'unchanged-retest', 1.0)
            summary.update(original=dict(accepted), delivery=dict(outcome='acceptance_passed', evidence=['report.md']))
            self.assertEqual(validate(state, request=request, case_dir=root), dict(status='ok', problems=[]))
            summary['original']['baseline'] = baseline
            self.assertInvalid(state, 'use acceptance_retested', request=request, case_dir=root)
            summary['delivery']['outcome'] = 'acceptance_retested'
            self.assertEqual(validate(state, request=request, case_dir=root), dict(status='ok', problems=[]))

            for report, error in (
                (acceptance(root, 'changed', 1, '4' * 64), 'no change_sha256'),
                (acceptance(root, 'other-task', 1, workload='d'), 'match the current workload, base and criterion'),
                (acceptance(root, 'other-criterion', 1, dtype='fp32'), 'match the current workload, base and criterion'),
                (observed('pass', 'missing.json'), 'cannot read acceptance report'),
            ):
                with self.subTest(evidence=report['evidence']):
                    summary['original'] = dict(report, baseline=baseline)
                    self.assertInvalid(state, error, case_dir=root)
            summary['original'] = dict(accepted, baseline=baseline)
            defect_goal = 'repair_defect_and_restore_acceptance'
            self.assertInvalid(state, 'completion_goal must match', request={'completion_goal': defect_goal}, case_dir=root)
            state['completion_goal'] = defect_goal
            self.assertInvalid(state, 'requires delivery_summary.delivery.candidate_id', case_dir=root)

    def test_specified_defect_requires_matching_request_and_focused_before_after(self):
        goal, change = 'repair_defect_and_restore_acceptance', '5' * 64
        request = repair_request(goal, defect_id='loss-mask-defect')
        state = dict(template(), status='done', stage='delivery', next_action=None,
                     delivery='report.md', completion_goal=goal)
        summary = state['delivery_summary']
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report.md').write_text('Numerical checks pass; specified defect resolved.\n')
            baseline = acceptance(root, 'baseline', 1)
            wrong = acceptance(root, 'different-workload', 1, change, workload='d')
            accepted = acceptance(root, 'correct-workload', 1, change)
            focus = focused_report(root, change, defect='different-defect')
            candidate = dict(id='candidate', focused='pass', original_relation='verified_fix', evidence=[focus],
                             focused_report=focus, base_sha256='e' * 64, change_sha256=change, original_workload=wrong)
            summary.update(original=dict(wrong), candidates=[candidate], delivery=dict(outcome='acceptance_passed',
                           evidence=['report.md'], candidate_id='candidate'))
            result = validate(state, request=request, case_dir=root)
            for problem in ('confirmed original workload', 'requested defect', 'explicit acceptance or reproduced native failure baseline'):
                self.assertTrue(any(problem in value for value in result['problems']), result)
            summary['original'] = dict(accepted, baseline=baseline)
            candidate['original_workload'] = accepted
            focused_report(root, '4' * 64, defect='loss-mask-defect')
            self.assertInvalid(state, 'final tested base and change', request=request, case_dir=root)
            focused_report(root, change, defect='loss-mask-defect')
            self.assertEqual(validate(state, request=request, case_dir=root), dict(status='ok', problems=[]))

            # Recovered execution followed by the first valid numerical verdict.
            (root / 'initial.log').write_text('Original path crashed.\n')
            (root / 'reproduced.log').write_text('Same crash reproduced.\n')
            summary['original']['baseline'] = dict(verdict='not_run', verification='not_run', evidence=[],
                 native_failure=dict(kind='native_failure', evidence='initial.log', reproduction='reproduced.log'))
            self.assertEqual(validate(state, request=request, case_dir=root), dict(status='ok', problems=[]))

    def test_done_cannot_skip_delivery_stage(self):
        state = dict(template(), status='done', stage='comparison', next_action=None, delivery='report.md')
        state['delivery_summary'].update(original=observed('pass', 'acceptance.json'),
             delivery=dict(outcome='acceptance_passed', evidence=['report.md']))
        self.assertInvalid(state, 'done requires the delivery or community_contribution stage', request={})

    def test_required_partial_stays_local_even_with_share_consent(self):
        state = dict(template(), status='partial', stage='delivery', delivery='report.md',
                     open_items=['B missing'], resume_when='B returns', next_action=None)
        request = dict(community_contribution=dict(consent='share'))
        self.assertEqual(validate(state, request=request), dict(status='ok', problems=[]))
        state.update(stage='community_contribution', status='in_progress', next_action='Prepare draft')
        self.assertInvalid(state, 'finish required local work', request=request)

    def test_cli_resume_keeps_confirmed_goal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request, state = root / 'request.json', root / 'case-state.json'
            request.write_text(json.dumps({'completion_goal': REPAIR_GOAL}))
            state.write_text(json.dumps(template()))
            command = [sys.executable, '-B', str(CLI), 'check', '--state', str(state), '--request', str(request)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('checkpoint completion_goal must match the confirmed request', result.stdout)
            state.write_text(json.dumps(dict(template(), completion_goal=REPAIR_GOAL)))
            self.assertEqual(json.loads(subprocess.check_output(command)), dict(status='ok', problems=[]))

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
            ('candidates', None, 'candidates must be a list'),
            ('destination', 'https://github.com/example/project/pulls', 'must be a NumericBridge intake URL'),
        ):
            with self.subTest(key=key):
                state = checkpoint()
                state['delivery_summary']['community'][0]['receipt_binding'][key] = value
                self.assertInvalid(state, expected)

    def test_evaluation_only_receipt_binds_draft_without_a_patch(self):
        state = dict(template(), status='done', stage='community_contribution', next_action=None, delivery='report.md')
        draft = dict(schema_version=5, destination=DESTINATION, review_evidence={})
        digest = 'a' * 64
        state['community_draft_sha256'] = digest
        binding = receipt_binding(draft, digest, f'submission-{digest}')
        self.assertEqual(binding['candidates'], [])
        state['delivery_summary'].update(original=observed('pass', 'evidence/acceptance.json'),
                                         delivery=dict(outcome='acceptance_passed', evidence=['report.md']),
                                         community=[dict(review_state='pending_review', receipt_binding=binding)])
        self.assertEqual(validate(state, request={}), dict(status='ok', problems=[]))

    def test_outdated_receipt_cannot_be_counted_for_a_new_patch(self):
        state = checkpoint()
        before = deepcopy(state['delivery_summary']['community'])
        state['delivery_summary']['candidates'][0]['patch_sha256'] = 'f' * 64
        self.assertInvalid(state, 'must match the current candidate')
        self.assertEqual(state['delivery_summary']['community'], before)

    def test_unknown_schema_and_not_run_are_not_evidence(self):
        state = template()
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

    def test_cli_creates_and_checks_one_format_without_legacy_commands(self):
        def cli(command, target, *extra):
            return subprocess.run([sys.executable, '-B', str(CLI), command, '--state', str(target), *extra],
                                  text=True, capture_output=True, check=False)

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.json'
            created = cli('template', path)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(json.loads(path.read_text()), template())
            checked = cli('check', path)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout), dict(status='ok', problems=[]))

            state = checkpoint()
            state['delivery_summary']['original']['evidence'] = []
            path.write_text(json.dumps(state))
            rejected = cli('check', path)
            self.assertEqual(rejected.returncode, 1, rejected.stderr)
            self.assertEqual(json.loads(rejected.stdout)['status'], 'invalid')
            state.pop('delivery_summary')
            path.write_text(json.dumps(state))
            raw = path.read_bytes()
            request = Path(directory) / 'request.json'
            request.write_text('{}')
            for args in ((), ('--request', str(request))):
                rejected = cli('check', path, *args)
                self.assertEqual(rejected.returncode, 1, rejected.stderr)
                self.assertIn('missing field: delivery_summary', json.loads(rejected.stdout)['problems'])
            self.assertEqual(cli('archive-receipt', path).returncode, 2)
            self.assertEqual(cli('template', path, '--with-delivery-summary').returncode, 2)
            self.assertEqual(path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
