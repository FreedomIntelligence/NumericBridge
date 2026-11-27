"""Test official snapshot verification without external requests."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from urllib.error import URLError

from .catalog_snapshot import CatalogUnavailable, OfficialCatalog, parse_manifest
from .catalog_snapshot_export import export
from .catalog_store import build_stage, make_directory, switch_current


def bundle(entries):
    raw = BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as archive:
        for name, value in entries:
            entry = tarfile.TarInfo(name)
            if isinstance(value, bytes):
                entry.size = len(value)
                archive.addfile(entry, BytesIO(value))
            else:
                entry.type, entry.linkname = value
                archive.addfile(entry)
    return raw.getvalue()


def manifest(raw, generation='approved-first'):
    return json.dumps(dict(schema_version=1, generation=generation, sha256=sha256(raw).hexdigest(),
                           bytes=len(raw))).encode()


ENTRIES = [('data', (tarfile.DIRTYPE, '')), ('evidence', (tarfile.DIRTYPE, '')),
           ('data/search-index.json', b'{}'), ('evidence/sample.json', b'{"sample": 1}')]


class SnapshotClientTests(unittest.TestCase):
    def test_retry_pin_cache_and_detect_corrupted_cache(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / 'cache'
            for name in ('first', 'second', 'third'):
                (root / name).mkdir()
            raw = bundle(ENTRIES)
            meta = manifest(raw)
            entry = parse_manifest(meta)
            requests = []
            net = {'current.json': meta, entry.filename: raw}

            def fetch(name):
                requests.append(name)
                if len(requests) == 1:
                    raise URLError('test offline')
                return BytesIO(net[name])

            first = OfficialCatalog(root / 'first', cache=cache, fetch=fetch)
            with self.assertRaisesRegex(CatalogUnavailable, 'over HTTPS'):
                first()
            self.assertEqual((first() / 'evidence/sample.json').read_bytes(), ENTRIES[-1][1])
            self.assertEqual(first.snapshot, entry)
            self.assertEqual(first(), first())
            self.assertEqual(requests, ['current.json', 'current.json', entry.filename])
            second = OfficialCatalog(root / 'second', cache=cache, fetch=fetch)
            self.assertEqual((second() / 'data/search-index.json').read_bytes(), b'{}')
            self.assertEqual(requests[-1], 'current.json')
            (cache / entry.filename).write_bytes(b'x' + raw[1:])
            third = OfficialCatalog(root / 'third', cache=cache, fetch=fetch)
            self.assertTrue(third().exists())
            self.assertEqual(requests[-2:], ['current.json', entry.filename])
            self.assertEqual((cache / entry.filename).read_bytes(), raw)

    def test_reject_checksum_errors_and_non_public_members_before_caching(self):
        cases = [(bundle(ENTRIES), b'wrong digest')]
        cases += [(blob, blob) for blob in (
            bundle([*ENTRIES, ('archive/reports/hidden.json', b'no')]),
            bundle([*ENTRIES, ('data/../../escape', b'no')]),
            bundle([*ENTRIES, ('data/link', (tarfile.SYMTYPE, '/etc/passwd'))]),
            bundle([*ENTRIES, ('data/search-index.json', b'duplicate')]),
            bundle(ENTRIES[:1]),
        )]
        for expected, actual in cases:
            with self.subTest(digest=sha256(expected).hexdigest()), TemporaryDirectory() as temporary:
                root = Path(temporary)
                meta = manifest(expected)
                resolver = OfficialCatalog(root, cache=root / 'cache',
                                           fetch=lambda name: BytesIO(meta if name == 'current.json' else actual))
                with self.assertRaises(CatalogUnavailable):
                    resolver()
                self.assertIsNone(resolver.snapshot)
                self.assertFalse((root / 'site').exists())
                self.assertEqual(list((root / 'cache').iterdir()), [])

    def test_manifest_cannot_select_a_different_origin(self):
        raw = manifest(bundle(ENTRIES))
        for bad in (raw[:-1] + b', "url":"https://example.invalid"}',
                    raw[:-1] + b', "schema_version":1}',
                    raw.replace(b'approved-first', b'../approved-first')):
            with self.assertRaises(ValueError):
                parse_manifest(bad)


class SnapshotExportTests(unittest.TestCase):
    def test_atomic_manifest_references_public_content_only_across_generations(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, output, source, reports = [root / name for name in ('store', 'out', 'source', 'reports')]
            for directory in (store, output, source, reports):
                make_directory(directory)
            make_directory(store / 'releases')
            make_directory(source / 'data')
            make_directory(source / 'evidence')
            for name in ('differences-raw', 'differences', 'evaluations', 'relations', 'repairs', 'search-index'):
                (source / f'data/{name}.json').write_text('{}')
            (reports / 'internal.md').write_text('never exported')
            old_release = store / 'releases/older'
            build_stage(old_release, source, reports, '1' * 40)
            switch_current(store, 'older')
            old = export(store, output)
            self.assertEqual(export(store, output), old)
            with tarfile.open(output / old.filename) as archived:
                self.assertEqual(set(p.name.split('/')[0] for p in archived), {'data', 'evidence'})
            newest = store / 'releases/newer'
            (source / 'evidence/example.md').write_text('new evidence')
            build_stage(newest, source, reports, '2' * 40)
            switch_current(store, 'newer')
            new = export(store, output)
            self.assertNotEqual(old.sha256, new.sha256)
            self.assertEqual(parse_manifest((output / 'current.json').read_bytes()), new)
            self.assertTrue((output / old.filename).exists())
            switch_current(store, 'older')
            self.assertEqual(export(store, output), old)

    def test_store_output_must_be_separate(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'output').mkdir()
            with self.assertRaisesRegex(ValueError, 'must not overlap'):
                export(root, root / 'output')


if __name__ == '__main__':
    unittest.main()
