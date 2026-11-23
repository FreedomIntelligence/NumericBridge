#!/usr/bin/env python3
"""Capture one side's software versions without launching a workload.

Call collect_environment() in the preflight worker after the native path has run,
then write_receipt() to a new JSON file. The CLI inventories the current process;
a fresh CLI process normally has no loaded accelerator runtime. Apply the same
contract on the target and reference. Receipts contain no hostnames, system paths
or environment dumps. Package metadata is explicitly labelled as installed.
"""
import argparse
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess

_FIELDS = ('framework', 'plugin', 'runtime', 'driver', 'container')
_BACKENDS = ('cpu', 'cuda', 'cann', 'other')
_COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
_VALUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+!:/@-]{0,239}')
_UNKNOWNS = {'unknown', 'n/a', 'none', 'null', 'not-applicable', 'not-recorded'}
_SOURCES = {'framework': {'installed-distribution', 'native-evidence'},
            'plugin': {'installed-distribution', 'native-evidence'},
            'runtime': {'loaded-cuda-runtime-api', 'loaded-cann-library-path', 'native-evidence'},
            'driver': {'nvidia-smi-driver-query', 'ascend-driver-version-file', 'native-evidence'},
            'container': {'launcher-image'}}


def _entry(value=None, source=None, applies=True):
    if not applies:
        return {'status': 'not-applicable'}
    if not isinstance(value, str) or not _VALUE.fullmatch(value) or value.lower() in _UNKNOWNS:
        return {'status': 'unknown'}
    return {'status': 'observed', 'value': value, 'source': source}


def _distribution(name):
    if not _COMPONENT.fullmatch(name):
        raise ValueError('expected a Python distribution name')
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {'name': name, **_entry(version, 'installed-distribution')}


def _loaded_paths(pattern):
    try:
        with open('/proc/self/maps', encoding='utf-8', errors='replace') as stream:
            paths = set()
            for line in stream:
                parts = line.rstrip('\n').split(maxsplit=5)
                if len(parts) == 6 and parts[5].startswith('/'):
                    path = Path(parts[5])
                    if pattern.fullmatch(path.name):
                        paths.add(path)
            return sorted(paths)
    except OSError:
        return []


def _cuda_runtime():
    paths = _loaded_paths(re.compile(r'libcudart(?:-[\da-f]+)?\.so(?:\.\d+)*'))
    if len(paths) != 1 or not hasattr(os, 'RTLD_NOLOAD'):
        return None
    try:
        library = ctypes.CDLL(str(paths[0]), mode=os.RTLD_NOLOAD | os.RTLD_LOCAL)
        get_version = library.cudaRuntimeGetVersion
        get_version.argtypes = [ctypes.POINTER(ctypes.c_int)]
        get_version.restype = ctypes.c_int
        result = ctypes.c_int()
        if get_version(ctypes.byref(result)) == 0 and result.value >= 1000:
            return f'{result.value // 1000}.{result.value % 1000 // 10}'
    except (AttributeError, OSError):
        pass
    return None


def _cann_runtime():
    paths = _loaded_paths(re.compile(r'(?:libascendcl|libopapi(?:_transformer)?)\.so(?:\.\d+)*'))
    versions = set()
    for path in paths:
        version = None
        for root in path.parents:
            if match := re.fullmatch(r'cann-(\d+\.\d+(?:\.\d+)*(?:[._-][A-Za-z0-9]+)*)', root.name):
                version = match[1]
                break
            if root.parent.name == 'ascend-toolkit' and re.fullmatch(r'\d+\.\d+(?:\.\d+)*', root.name):
                version = root.name
                break
        if version is None:
            return None
        versions.add(version)
    return next(iter(versions)) if len(versions) == 1 else None


def _cuda_driver():
    try:
        report = subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
                                capture_output=True, text=True, timeout=5, check=True)
        versions = {row.strip() for row in report.stdout.splitlines() if row.strip()}
        if len(versions) == 1:
            version = next(iter(versions))
            if re.fullmatch(r'\d+(?:\.\d+)+', version):
                return version
    except (OSError, UnicodeError, subprocess.SubprocessError):
        pass
    return None


def _cann_driver():
    try:
        with Path('/usr/local/Ascend/driver/version.info').open(encoding='utf-8') as stream:
            versions = set(re.findall(r'^Version\s*=\s*([^\s]+)[ \t]*$', stream.read(16384), re.MULTILINE))
        return next(iter(versions)) if len(versions) == 1 else None
    except (OSError, UnicodeError):
        return None


def collect_environment(backend, framework, *, plugin=None, no_plugin=False, container=None, bare_metal=False):
    """Return an allowlisted receipt. Supply a container image ID from the launcher.

    Select no_plugin/bare_metal only when known. CANN versions are inferred
    from versioned paths of mapped CANN libraries; mixed or unversioned paths
    remain unknown. CUDA uses the mapped libcudart API, not the driver banner
    or a framework's build version. Fields for other accelerators are reserved
    and unknown until a collector is added.
    """
    if backend not in _BACKENDS or (plugin and no_plugin) or (container and bare_metal):
        raise ValueError('invalid backend or conflicting environment options')
    runtime, driver = _entry(applies=backend != 'cpu'), _entry(applies=backend != 'cpu')
    if backend == 'cuda':
        runtime = _entry(_cuda_runtime(), 'loaded-cuda-runtime-api')
        driver = _entry(_cuda_driver(), 'nvidia-smi-driver-query')
    elif backend == 'cann':
        runtime = _entry(_cann_runtime(), 'loaded-cann-library-path')
        driver = _entry(_cann_driver(), 'ascend-driver-version-file')
    return dict(schema_version=1, backend=backend, framework=_distribution(framework),
                plugin=_distribution(plugin) if plugin else _entry(applies=not no_plugin),
                runtime=runtime, driver=driver, container=_entry(container, 'launcher-image', not bare_metal))


def validate_receipt(receipt):
    """Validate the portable receipt, including receipts from native adapters."""
    keys = {'schema_version', 'backend', *_FIELDS}
    if (not isinstance(receipt, dict) or set(receipt) != keys
            or type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
            or receipt['backend'] not in _BACKENDS):
        raise ValueError('invalid environment receipt schema')
    backend = receipt['backend']
    for field in _FIELDS:
        entry = receipt[field]
        if not isinstance(entry, dict):
            raise ValueError(f'invalid environment field: {field}')
        status = entry.get('status')
        expected = {'status'}
        if field == 'framework' or field == 'plugin' and ('name' in entry or status == 'observed'):
            expected.add('name')
            if not isinstance(entry.get('name'), str) or not _COMPONENT.fullmatch(entry['name']):
                raise ValueError(f'invalid environment component: {field}')
        if status == 'observed':
            expected.update(('value', 'source'))
            source = entry.get('source')
            if (not isinstance(source, str) or source not in _SOURCES[field]
                    or _entry(entry.get('value'), source)['status'] != 'observed'
                    or source in ('loaded-cuda-runtime-api', 'nvidia-smi-driver-query') and backend != 'cuda'
                    or source in ('loaded-cann-library-path', 'ascend-driver-version-file') and backend != 'cann'):
                raise ValueError(f'invalid environment observation: {field}')
        if (status not in ('observed', 'unknown', 'not-applicable') or set(entry) != expected
                or field == 'framework' and status == 'not-applicable'
                or field == 'plugin' and 'name' in entry and status == 'not-applicable'
                or field in ('runtime', 'driver') and (status == 'not-applicable') != (backend == 'cpu')):
            raise ValueError(f'invalid environment field state: {field}')


def write_receipt(path, receipt):
    """Write a new receipt without replacing an earlier run's evidence."""
    validate_receipt(receipt)
    payload = json.dumps(receipt, indent=2, allow_nan=False) + '\n'
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--backend', choices=_BACKENDS, required=True)
    parser.add_argument('--framework', required=True, help='Python distribution name')
    plugin = parser.add_mutually_exclusive_group()
    plugin.add_argument('--plugin', help='Python distribution name')
    plugin.add_argument('--no-plugin', action='store_true')
    container = parser.add_mutually_exclusive_group()
    container.add_argument('--container', help='image name, digest or both from launcher')
    container.add_argument('--bare-metal', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    a = vars(parser.parse_args(argv))
    output = a.pop('output')
    try:
        write_receipt(output, collect_environment(**a))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
