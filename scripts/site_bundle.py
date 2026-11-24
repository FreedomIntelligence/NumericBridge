"""Build the pinned frontend, optionally with an explicit local catalog, or generate legacy Pages redirects."""

import argparse
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_URL = 'https://github.com/FreedomIntelligence/NumericBridge-site.git'
SITE_ROOT = 'community/site'
REVISION = SITE_ROOT + '/frontend-revision.txt'
PUBLIC_PAGES = ('index.html', 'archive.html', 'paper.html', 'rsi.html')
LEGACY_ORIGIN = 'https://numericbridge.com'
LEGACY_PREFIX = '/NumericBridge'
LEGACY_PAGE = f'''<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>NumericBridge has moved</title>
<p>Open the <a href="{LEGACY_ORIGIN}/">NumericBridge community website</a>.</p>
<script>
const origin = '{LEGACY_ORIGIN}';
const prefix = '{LEGACY_PREFIX}';
const path = location.pathname;
const suffix = path === prefix ? '/' : path.startsWith(prefix + '/') ? path.slice(prefix.length) : path;
if (location.origin !== origin) location.replace(origin + suffix + location.search + location.hash);
</script>
</html>
'''


def git(repo, *args):
    return subprocess.check_output(('git', '-C', str(repo), *args), timeout=90)


def frontend_revision(repo, records_ref):
    revision = git(repo, 'show', f'{records_ref}:{REVISION}').decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected a full frontend commit SHA')
    return revision


@contextmanager
def frontend_source(revision, checkout):
    if checkout is not None:
        if git(checkout, 'rev-parse', f'{revision}^{{commit}}').decode().strip() != revision:
            raise ValueError('Frontend revision unavailable in supplied checkout')
        yield Path(checkout)
        return
    with TemporaryDirectory(prefix='numericbridge-frontend-') as tmp:
        git(tmp, 'init', '--quiet')
        git(tmp, '-c', 'protocol.file.allow=never', 'fetch', '--no-tags', '--depth=1', FRONTEND_URL, revision)
        if git(tmp, 'rev-parse', 'FETCH_HEAD').decode().strip() != revision:
            raise ValueError('Fetched frontend revision does not match the pin')
        yield Path(tmp)


def extract_git(repo, treeish, out, allowed, paths=()):
    proc = subprocess.Popen(('git', '-C', str(repo), 'archive', '--format=tar', treeish, *paths),
                            stdout=subprocess.PIPE)
    try:
        with proc.stdout as pipe, tarfile.open(fileobj=pipe, mode='r|') as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or not name.parts:
                    raise ValueError(f'Unexpected Git archive path: {name}')
                if not allowed(name):
                    raise ValueError(f'Unexpected site input: {name}')
                dest = out.joinpath(*name.parts)
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, dest.open('xb') as target:
                        shutil.copyfileobj(source, target)
                else:
                    raise ValueError(f'Expected a regular site asset: {name}')
        if proc.wait(timeout=15):
            raise ValueError('Unable to extract committed site inputs')
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def copy_frontend(revision, source, destination):
    names = [PurePosixPath(item.decode('utf-8')) for item in
             git(source, 'ls-tree', '-r', '--name-only', '-z', revision).split(b'\0') if item]
    if any(path.parts[0] in ('data', 'evidence') for path in names):
        raise ValueError('A frontend commit cannot contain canonical records or evidence')
    files = [path.as_posix() for path in names if len(path.parts) == 1 and path.suffix in ('.html', '.js', '.css')]
    if not set(PUBLIC_PAGES) <= set(files):
        raise ValueError('A required public page is missing from the frontend')
    extract_git(source, revision, destination, lambda path: path.parts[0] == 'assets' or path.as_posix() in files,
                (*files, 'assets'))


def copy_catalog_tree(source, destination):
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f'Expected a real catalog directory: {source}')
    destination.mkdir()
    for path in source.iterdir():
        target = destination / path.name
        if path.is_symlink():
            raise ValueError(f'Symlinks are not supported in catalog assets: {path}')
        if path.is_dir():
            copy_catalog_tree(path, target)
        elif path.is_file():
            shutil.copyfile(path, target)
        else:
            raise ValueError(f'Expected a regular catalog asset: {path}')


def build(output, *, repo=ROOT, records_ref='HEAD', frontend_checkout=None, frontend_only=False, data_site=None,
          legacy_pages=False):
    if sum((frontend_only, data_site is not None, legacy_pages)) != 1:
        raise ValueError('Choose exactly one of --frontend-only, --data-site or --legacy-pages')
    if legacy_pages and frontend_checkout is not None:
        raise ValueError('--legacy-pages does not use --frontend-checkout')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('The site output path must not already exist')
    if data_site is not None:
        # Resolve the active generation once; there must not be more symlinks inside it.
        data_site = Path(data_site).resolve(strict=True)
        if output.resolve().is_relative_to(data_site):
            raise ValueError('The output must be outside the catalog site')
        for name in ('data', 'evidence'):
            path = data_site / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f'Expected a real catalog directory: {path}')
    source_sha = git(repo, 'rev-parse', f'{records_ref}^{{commit}}').decode().strip()
    revision = None if legacy_pages else frontend_revision(repo, source_sha)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='numericbridge-site-', dir=output.parent) as temp:
        stage = Path(temp) / 'site'
        stage.mkdir()
        if legacy_pages:
            for name in (*PUBLIC_PAGES, '404.html'):
                (stage / name).write_text(LEGACY_PAGE, encoding='utf-8')
        else:
            with frontend_source(revision, frontend_checkout) as source:
                copy_frontend(revision, source, stage)
            if data_site is not None:
                for name in ('data', 'evidence'):
                    copy_catalog_tree(data_site / name, stage / name)
        stage.rename(output)
    return source_sha, revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-ref', '--records-ref', dest='source_ref', default='HEAD',
                        help='source commit containing the frontend pin (default: HEAD); never used for records')
    parser.add_argument('--frontend-checkout', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--frontend-only', action='store_true', help='build without catalog data or evidence')
    mode.add_argument('--data-site', type=Path, help='copy data/ and evidence/ from this explicitly selected local site')
    mode.add_argument('--legacy-pages', action='store_true', help='build only browser redirects for legacy GitHub Pages')
    args = parser.parse_args()
    try:
        source, frontend = build(args.output, records_ref=args.source_ref, frontend_checkout=args.frontend_checkout,
                                 frontend_only=args.frontend_only, data_site=args.data_site, legacy_pages=args.legacy_pages)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{parser.prog}: {exc}\n')
    if args.legacy_pages:
        print(f'Assembled legacy Pages redirects: source {source}')
    else:
        print(f'Assembled site: source {source}, frontend {frontend}')


if __name__ == '__main__':
    main()
