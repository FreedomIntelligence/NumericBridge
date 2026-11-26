"""Verify review-only wire data stays private using local Git and HTTP fixtures."""

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from starlette.testclient import TestClient

from community.intake import Inbox, PATH, PUBLIC_PATH, create_app
from community.normalize import apply_receipt, normalize_draft
from community.records.submission import SITE, Submissions, attribution, encode
from community.submission_client import send_reviewed
from tools.strict_pr import render_pr


AUTHENTICATED = 'https://numericbridge.com' + PATH
PUBLIC = 'https://numericbridge.com' + PUBLIC_PATH
TOKEN = 'fixture-upload-credential-for-isolated-tests'
REVIEW = 'review/private-fixture.json'
PUBLIC_EVIDENCE = 'evidence/submissions/public-fixture.json'
MARKER = 'MAINTAINER-ONLY FIXTURE SENTENCE'
PATCH = 'diff --git a/kernel.py b/kernel.py\n--- a/kernel.py\n+++ b/kernel.py\n@@ -1 +1 @@\n-before\n+' + MARKER + '\n'


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), '-c', 'core.hooksPath=/dev/null',
                                    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                    '-c', 'commit.gpgsign=false', *args], encoding='utf-8').strip()


def additions():
    def side(identity):
        return dict(id=identity, label=identity, hardware='CPU',
                    libraries=[dict(name='fixture', version='v1')], settings={})

    evaluation = dict(id='paired-layout', date='2026-01-02', title='Paired layout', task='inference',
                      status='completed', model=dict(name='Fixture', revision='v1'), summary='One mismatch.',
                      sides=[side('before'), side('reference')], common=[], differences=[], notes=[], archive=None,
                      acceptance=dict(verdict='fail', policy='fixture-v1', criterion='Zero mismatched outputs.'),
                      comparisons=[dict(id='paired', title='Paired', scope='One synthetic input',
                                        side_ids=['before', 'reference'],
                                        metrics=[dict(id='mismatches', label='Mismatches', value=1, unit='slot')],
                                        coverage=[dict(label='Inputs', observed=1, expected=1, unit='sample')])],
                      evidence=[dict(url=PUBLIC_EVIDENCE, label='Public experiment')])
    return dict(evaluations={'evaluations': [evaluation]}, repairs={}, relations={},
                evidence={PUBLIC_EVIDENCE: {'experiment': 'bounded synthetic comparison'}})


class ReviewVisibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / 'kernel.py').write_text('before\n', encoding='utf-8')
        site = self.repo / SITE
        (site / 'data').mkdir(parents=True)
        (site / 'index.html').write_text('<main>Public fixture</main>\n', encoding='utf-8')
        baseline = {'evaluations': {'evaluations': [], 'updated_at': '2026-01-01'},
                    'repairs': {'repairs': [], 'library_groups': [], 'updated_at': '2026-01-01'},
                    'differences': {'records': []}, 'relations': {'entities': [], 'links': []}}
        for key, document in baseline.items():
            (site / 'data' / f'{key}.json').write_bytes(encode(dict(schema_version=1, **document)))
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'Synthetic upstream and base catalog')
        self.commit = git(self.repo, 'rev-parse', 'HEAD')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', self.commit)
        git(self.repo, 'config', 'remote.origin.url', 'https://github.com/example/fixture.git')
        self.registry = {'example/fixture': self.repo}
        self.submissions = Submissions(self.repo, self.root / 'drafts', destination=AUTHENTICATED,
                                       trusted_repos=self.registry)
        self.inbox = Inbox(self.repo, self.root / 'state' / 'inbox', trusted_repos=self.registry)
        self.http = TestClient(create_app(self.inbox, TOKEN, public_url=PUBLIC,
                                          public_status_key='fixture-status-key'))
        self.addCleanup(self.http.close)

    def _attachment(self, *, checks=None, tested=True):
        pr = render_pr(repository='example/fixture', base_branch='main', target_commit=self.commit,
                       title='Synthetic private fix', section_contents=[MARKER, 'Change', 'Test scope', 'Patch notes'],
                       checks=checks or {}, attribution=attribution(), trusted_repos=self.registry)
        digest = sha256(PATCH.encode()).hexdigest()
        version = dict(candidate_id='private-fix', target_commit=self.commit, patch_sha256=digest,
                       tested_commit=self.commit if tested else None, tested_patch_sha256=digest if tested else None)
        return dict(upstream_pr_draft=pr, candidate_version=version, patch_text=PATCH)

    def _make(self, evidence=None, **options):
        public = additions()
        for document in (evidence or {}).values():
            version = document.get('candidate_version')
            if version:
                public['evidence'][PUBLIC_EVIDENCE].update(
                    patch_sha256=version['patch_sha256'], verification=dict(
                        tested_commit=version['tested_commit'], tested_patch_sha256=version['tested_patch_sha256'],
                        metrics=[dict(id='mismatches', before=1, after=0, unit='slot')]))
        return self.submissions.prepare('Synthetic contribution', public,
                                        review_evidence={} if evidence is None else evidence, **options)

    def _post(self, draft, *, public=False):
        raw = encode(draft, compact=True)
        headers = {'Content-Type': 'application/json', 'X-Draft-SHA256': sha256(raw).hexdigest()}
        if not public:
            headers['Authorization'] = 'Bearer ' + TOKEN
        return self.http.post(PUBLIC_PATH if public else PATH, content=raw, headers=headers)

    def test_v5_exact_bytes_private_storage_and_public_replay(self):
        review = {REVIEW: self._attachment()}
        prepared = self._make(review)
        preview = prepared['preview']
        self.assertEqual(preview['schema_version'], 5)
        self.assertEqual(preview['review_evidence'], review)
        self.assertEqual(prepared['preview_metadata']['base_commit'], self.commit)
        self.assertEqual(prepared['preview_metadata']['files'][SITE + PUBLIC_EVIDENCE],
                         sha256(encode(preview['additions']['evidence'][PUBLIC_EVIDENCE])).hexdigest())
        self.assertNotIn('base_commit', preview)
        self.assertNotIn('files', preview)
        raw = encode(preview, compact=True)
        digest = sha256(raw).hexdigest()
        self.assertEqual(digest, prepared['draft_sha256'])
        self.assertEqual(self.submissions.reviewed(prepared['draft_id'], digest), preview)
        incoming = self._post(preview)
        self.assertEqual(incoming.status_code, 201, incoming.text)
        receipt = incoming.json()['receipt_id']
        self.assertEqual(receipt, 'submission-' + digest)
        stored = self.inbox.directory / (receipt + '.json')
        self.assertEqual(stored.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o600)
        self.assertEqual(self._post(preview).status_code, 200)
        transport = Mock(url=AUTHENTICATED, token_file=self.root / 'unused-token')
        transport.send.return_value = incoming.json()
        bound = send_reviewed(self.submissions, transport, preview, digest)['receipt_binding']
        self.assertEqual(bound['candidates'][0]['candidate_id'], 'private-fix')
        self.assertEqual(bound['candidates'][0]['patch_sha256'], review[REVIEW]['candidate_version']['patch_sha256'])
        self.assertNotIn(MARKER, encode(bound).decode())
        output = self.root / 'publication'
        normalized = apply_receipt(self.repo, self.inbox.directory, receipt, output)
        self.assertEqual(normalized['counts']['review_evidence_files'], 1)
        self.assertEqual(normalized['evaluations'][0]['acceptance_verdict'], 'fail')
        self.assertFalse(normalized['evaluations'][0]['acceptance_passed'])
        self.assertTrue((output / 'site' / PUBLIC_EVIDENCE).is_file())
        self.assertFalse((output / 'site' / 'review').exists())
        for path in output.rglob('*'):
            if path.is_file():
                self.assertNotIn(MARKER.encode(), path.read_bytes(), path)
                self.assertNotIn(REVIEW.encode(), path.read_bytes(), path)

    def test_empty_review_forces_v5_and_only_authenticated_can_revise(self):
        prepared = self._make()
        self.assertEqual(prepared['preview']['review_evidence'], {})
        self.assertEqual(prepared['preview']['schema_version'], 5)
        legacy = self.submissions.prepare('Synthetic contribution', additions())
        self.assertEqual(legacy['preview']['schema_version'], 3)
        self.assertNotIn('preview_metadata', legacy)
        receipt = 'submission-' + 'a' * 64
        revision = self._make(replaces=[receipt], attribution_mode='nickname', attribution_name='Fixture')
        self.assertEqual(revision['preview']['supersedes'], [receipt])
        self.assertEqual(self._post(revision['preview']).status_code, 201)
        anonymous = prepared['preview'] | {'destination': PUBLIC}
        self.assertEqual(self._post(anonymous, public=True).status_code, 201)
        for invalid in ({'supersedes': [receipt]}, {'supersedes': []}, {'files': {}},
                        {'title': ' Noncanonical title'}, {'attribution': attribution('nickname', 'Fixture')}):
            self.assertEqual(self._post(anonymous | invalid, public=True).status_code, 422)

    def test_private_identifiers_are_blocked_on_both_intakes_and_client(self):
        draft = self._make({REVIEW: {'note': 'Run on the fixture.'}})['preview']
        draft['review_evidence'][REVIEW]['note'] = 'Evidence in /home/private-user/session'
        self.assertEqual(self._post(draft).status_code, 422)
        self.assertEqual(self._post(draft | {'destination': PUBLIC}, public=True).status_code, 422)
        self.assertFalse(list(self.inbox.directory.glob('submission-*.json')))
        transport = Mock(url=AUTHENTICATED, token_file=self.root / 'unused-token')
        with self.assertRaisesRegex(ValueError, 'private identifiers'):
            send_reviewed(self.submissions, transport, draft, sha256(encode(draft, compact=True)).hexdigest())
        transport.send.assert_not_called()

    def test_misplaced_review_fields_references_and_noncanonical_verdict(self):
        original = self._make({REVIEW: {'note': MARKER}})['preview']
        for value in (REVIEW, '/evidence/../review/private-fixture.json', '%2572eview%2Fprivate-fixture.json',
                      'rev&#x69;ew&#47;private-fixture.json', PATCH, {'patch_text': PATCH},
                      {'candidate_version': {'candidate_id': 'private-fix'}}):
            with self.subTest(value=value):
                draft = deepcopy(original)
                draft['additions']['evidence'][PUBLIC_EVIDENCE]['hidden'] = [value]
                self.assertEqual(self._post(draft).status_code, 422)
                with self.assertRaises(ValueError):
                    normalize_draft(draft)
        draft = deepcopy(original)
        evaluation = draft['additions']['evaluations']['evaluations'][0]
        evaluation['evidence'][0]['url'] = REVIEW
        self.assertEqual(self._post(draft).status_code, 422)
        draft = deepcopy(original)
        draft['additions']['evidence'][REVIEW] = draft['review_evidence'][REVIEW]
        self.assertEqual(self._post(draft).status_code, 422)
        draft = deepcopy(original)
        draft['additions']['evaluations']['evaluations'][0]['acceptance']['passed'] = True
        self.assertEqual(self._post(draft).status_code, 422)
        self.assertFalse(list(self.inbox.directory.glob('submission-*.json')))

    def test_combined_count_and_byte_limits_and_independent_strict_verification(self):
        with self.assertRaisesRegex(ValueError, 'combined'):
            self._make({f'review/item-{i}.json': {} for i in range(20)})
        result = self._make({f'review/item-{i}.json': {} for i in range(19)})
        self.assertEqual(len(result['preview']['review_evidence']), 19)
        public = additions()
        public['evidence'][PUBLIC_EVIDENCE]['padding'] = 'p' * (600 * 1024)
        with self.assertRaisesRegex(ValueError, '1 MiB combined'):
            self.submissions.prepare('Too large', public, review_evidence={REVIEW: {'padding': 'r' * (600 * 1024)}})
        with self.assertRaisesRegex(ValueError, 'review/<slug>'):
            self._make({'review/../malformed.json': {}})
        draft = self._make({REVIEW: self._attachment()})['preview']
        corrupted = deepcopy(draft)
        corrupted['review_evidence'][REVIEW]['upstream_pr_draft']['body_markdown'] += '\nChanged.'
        self.assertEqual(self._post(corrupted).status_code, 422)
        corrupted = deepcopy(draft)
        attachment = corrupted['review_evidence'][REVIEW]
        attachment['patch_text'] = 'Not a patch\n'
        attachment['candidate_version']['patch_sha256'] = sha256(attachment['patch_text'].encode()).hexdigest()
        self.assertEqual(self._post(corrupted).status_code, 422)
        self.inbox.records.trusted_repos = {}
        self.assertEqual(self._post(draft).status_code, 422)

    def test_new_wire_patch_cannot_bypass_paired_public_evidence(self):
        draft = self._make({REVIEW: self._attachment()})['preview']
        for edit in ('remove-summary', 'mismatched-tested-commit', 'remove-review-patch'):
            with self.subTest(edit=edit):
                broken = deepcopy(draft)
                if edit == 'remove-summary':
                    broken['additions']['evidence'][PUBLIC_EVIDENCE] = {'experiment': 'no metric'}
                elif edit == 'mismatched-tested-commit':
                    broken['additions']['evidence'][PUBLIC_EVIDENCE]['verification']['tested_commit'] = '1' * 40
                else:
                    broken['review_evidence'].clear()
                self.assertEqual(self._post(broken).status_code, 422)
        with self.assertRaisesRegex(ValueError, 'actual tested base'):
            self._make({REVIEW: self._attachment(tested=False)})
        self.assertEqual(self._post(draft).status_code, 201)

    def test_legacy_wire_and_auth_status_preserve_historical_bytes(self):
        old = 'submission-' + 'a' * 64
        for version in (2, 3, 4):
            with self.subTest(version=version):
                kwargs = {'replaces': [old]} if version == 4 else {}
                draft = self.submissions.prepare(f'Legacy v{version}', additions(), **kwargs)['preview']
                if version == 2:
                    draft['schema_version'] = 2
                    del draft['attribution']
                self.assertIn('base_commit', draft)
                self.assertIn('files', draft)
                self.assertEqual(self._post(draft).status_code, 201)
                raw = encode(draft, compact=True)
                receipt = 'submission-' + sha256(raw).hexdigest()
                self.assertEqual((self.inbox.directory / (receipt + '.json')).read_bytes(), raw)
                self.assertEqual(self._post(draft).status_code, 200)
                status = self.http.get(f'{PATH}/{receipt}', headers={'Authorization': 'Bearer ' + TOKEN})
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()['review_state'], 'pending_review')
        legacy = self.submissions.preview('Previously accepted PR', additions())
        legacy['additions']['evidence'][PUBLIC_EVIDENCE]['upstream_pr_draft'] = {'legacy': True}
        self.assertEqual(self._post(legacy).status_code, 422)
        with self.assertRaisesRegex(ValueError, 'schema 5'):
            self.submissions.prepare('Do not create a new old PR', legacy['additions'])
        raw = encode(legacy, compact=True)
        digest = sha256(raw).hexdigest()
        path = self.inbox.directory / f'submission-{digest}.json'
        path.touch(mode=0o600)
        path.write_bytes(raw)
        self.assertEqual(self._post(legacy).status_code, 200)
        self.assertEqual(path.read_bytes(), raw)
        status = self.http.get(f'{PATH}/submission-{digest}', headers={'Authorization': 'Bearer ' + TOKEN})
        self.assertEqual(status.status_code, 200)


if __name__ == '__main__':
    unittest.main()
