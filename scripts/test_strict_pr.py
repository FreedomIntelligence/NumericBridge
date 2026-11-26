"""Offline strict v1 PR regressions; no real credentials, remotes, or submissions."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.strict_pr import inspect_template, load_registry, render_pr, verify_pr


SLUG = 'fixture/project'
ANONYMOUS = {'mode': 'anonymous'}
TEMPLATE = '.github/pull_request_template.md'
OFFICIAL = ('<!-- Instructions stay in Git. -->\r\n'
            '## Summary ###\r\n\r\n'
            '### Observed result\r\n\r\n'
            '# Pre-commit checks\r\n\r\n'
            '- [x] Human reviewer ran the tests\r\n'
            '* [ ] Docs were updated\r\n').encode()


class StrictPRTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='strict-pr-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'repository'
        self.git('init', '-q', '-b', 'main', str(self.repo), cwd=self.root)
        self.registry = {SLUG: self.repo}
        self.commit = self.write_commit({TEMPLATE: OFFICIAL,
                                         'CONTRIBUTING.md': b'You must link the issue. Provide DCO. You must personally run tests.\n'})

    def git(self, *args, cwd=None):
        out = subprocess.run(['git', '-C', str(cwd or self.repo), '-c', 'core.hooksPath=/dev/null',
                              '-c', 'commit.gpgsign=false', '-c', 'user.name=Fixture',
                              '-c', 'user.email=fixture@example.invalid', *args], check=True,
                             capture_output=True, timeout=10)
        return out.stdout.decode().strip()

    def write_commit(self, files):
        for name, text in files.items():
            dest = self.repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(text)
        self.git('add', '--all')
        self.git('commit', '-qm', 'Offline fixture')
        return self.git('rev-parse', 'HEAD')

    def render(self, **overrides):
        return render_pr(**(dict(repository=SLUG, base_branch='main', target_commit=self.commit,
                                  title='Correct the synthetic result',
                                  section_contents=['A reproducible error.', 'Two samples are correct.\n\n```diff\n- bad\n+ good\n```',
                                                    'The maintainer will review the pending items.'],
                                  checks={}, attribution=ANONYMOUS, trusted_repos=self.registry) | overrides))

    def test_exact_template_headings_checklist_and_canonical_footer(self):
        metadata = inspect_template(repository=SLUG, target_commit=self.commit, trusted_repos=self.registry,
                                    base_branch='main')
        self.assertEqual(metadata['slots'], [dict(index=0, level=2, heading='Summary'),
                                             dict(index=1, level=3, heading='Observed result'),
                                             dict(index=2, level=1, heading='Pre-commit checks')])
        self.assertEqual(metadata['checklist'][0], dict(section_index=2, label='Human reviewer ran the tests'))
        data = self.render()
        self.assertEqual(data['template_proof'], dict(target_commit=self.commit, path=TEMPLATE,
                                                      sha256=sha256(OFFICIAL).hexdigest(), source='repository_template'))
        self.assertEqual(data['contribution_requirements']['commit'], self.commit)
        body = data['body_markdown']
        heading_lines = [line for line in body.splitlines() if line.startswith('#')]
        self.assertEqual(heading_lines, ['## Summary ###', '### Observed result', '# Pre-commit checks'])
        self.assertIn('- [ ] Human reviewer ran the tests\n* [ ] Docs were updated', body)
        self.assertNotIn('Instructions stay in Git.', body)
        self.assertNotIn('[x]', body)
        self.assertEqual(body.count('Diagnosed and verified with'), 1)
        self.assertTrue({'dco', 'human_review', 'human_test', 'issue_link', 'upstream_checklist'} <= set(data['pending_checks']))
        self.assertEqual(verify_pr(data, attribution=ANONYMOUS, trusted_repos=self.registry), data)

    def test_scope_of_trusted_footer_and_verification_against_tampering(self):
        named = {'mode': 'nickname', 'name': 'Reviewer_1'}
        data = self.render(attribution=named)
        self.assertIn('Discovered by Reviewer\\_1</sub>', data['body_markdown'])
        self.assertEqual(len(data['attribution']['scope']), 3)
        for updates in (dict(body_markdown=data['body_markdown'].replace('### Observed', '## Observed')),
                        dict(body_markdown=data['body_markdown'] + '\n## Summary\n\nDuplicate.'),
                        dict(template_proof=data['template_proof'] | {'sha256': 'a' * 64}),
                        dict(template_proof=data['template_proof'] | {'path': 'another.md'}),
                        dict(pending_checks=[]), dict(schema_version=True)):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, 'does not match'):
                verify_pr(data | updates, attribution=named, trusted_repos=self.registry)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            verify_pr(data, attribution=ANONYMOUS, trusted_repos=self.registry)
        with self.assertRaises(ValueError):
            self.render(attribution={'mode': 'anonymous', 'name': 'Unapproved'})

    def test_checklist_only_slot_can_keep_just_official_items(self):
        metadata = inspect_template(repository=SLUG, target_commit=self.commit, trusted_repos=self.registry,
                                    base_branch='main')
        self.assertEqual(metadata['checklist_only_slots'], [2])
        draft = self.render(section_contents=['Observed a mismatch.', 'The fixture matched.', ''])
        self.assertIn('# Pre-commit checks\n\n- [ ] Human reviewer ran the tests\n* [ ] Docs were updated',
                      draft['body_markdown'])
        self.assertIn('upstream_checklist', draft['pending_checks'])
        self.assertEqual(verify_pr(draft, attribution=ANONYMOUS, trusted_repos=self.registry), draft)
        with self.assertRaisesRegex(ValueError, 'non-empty visible content'):
            self.render(section_contents=['', 'The fixture matched.', ''])

    def test_reject_undefined_empty_and_misleading_slot_content(self):
        examples = ['', ' \t ', '<!-- only a comment -->', 'TODO', '{{fill me}}',
                    'Some prose.\n\n## Summary\n\nSpoofed section.',
                    'Some prose.\n\nLate section\n====',
                    'Some prose.\n\n- [x] Test passed', '&#32;', '[](https://example.test)',
                    'Some prose.\n- - ## Nested heading.', 'Some prose.\n\nAll target tests passed.',
                    'Some prose.\n\n<sub>Untrusted attribution</sub>',
                    'Some prose.\n\n```', '```\n \n```',
                    'Some prose.\n- I personally ran all tests.', 'Some prose.\n> Signed-off-by: Nobody']
        for contents in examples:
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                self.render(section_contents=[contents, 'Other evidence.', 'Manual checklist.'])
        with self.assertRaisesRegex(ValueError, 'one content block per'):
            self.render(section_contents=['Not enough.'])
        for title in ('', 'A\nB', 'Draft TODO'):
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, 'title'):
                self.render(title=title)

    def test_only_machine_checks_with_evidence_can_pass(self):
        success = self.render(checks={
            'repo_style_test': {'status': 'passed', 'evidence': 'Red: 1 failed; green: 1 passed.'},
            'native_on_target': {'status': 'failed', 'evidence': 'Target run did not complete.'},
            'performance': {'status': 'not_applicable', 'evidence': 'No performance claim.'},
            'issue_link': {'status': 'passed', 'evidence': f'https://github.com/{SLUG}/issues/10'},
        })
        self.assertEqual(success['pending_checks'], ['applies_cleanly', 'native_on_target', 'human_review',
                                                     'human_test', 'dco', 'upstream_checklist'])
        for bad in ({'native_on_target': {'status': 'passed'}},
                    {'native_on_target': {'status': 'not_applicable', 'evidence': 'Skipped.'}},
                    {'human_review': {'status': 'passed', 'evidence': 'An agent checked.'}},
                    {'human_test': {'status': 'not_applicable', 'evidence': 'An agent ran it.'}},
                    {'dco': {'status': 'failed', 'evidence': 'Will be signed later.'}},
                    {'issue_link': {'status': 'passed', 'evidence': 'https://other.example/issues/1'}},
                    {'unknown_item': {'status': 'pending'}}, {'template_body': {'status': 'passed'}},
                    {'upstream_checklist': {'status': 'passed'}}):
            with self.subTest(checks=bad), self.assertRaises(ValueError):
                self.render(checks=bad)

    def test_choice_and_new_pinned_commit(self):
        second = 'docs/PULL_REQUEST_TEMPLATE/test.md'
        latest = self.write_commit({second: b'## Different\n', TEMPLATE: b'## New heading\n'})
        with self.assertRaisesRegex(ValueError, 'Multiple official templates'):
            self.render(target_commit=latest, section_contents=['Byte-level change.'])
        result = self.render(target_commit=latest, template_path=second, section_contents=['Byte-level change.'])
        self.assertEqual(result['template_proof']['path'], second)
        self.assertEqual(result['template_proof']['target_commit'], latest)
        self.assertIn('## Different\n', result['body_markdown'])
        self.assertIn('### Observed result', self.render()['body_markdown'])
        with self.assertRaisesRegex(ValueError, 'not an official template'):
            self.render(target_commit=latest, template_path='/tmp/not-approved.md')

    def test_fallback_only_for_verified_absence_and_manual_for_unsupported(self):
        invalid = deepcopy(self.render())
        for content in (b'<details>\n<summary>Summary</summary>\n</details>\n',
                        b'No headings, just a visible checklist.\n- [ ] Test.\n',
                        b'## Duplicate\n## Duplicate\n', b'## Ignored\n\n    - [ ] Nested item\n',
                        b'## Placeholder\n\nTODO\n'):
            with self.subTest(content=content):
                commit = self.write_commit({TEMPLATE: content})
                result = self.render(target_commit=commit, section_contents=None)
                self.assertEqual(result['format_status'], 'manual_review')
                self.assertIsNone(result['body_markdown'])
                self.assertEqual(result['template_proof']['sha256'], sha256(content).hexdigest())
                self.assertIn('template_body', result['pending_checks'])
                self.assertEqual(verify_pr(result, attribution=ANONYMOUS, trusted_repos=self.registry), result)
                with self.assertRaisesRegex(ValueError, 'manual review'):
                    self.render(target_commit=commit, section_contents=['Bypass.'])
        self.git('rm', '-q', TEMPLATE)
        commit = self.write_commit({'example.py': b'print("demo")\n'})
        fallback = self.render(target_commit=commit, section_contents=['A problem.', 'A change.', 'A validation.', 'A patch.'])
        self.assertEqual(fallback['template_source'], 'numericbridge_default')
        self.assertTrue(fallback['fallback_used'])
        self.assertIsNone(fallback['template_proof']['path'])
        self.assertEqual([line for line in fallback['body_markdown'].splitlines() if line.startswith('#')],
                         ['## Problem', '## Change', '## Validation', '## Patch'])
        invalid['source']['target_commit'] = commit
        with self.assertRaisesRegex(ValueError, 'not an official template'):
            verify_pr(invalid, attribution=ANONYMOUS, trusted_repos=self.registry)

    def test_symlink_template_is_pinned_but_never_followed(self):
        secret = self.root / 'outside.md'
        secret.write_text('## Do not read this file\n', encoding='utf-8')
        self.git('rm', '-q', TEMPLATE)
        (self.repo / TEMPLATE).parent.mkdir(exist_ok=True)
        (self.repo / TEMPLATE).symlink_to(secret)
        commit = self.write_commit({})
        data = self.render(target_commit=commit, section_contents=None)
        self.assertEqual(data['format_status'], 'manual_review')
        self.assertEqual(data['template_proof']['sha256'], sha256(str(secret).encode()).hexdigest())
        self.assertIsNone(data['body_markdown'])
        secret.write_text('## Changed externally\n', encoding='utf-8')
        self.assertEqual(verify_pr(data, attribution=ANONYMOUS, trusted_repos=self.registry), data)

    def test_branch_ancestry_rev_resolution_and_absent_clone_fail_closed(self):
        self.git('switch', '-qc', 'unmerged')
        head = self.write_commit({TEMPLATE: b'## Open PR only\n'})
        self.git('switch', '-q', 'main')
        self.git('tag', '-am', 'A tag object', 'annotated', self.commit)
        tag = self.git('rev-parse', 'refs/tags/annotated')
        with self.assertRaisesRegex(ValueError, 'not reachable'):
            self.render(target_commit=head)
        with self.assertRaisesRegex(ValueError, 'full lowercase'):
            self.render(target_commit='HEAD')
        with self.assertRaisesRegex(ValueError, 'not an indirect'):
            self.render(target_commit=tag)
        with self.assertRaisesRegex(ValueError, 'No trusted offline'):
            self.render(trusted_repos={})
        with self.assertRaisesRegex(ValueError, 'valid short Git branch'):
            self.render(base_branch='refs/pull/1/head')
        self.git('remote', 'add', 'origin', 'https://invalid.example/no-network')
        with self.assertRaisesRegex(ValueError, 'authoritative base branch'):
            self.render()
        self.git('update-ref', 'refs/remotes/origin/main', self.commit)
        self.git('merge', '--ff-only', 'unmerged')
        self.assertEqual(self.render()['source']['target_commit'], self.commit)
        with self.assertRaisesRegex(ValueError, 'not reachable'):
            self.render(target_commit=head)

    def test_only_operator_registry_resolves_configured_local_git_directories(self):
        config = self.root / 'trusted.json'
        config.write_text(json.dumps({SLUG: 'repository'}), encoding='utf-8')
        self.assertEqual(load_registry(config), {SLUG: self.repo.resolve()})
        self.assertEqual(load_registry(None), {})
        config.write_text('{"fixture/project":"repository","fixture/project":"repository"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Duplicate repository'):
            load_registry(config)
        (self.repo / 'nested').mkdir()
        config.write_text('{"fixture/project":"repository/nested"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'root of a Git worktree'):
            load_registry(config)
        config.write_text('{"../project":"repository"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'owner/repo'):
            load_registry(config)


if __name__ == '__main__':
    unittest.main()
