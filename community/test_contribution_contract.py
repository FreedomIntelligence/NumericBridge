"""Test the workflow's MCP write surface and exact draft bindings without a network."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from community.mcp_submission import create_server
from community.records.submission import Submissions, attribution, encode
from tools.contribution_contract import candidate_versions, capabilities
from tools.pr_draft import NUMERICBRIDGE_REPO_URL, _extract_headings, check


PUBLIC = 'https://numericbridge.com/v1/public-submissions'
PRIVATE = 'https://numericbridge.com/v1/submissions'
EVIDENCE = 'evidence/submissions/synthetic-fix.json'


def fixture(patch='+new\n'):
    commit = '1' * 40
    body = (f'## Context\n\nEvidence.\n\n### Pre-commit Checks\n\n- [ ] CI\n\n'
            f'[NumericBridge]({NUMERICBRIDGE_REPO_URL})\n')
    return dict(candidate_version=dict(candidate_id='synthetic-fix', target_commit=commit,
                                       patch_sha256=sha256(patch.encode()).hexdigest(), tested_commit='2' * 40,
                                       tested_patch_sha256=sha256(patch.encode()).hexdigest()),
                upstream_pr_draft=dict(kind='upstream_pr_draft', repository='example/repo', base_branch='main',
                                       title='Synthetic repair', body_markdown=body, guidance_urls=['https://example.org'],
                                       pending_checks=['human_review', 'native_on_target'],
                                       template_source='repository_template',
                                       contribution_requirements={'commit':commit, 'template_headings':_extract_headings(body)}),
                patch_text=patch)


class TestCatalogSubmissions(Submissions):
    """Substitute only the catalog compiler; use the actual local draft byte store."""

    def preview(self, title, additions, *, attribution_mode='anonymous', attribution_name=None, replaces=()):
        draft = dict(schema_version=4 if replaces else 3, title=title, destination=self.destination,
                     attribution=attribution(attribution_mode, attribution_name), base_commit='0' * 40,
                     additions=additions, files={})
        if replaces:
            draft['supersedes'] = replaces
        return draft


class ContributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_upstream_writes_and_exact_receipt(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            intake = Mock(url=PUBLIC, token_file=None)
            sent = set()

            def post(draft, digest):
                self.assertEqual(sha256(encode(draft, compact=True)).hexdigest(), digest)
                response = dict(status='received', receipt_id=f'submission-{digest}',
                                review_state='pending_review', reused=digest in sent, status_token='a' * 64)
                sent.add(digest)
                return response

            intake.send.side_effect = post
            intake.status.side_effect = lambda receipt_id, **_: dict(status='found', receipt_id=receipt_id,
                                                                    review_state='pending_review')
            submissions = TestCatalogSubmissions(path, path / 'drafts', destination=PUBLIC)
            request = path / 'request.json'
            request.write_text(json.dumps({'community_contribution': {'consent':'share', 'attribution_mode':'anonymous',
                                                                      'confirmed_at':'2026-01-01T00:00:00Z'}}))
            server = create_server(submissions, intake)._lowlevel_server
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as client:
                        await client.initialize()

                        async def call(tool, **args):
                            result = await client.call_tool(tool, args)
                            self.assertFalse(result.is_error, result.content)
                            return result.structured_content

                        tools = (await client.list_tools()).tools
                        self.assertEqual([tool.name for tool in tools if tool.annotations.open_world_hint
                                          and not tool.annotations.read_only_hint], ['submit_community'])
                        mode = await call('community_contribution_capabilities')
                        self.assertEqual(mode['upstream_access'], 'read_only')
                        self.assertEqual(mode['attribution_modes'], ['anonymous'])
                        self.assertFalse(mode['supports_supersedes'])
                        params = dict(title='Synthetic community bundle', evidence={EVIDENCE:fixture()})
                        prepare = 'prepare_community_submission'
                        submit = 'submit_community'
                        lookup = 'community_submission_status'
                        for target in ('github', 'upstream', 'fork', 'push', 'https://github.com/example/repo'):
                            denied = await call(prepare, **params, delivery_target=target)
                            self.assertEqual(denied['status'], 'invalid')
                        self.assertEqual((await call(prepare, **params, supersedes=['submission-' + '3' * 64]))['status'], 'invalid')
                        self.assertFalse(submissions.drafts.exists())
                        self.assertEqual((await call(prepare, **params))['status'], 'invalid')

                        def historical_draft(evidence):
                            additions = dict(evaluations={}, repairs={}, relations={}, evidence=evidence)
                            draft = submissions.preview(params['title'], additions)
                            raw = encode(draft, compact=True)
                            digest = sha256(raw).hexdigest()
                            draft_id = f'draft-{digest}'
                            submissions.drafts.mkdir(exist_ok=True)
                            (submissions.drafts / f'{draft_id}.json').write_bytes(raw)
                            return dict(draft_id=draft_id, draft_sha256=digest,
                                        candidate_versions=candidate_versions(evidence))

                        ready = historical_draft(params['evidence'])
                        self.assertEqual(ready['candidate_versions'][0]['candidate_id'], 'synthetic-fix')
                        send_args = {key:ready[key] for key in ('draft_id', 'draft_sha256')} | dict(consent_record=str(request))
                        self.assertEqual((await call(submit, **send_args, delivery_target='upstream'))['status'], 'invalid_draft')
                        intake.send.assert_not_called()
                        received = await call(submit, **send_args)
                        self.assertEqual(received['review_state'], 'pending_review')
                        self.assertFalse(received['reused'])
                        self.assertNotIn('status_token', received)
                        self.assertEqual(received['receipt_binding']['candidates'], ready['candidate_versions'])
                        self.assertTrue((await call(submit, **send_args))['reused'])
                        later = historical_draft({EVIDENCE:fixture('+next\n')})
                        wrong = await call(lookup, receipt_id=received['receipt_id'], draft_sha256=later['draft_sha256'])
                        self.assertEqual(wrong['status'], 'invalid_binding')
                        intake.status.assert_not_called()
                        exact = await call(lookup, receipt_id=received['receipt_id'], draft_sha256=ready['draft_sha256'])
                        self.assertEqual(exact['receipt_binding'], received['receipt_binding'])
                        self.assertEqual(len(sent), 1)
                    tasks.cancel_scope.cancel()

    def test_routes_and_version_validation(self):
        self.assertTrue(capabilities(PRIVATE)['supports_supersedes'])
        self.assertFalse(capabilities(None)['can_submit'])
        for denied in ('https://github.com/v1/submissions', 'https://numericbridge.com.example/v1/submissions',
                       'https://numericbridge.com/v1/submissions?relay=1', 'http://numericbridge.com/v1/submissions'):
            with self.assertRaises(ValueError):
                capabilities(denied)
        self.assertEqual(candidate_versions({EVIDENCE:{'upstream_pr_draft':{}}}), [])
        good = fixture()
        with self.assertRaisesRegex(ValueError, 'UTF-8 bytes'):
            candidate_versions({EVIDENCE:good | {'patch_text':'different'}})
        with self.assertRaisesRegex(ValueError, 'target must match'):
            candidate_versions({EVIDENCE:good | {'candidate_version':good['candidate_version'] | {'target_commit':'3'*40}}})
        with self.assertRaisesRegex(ValueError, 'unique'):
            candidate_versions({EVIDENCE:good, 'evidence/submissions/synthetic-copy.json':good})
        with self.assertRaisesRegex(ValueError, 'native_on_target pending'):
            candidate_versions({EVIDENCE:good | {
                'upstream_pr_draft':good['upstream_pr_draft'] | {'pending_checks':[]}}})

    def test_pr_template_keeps_subheadings(self):
        draft = fixture()['upstream_pr_draft']
        self.assertEqual(check(draft)['status'], 'ok')
        body = draft['body_markdown'].replace('### Pre-commit Checks', '```markdown\n### Pre-commit Checks\n```')
        self.assertIn('missing repository template heading: Pre-commit Checks', check(draft | {'body_markdown':body})['problems'])
