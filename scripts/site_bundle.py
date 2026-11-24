"""Assemble a pinned public frontend with the canonical committed records."""

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
    if not {'index.html', 'archive.html', 'paper.html', 'rsi.html'} <= set(files):
        raise ValueError('A required public page is missing from the frontend')
    extract_git(source, revision, destination, lambda path: path.parts[0] == 'assets' or path.as_posix() in files,
                (*files, 'assets'))


def build(output, *, repo=ROOT, records_ref='HEAD', frontend_checkout=None, frontend_only=False):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('The site output path must not already exist')
    revision = frontend_revision(repo, records_ref)
    records_sha = git(repo, 'rev-parse', f'{records_ref}^{{commit}}').decode().strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='numericbridge-site-', dir=output.parent) as temp:
        stage = Path(temp) / 'site'
        stage.mkdir()
        with frontend_source(revision, frontend_checkout) as source:
            copy_frontend(revision, source, stage)
        if not frontend_only:
            extract_git(repo, f'{records_sha}:{SITE_ROOT}', stage,
                        lambda path: path.parts[0] in ('data', 'evidence'), ('data', 'evidence'))
        stage.rename(output)
    return records_sha, revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--records-ref', default='HEAD')
    parser.add_argument('--frontend-checkout', type=Path)
    parser.add_argument('--frontend-only', action='store_true', help='omit canonical data and evidence')
    args = parser.parse_args()
    records, frontend = build(args.output, records_ref=args.records_ref, frontend_checkout=args.frontend_checkout,
                              frontend_only=args.frontend_only)
    print(f'Assembled site: records {records}, frontend {frontend}')


if __name__ == '__main__':
    main()
