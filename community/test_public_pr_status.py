"""Exercise PR collection and publication boundaries with a fake public forge."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from community import public_pr_status as status


NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
URL = 'https://github.com/example/repo/pull/'


class PublicPRStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.registry = self.directory / 'registry.json'
        self.targets = self.directory / 'tracking-targets.json'
        self.cache = self.directory / 'cache.json'
        self.output = self.directory / 'public.json'
        self.admin = self.directory / 'admin.json'
        self.github = Mock(side_effect=self.github_response)
        self.gitcode = Mock(side_effect=AssertionError('Unexpected GitCode call'))

    def registry_at(self, path, *urls):
        path.write_text(json.dumps({'schema_version': 1, 'contributions': [
            {'forge': 'github', 'url': url, 'irrelevant_metadata': 'never-emit'} for url in urls
        ]}))

    @staticmethod
    def github_response(path):
        if '/pulls/' in path:
            number = int(path.rsplit('/', 1)[1])
            return dict(number=number, html_url=URL + str(number), base={'repo': {'private': False}},
                        state='open', updated_at=status.stamp(NOW), head={'sha': f'{number:040x}'})
        return dict(check_runs=[dict(status='completed', conclusion='success')])

    def tick(self, minute=0, *, watch=True, budget=2):
        kwargs = {'tracking_targets': self.targets, 'admin_output': self.admin} if watch else {}
        return status.refresh(self.registry, self.cache, self.output, now=NOW + timedelta(minutes=minute),
                              client=self.github, gitcode_client=self.gitcode, budget=budget, **kwargs)

    @staticmethod
    def entries(path):
        return json.loads(path.read_text())['pull_requests']

    def test_shared_budget_and_sanitized_split_outputs(self):
        published, watched, overlap = (URL + str(n) for n in range(1, 4))
        self.registry_at(self.registry, published, overlap)
        self.registry_at(self.targets, watched, overlap.upper() + '/')
        counts = []
        for minute in (0, 2, 4):
            self.github.reset_mock()
            counts.append(self.tick(minute))
            self.assertEqual(self.github.call_count, 2)  # One PR lookup and one check-runs lookup.
        self.assertEqual(counts, [1, 1, 2])
        self.gitcode.assert_not_called()
        self.assertEqual(set(self.entries(self.output)), {published, overlap})
        admin = self.entries(self.admin)
        self.assertEqual(set(admin), {published, watched, overlap})
        self.assertEqual(set(json.loads(self.cache.read_text())), set(admin))
        for row in admin.values():
            self.assertEqual(set(row), set(status.PUBLIC_FIELDS))
            self.assertEqual(row['state'], 'open')
            self.assertEqual(row['checks'], 'passing')
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.admin.stat().st_mode), 0o644)

    def test_transient_cache_retention_cannot_publish_watch_only_or_removed_rows(self):
        formerly_published, removed = (URL + str(n) for n in (1, 2))
        self.registry_at(self.registry, formerly_published)
        self.registry_at(self.targets, removed)
        self.tick(budget=4)
        cached = self.entries(self.admin)[formerly_published]

        self.registry_at(self.registry)
        self.registry_at(self.targets, formerly_published)
        self.github.side_effect = OSError('Transient forge error')
        self.assertEqual(self.tick(2), 0)
        self.assertEqual(self.entries(self.output), {})
        self.assertEqual(self.entries(self.admin), {formerly_published: cached})
        self.assertEqual(set(json.loads(self.cache.read_text())), {formerly_published})

        self.registry_at(self.registry, formerly_published)
        self.assertEqual(self.tick(4), 1)
        self.assertEqual(self.entries(self.output), {formerly_published: cached})

    def test_invalid_targets_are_rejected_before_any_write_or_forge_call(self):
        self.registry_at(self.registry, URL + '1')
        for path in (self.cache, self.output, self.admin):
            path.write_text('untouched')
        for invalid in ('{', '[]', '{"schema_version":2,"contributions":[]}',
                        '{"schema_version":1,"contributions":[null]}',
                        '{"schema_version":1,"contributions":[{"forge":"github","url":"https://github.com/login"}]}'):
            with self.subTest(invalid=invalid):
                self.targets.write_text(invalid)
                with self.assertRaises(ValueError):
                    self.tick()
                for path in (self.cache, self.output, self.admin):
                    self.assertEqual(path.read_text(), 'untouched')
        self.github.assert_not_called()
        self.gitcode.assert_not_called()

    def test_missing_pair_and_path_aliases_fail_without_touching_public_output(self):
        self.registry_at(self.registry, URL + '1')
        self.registry_at(self.targets, URL + '2')
        self.output.write_text('untouched')
        symlink, hardlink = self.directory / 'symlink', self.directory / 'hardlink'
        symlink.symlink_to(self.output)
        os.link(self.output, hardlink)
        cases = ({'tracking_targets': self.targets}, {'admin_output': self.admin},
                 {'tracking_targets': self.registry, 'admin_output': self.admin},
                 {'tracking_targets': self.targets, 'admin_output': self.cache},
                 *({'tracking_targets': self.targets, 'admin_output': path}
                   for path in (self.output, symlink, hardlink)))
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                status.refresh(self.registry, self.cache, self.output, client=self.github, **kwargs)
        self.assertEqual(self.output.read_text(), 'untouched')
        self.github.assert_not_called()

    def test_admin_output_failure_leaves_public_output_and_cache_untouched(self):
        self.registry_at(self.registry, URL + '1')
        self.registry_at(self.targets, URL + '2')
        self.output.write_text('existing public output')
        self.cache.write_text('{}')
        self.admin = self.directory / 'missing' / 'admin.json'
        with self.assertRaises(FileNotFoundError):
            self.tick()
        self.assertEqual(self.output.read_text(), 'existing public output')
        self.assertEqual(self.cache.read_text(), '{}')

    def test_legacy_mode_omits_admin_output_and_excludes_prior_watch_cache(self):
        published, watched = URL + '1', URL + '2'
        self.registry_at(self.registry, published)
        self.registry_at(self.targets, watched)
        self.assertEqual(self.tick(budget=4), 1)
        old_admin = self.admin.read_bytes()
        self.assertEqual(self.tick(2, watch=False), 1)
        self.assertEqual(set(self.entries(self.output)), {published})
        self.assertEqual(set(json.loads(self.cache.read_text())), {published})
        self.assertEqual(self.admin.read_bytes(), old_admin)

    def test_cli_passes_both_options_and_reports_configuration_errors(self):
        base = ['collector', '--registry', str(self.registry), '--cache', str(self.cache), '--output', str(self.output)]
        argv = base + ['--tracking-targets', str(self.targets), '--admin-output', str(self.admin)]
        with patch('sys.argv', argv), patch.object(status, 'refresh', return_value=2) as refresh, redirect_stdout(StringIO()):
            status.main()
        refresh.assert_called_once_with(self.registry, self.cache, self.output,
                                        tracking_targets=self.targets, admin_output=self.admin)
        with patch('sys.argv', argv[:-2]), redirect_stderr(StringIO()) as stderr, self.assertRaises(SystemExit) as error:
            status.main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn('must be supplied together', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
