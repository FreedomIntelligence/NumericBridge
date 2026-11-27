"""Fetch and cache a single official public catalog generation for local preview."""

from contextlib import closing
from dataclasses import dataclass
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tarfile
from tempfile import TemporaryDirectory
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zlib


ORIGIN = 'https://numericbridge.com/catalog-snapshots/'
MAX_MANIFEST = 4096
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_MEMBERS = 8192
CHUNK = 1024 * 1024
GENERATION = re.compile(r'[a-z0-9][a-z0-9_-]{0,79}')
DIGEST = re.compile(r'[a-f0-9]{64}')
COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


class CatalogUnavailable(ValueError):
    """The trusted catalog baseline could not be prepared."""


@dataclass(frozen=True)
class Snapshot:
    generation: str
    sha256: str
    bytes: int

    @property
    def filename(self):
        return self.sha256 + '.tar.gz'


def parse_manifest(raw):
    def unique(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError('Duplicate catalog manifest field')
            result[name] = value
        return result

    entry = json.loads(raw, object_pairs_hook=unique)
    if (not isinstance(entry, dict) or set(entry) != {'schema_version', 'generation', 'sha256', 'bytes'} or
            type(entry['schema_version']) is not int or entry['schema_version'] != 1 or
            not isinstance(entry['generation'], str) or not GENERATION.fullmatch(entry['generation']) or
            not isinstance(entry['sha256'], str) or not DIGEST.fullmatch(entry['sha256']) or
            type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_ARCHIVE):
        raise ValueError('Invalid official catalog manifest')
    return Snapshot(entry['generation'], entry['sha256'], entry['bytes'])


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_official(filename):
    # The manifest gives a digest, never a caller-controlled URL. Do not send
    # intake credentials or follow a redirect to another origin.
    req = Request(ORIGIN + filename, headers={'User-Agent': 'NumericBridge-Catalog/1'})
    return build_opener(NoRedirects()).open(req, timeout=12)


def stream_hash(source, expected_size, destination=None, *, deadline=None):
    size, digest = 0, sha256()
    while chunk := source.read(min(CHUNK, expected_size - size + 1)):
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError('Catalog archive download exceeded its time budget')
        size += len(chunk)
        if size > expected_size:
            raise ValueError('Catalog archive exceeds its declared size')
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    if size != expected_size:
        raise ValueError('Catalog archive size does not match the manifest')
    return digest.hexdigest()


class Bounded:
    def __init__(self, source):
        self.source = source
        self.remaining = MAX_EXPANDED

    def read(self, size):
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError('Expanded catalog exceeds its size limit')
        return data


def unpack(source, site):
    """Write only regular files and directories from the two public trees."""
    seen = set()
    with gzip.GzipFile(fileobj=source, mode='rb') as compressed:
        bounded = Bounded(compressed)
        with tarfile.open(fileobj=bounded, mode='r|') as archive:
            for member in archive:
                name = member.name
                parts = name.split('/')
                if (len(seen) >= MAX_MEMBERS or len(parts) > 20 or name in seen or
                        parts[0] not in ('data', 'evidence') or
                        any(not COMPONENT.fullmatch(part) or part.endswith('.') for part in parts) or
                        (not member.isdir() and (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or
                                                 len(parts) < 2 or member.sparse is not None))):
                    raise ValueError('Unsupported path or member in catalog archive')
                seen.add(name)
                target = site.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as stream, target.open('xb') as output:
                        while chunk := stream.read(CHUNK):
                            output.write(chunk)
        # Verify the gzip trailer and apply the size limit to any trailing bytes.
        while bounded.read(CHUNK):
            pass
    if not {'data', 'evidence'} <= seen or not (site / 'data/search-index.json').is_file():
        raise ValueError('Catalog archive does not contain both published trees')


def default_cache():
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'numericbridge/catalog-snapshots'


class OfficialCatalog:
    """Pin one verified generation per process; another process can reuse the archive."""

    def __init__(self, lifetime, *, cache=None, fetch=open_official):
        self.lifetime = Path(lifetime)
        self.cache = Path(cache) if cache is not None else default_cache()
        self.fetch = fetch
        self.snapshot = None
        self._site = None
        self._lock = Lock()

    def __call__(self):
        with self._lock:
            if self._site is None:
                try:
                    self._pin()
                except HTTPError as error:
                    error.close()
                    raise CatalogUnavailable(f'Official NumericBridge catalog returned HTTP {error.code}.') from error
                except URLError as error:
                    raise CatalogUnavailable('Could not reach the official NumericBridge catalog over HTTPS.') from error
                except (OSError, ValueError, tarfile.TarError, EOFError, zlib.error, RecursionError) as error:
                    # Keep user-facing output free of local paths, response bodies and credentials.
                    raise CatalogUnavailable('Could not verify or cache the official NumericBridge catalog; '
                                             f'retry contribution preparation ({type(error).__name__}).') from error
            return self._site

    def _matches(self, cached, entry):
        if not cached.is_file() or cached.is_symlink() or cached.stat().st_size != entry.bytes:
            return False
        with cached.open('rb') as stream:
            return stream_hash(stream, entry.bytes) == entry.sha256

    def _pin(self):
        with closing(self.fetch('current.json')) as response:
            raw = response.read(MAX_MANIFEST + 1)
        if len(raw) > MAX_MANIFEST:
            raise ValueError('Catalog manifest too large')
        entry = parse_manifest(raw)
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        cached = self.cache / entry.filename
        with TemporaryDirectory(prefix='.catalog-', dir=self.cache) as staging:
            downloaded = Path(staging) / 'archive'
            archive = cached
            if not self._matches(cached, entry):
                with closing(self.fetch(entry.filename)) as response, downloaded.open('xb') as stream:
                    digest = stream_hash(response, entry.bytes, stream, deadline=time.monotonic() + 120)
                if digest != entry.sha256:
                    raise ValueError('Catalog archive checksum does not match the official manifest')
                archive = downloaded
            # Keep the extracted directory private and pinned for this MCP process.
            with TemporaryDirectory(prefix='extract-', dir=self.lifetime) as temporary:
                site = Path(temporary) / 'site'
                site.mkdir()
                with archive.open('rb') as source:
                    # Verify the same open file that will be extracted.
                    if stream_hash(source, entry.bytes) != entry.sha256:
                        raise ValueError('Cached catalog archive was modified')
                    source.seek(0)
                    unpack(source, site)
                if archive == downloaded:
                    os.replace(downloaded, cached)
                pinned = self.lifetime / 'site'
                os.rename(site, pinned)
        self.snapshot, self._site = entry, pinned
