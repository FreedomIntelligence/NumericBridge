"""Prepare and verify append-only community drafts from an isolated Git checkout."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
from html import unescape
import json
import math
import os
from pathlib import Path
import re
import shutil
from stat import S_ISDIR, S_ISREG
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import unquote

from scripts.validate_immutability import compare_documents
from tools.contribution_contract import candidate_versions
from tools.operator_optimization import check as check_operator_optimization
from tools.pr_draft import check as check_pr_draft
from .catalog import Catalog
from .search import compile_index
from .validation import validate, validate_evaluations, validate_upstream


SITE = 'community/site/'
CATALOG_SITE_ENV = 'NUMERICBRIDGE_CATALOG_SITE'
CATALOG_INPUTS = ('evaluations.json', 'repairs.json', 'differences.json', 'relations.json')
DIGEST = re.compile(r'[a-f0-9]{64}')
EVIDENCE = re.compile(r'evidence/submissions/[a-z0-9]+(?:-[a-z0-9]+)*\.json')
REVIEW_EVIDENCE = re.compile(r'review/[a-z0-9]+(?:-[a-z0-9]+)*\.json')
REVIEW_PATH = re.compile(r'\breview/', re.IGNORECASE)
REVIEW_FIELDS = frozenset({'review_evidence', 'upstream_pr_draft', 'strict_upstream_pr_draft',
                           'candidate_version', 'patch_text', 'patch_lines', 'body_markdown'})
REVIEW_KINDS = frozenset({'upstream_pr_draft', 'strict_upstream_pr_draft'})
PATCH_HEADER = re.compile(r'(?:^|\n)(?:diff --git |--- [^\n]+\r?\n\+\+\+ [^\n]+\r?\n@@)')
MAX_EVIDENCE_FILES = 20
MAX_ADDITIONS_BYTES = 1024 * 1024
_UNSET = object()
COLLECTIONS = {'evaluations': ('evaluations',), 'repairs': ('repairs', 'library_groups'),
               'relations': ('entities', 'links')}
ATTRIBUTION_SCOPE = ('community_site', 'community_pr', 'upstream_pr')
RECEIPT = re.compile(r'submission-[a-f0-9]{64}')


def attribution(mode='anonymous', name=None):
    if mode == 'anonymous' and name is None:
        return {'mode': mode}
    if mode == 'github' and isinstance(name, str) and re.fullmatch(r'@[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', name) and len(name) <= 40:
        return {'mode': mode, 'name': name, 'scope': list(ATTRIBUTION_SCOPE)}
    if (mode == 'nickname' and isinstance(name, str) and 1 <= len(name) <= 40 and name == name.strip()
            and any(c.isalnum() for c in name) and all(c.isalnum() or c in ' _-.' for c in name)):
        return {'mode': mode, 'name': name, 'scope': list(ATTRIBUTION_SCOPE)}
    raise ValueError('Choose anonymous without a name, a nickname, or a GitHub @username')


def draft_attribution(draft):
    if draft.get('schema_version') == 2 and 'attribution' not in draft:
        return attribution()
    credit = draft.get('attribution')
    if (draft.get('schema_version') not in (3, 4, 5) or not isinstance(credit, dict)
            or credit != attribution(credit.get('mode'), credit.get('name'))):
        raise ValueError('Invalid draft attribution or scope')
    return credit


def supersedes(value):
    """Validate the list of earlier receipts replaced by a schema 4 or 5 draft."""
    if (not isinstance(value, (list, tuple)) or len(value) > 10
            or not all(isinstance(v, str) and RECEIPT.fullmatch(v) for v in value)
            or len(set(value)) != len(value)):
        raise ValueError('supersedes must list up to 10 distinct earlier submission-<sha256> receipts')
    return list(value)


def draft_supersedes(draft):
    schema = draft.get('schema_version')
    if schema == 4 or (schema == 5 and 'supersedes' in draft):
        values = supersedes(draft.get('supersedes'))
        if not values:
            raise ValueError('A revision draft must name the receipts it supersedes')
        return values
    if 'supersedes' in draft:
        raise ValueError('Only schema 4 and 5 drafts may supersede earlier receipts')
    return []


def encode(value, *, compact=False):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':') if compact else None,
                       indent=None if compact else 2) + '\n').encode('utf-8')


def validate_draft_envelope(draft):
    """Validate version-specific wire fields, independent of the catalog baseline."""
    if not isinstance(draft, dict) or type(draft.get('schema_version')) is not int:
        raise ValueError('Invalid submission format')
    common = {'schema_version', 'title', 'destination', 'additions'}
    preview = {'base_commit', 'files'}
    schema = draft['schema_version']
    fields = {
        2: common | preview,
        3: common | preview | {'attribution'},
        4: common | preview | {'attribution', 'supersedes'},
        5: common | {'attribution', 'review_evidence'},
    }.get(schema)
    if fields is None or set(draft) != fields | ({'supersedes'} if schema == 5 and 'supersedes' in draft else set()):
        raise ValueError('Invalid submission format')


def validate_additions_shape(additions):
    if (not isinstance(additions, dict) or set(additions) != {*COLLECTIONS, 'evidence'}
            or not all(isinstance(additions[key], dict) and additions[key].keys() <= set(fields)
                       and all(isinstance(value, list) for value in additions[key].values())
                       for key, fields in COLLECTIONS.items())
            or not isinstance(additions['evidence'], dict)
            or not all(isinstance(document, dict) for document in additions['evidence'].values())):
        raise ValueError('Invalid community additions')


def validate_public_content(value, *, check_paths=False):
    """Reject public PR/patch declarations; schema 5 also reserves the private namespace."""
    if isinstance(value, str):
        if PATCH_HEADER.search(value):
            raise ValueError('Raw patches must be placed in schema 5 review_evidence')
        if not check_paths:
            return
        # Inspect actual prose and decoded link spellings; reject excessive nesting.
        for _ in range(8):
            if REVIEW_PATH.search(value.replace('\\', '/')):
                raise ValueError('Public content cannot reference the review/ attachment namespace')
            decoded = unquote(unescape(value))
            if decoded == value:
                return
            value = decoded
        raise ValueError('Public content contains excessively nested URL or HTML escaping')
    elif isinstance(value, list):
        for item in value:
            validate_public_content(item, check_paths=check_paths)
    elif isinstance(value, dict):
        if value.keys() & REVIEW_FIELDS or (isinstance(value.get('kind'), str) and value['kind'] in REVIEW_KINDS):
            raise ValueError('Review-only PR and patch fields must be placed in schema 5 review_evidence')
        for key, item in value.items():
            if check_paths:
                validate_public_content(key, check_paths=True)
            validate_public_content(item, check_paths=check_paths)


def validate_visibility(title, additions, review_evidence):
    """Validate both visibility lanes without copying or interpreting review payloads."""
    if not isinstance(title, str) or title != title.strip() or not re.fullmatch(r'[^\x00-\x1f\x7f]{1,120}', title):
        raise ValueError('Use a single-line contribution title of at most 120 characters without surrounding spaces')
    validate_additions_shape(additions)
    if not isinstance(review_evidence, dict) or any(
            not isinstance(path, str) or len(path) > 160 or not REVIEW_EVIDENCE.fullmatch(path)
            or not isinstance(document, dict) for path, document in review_evidence.items()):
        raise ValueError('Review evidence must map review/<slug>.json to JSON objects')
    if len(additions['evidence']) + len(review_evidence) > MAX_EVIDENCE_FILES:
        raise ValueError(f'Limit public and review evidence to {MAX_EVIDENCE_FILES} files combined')
    if len(encode({'additions': additions, 'review_evidence': review_evidence}, compact=True)) > MAX_ADDITIONS_BYTES:
        raise ValueError('Limit public and review submission JSON to 1 MiB combined')
    if any(not isinstance(path, str) or len(path) > 160 or not EVIDENCE.fullmatch(path)
           for path in additions['evidence']):
        raise ValueError('New public evidence must be a JSON file under evidence/submissions/')
    validate_public_content(title, check_paths=True)
    validate_public_content(additions, check_paths=True)
    for evaluation in additions['evaluations'].get('evaluations', []):
        acceptance = evaluation.get('acceptance') if isinstance(evaluation, dict) else None
        if (not isinstance(acceptance, dict) or set(acceptance) != {'verdict', 'policy', 'criterion'}
                or acceptance['verdict'] not in ('pass', 'gap', 'fail', 'noise_limited', 'incomplete')
                or not all(isinstance(acceptance[key], str) and acceptance[key].strip()
                           for key in ('policy', 'criterion'))):
            raise ValueError('Schema 5 evaluation acceptance requires verdict, policy and criterion (no passed flag)')

    review_bindings = set()
    for version in candidate_versions(review_evidence):
        tested_commit, tested_patch = version['tested_commit'], version['tested_patch_sha256']
        if tested_commit is None or tested_patch is None:
            raise ValueError('A patch submission requires the actual tested base and patch digest')
        review_bindings.add((version['patch_sha256'], tested_commit, tested_patch))
    public_bindings = set()
    for document in additions['evidence'].values():
        if 'patch_sha256' not in document:
            continue
        verification = document.get('verification')
        _validate_paired_metrics(verification.get('metrics') if isinstance(verification, dict) else None)
        binding = (document['patch_sha256'], verification.get('tested_commit'), verification.get('tested_patch_sha256'))
        if not all(isinstance(value, str) for value in binding):
            raise ValueError('Public patch evidence requires its patch digest, tested base and tested patch digest')
        public_bindings.add(binding)
    if public_bindings != review_bindings:
        raise ValueError('Each review patch must match public paired evidence by patch digest, tested base and tested patch digest')


def _validate_paired_metrics(metrics):
    def number(value):
        return type(value) is int or type(value) is float and math.isfinite(value)

    if (not isinstance(metrics, list) or not 1 <= len(metrics) <= 40
            or any(not isinstance(metric, dict) or set(metric) != {'id', 'before', 'after', 'unit'}
                   or not isinstance(metric['id'], str) or not metric['id'].strip()
                   or not isinstance(metric['unit'], str) or not metric['unit'].strip()
                   or not number(metric['before']) or not number(metric['after']) for metric in metrics)
            or len({metric['id'] for metric in metrics}) != len(metrics)):
        raise ValueError('Patch evidence requires distinct paired metrics with finite before/after and a unit')


def _verify_review_evidence(review_evidence, *, credit, trusted_repos):
    pr_fields = {'upstream_pr_draft', 'candidate_version', 'patch_text'}
    for path, document in review_evidence.items():
        if not (document.keys() & REVIEW_FIELDS or
                (isinstance(document.get('kind'), str) and document['kind'] in REVIEW_KINDS)):
            continue
        if not pr_fields <= document.keys():
            raise ValueError(f'{path}: review PR needs upstream_pr_draft, candidate_version and patch_text')
        patch = document['patch_text']
        if not patch.startswith('diff --git ') or not patch.endswith('\n') or '\x00' in patch:
            raise ValueError(f'{path}: patch_text must be a Git unified diff preserving its final newline')
        from tools.strict_pr import verify_pr
        verified = verify_pr(document['upstream_pr_draft'], attribution=credit, trusted_repos=trusted_repos)
        native = verified['source']['checks'].get('native_on_target', {})
        version = document['candidate_version']
        if native.get('status') == 'passed' and (version['tested_commit'], version['tested_patch_sha256']) != (
                version['target_commit'], version['patch_sha256']):
            raise ValueError(f'{path}: a native pass must test the exact target commit and patch')


def run(cwd, *argv, payload=None, env=None):
    try:
        return subprocess.run(argv, cwd=cwd, input=payload, text=True, encoding='utf-8',
                              env=env, capture_output=True, check=True, timeout=180).stdout.strip()
    except FileNotFoundError as error:
        raise ValueError(f'Required command unavailable: {argv[0]}') from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'{argv[0]} failed while preparing the community preview') from error


def git(cwd, *args, **kwargs):
    return run(cwd, 'git', '-c', 'core.hooksPath=/dev/null', *args, **kwargs)


def add_catalog_site_argument(parser):
    parser.add_argument('--catalog-site', type=Path, help=(
        f'local frozen catalog site directory containing data/ and evidence/; overrides {CATALOG_SITE_ENV}'))


def _copy_catalog_tree(source, destination):
    if not S_ISDIR(source.lstat().st_mode):
        raise ValueError(f'Expected a regular catalog directory: {source}')
    destination.mkdir()
    for path in source.iterdir():
        mode = path.lstat().st_mode
        target = destination / path.name
        if S_ISDIR(mode):
            _copy_catalog_tree(path, target)
        elif S_ISREG(mode):
            shutil.copyfile(path, target)
        else:
            raise ValueError(f'Expected a regular catalog file or directory: {path}')


class Submissions:
    def __init__(self, repo, drafts, *, destination=None, base_ref='refs/remotes/origin/main',
                 catalog_site=None, trusted_repos=None, catalog_provider=None):
        self.repo = Path(repo).resolve()
        self.drafts = Path(drafts).resolve()
        self.destination = destination
        self.base_ref = base_ref
        self.trusted_repos = {} if trusted_repos is None else trusted_repos
        source = catalog_site if catalog_site is not None else os.environ.get(CATALOG_SITE_ENV)
        self.catalog_site = Path(source).expanduser().absolute() if source else None
        self.catalog_provider = catalog_provider

    def baseline(self):
        return git(self.repo, 'rev-parse', '--verify', self.base_ref + '^{commit}')

    def _seed_catalog(self, root):
        site = root / SITE
        if site.resolve() != site:
            raise ValueError('Expected a regular community/site directory in the baseline checkout')
        source = self.catalog_site
        if source is None and self.catalog_provider is not None:
            source = self.catalog_provider()
        if source is None:
            if not all((site / 'data' / name).is_file() for name in CATALOG_INPUTS):
                raise ValueError('No catalog data is tracked at the code baseline. Set --catalog-site or '
                                 f'{CATALOG_SITE_ENV} to a validated local frozen catalog site directory '
                                 '(download and extract a snapshot separately if needed).')
            return

        # Resolve a generation pointer once, so data and evidence come from the same generation.
        try:
            source = source.resolve(strict=True)
            site.mkdir(parents=True, exist_ok=True)
            for name in ('data', 'evidence'):
                destination = site / name
                if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
                    destination.unlink()
                elif destination.is_dir():
                    shutil.rmtree(destination)
                _copy_catalog_tree(source / name, destination)
            for name in CATALOG_INPUTS:
                if not (site / 'data' / name).is_file():
                    raise ValueError(f'Missing catalog input: data/{name}')
            validate(site)
            validate_evaluations(site)
            Catalog(site)
            if (site / 'data/upstream.json').is_file():
                validate_upstream(site)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
            raise ValueError(f'Invalid local catalog site {source}: {error}') from error

    @contextmanager
    def checkout(self, base):
        with TemporaryDirectory(prefix='numericbridge-submission-') as temporary:
            root = Path(temporary).resolve() / 'checkout'
            git(self.repo, 'clone', '--quiet', '--no-checkout', '--shared', '--', str(self.repo), str(root))
            git(root, 'checkout', '--quiet', '--detach', base)
            self._seed_catalog(root)
            yield root

    def apply(self, root, additions):
        site = root / SITE
        changed = []
        candidate_versions(additions['evidence'])
        entities = (additions.get('relations') or {}).get('entities') or []
        for path, document in additions['evidence'].items():
            if len(path) > 160 or not EVIDENCE.fullmatch(path):
                raise ValueError('New evidence must be a JSON file under evidence/submissions/')
            target = site / path
            if target.resolve() != target:
                raise ValueError('Submission evidence paths must be regular files in the review checkout')
            if isinstance(document, dict) and 'upstream_pr_draft' in document:
                review = check_pr_draft(document['upstream_pr_draft'])
                if review['status'] != 'ok':
                    raise ValueError(f'{path}: upstream_pr_draft ' + '; '.join(review['problems']))
            if isinstance(document, dict) and 'operator_optimization' in document:
                opt_review = check_operator_optimization(document['operator_optimization'], entities=entities)
                if opt_review['status'] != 'ok':
                    raise ValueError(f'{path}: operator_optimization ' + '; '.join(opt_review['problems']))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(encode(document))
            changed.append(SITE + path)
        for name, keys in COLLECTIONS.items():
            values = additions[name]
            if not any(values.values()):
                continue
            path = f'{SITE}data/{name}.json'
            raw = (root / path).read_bytes()
            old = json.loads(raw)
            new = old.copy()
            for key, items in values.items():
                if items:
                    new[key] = [*old.get(key, []), *items]
            if 'updated_at' in old:
                new['updated_at'] = max([old['updated_at'], *(item['date'] for item in values.get(name, []))])
            violations = compare_documents(path, old, new, keys)
            if violations:
                raise ValueError('; '.join(violations))
            (root / path).write_bytes(encode(new, compact=len(raw.splitlines()) == 1))
            changed.append(path)
        validate(site)
        validate_evaluations(site)
        index = f'{SITE}data/search-index.json'
        (root / index).write_bytes(encode(compile_index(Catalog(site)), compact=True))
        changed.append(index)
        return {path: sha256((root / path).read_bytes()).hexdigest() for path in sorted(changed)}

    @contextmanager
    def preview_checkout(self, title, additions, *, review_evidence=_UNSET, _preview_metadata=None,
                         attribution_mode='anonymous', attribution_name=None, replaces=()):
        """Yield the public checkout and exact wire body; v5 metadata is separate."""
        credit = attribution(attribution_mode, attribution_name)
        replaced = supersedes(replaces)
        if not isinstance(title, str) or not re.fullmatch(r'[^\x00-\x1f\x7f]{1,120}', title.strip()):
            raise ValueError('Use a single-line contribution title of at most 120 characters')
        separated = review_evidence is not _UNSET
        if separated:
            validate_visibility(title, additions, review_evidence)
        elif len(encode(additions, compact=True)) > MAX_ADDITIONS_BYTES:
            raise ValueError('Limit a submission to 1 MiB of reviewed public JSON')
        if not any(items for name in COLLECTIONS for items in additions[name].values()):
            raise ValueError('Include at least one community record or relation')
        if separated:
            _verify_review_evidence(review_evidence, credit=credit, trusted_repos=self.trusted_repos)
        base = self.baseline()
        with self.checkout(base) as root:
            files = self.apply(root, additions)
            if _preview_metadata is not None:
                _preview_metadata.update(base_commit=base, files=files)
            if separated:
                draft = dict(schema_version=5, title=title.strip(), destination=self.destination,
                             attribution=credit, additions=additions, review_evidence=review_evidence)
            else:
                draft = dict(schema_version=4 if replaced else 3, title=title.strip(), destination=self.destination,
                             attribution=credit, base_commit=base, additions=additions, files=files)
            if replaced:
                draft['supersedes'] = replaced
            yield root, draft

    def preview(self, title, additions, **options):
        with self.preview_checkout(title, additions, **options) as (_, draft):
            return draft

    def prepare(self, title, additions, *, review_evidence=_UNSET, **attribution_options):
        metadata = {}
        if review_evidence is _UNSET:
            validate_public_content(additions)
            preview = self.preview(title, additions, **attribution_options)
        else:
            preview = self.preview(title, additions, review_evidence=review_evidence,
                                   _preview_metadata=metadata, **attribution_options)
        raw = encode(preview, compact=True)
        digest = sha256(raw).hexdigest()
        self.drafts.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.drafts / f'draft-{digest}.json'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('Stored draft differs from its preview')
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
        result = dict(status='ready', draft_id=f'draft-{digest}', draft_sha256=digest, preview=preview)
        if review_evidence is not _UNSET:
            result['preview_metadata'] = metadata
        return result

    def reviewed(self, draft_id, digest):
        if not DIGEST.fullmatch(digest) or draft_id != f'draft-{digest}':
            raise ValueError('Draft ID and SHA-256 must match the exact reviewed preview')
        raw = (self.drafts / f'{draft_id}.json').read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError('Stored draft changed; prepare and review a new draft')
        reviewed = json.loads(raw)
        draft_attribution(reviewed)
        draft_supersedes(reviewed)
        if reviewed.get('schema_version') == 5:
            validate_draft_envelope(reviewed)
            if raw != encode(reviewed, compact=True):
                raise ValueError('Invalid submission format')
            validate_visibility(reviewed['title'], reviewed['additions'], reviewed['review_evidence'])
        if reviewed.get('destination') != self.destination:
            raise ValueError('Draft destination changed; prepare and review a new draft')
        return reviewed


def main():
    """Validate a local preview using title and additions from a JSON file."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new local JSON preview file')
    parser.add_argument('--upstream-registry', type=Path, help='trusted upstream clones for strict review drafts')
    add_catalog_site_argument(parser)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        with args.input.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Local preview input must not exceed 1 MiB')
        request = json.loads(raw)
        credit = request.get('attribution', {'mode': 'anonymous'})
        trusted_repos = None
        if args.upstream_registry is not None:
            from tools.strict_pr import load_registry
            trusted_repos = load_registry(args.upstream_registry)
        metadata = {}
        options = ({'review_evidence': request['review_evidence'], '_preview_metadata': metadata}
                   if 'review_evidence' in request else {})
        preview = Submissions(root, args.output.parent, catalog_site=args.catalog_site,
                              trusted_repos=trusted_repos).preview(
            request['title'], request['additions'],
            attribution_mode=credit['mode'], attribution_name=credit.get('name'),
            replaces=request.get('supersedes', []), **options)
        result = dict(status='local_draft', preview=preview)
        if metadata:
            result['preview_metadata'] = metadata
        data = encode(result)
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
