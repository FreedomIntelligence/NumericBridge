"""Export standalone skills, bundling the canonical execution package once.

python -m scripts.install --output /path/to/client/skills
Existing skill folders are rejected. No community records, model runtime or device are used.
"""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
INSTALLABLE_SKILLS = ('framework-adapt', 'numericbridge', 'operator-optimize', 'operator-verify', 'precision-compare', 'precision-repair')
COMMUNITY_DATA_NAMES = {'community-search.json', 'search-index.json', 'evaluations.json',
                        'repairs.json', 'relations.json'}


def check_workflow_file(path):
    """Reject catalog files and renamed catalog JSON, not numerical fixtures or search code."""
    path = Path(path)
    if path.name.lower() in COMMUNITY_DATA_NAMES:
        raise ValueError(f'Community record data cannot be bundled: {path}')
    if path.suffix.lower() != '.json':
        return
    try:
        data = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and (
            data.get('kind') == 'numericbridge-search-index' or
            ('schema_version' in data and
             (any(isinstance(data.get(key), list) for key in ('evaluations', 'repairs')) or
              all(isinstance(data.get(key), list) for key in ('entities', 'links'))))):
        raise ValueError(f'Community record data cannot be bundled: {path}')


def _copy_workflow_file(source, destination):
    check_workflow_file(source)
    return shutil.copy2(source, destination)


def installable_folders(source):
    """Select the released skill packages present in a source directory."""
    return [folder for name in INSTALLABLE_SKILLS
            if (folder := Path(source) / name).is_dir()]


def install_skills(source, destination):
    """Copy selected skill folders and include execution tools in Compare."""
    source, destination = Path(source), Path(destination)
    folders = installable_folders(source)
    if not folders:
        raise ValueError(f'{source}: no installable skills')
    for folder in folders:
        if (destination / folder.name).exists():
            raise FileExistsError(destination / folder.name)
    for folder in folders:
        shutil.copytree(folder, destination / folder.name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.lake'),
                        copy_function=_copy_workflow_file)
        if folder.name == 'precision-compare':
            shutil.copytree(ROOT / 'tools',
                            destination / folder.name / 'scripts/tools',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.md'),
                            copy_function=_copy_workflow_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    install_skills(ROOT / 'skills', args.output)
    print('Exported standalone skills to ' + str(args.output.resolve()))


if __name__ == '__main__':
    main()
