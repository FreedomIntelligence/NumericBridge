#!/usr/bin/env python3
"""Record one argv execution in a fresh UTC run directory (standard library only).
Call: record_run(output_root, side, task, timeout, argv, cwd=None, execution=None) -> (Path, dict)
CLI: python scripts/record_run.py --output-root ROOT --side SIDE --task TASK --timeout SECONDS -- ARGV...
Input: Literal argv or callable(Path) -> argv; execution is an optional JSON file path.
Output: Fresh run with command.json and full logs; failures add log tails and failure_class/category.
Requires: Standard library; new run path; timeout terminates the owned process group on POSIX.

Details:
CLI: [--cwd DIR] [--execution E.json] [--tail-bytes 2048]; stdout is one JSON summary
(run, status, returncode, logs with path/bytes; failures add tails and the class).
Execution JSON: {"argv_prefix":[...], "env":{"NAME":"value","REMOVE":null},
"path_prepend":["/abs/bin",...]}; literal values, no shell or expansion. The prefix
wraps each command; overrides apply to the local child (remote adapters forward
them). The profile is retained in run/execution.json with its hash.
A callable argv receives the reserved run directory. Run names are
<side>-<task>-YYYYmmddTHHMMSSZ with alphanumeric-hyphen IDs; existing directories
and same-second collisions fail before launch.
command.json: start/end UTC, argv, cwd, timeout, child PID, status (running,
completed, failed, timed_out, launch_failed, interrupted), returncode (child exit;
128+signal; 124 timeout; 127 launch failure; 130 interruption), child_returncode and
wall_seconds. stdout.bin/stderr.bin receive child bytes directly.
summarize_run(folder, record=None, tail_bytes=2048, include_tails=False) reads a run
without launching. classify_failure(record, text) maps status and the last 16 KiB of
logs to failure_class (out_of_memory, collective_timeout, device_unavailable,
import_error, missing_file, unsupported_operation, index_out_of_bounds, collective_error,
nonfinite_values, killed, python_exception, timed_out, launch_failed, interrupted,
unknown) and category (setup_or_resource, unsupported, reproducible_failure,
interrupted) with the matching line as evidence.
On timeout/interruption the POSIX process group gets SIGTERM, then SIGKILL after
0.5 s; descendants that leave the group are not covered.
"""
__all__ = ['record_run', 'summarize_run', 'classify_failure']

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


def _utc_now():
    return datetime.now(timezone.utc)


def _write_record(folder, record):
    temporary = folder / 'command.json.tmp'
    temporary.write_text(json.dumps(record, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(folder / 'command.json')


def _terminate(process):
    if os.name == 'posix':
        def send(sig):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        send(signal.SIGTERM)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        # The leader may have exited while a descendant still owns the group.
        send(signal.SIGKILL)
    else:
        process.kill()
    process.wait()


def load_execution(path=None):
    """Return a validated literal execution profile and effective child environment."""
    profile = {} if path is None else json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(profile, dict) or set(profile) - {'env', 'path_prepend', 'argv_prefix'}:
        raise ValueError('execution fields: env, path_prepend, argv_prefix')
    overrides = profile.get('env', {})
    if not isinstance(overrides, dict) or any(
            not isinstance(k, str) or not k or '=' in k or '\0' in k
            or (v is not None and (not isinstance(v, str) or '\0' in v))
            for k, v in overrides.items()):
        raise ValueError('execution env must map variable names to strings or null')
    for field in ('path_prepend', 'argv_prefix'):
        values = profile.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v or '\0' in v for v in values):
            raise ValueError('execution ' + field + ' must contain nonempty strings')
    if any(not Path(v).is_absolute() or os.pathsep in v for v in profile.get('path_prepend', [])):
        raise ValueError('execution path_prepend must contain absolute individual paths')
    environment = dict(os.environ)
    for key, value in overrides.items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    prepend = profile.get('path_prepend', [])
    if prepend:
        environment['PATH'] = os.pathsep.join(prepend + ([environment['PATH']] if environment.get('PATH') else []))
    return profile, environment


def record_run(output_root, side, task, timeout, argv, cwd=None, execution=None):
    """Validate, reserve a unique run, and execute literal argv; see module contract."""
    for name, value in [('side', side), ('task', task)]:
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', value):
            raise ValueError(name + ' must contain alphanumeric segments separated by single hyphens')
    if isinstance(timeout, bool):
        raise ValueError('timeout must be finite and positive')
    try:
        timeout = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('timeout must be finite and positive') from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    def validate_argv(value):
        if (not isinstance(value, (list, tuple)) or not value
                or any(not isinstance(a, str) or '\0' in a for a in value) or not value[0]):
            raise ValueError('argv must be a nonempty sequence of strings without NUL bytes')
        return list(value)
    profile, environment = load_execution(execution)
    prefix = profile.get('argv_prefix', [])
    prepared = None if callable(argv) else prefix + validate_argv(argv)
    directory = Path.cwd() if cwd is None else Path(cwd).resolve()
    if not directory.is_dir():
        raise ValueError('cwd must be an existing directory')
    started = _utc_now()
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / f'{side}-{task}-{started.strftime("%Y%m%dT%H%M%SZ")}'
    folder.mkdir(exist_ok=False)
    record = dict(start=started.isoformat(), end=None, argv=prepared, cwd=str(directory),
                  timeout=timeout, child_pid=None, returncode=None, child_returncode=None,
                  status='running', wall_seconds=None)
    if execution is not None:
        path = Path(execution).resolve()
        snapshot = folder / 'execution.json'
        snapshot.write_text(json.dumps(profile, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        record['execution'] = dict(path=str(path), snapshot=str(snapshot),
                                   sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
    begin = time.monotonic()
    _write_record(folder, record)
    with (folder / 'stdout.bin').open('xb') as stdout, (folder / 'stderr.bin').open('xb') as stderr:
        try:
            if prepared is None:
                prepared = prefix + validate_argv(argv(folder))
                record['argv'] = prepared
                _write_record(folder, record)
            process = subprocess.Popen(prepared, cwd=directory, env=environment, stdout=stdout, stderr=stderr,
                                       start_new_session=os.name == 'posix')
        except Exception as exc:
            record.update(status='launch_failed', returncode=127,
                          error=dict(type=type(exc).__name__, message=str(exc)))
        else:
            record['child_pid'] = process.pid
            _write_record(folder, record)
            try:
                raw_code = process.wait(timeout=timeout)
                record.update(status='completed' if raw_code == 0 else 'failed',
                              returncode=raw_code if raw_code >= 0 else 128 - raw_code)
            except subprocess.TimeoutExpired:
                _terminate(process)
                record.update(status='timed_out', returncode=124)
            except KeyboardInterrupt:
                _terminate(process)
                record.update(status='interrupted', returncode=130)
            record['child_returncode'] = process.returncode
    record.update(end=_utc_now().isoformat(), wall_seconds=time.monotonic() - begin)
    _write_record(folder, record)
    return folder, record


FAILURE_PATTERNS = (
    ('out_of_memory', 'setup_or_resource', r'out of memory|OutOfMemoryError|ACL_ERROR_RT_MEMORY_ALLOCATION|'
                                           r'memory allocation failed|Cannot allocate memory|std::bad_alloc'),
    ('collective_timeout', 'setup_or_resource', r'(NCCL|HCCL|ProcessGroup|Gloo)[^\n]*(timeout|timed out|time out)|'
                                                r'Watchdog caught collective'),
    ('device_unavailable', 'setup_or_resource', r'no CUDA GPUs are available|CUDA_ERROR_NO_DEVICE|devices? (is|are) busy|'
                                                r'DEVICE_BUSY|invalid device ordinal|rtSetDevice'),
    ('import_error', 'setup_or_resource', r'ModuleNotFoundError|ImportError|undefined symbol|'
                                          r'cannot open shared object file|GLIBC\S* not found'),
    ('missing_file', 'setup_or_resource', r'FileNotFoundError|No such file or directory'),
    ('unsupported_operation', 'unsupported', r'NotImplementedError|not implemented for|is not supported|not supported on|'
                                             r'Could not run \S+ with arguments from the'),
    ('index_out_of_bounds', 'reproducible_failure', r'IndexError|index out of (range|bounds)|out of bounds|'
                                                    r'illegal memory access|integer overflow'),
    ('collective_error', 'reproducible_failure', r'NCCL error|HCCL error|nccl(Internal|System|Invalid)\w*Error'),
    ('nonfinite_values', 'reproducible_failure', r'(loss|grad\w*) (is|became) (nan|inf)|NaN or Inf found'),
)


def classify_failure(record, text):
    """Rule-based class/category for an unsuccessful run from its status and log tail text."""
    status = record.get('status')
    if status == 'completed':
        return None
    if status in ('timed_out', 'launch_failed', 'interrupted'):
        category = 'interrupted' if status == 'interrupted' else 'setup_or_resource'
        return dict(failure_class=status, category=category, evidence=str(record.get('error', '')) or None)
    for name, category, pattern in FAILURE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start, end = text.rfind('\n', 0, match.start()) + 1, text.find('\n', match.end())
            line = text[start:end if end >= 0 else len(text)]
            return dict(failure_class=name, category=category, evidence=line.strip()[:300])
    if record.get('returncode') in (137, 128 + 9):
        return dict(failure_class='killed', category='setup_or_resource', evidence='SIGKILL (host memory limit or scheduler)')
    exceptions = re.findall(r'^(\w+(?:Error|Exception)): ?(.*)$', text, re.MULTILINE)
    if exceptions:
        name, message = exceptions[-1]
        return dict(failure_class='python_exception', category='reproducible_failure', evidence=f'{name}: {message}'[:300])
    return dict(failure_class='unknown', category='reproducible_failure', evidence=None)


def summarize_run(folder, record=None, tail_bytes=2048, include_tails=False):
    """Return compact status/log paths, bounded failure tails and a failure class for an existing run."""
    folder = Path(folder)
    if type(tail_bytes) is not int or tail_bytes < 0:
        raise ValueError("tail_bytes must be nonnegative")
    if record is None:
        record = json.loads((folder / "command.json").read_text(encoding="utf-8"))
    result = dict(run=str(folder), status=record['status'],
                  returncode=record['returncode'], logs={})
    if 'error' in record:
        result['error'] = record['error']
    scan = []
    for name in ('stdout', 'stderr'):
        path = folder / (name + '.bin')
        with path.open('rb') as stream:
            size = stream.seek(0, os.SEEK_END)
            entry = dict(path=str(path), bytes=size)
            if record['status'] != 'completed':
                stream.seek(max(0, size - 16384))
                scan.append(stream.read().decode('utf-8', errors='replace'))
            if (record['status'] != 'completed' or include_tails) and tail_bytes:
                stream.seek(max(0, size - tail_bytes))
                entry.update(tail=stream.read(tail_bytes).decode('utf-8', errors='replace'),
                             truncated=size > tail_bytes)
        result['logs'][name] = entry
    failure = classify_failure(record, '\n'.join(scan))
    if failure:
        result.update(failure)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--side', required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--timeout', required=True, type=float)
    parser.add_argument('--cwd')
    parser.add_argument('--execution')
    parser.add_argument('--tail-bytes', type=int, default=2048)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.tail_bytes < 0:
        parser.error('tail-bytes must be nonnegative')
    # Require the explicit separator so child flags cannot become recorder flags.
    if not args.command or args.command[0] != '--':
        parser.error('supply -- followed by the child argv')
    try:
        folder, record = record_run(args.output_root, args.side, args.task, args.timeout,
                                    args.command[1:], args.cwd, args.execution)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summarize_run(folder, record, args.tail_bytes)))
    return record['returncode']


if __name__ == '__main__':
    sys.exit(main())
