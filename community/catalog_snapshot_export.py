"""Export an operator-verified public catalog generation; never change the catalog."""

import argparse
from dataclasses import asdict
import fcntl
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from .catalog_snapshot import MAX_ARCHIVE, MAX_EXPANDED, MAX_MANIFEST, MAX_MEMBERS, Snapshot, parse_manifest, stream_hash
from .catalog_store import DEFAULT_ROOT, directory, read_current, require, sync_directory, verify_release


class VerifiedReader:
    def __init__(self, source):
        self.source = source
        self.digest = sha256()

    def read(self, size):
        data = self.source.read(size)
        self.digest.update(data)
        return data


def archive_public(release, hashes, output):
    public = {name[5:]: digest for name, digest in hashes.items()
              if name.startswith(('site/data/', 'site/evidence/'))}
    require(len(public) + 2 < MAX_MEMBERS, 'Too many public catalog files')
    size = 0
    folders = {'data', 'evidence'}
    with output.open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0,
                                                compresslevel=6) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
            for folder in sorted(folders):
                entry = tarfile.TarInfo(folder)
                entry.type, entry.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(entry)
            for name in sorted(public):
                parents = list(reversed(Path(name).parents))
                for parent in parents:
                    folder = parent.as_posix()
                    if folder == '.' or folder in folders:
                        continue
                    folders.add(folder)
                    entry = tarfile.TarInfo(folder)
                    entry.type, entry.mode = tarfile.DIRTYPE, 0o755
                    archive.addfile(entry)
                source_path = release / 'site' / name
                with source_path.open('rb') as source:
                    length = os.fstat(source.fileno()).st_size
                    size += length
                    require(size <= MAX_EXPANDED - 16 * 1024 * 1024, 'Public catalog exceeds export size limit')
                    reader = VerifiedReader(source)
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode = length, 0o644
                    archive.addfile(entry, reader)
                    require(reader.digest.hexdigest() == public[name], 'Published catalog changed while archiving')
    require({'data', 'evidence'} <= folders and len(public) + len(folders) <= MAX_MEMBERS,
            'Invalid public catalog archive layout')


def matching(path, entry):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != entry.bytes:
        return False
    with path.open('rb') as stream:
        return stream_hash(stream, entry.bytes) == entry.sha256


def export(root, output):
    root, output = (path.resolve(strict=True) for path in (root, output))
    require(not (root.is_relative_to(output) or output.is_relative_to(root)),
            'Snapshot output and catalog store must not overlap')
    with directory(root, controlled=True) as store, directory(output, controlled=True) as destination:
        # A store-change event can arrive before promotion releases its lock.
        fcntl.flock(store, fcntl.LOCK_SH)
        fcntl.flock(destination, fcntl.LOCK_EX)
        generation = read_current(root)
        require(generation, 'No published catalog generation')
        manifest = output / 'current.json'
        if manifest.is_file() and not manifest.is_symlink():
            with manifest.open('rb') as stream:
                raw = stream.read(MAX_MANIFEST + 1)
            if len(raw) <= MAX_MANIFEST:
                try:
                    entry = parse_manifest(raw)
                except ValueError:
                    entry = None
                if entry and entry.generation == generation and matching(output / entry.filename, entry):
                    return entry
        release = root / 'releases' / generation
        hashes = verify_release(release)
        with TemporaryDirectory(prefix='.catalog-', dir=output) as work:
            archive = Path(work) / 'archive'
            archive_public(release, hashes, archive)
            size = archive.stat().st_size
            require(0 < size <= MAX_ARCHIVE, 'Compressed public catalog exceeds export size limit')
            with archive.open('rb') as stream:
                digest = stream_hash(stream, size)
                os.fsync(stream.fileno())
            entry = Snapshot(generation, digest, size)
            archive.chmod(0o644)
            os.replace(archive, output / entry.filename)
            sync_directory(output)
            candidate = Path(work) / 'manifest'
            with candidate.open('xb') as stream:
                stream.write((json.dumps({'schema_version': 1, **asdict(entry)}, sort_keys=True) + '\n').encode('ascii'))
                stream.flush()
                os.fsync(stream.fileno())
            candidate.chmod(0o644)
            os.replace(candidate, manifest)
            sync_directory(output)
        return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--output', type=Path, required=True, help='pre-created operator-owned public snapshot directory')
    args = parser.parse_args()
    try:
        entry = export(args.root, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Catalog snapshot export: {error}\n')
    print(f'NumericBridge catalog snapshot: {entry.generation}, SHA-256 {entry.sha256}, {entry.bytes} bytes')


if __name__ == '__main__':
    main()
