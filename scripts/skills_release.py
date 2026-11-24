"""Build and smoke-test a versioned ZIP from the standalone skills exporter.

From a clean checkout with requirements-dev.txt and CPU Torch installed:
  python -B -m scripts.skills_release --version v1.2.3 --output /tmp/skills-dist
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .install import INSTALLABLE_SKILLS, ROOT, check_workflow_file, install_skills
from .validate import validate_links, validate_skills

COMPARE = Path('precision-compare')
VERSION = r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'


def release_inventory():
    """Map the export layout to its tracked inputs; do not publish local files."""
    folders = [f'skills/{name}' for name in INSTALLABLE_SKILLS]
    listed = subprocess.check_output(
        ['git', 'ls-files', '--stage', '-z', '--', *folders, 'tools', 'LICENSE'], cwd=ROOT)
    inventory = {}
    for entry in os.fsdecode(listed).split('\0'):
        if not entry:
            continue
        metadata, name = entry.split('\t', 1)
        mode, _, stage = metadata.split()
        relative = Path(name)
        source = ROOT / relative
        if '__pycache__' in relative.parts or relative.name.endswith('.pyc'):
            continue
        if relative.parts[0] == 'tools':
            if any(part.endswith('.md') for part in relative.parts):
                continue
            member = COMPARE / 'scripts' / relative
        elif relative.parts[0] == 'skills':
            member = Path(*relative.parts[1:])
        else:
            member = relative
        if (stage != '0' or mode not in ('100644', '100755') or not source.is_file()
                or source.resolve() != source):
            raise ValueError(f'Release input must be a tracked regular file: {name}')
        if member in inventory:
            raise ValueError(f'Duplicate release member: {member}')
        inventory[member] = (source, int(mode, 8))
    if Path('LICENSE') not in inventory:
        raise ValueError('The license must be tracked')
    return inventory


def make_zip(export, archive, inventory):
    files = {path.relative_to(export) for path in export.rglob('*') if path.is_file()}
    if files != inventory.keys():
        raise ValueError(f'Unexpected export inventory: extra={sorted(files - inventory.keys())}, '
                         f'missing={sorted(inventory.keys() - files)}')
    with ZipFile(archive, 'x', compression=ZIP_DEFLATED) as zipped:
        for member in sorted(files, key=Path.as_posix):
            source, mode = inventory[member]
            data = (export / member).read_bytes()
            if data != source.read_bytes():
                raise ValueError(f'Exported bytes differ from source: {member}')
            # Fixed entry timestamps, permissions and order avoid host metadata drift.
            info = ZipInfo(member.as_posix())
            info.create_system = 3
            info.external_attr = mode << 16
            zipped.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def smoke_zip(archive, root):
    """Validate what users extract, not the source tree or intermediate export."""
    installed = root / 'skills'
    with ZipFile(archive) as zipped:
        zipped.extractall(installed)
    for path in installed.rglob('*'):
        if path.is_file():
            check_workflow_file(path)
    folders = validate_skills(root)
    if {folder.name for folder in folders} != set(INSTALLABLE_SKILLS):
        raise ValueError('ZIP does not contain the full skill suite')
    _, errors = validate_links(root, sorted(installed.rglob('*.md')))
    if errors:
        raise ValueError('\n'.join(errors))

    work = root / 'work'
    work.mkdir()

    def run(relative_path, *args):
        completed = subprocess.run(
            [sys.executable, '-I', '-B', str(installed / COMPARE / relative_path), *args],
            cwd=work, stdout=subprocess.PIPE, check=True, text=True, timeout=120)
        return json.loads(completed.stdout)

    gradients = run('examples/compare_gradients.py', '--output-root', str(work))
    artifacts = Path(gradients['artifacts']).resolve()
    if artifacts.parent != work.resolve():
        raise ValueError('CPU example wrote outside its clean work directory')
    report = json.loads((artifacts / 'report.json').read_text(encoding='utf-8'))
    if report['status'] != 'completed' or report['coverage']['compared_elements'] != 12:
        raise ValueError('Extracted CPU gradient comparison did not complete all 12 values')
    print(f'Clean ZIP installation: {len(folders)} skills; links valid; no bundled community data; '
          'CPU FP32/BF16 gradient comparison covered 12 values.')


def build_release(version, output):
    if not re.fullmatch(VERSION, version):
        raise ValueError('Version must have the form vMAJOR.MINOR.PATCH (e.g. v1.2.3)')
    name = f'numericbridge-skills-{version}.zip'
    assets = [output / name, output / f'{name}.sha256']
    for asset in assets:
        if asset.exists() or asset.is_symlink():
            raise FileExistsError(f'Refusing to replace {asset}')
    inventory = release_inventory()
    with tempfile.TemporaryDirectory(prefix='numericbridge-release-') as tmp:
        root = Path(tmp).resolve()
        export = root / 'export'
        install_skills(ROOT / 'skills', export)
        shutil.copyfile(ROOT / 'LICENSE', export / 'LICENSE')
        archive = root / name
        make_zip(export, archive, inventory)
        smoke_zip(archive, root / 'clean-install')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        output.mkdir(parents=True, exist_ok=True)
        with assets[0].open('xb') as destination, archive.open('rb') as source:
            shutil.copyfileobj(source, destination)
        with assets[1].open('x', encoding='ascii', newline='\n') as checksum:
            checksum.write(f'{digest}  {name}\n')
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, help='version as vMAJOR.MINOR.PATCH')
    parser.add_argument('--output', required=True, type=Path, help='directory for new release assets')
    args = parser.parse_args()
    try:
        assets = build_release(args.version, args.output)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    for asset in assets:
        print(asset.resolve())


if __name__ == '__main__':
    main()
