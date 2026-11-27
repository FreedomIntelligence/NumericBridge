"""Offline regression across synthetic serving and SFT diagnostic rounds."""

from copy import deepcopy
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


rounds = import_module('skills.precision-repair.scripts.diagnostic_round')
diagnosis = import_module('skills.precision-repair.scripts.diagnosis_state')


def digest(value):
    return sha256(value.encode()).hexdigest()


def probe(task='inference', *, untapped='same', tapped='same'):
    stock = dict(side='B', run_id=f'{task}-stock', workload_sha256=digest(task+' request'),
                 runtime_sha256=digest('synthetic environment'), schedule_sha256=digest('actual batch order'),
                 coordinates_sha256=digest(task+' coordinates'), endpoint_sha256=digest(untapped),
                 expected=24, observed=24, evidence=f'{task}-stock.json')
    return dict(schema_version=1, untapped=stock, tapped=stock | dict(
        run_id=f'{task}-tap', evidence=f'{task}-tapped.json', endpoint_sha256=digest(tapped)))


def check(id_, kind='offline', *, estimate=0, phase='planned', **extra):
    return dict(id=id_, fingerprint=digest(id_), kind=kind, phase=phase,
                question='Does the measured boundary account for the difference?',
                on_match='Narrow to this boundary', on_mismatch='Investigate its incoming state',
                estimated_accelerator_seconds=estimate) | extra


def ledger(*checks, cap=120, count=4):
    return dict(schema_version=1, trigger=dict(verdict='fail', evidence='acceptance.json'),
                accelerator_seconds_limit=cap, check_limit=count, checks=list(checks))


class DiagnosticRoundTests(unittest.TestCase):
    def test_native_fault_enters_triage_state_and_round_without_acceptance(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = Path(rounds.__file__).resolve().parent

            def invoke(script, *args):
                return subprocess.check_output([sys.executable, '-IB', str(scripts / (script + '.py')),
                                                *args], cwd=root, text=True)

            trigger = dict(kind='native_failure', evidence='original.json', reproduction='repeat.json')
            for ref in (trigger['evidence'], trigger['reproduction']):
                (root / ref).write_text(json.dumps(dict(failure_class='index_out_of_bounds',
                                                      evidence='index 24 outside capacity 24')))
            (root / 'trigger.json').write_text(json.dumps(trigger))
            invoke('triage', '--report', 'original.json', '--output', 'triage.json')
            init = ['init', '--state', 'diagnosis.json', '--triage', 'triage.json']
            first = json.loads(invoke('diagnosis_state', *init, '--native-failure', 'trigger.json'))
            self.assertEqual(first['action'], 'check')
            self.assertEqual(first['hypothesis']['statement'], 'index or capacity overflow')
            state = json.loads((root / 'diagnosis.json').read_text())
            self.assertEqual(state['trigger'], trigger)
            rendered = invoke('diagnosis_state', 'render', '--state', 'diagnosis.json')
            self.assertIn('native failure (not an acceptance verdict)', rendered)
            self.assertIn('[reproduction](repeat.json)', rendered)

            plan = ledger(check('check-index', phase='finished', outcome='match',
                                actual_accelerator_seconds=0, evidence=['repeat.json']), count=1)
            plan['trigger'] = state['trigger']
            plan['bounded_delivery'] = dict(established='The same capacity fault reproduces.',
                                            not_established='A supported native pass.',
                                            reopen_when='A verified change to the capacity model.',
                                            evidence=['repeat.json'])
            (root / 'round.json').write_text(json.dumps(plan))
            result = json.loads(invoke('diagnostic_round', 'round', '--input', 'round.json'))
            self.assertEqual(result['action'], 'deliver_bounded')
            self.assertEqual(result['trigger'], trigger)
            self.assertIsNone(result['original_verdict'])

    def test_native_trigger_requires_reproduction_and_cannot_carry_a_verdict(self):
        state_tool = import_module('skills.precision-repair.scripts.diagnosis_state')
        original = dict(kind='native_failure', evidence='original.json')
        for trigger in (original, original | {'reproduction': 'original.json'},
                        original | {'reproduction': 'repeat.json', 'verdict': 'fail'}):
            with self.subTest(trigger=trigger), TemporaryDirectory() as directory:
                plan = ledger()
                plan['trigger'] = trigger
                with self.assertRaisesRegex(ValueError, 'reproduction'):
                    rounds.next_action(plan)
                with self.assertRaisesRegex(ValueError, 'reproduction'):
                    state_tool.init(Path(directory) / 'state.json', native_failure=trigger)


    def test_inference_reuses_negative_and_delivers_with_original_fail(self):
        no_op = probe()
        verified = check('opaque-tap', 'probe', estimate=50, phase='finished', actual_accelerator_seconds=48,
                         outcome='mismatch', evidence=['matched-operators.json'], probe_control=no_op)
        repeat = check('copy-of-opaque-tap', 'probe', estimate=50, fingerprint=verified['fingerprint'])
        expensive = check('another-complete-native-run', 'native', estimate=115)
        offline = check('review-known-controls', depends_on=['copy-of-opaque-tap'])
        plan = ledger(verified, repeat, expensive, offline)
        next_ = rounds.next_action(plan)
        self.assertEqual((next_['action'], next_['check_id']), ('run_check', 'review-known-controls'))
        self.assertEqual(next_['reused'], [{'id':'copy-of-opaque-tap', 'source':'opaque-tap'}])
        self.assertEqual(next_['settled'][0]['decision'], verified['on_mismatch'])
        offline.update(phase='finished', outcome='mismatch', actual_accelerator_seconds=0,
                       evidence=['local-vs-endpoint.json'])
        plan['bounded_delivery'] = dict(established='Local divergence reproduces with matched input.',
                                        not_established='No supported end-to-end native pass.',
                                        reopen_when='A production candidate changes the measured boundary.',
                                        evidence=['matched-operators.json', 'local-vs-endpoint.json'])
        report = rounds.next_action(plan)
        self.assertEqual(report['action'], 'deliver_bounded')
        self.assertEqual(report['original_verdict'], 'fail')
        self.assertEqual(report['deferred'], [{'id':'another-complete-native-run','reason':'budget'}])

    def test_sft_gradients_require_matching_batches_coordinates_and_endpoints(self):
        cpu = check('cpu-gradient-hook', 'probe', resource='cpu')
        self.assertEqual(rounds.next_action(ledger(cpu, cap=0))['check_id'], 'cpu-gradient-hook')
        self.assertTrue(rounds.probe_control(probe('sft'))['usable_for_attribution'])
        wrong = probe('sft', tapped='gradient changed under hooks')
        wrong['tapped'].update(schedule_sha256=digest('repacked microbatches'),
                               coordinates_sha256=digest('selected different gradient coordinates'), observed=23)
        self.assertEqual(rounds.probe_control(wrong)['reasons'], [
            'schedule_sha256_mismatch', 'coordinates_sha256_mismatch', 'incomplete_coverage', 'endpoint_changed'])
        reused_run = probe('sft')
        reused_run['tapped']['run_id'] = reused_run['untapped']['run_id']
        self.assertEqual(rounds.probe_control(reused_run)['reasons'], ['not_independent_runs'])
        finished = check('gradient-hook', 'probe', estimate=100, phase='finished', actual_accelerator_seconds=121,
                         outcome='match', evidence=['gradient-comparison.json'], probe_control=wrong)
        plan = ledger(finished, check('native-repeat', 'native', estimate=60))
        result = rounds.next_action(plan)
        self.assertEqual(result['action'], 'summarize')
        self.assertIsNone(result['settled'][0]['decision'])
        self.assertEqual(result['budget']['accelerator_seconds_spent'], 121)
        plan['bounded_delivery'] = dict(established='Claim unsupported by the tap.', not_established='Cause.',
                                        reopen_when='Tap is transparent.', evidence=['gradient-comparison.json'])
        with self.assertRaisesRegex(ValueError, 'unverified probe'):
            rounds.next_action(plan)

    def test_resume_reconciles_job_and_advances_only_independent_work(self):
        job = check('active-native', 'native', phase='running', estimate=100, job_receipt='native-job.json')
        follow = check('after-native', depends_on=['active-native'])
        another = check('another-native', 'native', estimate=30)
        offline = check('offline-independent')
        repeated = check('duplicate-active', 'native', estimate=100, fingerprint=job['fingerprint'])
        result = rounds.next_action(ledger(job, follow, another, offline, repeated))
        self.assertEqual(result['action'], 'reconcile_running')
        self.assertEqual(result['jobs'], [{'id':'active-native', 'job_receipt':'native-job.json'}])
        self.assertEqual(result['independent_check'], 'offline-independent')
        self.assertEqual(result['budget']['accelerator_seconds_reserved'], 100)
        self.assertEqual({row['id']:row['reason'] for row in result['deferred']},
                         {'after-native':'pending_dependency', 'another-native':'budget',
                          'duplicate-active':'pending_dependency'})

    def test_reject_same_decisions_and_recover_failed_capture(self):
        unchanged = check('undiscriminating', on_mismatch='Narrow to this boundary')
        with self.assertRaisesRegex(ValueError, 'same decision'):
            rounds.next_action(ledger(unchanged))
        crash = check('failed-capture', 'probe', estimate=10, phase='finished', actual_accelerator_seconds=9,
                      outcome='inconclusive', evidence=['native-terminal-status.json'])
        evidence = rounds.next_action(ledger(crash))['settled'][0]
        self.assertEqual(evidence['probe_control']['reasons'], ['no_probe_receipts'])
        self.assertIsNone(evidence['decision'])
        copy = deepcopy(crash) | {'id':'duplicate-execution'}
        with self.assertRaisesRegex(ValueError, 'duplicate execution'):
            rounds.next_action(ledger(crash, copy))

    def test_next_ignores_ruled_out_configuration_and_keeps_audit_history(self):
        with TemporaryDirectory() as directory:
            state = diagnosis.init(Path(directory) / 'diagnosis.json', {'verdict': 'fail'})
            config = diagnosis.add(state, 'The setup differs', 'Compare configuration')
            residual = diagnosis.add(state, 'Residual numerical difference', 'Compare matched operands')
            diagnosis.record(state, config, 'config_mismatch', 'Excluded by the matched setup', status='ruled_out')
            result = diagnosis.next_action(state)
            self.assertEqual((result['action'], result['hypothesis']['id']), ('check', residual))
            self.assertEqual(state['checks'][0]['label'], 'config_mismatch')

    def test_next_routes_active_configuration_and_resumes_after_correction(self):
        with TemporaryDirectory() as directory:
            state = diagnosis.init(Path(directory) / 'diagnosis.json', {'verdict': 'fail'})
            config = diagnosis.add(state, 'The setup differs', 'Compare configuration')
            residual = diagnosis.add(state, 'Residual numerical difference', 'Compare matched operands')
            with self.assertRaisesRegex(ValueError, 'active config_mismatch'):
                diagnosis.record(state, residual, 'config_corrected', 'Unrelated configuration change', ['other.json'])
            diagnosis.record(state, config, 'config_mismatch', 'Setup differs', ['old-settings.json'])
            result = diagnosis.next_action(state)
            self.assertEqual((result['action'], result['hypotheses']), ('recompare', [config]))
            with self.assertRaisesRegex(ValueError, 'evidence'):
                diagnosis.record(state, config, 'config_corrected', 'Matched after retry')
            diagnosis.record(state, config, 'config_corrected', 'Comparable rerun still differs', ['comparable-rerun.json'])
            result = diagnosis.next_action(state)
            self.assertEqual((result['action'], result['hypothesis']['id']), ('check', residual))
            self.assertEqual([check['label'] for check in state['checks']], ['config_mismatch', 'config_corrected'])

    def test_next_checks_or_refines_residual_before_handing_off_confirmed_primary(self):
        with TemporaryDirectory() as directory:
            state = diagnosis.init(Path(directory) / 'diagnosis.json', {'verdict': 'fail'}, max_checks=3)
            primary = diagnosis.add(state, 'Main cause', 'Test the controlled intervention')
            residual = diagnosis.add(state, 'Residual cause', 'Check remaining failing metric')
            diagnosis.record(state, primary, 'primary_cause_confirmed', 'One cause reproduced', ['intervention.json'])
            result = diagnosis.next_action(state)
            self.assertEqual((result['action'], result['hypothesis']['id']), ('check', residual))
            self.assertEqual(result['confirmed_primary'], [primary])
            diagnosis.record(state, residual, 'local_source_candidate', 'Narrow this interval', ['matched-operands.json'])
            result = diagnosis.next_action(state)
            self.assertEqual(result['action'], 'refine')
            self.assertEqual([h['id'] for h in result['supported']], [residual])
            diagnosis.record(state, residual, 'unresolved', 'No actionable control remains', blocker='budget')
            result = diagnosis.next_action(state)
            self.assertEqual((result['action'], result['hypotheses']), ('repair', [primary]))
            self.assertEqual(state['trigger']['verdict'], 'fail')
            self.assertIn('check budget spent', result['reason'])

    def test_primary_label_alone_is_not_a_repair_handoff(self):
        with TemporaryDirectory() as directory:
            state = diagnosis.init(Path(directory) / 'diagnosis.json', {'verdict': 'fail'})
            rejected = diagnosis.add(state, 'Suspected cause', 'Discriminate cause')
            diagnosis.record(state, rejected, 'primary_cause_confirmed', 'Control ruled out the cause',
                             status='ruled_out')
            self.assertEqual(diagnosis.next_action(state)['action'], 'stop')

    def test_probe_without_attribution_does_not_unlock_or_reuse_but_allows_fault_diagnosis(self):
        unusable = (
            dict(outcome='inconclusive'),
            dict(outcome='inconclusive', probe_control=probe()),
            dict(outcome='match', probe_control=probe(tapped='changed endpoint')),
        )
        for outcome in unusable:
            with self.subTest(outcome=outcome):
                tap = check('bad-tap', 'probe', phase='finished', estimate=15, actual_accelerator_seconds=12,
                            evidence=['tap-result.json'], **outcome)
                alias = check('same-method', 'probe', estimate=15, fingerprint=tap['fingerprint'])
                direct = check('attribute-direct', depends_on=['bad-tap'])
                indirect = check('attribute-alias', depends_on=['same-method'])
                fault = check('debug-tap-failure', after=['bad-tap'])
                independent = check('unrelated-control')
                result = rounds.next_action(ledger(tap, alias, direct, indirect, fault, independent))
                self.assertIsNone(result['settled'][0]['decision'])
                self.assertFalse(result['settled'][0]['usable_result'])
                self.assertEqual(result['budget']['accelerator_seconds_spent'], 12)
                self.assertEqual(result['reused'], [])
                self.assertEqual(result['check_id'], 'debug-tap-failure')
                self.assertEqual({r['id']: r['reason'] for r in result['deferred']},
                                 dict(zip(('same-method', 'attribute-direct', 'attribute-alias'),
                                          ('unusable_prior_result', 'unusable_dependency', 'unusable_dependency'))))
                running_tap = check('bad-tap', 'probe', phase='running', estimate=15, job_receipt='tap-job.json')
                report = rounds.next_action(ledger(running_tap, fault, independent))
                self.assertEqual((report['action'], report['independent_check']), ('reconcile_running', 'unrelated-control'))

    def test_probe_attribution_taint_is_transitive_and_not_reusable(self):
        tap = check('bad-tap', 'probe', phase='finished', estimate=15, actual_accelerator_seconds=12,
                    evidence=['tap-result.json'], outcome='inconclusive', probe_control=probe())
        derived = check('stale-attribution', phase='finished', outcome='match', actual_accelerator_seconds=0,
                        evidence=['derived-from-tap.json'], depends_on=['bad-tap'])
        alias = check('reuse-stale-attribution', fingerprint=derived['fingerprint'])
        downstream = check('next-attribution', depends_on=['stale-attribution'])
        plan = ledger(tap, derived, alias, downstream)
        report = rounds.next_action(plan)
        self.assertEqual(report['action'], 'summarize')
        self.assertEqual(report['reused'], [])
        self.assertIsNone(report['settled'][1]['decision'])
        self.assertFalse(report['settled'][1]['usable_result'])
        for ref in ('tap-result.json', 'derived-from-tap.json'):
            plan['bounded_delivery'] = dict(established='Unsupported attribution.', not_established='Cause.',
                                            reopen_when='Confirmed evidence.', evidence=[ref])
            with self.assertRaisesRegex(ValueError, 'unverified probe|unusable result'):
                rounds.next_action(plan)

    def test_diagnostic_only_dependency_can_explain_probe_failure_not_original(self):
        tap = check('bad-tap', 'probe', phase='finished', estimate=15, actual_accelerator_seconds=12,
                    evidence=['tap-crash.json'], outcome='inconclusive')
        debug = check('probe-crash-control', phase='finished', actual_accelerator_seconds=0,
                      outcome='match', evidence=['probe-crash-explanation.json'], after=['bad-tap'])
        report = rounds.next_action(ledger(tap, debug, check('attribution', depends_on=['probe-crash-control'])))
        self.assertEqual(report['settled'][1]['decision'], debug['on_match'])
        self.assertFalse(report['settled'][1]['usable_result'])
        self.assertEqual(report['deferred'][0]['reason'], 'unusable_dependency')
        plan = ledger(tap, debug)
        plan['bounded_delivery'] = dict(established='Original classification not inferred from the crash.',
                                        not_established='Cause of original discrepancy.', reopen_when='Transparent probe.',
                                        evidence=['probe-crash-explanation.json'])
        with self.assertRaisesRegex(ValueError, 'unusable result'):
            rounds.next_action(plan)

    def test_cli_is_standard_library_and_never_launches_run(self):
        with TemporaryDirectory() as root:
            document = Path(root) / 'round.json'
            document.write_text(json.dumps(ledger(check('requires-outside-dispatch'))))
            raw = subprocess.check_output([sys.executable, '-IB', str(Path(rounds.__file__).resolve()), 'round',
                                           '--input', str(document)], cwd=root)
            self.assertEqual(json.loads(raw)['action'], 'run_check')
            self.assertEqual(list(Path(root).iterdir()), [document])


if __name__ == '__main__':
    unittest.main()
