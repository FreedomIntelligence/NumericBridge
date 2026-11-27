"""Test typed MCP input against real local preview and an in-process inbox."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from urllib.error import URLError

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import ValidationError
from starlette.testclient import TestClient

from community.catalog_snapshot import OfficialCatalog, parse_manifest
from community.contribution_input import Contribution, compile_contribution
from community.intake import Inbox, create_app
from community.mcp_submission import create_server
from community.records.catalog import Catalog
from community.records.submission import Submissions, encode
from community.test_catalog_snapshot import ENTRIES, bundle, manifest


PUBLIC = 'https://numericbridge.com/v1/public-submissions'
TOKEN = 'test-only-operator-secret-0123456789012345'
REPOSITORY = 'example/fixture'
PATCH = ('diff --git a/kernel.py b/kernel.py\n--- a/kernel.py\n+++ b/kernel.py\n'
         '@@ -1 +1 @@\n-bug = True\n+bug = False\n')


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), '-c', 'core.hooksPath=/dev/null',
                                    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                    '-c', 'commit.gpgsign=false', *args], text=True).strip()


def git_fixture(path):
    path.mkdir()
    git(path, 'init', '-q', '-b', 'main')
    (path / 'kernel.py').write_text('bug = True\n')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'Synthetic merged fixture')
    return git(path, 'rev-parse', 'HEAD')


def snapshot(path):
    (path / 'data').mkdir(parents=True)
    (path / 'evidence').mkdir()
    values = {'evaluations': {'evaluations':[], 'updated_at':'2026-01-01'},
              'repairs': {'repairs':[], 'library_groups':[], 'updated_at':'2026-01-01'},
              'differences': {'records':[]}, 'relations': {'entities':[], 'links':[]}}
    for key, document in values.items():
        (path / 'data' / f'{key}.json').write_bytes(encode(dict(schema_version=1, **document)))


def packet(commit):
    setup = dict(hardware='CPU', libraries=[dict(name='Fixture engine', version='1.0')], settings={'dtype':'bf16'})
    issue = dict(kind='issue', id='layout', title='Unexpected padded result', task='inference',
                 subject=dict(kind='operator', name='Synthetic reduction', shape=[8, 32], dtype='bf16'),
                 setup=setup, classification='confirmed-defect', trigger='Use the padded fixture.',
                 expected='Return the same reduction.', observed='One slot was wrong.',
                 reproducer='Run the synthetic comparison with padded input.')
    evaluation = dict(kind='evaluation', id='paired', title='Synthetic paired comparison', task='inference',
                      model=dict(name='Synthetic model', revision='v1'), workload='Compare paired output scores.',
                      sides=[setup | {'id':'a'}, setup | {'id':'b'}], metrics=[dict(id='mismatch', value=1, unit='slot')],
                      coverage=dict(observed=8, expected=8, unit='sample'),
                      acceptance=dict(verdict='fail', policy='fixture-v1', criterion='Zero mismatched slots.'),
                      reproducer='Run paired inference on the public fixture.')
    patch = dict(kind='patch_candidate', id='fix', title='Fix padded placement', issue_id='layout',
                 change='Correct the layout in the fixture.',
                 target=dict(repository=REPOSITORY, base_branch='main', commit=commit), patch=PATCH,
                 original_relation='independent', verification=dict(scope='Padded layout regression only.',
                     tested_commit=commit, tested_patch_sha256=sha256(PATCH.encode()).hexdigest(), execution='native',
                     command='python -m unittest fixture', baseline=dict(verdict='fail', summary='One wrong slot.'),
                     patched=dict(verdict='pass', summary='All eight slots match.'),
                     coverage=dict(observed=8, expected=8, unit='fixture slot'),
                      metrics=[dict(id='mismatch', before=1, after=0, unit='slot')]))
    return dict(schema_version=1, id='fixture', title='Independent patch with failed original comparison',
                measured_on='2026-01-02', items=[evaluation,issue,patch])


class TypedContributionTests(unittest.IsolatedAsyncioTestCase):
    def test_validation_rejects_shape_and_proof_conflicts(self):
        raw = packet('1'*40)
        Contribution.model_validate(raw)
        failing = deepcopy(raw)
        failing['items'][0]['acceptance']['passed'] = True
        with self.assertRaises(ValidationError):
            Contribution.model_validate(failing)
        failing = deepcopy(raw)
        failing['items'][2]['verification']['patched']['verdict'] = 'fail'
        with self.assertRaises(ValidationError):
            Contribution.model_validate(failing)
        failing = deepcopy(raw)
        failing['items'][1].pop('reproducer')
        with self.assertRaises(ValidationError):
            Contribution.model_validate(failing)
        failing = deepcopy(raw)
        failing['items'][1]['references'] = [{'label':'Private auth', 'url':'https://example.org/log?token=secret'}]
        with self.assertRaises(ValidationError):
            Contribution.model_validate(failing)

    def test_repairs_require_paired_problem_metrics_without_a_category(self):
        data = packet('1' * 40)
        verification = data['items'][2]['verification']
        with_category = deepcopy(data)
        with_category['items'][2]['verification']['kind'] = 'functional'
        with self.assertRaises(ValidationError):
            Contribution.model_validate(with_category)
        good = verification.pop('metrics')
        with self.assertRaises(ValidationError):
            Contribution.model_validate(data)
        for invalid in ([], [dict(id='mae', after=0.01, unit='absolute')],
                        [good[0] | {'after': float('inf')}], [good[0] | {'unit': ''}], [good[0]] * 2):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                verification['metrics'] = invalid
                Contribution.model_validate(data)
        for measurement in (good[0], dict(id='failed-runs', before=3, after=0, unit='run')):
            with self.subTest(measurement=measurement):
                verification['metrics'] = [measurement]
                report = Contribution.model_validate(data).items[2].verification
                self.assertEqual(report.metrics[0].model_dump(), measurement)

    def test_pass_only_submission_is_searchable_without_an_issue_or_patch(self):
        with TemporaryDirectory(prefix='numericbridge-pass-only-') as directory:
            root = Path(directory).resolve()
            code = root / 'code'
            commit = git_fixture(code)
            git(code, 'update-ref', 'refs/remotes/origin/main', commit)
            site = root / 'catalog'
            snapshot(site)
            drafts = Submissions(code, root / 'drafts', destination=PUBLIC, catalog_site=site)
            inbox = Inbox(code, root / 'inbox', catalog_site=site)
            data = packet(commit)
            data.update(id='passing-fixture', title='Passing synthetic comparison', items=data['items'][:1])
            evaluation = data['items'][0]
            evaluation['acceptance']['verdict'] = 'pass'
            evaluation['metrics'][0]['value'] = 0
            public, review = compile_contribution(data, {'mode':'anonymous'})
            prepared = drafts.prepare(data['title'], public, review_evidence=review)
            wire = prepared['preview']
            self.assertEqual(wire['schema_version'], 5)
            self.assertEqual(wire['review_evidence'], {})
            raw = encode(wire, compact=True)
            with TestClient(create_app(inbox, TOKEN, public_url=PUBLIC, public_status_key='test-signature-2026')) as app:
                response = app.post('/v1/public-submissions', content=raw, headers={
                    'content-type':'application/json', 'x-draft-sha256':sha256(raw).hexdigest()})
            self.assertEqual(response.status_code, 201, response.text)
            with drafts.preview_checkout(wire['title'], wire['additions']) as (checkout, _):
                catalog = Catalog(checkout / 'community/site')
                query = dict(hardware='CPU', library='Fixture engine', version='1.0',
                             model='Synthetic model', task='inference')
                result = catalog.search(query)
                self.assertEqual(result['status'], 'matched')
                self.assertEqual([row['id'] for row in result['matches']], ['contribution-passing-fixture'])
                match = result['matches'][0]
                self.assertEqual(match['issues'], [])
                self.assertEqual(match['solutions'], [])
                for configuration in match['configurations']:
                    self.assertEqual(configuration['record']['acceptance']['verdict'], 'pass')
                self.assertEqual(result['recommendation']['basis'], 'recorded-numerical-acceptance')
                self.assertEqual(catalog.search(query | {'version':'2.0'})['status'], 'no-records')

    def test_official_layout_keeps_checklist_and_one_paired_table(self):
        with TemporaryDirectory(prefix='numericbridge-official-template-') as directory:
            upstream = Path(directory) / 'upstream'
            git_fixture(upstream)
            template = upstream / '.github/PULL_REQUEST_TEMPLATE.md'
            template.parent.mkdir()
            template.write_text('## Summary\n\n### Test plan\n\n## Checklist\n\n- [ ] Human review\n')
            git(upstream, 'add', '.')
            git(upstream, 'commit', '-qm', 'Synthetic official template')
            data = packet(git(upstream, 'rev-parse', 'HEAD'))
            patch = data['items'][2]
            patch['template_slots'] = [{'sections':['problem', 'change', 'patch']},
                                       {'sections':['validation']}, {'sections':[]}]
            patch['verification']['metrics'][0]['unit'] = 'slot|lane'
            _, review = compile_contribution(data, {'mode':'anonymous'}, trusted_repos={REPOSITORY:upstream})
            pr = review['review/fixture-fix.json']['upstream_pr_draft']
            body = pr['body_markdown']
            self.assertEqual([line for line in body.splitlines() if line.startswith('#')],
                             ['## Summary', '### Test plan', '## Checklist'])
            self.assertEqual(body.count('| Metric | Before | After | Unit |'), 1)
            self.assertIn('| mismatch | 1 | 0 | slot&#124;lane |', body)
            self.assertIn('## Checklist\n\n- [ ] Human review\n\n---', body)
            self.assertIn('upstream_checklist', pr['pending_checks'])

    async def test_automatic_catalog_recovers_and_prepares_without_sending(self):
        with TemporaryDirectory(prefix='numericbridge-auto-catalog-') as directory:
            root = Path(directory)
            code = root / 'code'
            commit = git_fixture(code)  # No remote-tracking branch and no catalog data in Git.
            source = root / 'source'
            snapshot(source)
            archive = bundle([*ENTRIES, *[(f'data/{path.name}', path.read_bytes())
                                          for path in sorted((source / 'data').iterdir())]])
            meta = manifest(archive)
            entry = parse_manifest(meta)
            calls = []

            def fetch(name):
                calls.append(name)
                if len(calls) <= 2:
                    raise URLError('fixture network unavailable')
                return BytesIO({'current.json': meta, entry.filename: archive}[name])

            provider = OfficialCatalog(root, cache=root / 'cache', fetch=fetch)
            intake = Mock(url=PUBLIC, token_file=None)
            drafts = Submissions(code, root / 'drafts', destination=PUBLIC, base_ref='HEAD', catalog_provider=provider)
            server = create_server(drafts, intake)._lowlevel_server
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as client:
                        await client.initialize()

                        async def call(name, args):
                            result = await client.call_tool(name, args)
                            self.assertFalse(result.is_error, result.content)
                            return result.structured_content

                        typed = packet(commit)
                        typed.update(id='auto-catalog', title='Snapshot preview', items=typed['items'][:1])
                        params = {'contribution': typed}
                        check = 'check_contribution_catalog'
                        prepare = 'prepare_contribution'
                        self.assertEqual((await call(check, {}))['status'], 'catalog_unavailable')
                        self.assertEqual((await call(prepare, params))['status'], 'catalog_unavailable')
                        self.assertFalse(drafts.drafts.exists())
                        prepared = await call(prepare, params)
                        self.assertEqual(prepared['status'], 'ready', prepared)
                        self.assertEqual(prepared['preview']['attribution'], {'mode': 'anonymous'})
                        self.assertEqual(await call(check, {}), {'status': 'ready', 'generation': entry.generation,
                                                                 'sha256': entry.sha256})
                        self.assertEqual(calls, ['current.json'] * 3 + [entry.filename])
                        intake.send.assert_not_called()
                    tasks.cancel_scope.cancel()

    async def test_typed_mcp_roundtrip_revalidates_real_records_and_hides_private_material(self):
        with TemporaryDirectory(prefix='numericbridge-typed-') as root:
            root = Path(root).resolve()
            upstream = root / 'upstream'
            commit = git_fixture(upstream)
            code = root / 'code'
            code_commit = git_fixture(code)
            git(code, 'update-ref', 'refs/remotes/origin/main', code_commit)
            catalog = root / 'catalog'
            snapshot(catalog)
            request = root / 'request.json'
            request.write_text(json.dumps({'community_contribution': {'consent':'share', 'attribution_mode':'anonymous',
                                                                      'confirmed_at':'2026-01-01T00:00:00Z'}}))
            reviews = {REPOSITORY:upstream}
            drafts = Submissions(code, root / 'drafts', destination=PUBLIC, catalog_site=catalog, trusted_repos=reviews)
            inbox = Inbox(code, root / 'inbox', catalog_site=catalog, trusted_repos=reviews)
            intake = Mock(url=PUBLIC, token_file=None)
            app = TestClient(create_app(inbox, TOKEN, public_url=PUBLIC, public_status_key='test-signature-2026'))
            self.addCleanup(app.close)

            def post(document, digest):
                raw = encode(document, compact=True)
                self.assertEqual(sha256(raw).hexdigest(), digest)
                response = app.post('/v1/public-submissions', content=raw,
                                    headers={'content-type':'application/json', 'x-draft-sha256':digest})
                self.assertIn(response.status_code, (200,201), response.text)
                return response.json()

            intake.send.side_effect = post
            server = create_server(drafts, intake)._lowlevel_server
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as client:
                        await client.initialize()
                        data = packet(commit)
                        inspected = await client.call_tool('inspect_upstream_pr_template', {'target':data['items'][2]['target']})
                        self.assertEqual(inspected.structured_content['template_proof']['source'], 'numericbridge_default')
                        result = await client.call_tool('prepare_contribution', {'contribution': data})
                        self.assertFalse(result.is_error, result.content)
                        ready = result.structured_content
                        self.assertEqual(ready['status'], 'ready', ready)
                        wire = ready['preview']
                        self.assertEqual(wire['schema_version'], 5)
                        self.assertNotIn('files', wire)
                        self.assertIn('files', ready['preview_metadata'])
                        self.assertEqual(wire['additions']['evaluations']['evaluations'][0]['acceptance']['verdict'], 'fail')
                        attachment = wire['review_evidence']['review/fixture-fix.json']
                        self.assertEqual(attachment['patch_text'], PATCH)
                        self.assertIn('human_review', attachment['upstream_pr_draft']['pending_checks'])
                        self.assertNotIn('native_on_target', attachment['upstream_pr_draft']['pending_checks'])
                        body = attachment['upstream_pr_draft']['body_markdown']
                        self.assertIn('| Metric | Before | After | Unit |', body)
                        self.assertIn('| mismatch | 1 | 0 | slot |', body)
                        self.assertIn('**Native on target:** Verified in this scope.', body)
                        verification = wire['additions']['evidence']['evidence/submissions/fixture-fix.json']['verification']
                        self.assertNotIn('kind', verification)
                        self.assertEqual(verification['metrics'], [{'id':'mismatch', 'before':1, 'after':0, 'unit':'slot'}])
                        altered = deepcopy(wire)
                        altered['additions']['evidence']['evidence/submissions/fixture-fix.json']['verification']['metrics'] = []
                        altered_bytes = encode(altered, compact=True)
                        refused = app.post('/v1/public-submissions', content=altered_bytes, headers={
                            'content-type':'application/json', 'x-draft-sha256':sha256(altered_bytes).hexdigest()})
                        self.assertEqual(refused.status_code, 422)
                        public = encode(wire['additions']).decode()
                        self.assertNotIn('body_markdown', public)
                        self.assertNotIn(PATCH, public)
                        self.assertNotIn('review/fixture-fix.json', public)
                        stale = deepcopy(data)
                        stale['items'][2]['verification']['tested_patch_sha256'] = '2' * 64
                        stale_result = await client.call_tool('prepare_contribution', {'contribution': stale})
                        stale_ready = stale_result.structured_content
                        self.assertEqual(stale_ready['status'], 'ready', stale_ready)
                        self.assertNotEqual(stale_ready['draft_sha256'], ready['draft_sha256'])
                        stale_pr = stale_ready['preview']['review_evidence']['review/fixture-fix.json']['upstream_pr_draft']
                        self.assertIn('native_on_target', stale_pr['pending_checks'])
                        self.assertIn('**Native on target:** Pending.', stale_pr['body_markdown'])
                        intake.send.assert_not_called()
                        approval = await client.call_tool('submit_community', {
                            'draft_id':ready['draft_id'], 'draft_sha256':ready['draft_sha256']})
                        self.assertEqual(approval.structured_content['status'], 'approval_unavailable')
                        self.assertIn(ready['draft_sha256'], approval.structured_content['fallback_command'])
                        intake.send.assert_not_called()
                        send = await client.call_tool('submit_community', {
                            'draft_id':ready['draft_id'], 'draft_sha256':ready['draft_sha256'], 'consent_record':str(request)})
                        self.assertEqual(send.structured_content.get('status'), 'received', send.structured_content)
                        self.assertEqual(send.structured_content['receipt_binding']['candidates'], ready['candidate_versions'])
                        self.assertTrue((root/'inbox'/(send.structured_content['receipt_id']+'.json')).is_file())
                        with drafts.preview_checkout(wire['title'], wire['additions']) as (candidate, _):
                            site = candidate/'community/site'
                            public_case = Catalog(site).case_bundle('contribution-fixture')
                            self.assertIn('fixture-fix', json.dumps(public_case))
                            self.assertFalse((site/'review').exists())
                            self.assertNotIn('body_markdown', (site/'evidence/submissions/fixture-fix.json').read_text())
                    tasks.cancel_scope.cancel()


if __name__ == '__main__':
    unittest.main()
