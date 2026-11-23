#!/usr/bin/env python3
"""Check a native worker's environment for known launch hazards before preflight (run it in the worker).
Call: check(distributions=(), run_dir=None, temp_dirs=()); CLI: python scripts/preflight_env.py
      [--distribution NAME ...] [--run-dir DIR] [--temp-dir DIR ...] [--output NEW.json]
Input: the framework/plugin distributions to audit and the directories the launch will use.
Output: {"status":"ok"|"hazards", "checks":[{name, status: ok|hazard|warn, detail, fix}]}.
Requires: Standard library; run with the launch interpreter and environment.

Details:
rank_env: inherited LOCAL_RANK/RANK/WORLD_SIZE/LOCAL_WORLD_SIZE that are not single integers
  (a comma list breaks engines that spawn their own ranks) -> unset in the launcher.
ipc_path: run_dir + a typical socket name longer than the Unix domain socket limit (~104-108
  bytes) -> use a short private directory for IPC/runtime files.
temp_fs: temp/compiler-cache directories on a network filesystem (nfs, cifs, fuse, lustre,
  ceph...) -> use node-local scratch for Triton/Inductor/compiler caches.
fork_threads (advisory warn): OMP/MKL thread pools with the fork start method -> spawn if it aborts.
dependencies: each distribution's declared requirements (importlib.metadata) missing or
  outside the declared specifier; audited in one pass so all gaps are installed together.
Every hazard is environment-owned unless the same failure reproduces in a clean,
documented environment; record that decision per issue.
CLI exit: 0=ok, 1=hazards, 2=input error.
"""

__all__ = ['check']
import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SOCKET_LIMIT = 104
NETWORK_FS = ('nfs', 'cifs', 'smb', 'fuse', 'lustre', 'ceph', 'gpfs', 'beegfs', 'sshfs', 'glusterfs', '9p')


def _row(name, ok, detail, fix=None):
    return dict(name=name, status='ok' if ok else 'hazard', detail=detail, **({} if ok else {'fix': fix}))


def _fs_type(path):
    for argv in (['stat', '-f', '-c', '%T', str(path)], ['stat', '-f', '-f', '%T', str(path)]):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().lower()
    return 'unknown'


def _requirement_gaps(name):
    gaps = []
    try:
        requires = metadata.requires(name) or []
    except metadata.PackageNotFoundError:
        return [f'{name}: not installed']
    for spec in requires:
        if ';' in spec:
            requirement, marker = spec.split(';', 1)
            if 'extra' in marker:
                continue
        else:
            requirement = spec
        match = re.match(r'\s*([A-Za-z0-9._-]+)', requirement)
        if not match:
            continue
        dist = match.group(1)
        try:
            installed = metadata.version(dist)
        except metadata.PackageNotFoundError:
            gaps.append(f'{name} requires {requirement.strip()}: missing')
            continue
        try:
            from packaging.requirements import Requirement
            if not Requirement(requirement).specifier.contains(installed, prereleases=True):
                gaps.append(f'{name} requires {requirement.strip()}: installed {installed}')
        except Exception:  # packaging unavailable or unparsable: presence check only
            pass
    return gaps


def check(distributions=(), run_dir=None, temp_dirs=()):
    rows = []
    bad = {k: v for k, v in os.environ.items()
           if k in ('LOCAL_RANK', 'RANK', 'WORLD_SIZE', 'LOCAL_WORLD_SIZE') and not re.fullmatch(r'\d+', v)}
    rows.append(_row('rank_env', not bad, bad or 'no inherited non-integer rank variables',
                     'unset these in the launcher before the engine spawns its ranks'))
    if run_dir:
        probe = str(Path(run_dir).resolve() / 'ipc-0123456789abcdef.sock')
        rows.append(_row('ipc_path', len(probe.encode()) < SOCKET_LIMIT, f'{len(probe.encode())} bytes: {probe}',
                         'use a short private IPC/runtime directory (for example under /tmp)'))
    dirs = list(temp_dirs) or [tempfile.gettempdir()]
    for key in ('TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR', 'XDG_CACHE_HOME'):
        if os.environ.get(key):
            dirs.append(os.environ[key])
    for path in dict.fromkeys(dirs):
        kind = _fs_type(path) if Path(path).exists() else 'missing'
        rows.append(_row('temp_fs', not any(n in kind for n in NETWORK_FS), f'{path}: {kind}',
                         'move compiler caches and scratch to node-local storage'))
    threads = {k: os.environ[k] for k in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS') if os.environ.get(k)}
    method = os.environ.get('VLLM_WORKER_MULTIPROC_METHOD', '')
    forky = method == 'fork' or (not method and sys.platform.startswith('linux'))
    risky = forky and (not threads or any(v != '1' for v in threads.values()))
    row = _row('fork_threads', not risky, f'start method {method or "platform default"}, threads {threads or "unset"}',
               'if a worker aborts in an OpenMP/thread-pool assertion, use spawn or OMP_NUM_THREADS=1')
    if risky:
        row['status'] = 'warn'  # advisory: common default, a hazard only if it fails
    rows.append(row)
    gaps = [g for name in distributions for g in _requirement_gaps(name)]
    if distributions:
        rows.append(_row('dependencies', not gaps, gaps or 'declared requirements satisfied',
                         'install every listed item in a case-private environment or overlay, then rerun'))
    status = 'hazards' if any(r['status'] == 'hazard' for r in rows) else 'ok'
    return dict(status=status, python=sys.executable, checks=rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--distribution', action='append', default=[])
    parser.add_argument('--run-dir')
    parser.add_argument('--temp-dir', action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        result = check(args.distribution, args.run_dir, args.temp_dir)
        text = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
        if args.output:
            with args.output.open('x') as stream:
                stream.write(text)
        print(text, end='')
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
