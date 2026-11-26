"""Offline regression for upstream status and draft provenance, using only local Git repos."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tools import pr_draft, upstream_status


SOURCE = 'kernel.py'
BEFORE = 'print("defective fixture")\n'
AFTER = 'print("fixed fixture")\n'
PATTERN = r'^print\("defective fixture"\)$'
RELEASE = 'v1.1.0'
PIN = 'releases/v1.0.0'
MERGE_SUBJECT = 'Merge synthetic layout fix'
OPEN_SUBJECT = 'Proposed synthetic layout fix'
PR_TEMPLATE = '.github/PULL_REQUEST_TEMPLATE.md'


def git(repo, *args):
    return subprocess.run(
        ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
         '-c', 'tag.gpgsign=false', '-c', 'user.name=Local Fixture',
         '-c', 'user.email=fixture@example.invalid', *args],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout


def commit(repo, subject):
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', subject)
    return git(repo, 'rev-parse', 'HEAD').strip()


class MergedOnlyTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='numericbridge-merged-only-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.origin, self.clone, self.patch = root / 'origin', root / 'clone', root / 'candidate.patch'
        git(root, 'init', '-q', '-b', 'main', str(self.origin))
        (self.origin / SOURCE).write_text(BEFORE, encoding='utf-8')
        self.pin_commit = commit(self.origin, 'Pinned distribution')
        git(self.origin, 'branch', PIN)
        (self.origin / 'release-notes.txt').write_text('Release still contains the defect.\n', encoding='utf-8')
        self.release_commit = commit(self.origin, 'Publish v1.1.0')
        git(self.origin, 'tag', RELEASE)
        (self.origin / 'main-notes.txt').write_text('Unrelated main change.\n', encoding='utf-8')
        self.main_commit = commit(self.origin, 'Continue development')
        git(root, 'clone', '-q', str(self.origin), str(self.clone))
        (self.clone / SOURCE).write_text(AFTER, encoding='utf-8')
        self.patch.write_text(git(self.clone, 'diff', '--', SOURCE), encoding='utf-8')
        git(self.clone, 'restore', '--', SOURCE)

    def open_pr(self):
        git(self.origin, 'checkout', '-qb', 'candidate')
        (self.origin / SOURCE).write_text(AFTER, encoding='utf-8')
        template = self.origin / PR_TEMPLATE
        template.parent.mkdir(exist_ok=True)
        template.write_text('## Open PR only\n\nNot an upstream requirement.\n', encoding='utf-8')
        candidate = commit(self.origin, OPEN_SUBJECT)
        git(self.origin, 'update-ref', 'refs/pull/7/head', candidate)
        tree = git(self.origin, 'rev-parse', f'{candidate}^{{tree}}').strip()
        preview = git(self.origin, 'commit-tree', tree, '-p', self.main_commit, '-p', candidate,
                      '-m', 'Simulated open pull request merge preview').strip()
        git(self.origin, 'update-ref', 'refs/pull/7/merge', preview)
        git(self.origin, 'checkout', '-q', 'main')
        git(self.clone, 'fetch', '-q', 'origin', 'refs/heads/candidate:refs/remotes/origin/candidate',
            '+refs/pull/7/*:refs/pull/7/*')
        return candidate

    def status(self, release=RELEASE, *, fetch=False, refs=None):
        return upstream_status.check(self.clone, refs or ('main', release, PIN), paths=[SOURCE],
                                     pattern=PATTERN, patch=self.patch, log_grep=['synthetic layout fix'],
                                     fetch=fetch)

    def assert_row(self, row, commit_id, state, *, history=()):
        self.assertEqual(row['commit'], commit_id)
        self.assertEqual(row['state'], state)
        pending = state == 'present'
        self.assertIs(row['present'], pending)
        self.assertIs(row['applies'], pending)
        self.assertIs(row['reverse_applies'], not pending)
        self.assertEqual([item['commit'] for item in row['merged_commits']], list(history))

    def build_fixture(self, pinned_row):
        # Model a case build using the selected Git object, not the clone's working tree.
        source = git(self.clone, 'show', f"{pinned_row['commit']}:{SOURCE}")
        return subprocess.run([sys.executable, '-IB', '-'], input=source, check=True,
                              capture_output=True, text=True, timeout=30).stdout.strip()

    def draft(self, status, *, backport=False, fixed_in=None, fixed_commit=None):
        main, release, pin = status['refs']
        target = pin if backport else main
        decision = dict(hypothesis='layout', repository='fixture/runtime', target_branch=target['ref'],
                        decision='backport_draft' if backport else 'pr_draft_main',
                        reason='fixed_upstream_backport' if backport else 'still_present',
                        upstream=dict(main=main['state'], latest_release=release['state'], pinned=pin['state']),
                        fixed_in=fixed_in, fixed_commit=fixed_commit,
                        upgrade_blocker='distribution pins v1.0.0' if backport else None,
                        checks={'applies_cleanly': 'pass' if target['applies'] else 'fail'})
        sections = dict(title='Repair the fixture layout', problem='The fixture reports a defect.',
                        change='Produce the corrected output.', reproducer='Run the synthetic fixture.',
                        validation='Check the selected Git tree.',
                        patch='```diff\n' + self.patch.read_text(encoding='utf-8') + '```')
        result = pr_draft.scaffold(decision, sections, upstream=status, repo=self.clone, ref=target['commit'])
        self.assertEqual(pr_draft.check(result)['status'], 'ok')
        self.assertEqual(result['contribution_requirements']['commit'], target['commit'])
        self.assertEqual(result['base_branch'], target['ref'])
        self.assertNotIn('applies_cleanly', result['pending_checks'])
        return result

    def test_open_pr_refs_and_checkout_do_not_change_build_adoption_or_draft(self):
        before = self.status()
        for row, expected in zip(before['refs'], (self.main_commit, self.release_commit, self.pin_commit)):
            self.assert_row(row, expected, 'present')
        main_draft = self.draft(before)
        candidate = self.open_pr()
        git(self.clone, 'checkout', '-qb', 'candidate', candidate)
        for ref in ('refs/heads/candidate', 'refs/remotes/origin/candidate', 'refs/pull/7/head', 'refs/pull/7/merge'):
            self.assertEqual(git(self.clone, 'show', f'{ref}:{SOURCE}'), AFTER)
        self.assertEqual((self.clone / SOURCE).read_text(encoding='utf-8'), AFTER)
        self.assertTrue((self.clone / PR_TEMPLATE).is_file())

        after = self.status(fetch=True)
        self.assertEqual(after, before)
        self.assertEqual(self.build_fixture(after['refs'][2]), 'defective fixture')
        self.assertEqual(self.draft(after), main_draft)
        self.assertEqual(git(self.clone, 'rev-parse', 'HEAD').strip(), candidate)
        self.assertEqual(git(self.clone, 'status', '--porcelain'), '')
        self.assertEqual(main_draft['decision']['decision'], 'pr_draft_main')
        self.assertEqual(main_draft['template_source'], 'numericbridge_default')
        self.assertNotIn(OPEN_SUBJECT, main_draft['body_markdown'])

    def test_local_only_main_does_not_override_tracked_upstream_main(self):
        candidate = self.open_pr()
        git(self.clone, 'checkout', '-q', '--detach', candidate)
        git(self.clone, 'branch', '-f', 'main', candidate)
        git(self.clone, 'branch', '-f', PIN, candidate)
        self.assertEqual(git(self.clone, 'rev-parse', 'main').strip(), candidate)
        self.assertEqual(git(self.clone, 'rev-parse', 'origin/main').strip(), self.main_commit)
        upstream = self.status(fetch=True)
        self.assert_row(upstream['refs'][0], self.main_commit, 'present')
        self.assert_row(upstream['refs'][2], self.pin_commit, 'present')
        explicit = self.status(refs=('refs/heads/main', candidate))
        self.assertEqual([row['commit'] for row in explicit['refs']], [candidate, candidate])

    def test_merge_changes_main_not_release_or_pin_until_each_contains_fix(self):
        candidate = self.open_pr()
        git(self.origin, 'merge', '--no-ff', '-qm', MERGE_SUBJECT, 'candidate')
        merge_commit = git(self.origin, 'rev-parse', 'HEAD').strip()
        self.assertNotEqual(merge_commit, candidate)
        self.assertEqual(git(self.clone, 'rev-parse', 'main').strip(), self.main_commit)

        status = self.status(fetch=True)
        main, release, pin = status['refs']
        self.assertEqual(git(self.clone, 'rev-parse', 'origin/main').strip(), merge_commit)
        self.assert_row(main, merge_commit, 'fixed', history=(merge_commit,))
        self.assert_row(release, self.release_commit, 'present')
        self.assert_row(pin, self.pin_commit, 'present')
        self.assertEqual(self.build_fixture(main), 'fixed fixture')
        self.assertEqual(self.build_fixture(pin), 'defective fixture')
        unpublished = self.draft(status, backport=True, fixed_commit=merge_commit)
        self.assertEqual(unpublished['decision']['decision'], 'backport_draft')
        self.assertIn('an unreleased main commit', unpublished['body_markdown'])
        self.assertIn(merge_commit, unpublished['body_markdown'])
        self.assertIn('distribution pins v1.0.0', unpublished['body_markdown'])
        self.assertNotIn(OPEN_SUBJECT, unpublished['body_markdown'])

        fixed_tag = 'v1.2.0'
        git(self.origin, 'tag', fixed_tag, merge_commit)
        released = self.status(fixed_tag, fetch=True)
        main, release, pin = released['refs']
        self.assert_row(main, merge_commit, 'fixed', history=(merge_commit,))
        self.assert_row(release, merge_commit, 'fixed', history=(merge_commit,))
        self.assert_row(pin, self.pin_commit, 'present')
        backport = self.draft(released, backport=True, fixed_in=fixed_tag, fixed_commit=merge_commit)
        self.assertIn(f'Fixed upstream in {fixed_tag}', backport['body_markdown'])
        self.assertEqual(backport['contribution_requirements']['commit'], self.pin_commit)


if __name__ == '__main__':
    unittest.main()
