"""Prepare an isolated single-host vLLM launch on authorized 910C or B200 devices.

Run in the provisioned worker with its actual Python: python -m tools.native_vllm
--profile JSON [--run-dir DIR --execute]. Discovery stays within listed model
candidates and work root. No install, network fetch, device-busy override, or
fallback to another device/backend. The small runtime binding probe imports the
framework but loads no weights. A successful environment check is not native
readiness; only the task-owned collector can produce that evidence.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .preflight_env import SOCKET_LIMIT, NETWORK_FS, _fs_type

RANK_KEYS = {'RANK', 'LOCAL_RANK', 'WORLD_SIZE', 'LOCAL_WORLD_SIZE', 'MASTER_ADDR', 'MASTER_PORT',
             'GROUP_RANK', 'ROLE_RANK', 'ROLE_WORLD_SIZE', 'NODE_RANK', 'RANK_TABLE_FILE'}
VISIBLE_KEYS = {'ASCEND_RT_VISIBLE_DEVICES', 'ASCEND_VISIBLE_DEVICES', 'CUDA_VISIBLE_DEVICES',
                'CUDA_DEVICE_ORDER', 'HIP_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES'}
UUID = re.compile(r'(?:GPU-)?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z', re.I)
SHA = re.compile(r'[0-9a-f]{64}\Z')
MODULE = re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*)*\Z')
RUNTIME_PROBE = r'''
import importlib, json, sys
mode, mods = sys.argv[1], sys.argv[2:]
for name in mods:
    importlib.import_module(name)
import torch
if mode == '910c':
    import torch_npu
    count = torch.npu.device_count()
    assert torch.npu.is_available()
    assert all('910' in torch.npu.get_device_name(i) for i in range(count))
    data = {'count': count}
else:
    assert torch.cuda.is_available()
    data = {'uuids': [str(torch.cuda.get_device_properties(i).uuid) for i in range(torch.cuda.device_count())]}
print('NB_BINDING=' + json.dumps(data))
'''


def require(ok, message):
    if not ok:
        raise ValueError(message)


def absolute(path):
    require(isinstance(path, str) and Path(path).is_absolute() and '..' not in Path(path).parts,
            'Worker paths must be explicit absolute paths')
    return Path(path)


def authorize(profile):
    required = {'schema_version', 'hardware', 'approved_devices', 'devices', 'model_candidates', 'model_files', 'work_root'}
    optional = {'argv', 'extra_imports', 'device_mode', 'min_free_bytes', 'vllm_version'}
    require(isinstance(profile, dict) and required <= profile.keys() <= required | optional and profile['schema_version'] == 1,
            'Invalid native launch profile')
    require(profile['hardware'] in ('910c', 'b200'), 'Unsupported hardware')
    allowed, selected = profile['approved_devices'], profile['devices']
    mode = profile.get('device_mode', 'logic-id' if profile['hardware'] == '910c' else 'index')
    require(mode in (('logic-id',) if profile['hardware'] == '910c' else ('index', 'uuid')), 'Invalid device numbering scheme')
    def valid_device(item):
        return (isinstance(item, str) and UUID.fullmatch(item) is not None if mode == 'uuid'
                else type(item) is int and item >= 0)
    require(all(isinstance(x, list) and x and all(valid_device(v) for v in x) and len(set(x)) == len(x)
                for x in (allowed, selected)) and set(selected) <= set(allowed), 'Only explicitly granted devices may be used')
    require(type(profile.get('min_free_bytes', 0)) is int and profile.get('min_free_bytes', 0) >= 0, 'Invalid space requirement')
    extras = profile.get('extra_imports', [])
    require(isinstance(extras, list) and all(isinstance(x, str) and MODULE.fullmatch(x) for x in extras), 'Invalid import names')
    return mode


def checked_directory(value, *, writable=False):
    path = absolute(value)
    require(path.is_dir() and path.resolve() == path, 'Authorized directory is missing or symlinked')
    if writable:
        info = path.stat()
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o077 and os.access(path, os.W_OK | os.X_OK),
                'Worker directory must be owned, writable and private (0700)')
    return path


def model_directory(candidates, files):
    require(isinstance(candidates, list) and candidates and len(candidates) <= 16, 'Missing authorized model candidates')
    require(isinstance(files, dict) and 'config.json' in files and any(p.endswith('.safetensors') for p in files),
            'Use the full pinned manifest (including config and weight shards)')
    for name, digest in files.items():
        require(isinstance(name, str) and name not in ('', '.') and not Path(name).is_absolute() and '..' not in Path(name).parts
                and isinstance(digest, str) and SHA.fullmatch(digest), 'Invalid model manifest entry')
    for value in candidates:
        path = absolute(value)
        if not path.is_dir() or path.resolve() != path:
            continue
        for name, expected in files.items():
            artifact = path / name
            if not artifact.is_file() or artifact.resolve() != artifact:
                break
            digest = hashlib.sha256()
            with artifact.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != expected:
                break
        else:
            return path
    raise ValueError('No allowed model candidate matched the fixed file manifest')


def _query(argv, *, env=None, timeout=20):
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f'{Path(argv[0]).name} probe failed; check private worker diagnostics')
    return result.stdout


def _uuid(text):
    match = UUID.fullmatch(text)
    require(match is not None, 'Missing or malformed B200 device UUID')
    return match[1].lower()


def resolve_devices(hardware, selected, mode, *, query=_query):
    if hardware == '910c':
        data = query(['npu-smi', 'info', '-m'])
        require('Chip Logic ID' in data and 'NPU ID' in data, 'Unrecognized 910C inventory format; do not guess number mapping')
        rows = {}
        for line in data.splitlines():
            fields = line.split()
            if len(fields) >= 5 and all(v.isascii() and v.isdecimal() for v in fields[:4]):
                card, chip, logical, physical = map(int, fields[:4])
                require(logical not in rows, 'Ambiguous 910C logical device mapping')
                rows[logical] = dict(card=card, chip=chip, logic=logical, physical=physical, name=' '.join(fields[4:]))
        require(all(i in rows for i in selected), 'Authorized Chip Logic ID missing from 910C inventory')
        chosen = [rows[i] for i in selected]
        require(len({row['physical'] for row in chosen}) == len(chosen), 'Ambiguous 910C physical mapping')
        for row in chosen:
            require('910' in row['name'], 'Authorized accelerator did not report an Ascend 910-family chip')
            out = query(['npu-smi', 'info', '-t', 'proc-mem', '-i', str(row['card']), '-c', str(row['chip'])])
            require('No process in device.' in out, 'An authorized NPU is busy or its process status cannot be verified')
        return {'ASCEND_RT_VISIBLE_DEVICES': ','.join(map(str, selected))}, chosen
    rows = []
    for item in selected:
        identifier = str(item) if mode == 'index' else 'GPU-' + _uuid(item)
        result = query(['nvidia-smi', '-i', identifier, '--query-gpu=index,uuid,name', '--format=csv,noheader,nounits'])
        fields = result.strip().split(',')
        require(len(fields) == 3 and '\n' not in result.strip(), 'Expected one GPU per authorized identifier')
        index, raw_uuid, name = (part.strip() for part in fields)
        require(index.isdecimal() and 'B200' in name.upper() and not name.startswith('MIG'), 'Expected a physical B200 device')
        uid = _uuid(raw_uuid)
        require((int(index) == item if mode == 'index' else uid == _uuid(item)), 'B200 inventory changed')
        require(not query(['nvidia-smi', '-i', 'GPU-' + uid, '--query-compute-apps=pid', '--format=csv,noheader']).strip(),
                'An authorized B200 is busy or its process status cannot be verified')
        rows.append(dict(index=int(index), uuid=uid))
    require(len({row['uuid'] for row in rows}) == len(rows), 'Duplicate B200 device identity')
    device_list = [str(row['index']) if mode == 'index' else 'GPU-' + row['uuid'] for row in rows]
    return {'CUDA_DEVICE_ORDER': 'PCI_BUS_ID', 'CUDA_VISIBLE_DEVICES': ','.join(device_list)}, rows


def clean_environment(parent, visibility, scratch):
    env = {key: value for key, value in parent.items() if key not in RANK_KEYS | VISIBLE_KEYS}
    env.update(visibility)
    env.update({k: str(scratch) for k in ('TMPDIR', 'TEMP', 'TMP', 'VLLM_RPC_BASE_PATH')})
    for key, folder in [('XDG_CACHE_HOME', 'xdg'), ('HF_HOME', 'hf'), ('TRITON_CACHE_DIR', 'triton'),
                        ('TORCHINDUCTOR_CACHE_DIR', 'inductor'), ('VLLM_CACHE_ROOT', 'vllm')]:
        env[key] = str(scratch / folder)
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
               PYTHONDONTWRITEBYTECODE='1', VLLM_WORKER_MULTIPROC_METHOD='spawn', OMP_NUM_THREADS='1')
    return env


def verify_binding(profile, devices, env, *, query=_query):
    hardware = profile['hardware']
    modules = ['vllm', *(['vllm_ascend'] if hardware == '910c' else []), *profile.get('extra_imports', [])]
    code = RUNTIME_PROBE
    if 'vllm_version' in profile:
        expected = profile['vllm_version']
        require(isinstance(expected, str) and expected, 'Invalid pinned vLLM version')
        code = 'from importlib.metadata import version\nassert version("vllm") == ' + repr(expected) + '\n' + code
    text = query([sys.executable, '-B', '-c', code, hardware, *modules], env=env, timeout=120)
    lines = [line.partition('=')[2] for line in text.splitlines() if line.startswith('NB_BINDING=')]
    require(len(lines) == 1, 'Native import/binding probe emitted no unique result')
    observed = json.loads(lines[0])
    if hardware == '910c':
        require(observed == {'count': len(devices)}, 'Ascend runtime visibility does not match granted logical IDs')
    else:
        require([_uuid(value) for value in observed['uuids']] == [row['uuid'] for row in devices],
                'CUDA-visible UUIDs do not match granted B200s; do not launch')


@contextmanager
def prepared(profile, *, query=_query):
    mode = authorize(profile)
    work = checked_directory(profile['work_root'], writable=True)
    free = os.statvfs(work)
    require(free.f_frsize * free.f_bavail >= profile.get('min_free_bytes', 0), 'Insufficient allowed workspace capacity')
    model = model_directory(profile['model_candidates'], profile['model_files'])
    scratch_root = Path('/tmp').resolve()
    storage = _fs_type(scratch_root)
    require(storage != 'unknown' and not any(label in storage for label in NETWORK_FS), 'Local IPC scratch not verified')
    with tempfile.TemporaryDirectory(prefix='nb-', dir=scratch_root) as directory:
        scratch = Path(directory)
        require(len(os.fsencode(scratch / 'ipc-0123456789abcdef.sock')) < SOCKET_LIMIT, 'IPC socket path is too long')
        visibility, devices = resolve_devices(profile['hardware'], profile['devices'], mode, query=query)
        env = clean_environment(os.environ, visibility, scratch)
        verify_binding(profile, devices, env, query=query)
        yield work, model, env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--run-dir')
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.profile.read_bytes())
        with prepared(config) as (work, model, env):
            if not args.execute:
                require(args.run_dir is None, '--run-dir requires --execute')
                print(json.dumps({'status': 'environment_checked', 'hardware': config['hardware']}))
                return 0
            run = checked_directory(args.run_dir, writable=True)
            require(run != work and run.is_relative_to(work), 'Native run directory must be strictly inside the authorized work root')
            command = config.get('argv')
            require(isinstance(command, list) and command and all(isinstance(x, str) and x and '\0' not in x for x in command)
                    and command[0] == '{python}', 'A trusted native collector argv using {python} is required')
            command = [v.replace('{python}', sys.executable).replace('{model}', str(model)).replace('{run}', str(run)) for v in command]
            return subprocess.run(command, cwd=run, env=env).returncode
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'preflight_blocked', 'reason': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
