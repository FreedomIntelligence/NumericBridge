"""Operator-only POSIX release store for a reviewed NumericBridge catalog."""

import argparse
from collections import Counter
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from scripts.validate_immutability import COLLECTIONS, _same_json, compare_documents
from .records.acknowledgments import validate as validate_acknowledgments
from .records.catalog import Catalog
from .records.differences import validate as validate_differences
from .records.hardware import canonical_b200_case, canonical_b200_evaluation
from .records.search import validate_snapshot
from .records.validation import validate as validate_repairs, validate_evaluations, validate_upstream


DEFAULT_ROOT = Path('/var/lib/numericbridge-catalog')
GENERATION = re.compile(r'[a-z0-9][a-z0-9_-]{0,79}')
COMPONENT = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}')
REVISION = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
PRIVATE = re.compile(r'(?:^|[-_.])(?:private|credential[s]?|secret[s]?|inbox|receipt[s]?|'
                     r'handoff[s]?|api[-_]?key|access[-_]?token|id_rsa|id_ed25519)(?:$|[-_.])', re.I)
RECEIPT = re.compile(r'submission-[a-f0-9]{64}(?:[._-].*)?', re.I)
DATA = frozenset('''acknowledgments.json content.json differences-raw.json differences.json
 evaluations.json frameworks.json local-evidence.json paper-results.csv paper-results.json
 relations.json repairs.json rsi.json search-index.json solution-results.json upstream.json
 deepseek41-adapted-core-20261109.json deepseek41-cache-lifecycle-20261110.json
 deepseek41-compressor-2k-20261108.json deepseek41-fresh-process-20261108.json
 deepseek41-full40-attention-20261109.json deepseek41-http128-20261109.json
 deepseek41-native-indexer-20261108.json deepseek41-native-producer-key-cache-20261109.json
 deepseek41-native-query-key-selection-20261109.json deepseek41-native-query-tail-20261108.json
 deepseek41-npu512-physical-cache-20261110.json deepseek41-paired-output-head95-20261109.json
 deepseek41-retained-mode2-20261109.json deepseek41-six-forward-selectors-20261109.json
 deepseek41-upstream-extension-20261108.json'''.split())
LEGACY = {
    'site/evidence/peft-compare-repair-forwardtest.zip': '5b07ecf43a2dd52b7e580e906a8c23427c9434d7fc4a4d7f3ccb8fdcbbaab660',
    'site/evidence/gemma4-native-precision-source-20261107/source-release-v1.zip': '44b52feaacb5c2cdc2f591fd9f2717d53b3b8c8f220df60c49578461cd612227',
    'site/evidence/gemma4-native-precision-source-20261107/source-supplement-v1.zip': '11374a7a36eeb5c164554ce342f6999d1bacc6d8c84d6ec7c73ae2432a538079',
    'site/evidence/torch-native-multitask-gpu-20261024.npz': 'bb298347f81feb6c0699c8064fceda45734838c6454347215929cf0a1e335b8f',
    'site/evidence/torch-native-multitask-npu-20261024.npz': '52cc18f96678b402e9f831de239c3fd4a0f8be2113d23c5fb535a0ee4b61e947',
    'archive/reports/ascend-upstream-workflow-rerun-20261120/submission.json': '6d49fd08c2f76653c49e569962425921cb07e9793ae2645e7b90d2eccc2395eb',
    'archive/reports/ascend-upstream-workflow-20261120/submission.json': 'f283f2f152119c0a21ed72dfa56fa1f16746775960a88e29d44a3d8354c26d9a',
}
TEXT_EXTENSIONS = {'.csv', '.json', '.md', '.patch', '.py', '.svg'}
REQUIRED = {'differences-raw.json', 'differences.json', 'evaluations.json', 'relations.json',
            'repairs.json', 'search-index.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_component(part):
    return (COMPONENT.fullmatch(part) and not part.endswith('.') and
            not PRIVATE.search(part) and not RECEIPT.fullmatch(part))


def validate_revision(value):
    require(REVISION.fullmatch(value) and set(value) != {'0'},
            'source revision must be a full nonzero lowercase Git object ID')


@contextmanager
def directory(path, parent=None, *, controlled=False):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if controlled:
            owner_only(fd, path)
        yield fd
    finally:
        os.close(fd)


def owner_only(fd, path):
    status = os.fstat(fd)
    require(status.st_uid == os.geteuid() and not status.st_mode & 0o022,
            f'Expected operator-owned, non-shared-writable path: {path}')


@contextmanager
def regular(fd, name, *, controlled=False):
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
            f'Not a singly linked regular file: {name}')
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(child, 'rb') as stream:
        if controlled:
            owner_only(child, name)
        def identity(st):
            return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_size,
                    st.st_mtime_ns, st.st_ctime_ns)
        require(identity(os.fstat(child)) == identity(before), f'File changed while opening: {name}')
        yield stream
        require(identity(os.fstat(child)) == identity(before), f'File changed while reading: {name}')


def names(fd):
    return set(os.listdir(fd))


def make_directory(path, mode=0o755):
    path.mkdir(mode=mode)
    path.chmod(mode)


def sync_directory(path):
    with directory(path) as fd:
        os.fsync(fd)


def write_metadata(path, data):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def transfer(src, target):
    """Hash raw bytes, optionally copying to a new file without preserving input modes."""
    digest = sha256()
    def consume(output=None):
        for block in iter(lambda: src.read(1024 * 1024), b''):
            digest.update(block)
            if output is not None:
                output.write(block)
    if target is None:
        consume()
    else:
        with target.open('xb') as output:
            os.fchmod(output.fileno(), 0o644)
            consume(output)
            output.flush()
            os.fsync(output.fileno())
    return digest.hexdigest()


def scan(fd, prefix, out, destination=None, depth=0):
    entries = names(fd)
    require(len({n.casefold() for n in entries}) == len(entries), f'Case collision in {prefix}')
    require(depth <= 16 and (depth == 0 or entries), f'Empty or overly deep directory: {prefix}')
    for name in sorted(entries):
        path = f'{prefix}/{name}'
        require(valid_component(name), f'Noncanonical or private path: {path!r}')
        mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
        target = destination / name if destination is not None else None
        if stat.S_ISDIR(mode) and prefix != 'site/data':
            if target is not None:
                make_directory(target)
            with directory(name, fd, controlled=destination is None) as child:
                scan(child, path, out, target, depth + 1)
        else:
            allowed = name in DATA if prefix == 'site/data' else Path(name).suffix in TEXT_EXTENSIONS
            require(allowed or path in LEGACY, f'File is not on the publication allowlist: {path}')
            with regular(fd, name, controlled=destination is None) as src:
                out[path] = transfer(src, target)
            require(path not in LEGACY or out[path] == LEGACY[path], f'Legacy artifact bytes differ: {path}')
    require(names(fd) == entries, f'Input changed while reading: {prefix}')
    if destination is not None:
        sync_directory(destination)


def scan_site(fd, out, destination=None):
    require(names(fd) == {'data', 'evidence'}, 'SITE must contain exactly data/ and evidence/')
    for name in ('data', 'evidence'):
        target = destination / name if destination is not None else None
        if target is not None:
            make_directory(target)
        with directory(name, fd, controlled=destination is None) as child:
            scan(child, 'site/' + name, out, target)
    require(names(fd) == {'data', 'evidence'}, 'SITE roots changed during read')
    require(all('site/data/' + name in out for name in REQUIRED), 'Missing required canonical catalog data')


def manifest(hashes):
    return ''.join(f'{digest}  {name}\n' for name, digest in sorted(hashes.items())).encode('ascii')


def verify_release(path):
    hashes = {}
    with directory(path, controlled=True) as fd:
        require(names(fd) == {'site', 'archive', 'SOURCE_REVISION', 'SHA256SUMS'}, f'Invalid release layout: {path}')
        with directory('site', fd, controlled=True) as site:
            scan_site(site, hashes)
        with directory('archive', fd, controlled=True) as archive:
            require(names(archive) == {'reports'}, f'Invalid archive layout: {path}')
            with directory('reports', archive, controlled=True) as reports:
                scan(reports, 'archive/reports', hashes)
        with regular(fd, 'SOURCE_REVISION', controlled=True) as source:
            raw = source.read(67)
        require(raw.endswith(b'\n'), 'Malformed SOURCE_REVISION')
        validate_revision(raw[:-1].decode('ascii'))
        hashes['SOURCE_REVISION'] = sha256(raw).hexdigest()
        expected = manifest(hashes)
        with regular(fd, 'SHA256SUMS', controlled=True) as checksums:
            require(checksums.read(len(expected) + 1) == expected, f'Checksum manifest mismatch: {path}')
    return hashes


def json_document(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'Duplicate JSON key in {path.name}: {key}')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f'Non-finite JSON value in {path.name}: {value}')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_float=Decimal, parse_constant=invalid)


def local_reference(value, hashes):
    parts = value.split('/')
    require(len(parts) >= 2 and parts[0] in ('data', 'evidence') and
            all(valid_component(part) for part in parts) and 'site/' + value in hashes,
            f'Noncanonical or missing local evidence path: {value!r}')


def validate_site(release, hashes):
    site = release / 'site'
    documents = {}
    for name in hashes:
        if not name.endswith('.json') or name in LEGACY:
            continue
        document = json_document(release / name)
        if isinstance(document, dict):
            require('additions' not in document and 'receipt_id' not in document,
                    f'Private intake/submission envelope is not publishable: {name}')
        if name.startswith('site/data/'):
            documents[Path(name).name] = document
    for filename, group in (('evaluations.json', 'evaluations'), ('repairs.json', 'repairs'),
                            ('upstream.json', 'contributions')):
        if filename in documents:
            for row in documents[filename][group]:
                for evidence in row.get('evidence', []):
                    url = urlsplit(evidence['url'])
                    if not url.scheme and not url.netloc:
                        local_reference(url.path, hashes)
    for group in ('entities', 'links'):
        for row in documents['relations.json'][group]:
            for evidence in row['evidence']:
                local_reference(evidence['file'], hashes)
            for source in row.get('details', {}).get('sources', []):
                if 'file' in source:
                    local_reference(source['file'], hashes)
    catalog = Catalog(site)
    validate_repairs(site)
    validate_evaluations(site)
    validate_differences(site)
    survey = documents['differences.json']
    kinds = Counter(row['comparison_kind'] for row in survey['records'])
    require(_same_json(dict(kinds), survey['coverage']['by_comparison_kind']),
            'Survey comparison-kind coverage does not match observations')
    validate_acknowledgments(site, catalog)
    if 'upstream.json' in documents:
        validate_upstream(site)
    validate_snapshot(catalog, site / 'data/search-index.json')
    return documents


def shape(document, field, expected_type):
    value = document[field]
    require(isinstance(value, expected_type), f'Invalid collection: {field}')
    return value


def corrected_b200_baseline(filename, label, old, selected):
    if filename == 'evaluations.json':
        field, kind, transform = 'evaluations', None, canonical_b200_evaluation
    else:
        require(filename == 'relations.json', 'B200 corrections are only for evaluations and case entities')
        field, kind, transform = 'entities', 'case', canonical_b200_case
    remaining = set(selected)
    expected = []
    for row in shape(old, field, list):
        identity = row['id']
        if identity in remaining and (kind is None or row.get('kind') == kind):
            corrected = transform(row)
            require(not _same_json(row, corrected), f'{label}: no B20Z display correction for {identity}')
            row = corrected
            remaining.remove(identity)
        expected.append(row)
    suffix = ' as a case' if kind else ''
    require(not remaining, f'{label}: not published{suffix} in current generation: {", ".join(sorted(remaining))}')
    return {**old, field: expected}


def preserve_document(filename, old, new, *, b200_selected=()):
    label = 'community/site/data/' + filename
    if b200_selected:
        # Compare against a derived baseline without editing the prior release.
        old = corrected_b200_baseline(filename, label, old, b200_selected)
    rows = list(COLLECTIONS.get(label, ()))
    extra_rows = {'acknowledgments.json': ('entries',), 'content.json': ('records',),
                  'differences.json': ('branches',), 'upstream.json': ('contributions',)}
    maps = {'differences-raw.json': ('sources',), 'frameworks.json': ('evaluations', 'repairs')}.get(filename, ())
    dates = {'evaluations.json': ('updated_at',), 'repairs.json': ('updated_at',),
             'differences.json': ('updated_at', 'snapshot_date'), 'upstream.json': ('updated_at',)}.get(filename, ())
    for field in rows:
        prior, current = shape(old, field, list), shape(new, field, list)
        identities = [item['id'] for item in current]
        require(all(isinstance(identity, str) and identity for identity in identities) and
                len(set(identities)) == len(identities), f'{label}: invalid or duplicate {field} identity')
        errors = compare_documents(label, {'schema_version': old['schema_version'], field: prior},
                                   {'schema_version': new['schema_version'], field: current}, (field,))
        require(not errors, '\n'.join(errors))
    # Preserve ordering as well as identity: existing JSON Pointers remain valid.
    for field in rows + list(extra_rows.get(filename, ())):
        prior, current = shape(old, field, list), shape(new, field, list)
        require(_same_json(prior, current[:len(prior)]), f'{label}: old {field} rows must stay as a prefix')
    for field in maps:
        prior, current = shape(old, field, dict), shape(new, field, dict)
        require(prior.keys() <= current.keys() and all(_same_json(value, current[key]) for key, value in prior.items()),
                f'{label}: old {field} entries were changed or removed')
    for field in dates:
        if field in old or field in new:
            require(field in old and field in new and date.fromisoformat(new[field]) >= date.fromisoformat(old[field]),
                    f'{label}: {field} cannot be added, removed or moved backwards')
    ignored = set(rows) | set(extra_rows.get(filename, ())) | set(maps) | set(dates)
    def frozen(doc):
        result = {key: value for key, value in doc.items() if key not in ignored}
        if filename == 'differences.json':
            derived = {'branches', 'unique_source_files', 'raw_json_sources', 'recorded_views',
                       'numeric_metrics', 'by_comparison_kind'}
            result['coverage'] = {key: value for key, value in doc['coverage'].items() if key not in derived}
        return result
    require(_same_json(frozen(old), frozen(new)), f'{label}: existing or unrecognized metadata changed')


APPENDABLE = {Path(path).name for path in COLLECTIONS} | {
    'acknowledgments.json', 'content.json', 'differences-raw.json', 'frameworks.json',
}


def preserve(previous, before, after, documents, *, b200_selections=None):
    for name, digest in before.items():
        require(name in after, f'Required historical file was removed: {name}')
        selected = (b200_selections or {}).get(name, ())
        if (digest == after[name] and not selected) or name in {'SOURCE_REVISION', 'site/data/search-index.json'}:
            continue
        filename = Path(name).name
        require(name.startswith('site/data/') and filename in APPENDABLE,
                f'Historical file must retain its original bytes: {name}')
        preserve_document(filename, json_document(previous / name), documents[filename], b200_selected=selected)


def directory_path(value):
    requested = value.absolute()
    require(not requested.is_symlink() and '..' not in requested.parts, f'Invalid directory: {requested}')
    # Resolve system aliases in ancestors (e.g. /tmp on macOS), not the leaf.
    # directory() will still open the leaf with O_NOFOLLOW.
    return requested.parent.resolve(strict=True) / requested.name


def checked_input(value, root):
    resolved = directory_path(value)
    require(not resolved.is_relative_to(root) and not root.is_relative_to(resolved),
            f'Input and release store must not overlap: {resolved}')
    return resolved


def read_current(root):
    path = root / 'current'
    try:
        target = os.readlink(path)
    except FileNotFoundError:
        return None
    prefix = 'releases/'
    require(target.startswith(prefix) and GENERATION.fullmatch(target[len(prefix):]),
            'current must be a relative releases/<generation> symlink')
    return target[len(prefix):]


def switch_current(root, generation):
    with TemporaryDirectory(prefix='.switch-', dir=root) as work:
        link = Path(work) / 'current'
        link.symlink_to('releases/' + generation)
        sync_directory(link.parent)
        os.replace(link, root / 'current')
        sync_directory(root)


def build_stage(stage, source, reports, revision):
    site = stage / 'site'
    archive = stage / 'archive'
    report_target = archive / 'reports'
    for folder in (stage, site, archive, report_target):
        make_directory(folder, 0o700 if folder == archive else 0o755)
    hashes = {}
    with directory(source) as candidate:
        scan_site(candidate, hashes, site)
    if reports is not None:
        with directory(reports) as source_reports:
            scan(source_reports, 'archive/reports', hashes, report_target)
    else:
        sync_directory(report_target)
    sync_directory(site)
    sync_directory(archive)
    raw = (revision + '\n').encode('ascii')
    write_metadata(stage / 'SOURCE_REVISION', raw)
    hashes['SOURCE_REVISION'] = sha256(raw).hexdigest()
    write_metadata(stage / 'SHA256SUMS', manifest(hashes))
    sync_directory(stage)
    return hashes


def b200_selection(args, kind):
    selected = getattr(args, f'b200_{kind}s', None)
    if selected is None:
        return ()
    require(isinstance(selected, Sequence) and not isinstance(selected, (str, bytes)),
            f'B200 corrections require a sequence of {kind} IDs')
    selected = tuple(selected)
    require(all(isinstance(identity, str) and identity for identity in selected),
            f'B200 corrections require a sequence of {kind} IDs')
    require(len(set(selected)) == len(selected), f'Duplicate B200 {kind} ID')
    return selected


def operate(args, root):
    selections = {name: selected for name, kind in (('site/data/evaluations.json', 'evaluation'),
                                                   ('site/data/relations.json', 'case'))
                  if (selected := b200_selection(args, kind))}
    require(not selections or args.command == 'promote', 'B200 corrections require an explicit promotion')
    current = read_current(root)
    require(not selections or current is not None, 'B200 corrections require a current generation')
    releases = root / 'releases'
    transitions = root / '.promotions'
    if current is not None or os.path.lexists(releases):
        with directory(releases, controlled=True) as fd:
            require(args.command != 'seed' or not names(fd), 'Cannot seed a nonempty releases directory')
    if os.path.lexists(transitions):
        with directory(transitions, controlled=True) as fd:
            require(args.command != 'seed' or not names(fd), 'Cannot seed a nonempty promotion history')
    previous = releases / current if current is not None else None
    prefix = 'Dry run: ' if args.dry_run else ''
    if args.command == 'rollback':
        require(current == args.generation, f'Expected current {args.generation}; found {current}')
        with directory(transitions, controlled=True) as fd, regular(fd, current, controlled=True) as stream:
            receipt = stream.read(147).decode('ascii')
        match = re.fullmatch(r'([a-z0-9][a-z0-9_-]{0,79}) ([a-f0-9]{64})\n', receipt)
        require(match and match[1] != current, f'No valid prior generation recorded for {current}')
        prior, digest = match.groups()
        require(sha256(manifest(verify_release(releases / prior))).hexdigest() == digest,
                f'Prior generation checksum differs from promotion receipt: {prior}')
        if not args.dry_run:
            switch_current(root, prior)
        return f'{prefix}rollback {current} -> {prior}'

    require(current == (None if args.command == 'seed' else args.expect_current),
            f'Unexpected current generation: {current}; seed is only for an uninitialized store')
    final = releases / args.generation
    transition = transitions / args.generation
    require(not os.path.lexists(final) and not os.path.lexists(transition), f'Generation already used: {args.generation}')
    source = checked_input(args.site, root)
    reports = checked_input(args.reports, root) if args.reports is not None else None
    if reports is not None:
        require(not source.is_relative_to(reports) and not reports.is_relative_to(source),
                'Candidate SITE and REPORTS must not overlap')
    validate_revision(args.source_revision)
    before = verify_release(previous) if previous is not None else {}
    if reports is None and previous is not None:
        reports = previous / 'archive/reports'
    if args.dry_run:
        staging = Path('/tmp').resolve(strict=True)
        require(not staging.is_relative_to(root) and not staging.is_relative_to(source) and
                (reports is None or not staging.is_relative_to(reports)), 'Temporary storage overlaps input or store')
    else:
        for folder, mode in ((releases, 0o755), (transitions, 0o700)):
            if not os.path.lexists(folder):
                make_directory(folder, mode)
        sync_directory(root)
        staging = releases
    with TemporaryDirectory(prefix='.stage-', dir=staging) as work:
        stage = Path(work) / 'release'
        hashes = build_stage(stage, source, reports, args.source_revision)
        documents = validate_site(stage, hashes)
        if previous is not None:
            preserve(previous, before, hashes, documents, b200_selections=selections)
        require(verify_release(stage) == hashes, 'Stage changed during validation')
        if not args.dry_run:
            os.rename(stage, final)
            sync_directory(releases)
            if current is not None:
                prior_manifest = sha256(manifest(before)).hexdigest()
                write_metadata(transition, f'{current} {prior_manifest}\n'.encode('ascii'))
                sync_directory(transitions)
            switch_current(root, args.generation)
    return f'{prefix}{args.command} {current or "(unseeded)"} -> {args.generation}; {len(hashes)} checksummed files'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    for command in ('seed', 'promote', 'rollback'):
        operation = subcommands.add_parser(command)
        operation.add_argument('--root', type=Path, default=DEFAULT_ROOT, help='pre-created operator-owned store')
        operation.add_argument('--generation', required=True, help='new generation, or the expected current for rollback')
        operation.add_argument('--dry-run', action='store_true', help='validate without writing to the store')
        if command != 'rollback':
            operation.add_argument('--site', type=Path, required=True, help='reviewed candidate containing data/ and evidence/ only')
            operation.add_argument('--reports', type=Path, help='complete reviewed report archive; default: inherit on promotion')
            operation.add_argument('--source-revision', required=True, help='full independently approved source revision')
        if command == 'promote':
            operation.add_argument('--expect-current', required=True, help='generation the operator expects to replace')
            operation.add_argument('--b200-evaluation', dest='b200_evaluations', action='append', metavar='ID',
                                   help='require the exact B20Z-to-B200 correction for this published evaluation (repeatable)')
            operation.add_argument('--b200-case', dest='b200_cases', action='append', metavar='ID',
                                   help='require the exact B20Z-to-B200 hardware correction for this published case (repeatable)')
    args = parser.parse_args()
    try:
        require(GENERATION.fullmatch(args.generation), 'Invalid generation name')
        root = directory_path(args.root)
        with directory(root, controlled=True) as fd:
            # Lock the existing inode, not a lockfile that dry-run would create.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Catalog store is locked by another operator') from error
            print(operate(args, root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError, InvalidOperation) as error:
        parser.exit(1, f'Catalog store: {error}\n')


if __name__ == '__main__':
    main()
