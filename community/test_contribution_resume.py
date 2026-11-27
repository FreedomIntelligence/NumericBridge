"""Synthetic, no-network regressions for the completed-case preview recovery."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import URLError

from .catalog_snapshot import OfficialCatalog, parse_manifest
from .contribution_resume import PUBLIC_INTAKE, resume
from .records.submission import encode
from .test_catalog_snapshot import ENTRIES, bundle, manifest


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(prefix='numericbridge-test-resume-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.case = self.root / 'case'
        self.case.mkdir()
        (self.case / 'community-draft').mkdir()
        self.out = self.root / 'drafts'
        self.state_path = self.case / 'case-state.json'
        self.request_path = self.case / 'request.json'
        self.typed_path = self.case / 'community-draft/contribution.json'
        self.acceptance = self.case / 'acceptance.json'
        self.acceptance.write_bytes(encode(dict(schema_version=1, kind='acceptance', verdict='gap', passed=True)))
        (self.case / 'report.md').write_text('Frozen synthetic A/B result.\n')
        (self.case / 'paired.json').write_text('{"count":8}\n')
        self.state = dict(status='needs_human', stage='community_contribution', sessions=1, running_jobs=[],
                          access_blocker='Waiting for a frozen catalog', next_action='Supply a frozen catalog',
                          delivery='report.md', acceptance_digest=sha256(self.acceptance.read_bytes()).hexdigest(),
                          delivery_summary=dict(schema_version=1, candidates=[],
                              original=dict(verdict='gap', verification='supported_native', evidence=['acceptance.json']),
                              delivery=dict(outcome='acceptance_passed', evidence=['report.md', 'paired.json'])),
                          community_candidate=dict(typed_input='community-draft/contribution.json',
                                                   official_draft_prepared=False, intake_attempted=False, receipt=None))
        self.request = dict(task='inference', community_contribution=dict(attribution_mode='anonymous'))
        item = dict(kind='evaluation', id='paired', title='Synthetic evaluation', task='inference',
                    model=dict(name='Fixture model', revision='v1'), workload='Compare synthetic outputs.',
                    acceptance=dict(verdict='gap', policy='fixture-v1', criterion='Use fixture thresholds.'),
                    sides=[dict(id=side, hardware='CPU', libraries=[dict(name='Fixture engine', version='1.0')])
                           for side in ('a', 'b')],
                    metrics=[dict(id='absolute-error', value=0.002, unit='absolute')],
                    coverage=dict(observed=8, expected=8, unit='sample'),
                    reproducer='Use the synthetic workload in the fixture suite.')
        self.packet = dict(schema_version=1, id='fixture', title='Synthetic completed delivery',
                           measured_on='2026-01-02', items=[item])
        self.write_inputs()
        catalog = dict(evaluations=dict(evaluations=[], updated_at='2026-01-01'),
                       repairs=dict(repairs=[], library_groups=[], updated_at='2026-01-01'),
                       differences=dict(records=[]), relations=dict(entities=[], links=[]))
        archive = bundle([*ENTRIES, *[(f'data/{key}.json', encode(dict(schema_version=1, **value)))
                                      for key, value in catalog.items()]])
        self.meta = manifest(archive)
        self.entry = parse_manifest(self.meta)
        self.responses = {'current.json': self.meta, self.entry.filename: archive}
        self.fetches = []
        self.offline = False

    def write_inputs(self):
        for path, value in ((self.state_path, self.state), (self.request_path, self.request),
                            (self.typed_path, self.packet)):
            path.write_bytes(encode(value))

    def fetch(self, name):
        self.fetches.append(name)
        if self.offline:
            raise URLError('Synthetic service outage')
        return BytesIO(self.responses[name])

    def recover(self, **options):
        def catalog(*args, **kwargs):
            return OfficialCatalog(*args, fetch=self.fetch, **kwargs)

        return resume(self.case, self.out, catalog_factory=catalog, **options)

    def snapshot_case(self):
        return {path.relative_to(self.case): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.case.rglob('*') if path.is_file()}

    def test_old_frozen_catalog_checkpoint_creates_exact_preview_once(self):
        before = self.snapshot_case()
        with patch.dict('os.environ', {'NUMERICBRIDGE_CATALOG_SITE': str(self.root / 'stale-site')}):
            first = self.recover()
            self.assertEqual(first['status'], 'preview_ready', first)
            path = Path(first['draft_path'])
            saved = path.stat()
            second = self.recover()
        self.assertEqual(second, first)
        self.assertEqual(first['consent'], 'awaiting_consent')
        self.assertIs(first['sent'], False)
        self.assertEqual(first['catalog'], {'generation': self.entry.generation, 'sha256': self.entry.sha256})
        self.assertEqual(path.read_bytes(), encode(first['preview'], compact=True))
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), first['draft_sha256'])
        self.assertEqual(first['preview']['destination'], PUBLIC_INTAKE)
        self.assertEqual(first['preview']['additions']['evaluations']['evaluations'][0]['acceptance']['verdict'], 'gap')
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), (saved.st_ino, saved.st_mtime_ns))
        self.assertEqual(list(self.out.iterdir()), [path])
        self.assertEqual(self.snapshot_case(), before)
        self.assertEqual(self.fetches, ['current.json', self.entry.filename] * 2)

    def test_outage_and_missing_setup_do_not_become_consent(self):
        self.typed_path.unlink()
        self.assertEqual(self.recover()['status'], 'setup_required')
        self.assertEqual(self.fetches, [])
        self.write_inputs()
        before = self.snapshot_case()
        self.offline = True
        self.assertEqual(self.recover()['status'], 'catalog_unavailable')
        self.assertFalse(self.out.exists())
        self.assertEqual(self.snapshot_case(), before)
        self.offline = False
        self.assertEqual(self.recover()['status'], 'preview_ready')

    def test_registered_consent_and_decline_are_not_send_actions(self):
        self.request['community_contribution'].update(consent='share', confirmed_at='2026-01-02T00:00:00Z')
        self.write_inputs()
        result = self.recover()
        self.assertEqual((result['status'], result['consent'], result['sent']), ('preview_ready', 'recorded', False))
        self.request['community_contribution']['consent'] = 'decline'
        self.write_inputs()
        self.fetches.clear()
        result = self.recover()
        self.assertEqual((result['status'], result['consent'], result['sent']), ('local_only', 'declined', False))
        self.assertEqual(self.fetches, [])

    def test_previous_receipt_is_reported_without_typed_input_or_network(self):
        digest = 'a' * 64
        receipt_id = 'submission-' + digest
        self.state['community_draft_sha256'] = digest
        self.state['delivery_summary']['community'] = [dict(review_state='pending_review', receipt_binding=dict(
            schema_version=1, destination=PUBLIC_INTAKE, draft_sha256=digest, receipt_id=receipt_id, candidates=[]))]
        self.write_inputs()
        self.typed_path.unlink()
        self.assertEqual(self.recover(), dict(status='already_received', sent=False, receipt_id=receipt_id,
                                              draft_sha256=digest, review_state='pending_review'))
        self.state['community_draft_sha256'] = 'b' * 64
        self.write_inputs()
        self.assertEqual(self.recover()['status'], 'receipt_unverified')
        self.assertEqual(self.fetches, [])
        self.assertFalse(self.out.exists())

    def test_legacy_intake_attempt_or_unreconciled_jobs_do_not_trigger_work(self):
        self.state['community_candidate']['intake_attempted'] = True
        self.write_inputs()
        self.assertEqual(self.recover()['status'], 'receipt_unverified')
        self.state['community_candidate']['intake_attempted'] = False
        self.state['running_jobs'] = [dict(host='fixture', job_or_pid=123, check_command='exit 99')]
        self.write_inputs()
        self.assertEqual(self.recover()['status'], 'invalid_case')
        self.assertEqual(self.fetches, [])
        self.assertFalse(self.out.exists())

    def test_numerical_pins_and_containment_fail_without_preview(self):
        saved = self.acceptance.read_bytes()
        self.acceptance.write_bytes(saved + b'\n')  # Same verdict, different pinned bytes.
        self.assertEqual(self.recover()['status'], 'invalid_case')
        self.acceptance.write_bytes(saved)
        (self.case / 'paired.json').unlink()
        self.assertEqual(self.recover()['status'], 'invalid_case')
        (self.case / 'paired.json').write_text('{"count":8}\n')
        self.packet['items'][0]['acceptance']['verdict'] = 'pass'
        self.write_inputs()
        self.assertEqual(self.recover()['status'], 'preview_invalid')
        self.packet['items'][0]['acceptance']['verdict'] = 'gap'
        self.state['community_candidate']['typed_input'] = '../external.json'
        self.write_inputs()
        self.assertEqual(self.recover()['status'], 'setup_required')
        self.assertEqual(self.fetches, [])
        self.assertFalse(self.out.exists())

    def test_existing_preview_pin_and_draft_conflict_are_not_overwritten(self):
        first = self.recover()
        self.assertEqual(first['status'], 'preview_ready', first)
        self.state['community_draft_sha256'] = first['draft_sha256']
        self.state['community_candidate']['draft_id'] = first['draft_id']
        self.write_inputs()
        self.assertEqual(self.recover(), first)
        draft = Path(first['draft_path'])
        draft.write_bytes(b'tampered')
        self.assertEqual(self.recover()['status'], 'setup_required')
        self.assertEqual(draft.read_bytes(), b'tampered')
        self.packet['title'] = 'Changed proposal'
        self.write_inputs()
        self.fetches.clear()
        self.assertEqual(self.recover()['status'], 'preview_invalid')
        self.assertEqual(self.fetches, [])
        self.assertEqual(list(self.out.iterdir()), [draft])

    def test_scan_findings_are_redacted_and_case_output_is_rejected(self):
        self.out = self.case / 'new-drafts'
        self.assertEqual(self.recover()['status'], 'setup_required')
        self.assertFalse(self.out.exists())
        self.out = self.root / 'drafts'
        self.packet['items'][0]['reproducer'] = 'See /Users/example/private/data.json for details.'
        self.write_inputs()
        findings = self.recover()
        self.assertEqual(findings['status'], 'scan_findings', findings)
        self.assertNotIn('/Users/', str(findings))
        self.assertEqual(self.fetches, [])
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
