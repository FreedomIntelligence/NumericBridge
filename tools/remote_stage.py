"""Attach to or start a single fenced remote stage; return waiting as exit 75.

Controller: python -m tools.remote_stage run --profile PROFILE --key STAGE_KEY --output NEW.json
Worker: same module in a verified remote checkout, launched by the controller.

A profile supplies an authorized SSH host (null for local tests), remote Python,
absolute checkout and private state paths, literal argv, a time limit, and
optional absolute input files or {run}-relative readiness evidence to verify.
No shell is used for native command execution. The only SSH shell command is
constructed from quoted literal arguments. Use a pinned worker checkout and
never take a profile/command from a remote reply or from an untrusted packet.
"""

import argparse
import base64
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time


KEY = re.compile(r'[0-9a-f]{64}\Z')
HOST = re.compile(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
WAIT = 75
MAX_PROFILE = 32768
MAX_RECEIPT = 1024 * 1024
UNSET = object()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def load(path, limit):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, f'File exceeds size limit: {path.name}')
    return json.loads(raw)


def atomic(path, value):
    with NamedTemporaryFile(dir=path.parent, prefix='.receipt-', delete=False) as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
        temp = Path(stream.name)
    temp.replace(path)


def literal(value):
    return isinstance(value, str) and bool(value) and all(ord(c) >= 32 and c != '\x7f' for c in value)


def absolute(value):
    return literal(value) and Path(value).is_absolute() and '..' not in Path(value).parts


def relative(value):
    return literal(value) and value != '.' and not Path(value).is_absolute() and '..' not in Path(value).parts


def profile(value):
    required = {'schema_version', 'host', 'python', 'checkout', 'state_dir', 'argv', 'timeout_seconds', 'readiness'}
    optional = {'inputs', 'max_attempts', 'retry_exit_codes', 'worker_revision'}
    require(isinstance(value, dict) and required <= value.keys() <= required | optional,
            'Invalid remote stage profile fields')
    require(type(value['schema_version']) is int and value['schema_version'] == 1, 'Unsupported remote profile')
    require(value['host'] is None or isinstance(value['host'], str) and HOST.fullmatch(value['host']), 'Invalid SSH host')
    require(all(absolute(value[k]) for k in ('python', 'checkout', 'state_dir')), 'Worker paths must be absolute')
    if value['host'] or 'worker_revision' in value:
        require(isinstance(value.get('worker_revision'), str) and re.fullmatch('[a-f0-9]{40}', value['worker_revision']),
                'SSH worker requires a pinned checkout commit')
    argv = value['argv']
    require(isinstance(argv, list) and 0 < len(argv) <= 100 and all(isinstance(arg, str) and '\0' not in arg
            and not arg.startswith('TODO') for arg in argv), 'Native argv must be literal, without TODO placeholders')
    require(absolute(argv[0]), 'Native executable must have an absolute path')
    require(type(value['timeout_seconds']) is int and 0 < value['timeout_seconds'] <= 86400, 'Invalid worker timeout')
    inputs = value.get('inputs', {})
    require(isinstance(inputs, dict) and len(inputs) <= 64 and all(absolute(k) and isinstance(v, str) and KEY.fullmatch(v)
                                                                   for k, v in inputs.items()), 'Invalid fixed remote inputs')
    readiness = value.get('readiness')
    require(isinstance(readiness, dict) and set(readiness) == {'path', 'equals'} and
            relative(readiness['path']) and isinstance(readiness['equals'], dict) and bool(readiness['equals']),
            'Invalid readiness JSON check')
    attempts = value.get('max_attempts', 1)
    codes = value.get('retry_exit_codes', [])
    require(type(attempts) is int and 1 <= attempts <= 4 and isinstance(codes, list) and
            all(type(code) is int and 1 <= code <= 125 for code in codes), 'Invalid retry policy')
    require(attempts == 1 or bool(codes), 'Retries require explicitly allowed exit codes')
    return value


def file_digest(path):
    require(path.is_file() and not path.is_symlink(), 'Expected a regular pinned input or readiness file')
    h = sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def readiness(spec, run):
    rule = spec.get('readiness')
    if not rule:
        return None
    path = run / rule['path']
    require(path.resolve().is_relative_to(run) and not path.is_symlink(), 'Readiness path escaped the run')
    data = load(path, MAX_RECEIPT)
    require(isinstance(data, dict) and all(canonical(data.get(k, None)) == canonical(v) and k in data
                                          for k, v in rule['equals'].items()), 'Readiness check failed')
    return file_digest(path)


def identity(key, spec):
    require(isinstance(key, str) and KEY.fullmatch(key), 'Expected a workflow stage fingerprint')
    return sha256(canonical([key, spec])).hexdigest()


def worker_root(spec):
    root = Path(spec['state_dir'])
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = root.stat()
    require(root.resolve() == root and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            'Remote state directory must be private, owned and not symlinked')
    return root


@contextmanager
def gate(root, name):
    import fcntl
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def release_status(attempt, token, stamp):
    import fcntl
    result = dict(token=token, attempt=stamp)
    final = attempt / 'terminal.json'
    if final.exists():
        report = load(final, MAX_RECEIPT)
        require(report['token'] == token and report['attempt'] == stamp, 'Terminal receipt belongs to a different run')
        return report
    with (attempt / 'worker.lock').open('rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(result, status='running')
    # Re-read after probing: process can have exited between both observations.
    if final.exists():
        return release_status(attempt, token, stamp)
    return dict(result, status='unknown', reason='reservation exists without a live worker or terminal receipt')


def _detach(spec, attempt, token, stamp):
    import fcntl
    with (attempt / 'worker.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with (attempt / 'worker.log').open('xb') as output:
            subprocess.Popen([spec['python'], '-B', '-m', 'tools.remote_stage', '_execute',
                              '--directory', str(attempt), '--token', token, '--attempt', str(stamp),
                              '--lock-fd', str(lock.fileno())], cwd=spec['checkout'],
                             start_new_session=True, pass_fds=(lock.fileno(),), stdin=subprocess.DEVNULL,
                             stdout=output, stderr=output)


def worker(spec, key):
    token = identity(key, spec)
    root = worker_root(spec)
    with gate(root, 'stage.gate'):
        # state_dir is reserved for one logical stage, across controller workspaces.
        # A changed stage signature may not overlap an unverified prior attempt.
        for previous in root.iterdir():
            if not KEY.fullmatch(previous.name) or previous.name == token:
                continue
            stamps = sorted((int(p.name) for p in previous.iterdir() if p.name.isdecimal()), reverse=True)
            if not stamps or not (previous / str(stamps[0]) / 'worker.lock').is_file():
                return dict(token=token, attempt=1, status='unknown', reason='prior stage identity lacks terminal evidence')
            prior = release_status(previous / str(stamps[0]), previous.name, stamps[0])
            if prior['status'] not in ('completed', 'failed'):
                return dict(token=token, attempt=1, status='unknown', reason='prior stage identity lacks terminal evidence')
        scope = root / token
        scope.mkdir(mode=0o700, exist_ok=True)
        for stamp in range(1, spec.get('max_attempts', 1) + 1):
            attempt = scope / str(stamp)
            if attempt.exists():
                # Missing lock is an ambiguous crash window; never relaunch it.
                if not (attempt / 'worker.lock').is_file():
                    return dict(token=token, attempt=stamp, status='unknown', reason='incomplete worker reservation')
                report = release_status(attempt, token, stamp)
                if report['status'] == 'completed':
                    require(report.get('readiness_sha256') == readiness(spec, attempt), 'Recorded readiness evidence changed')
                if not (report['status'] == 'failed' and stamp < spec.get('max_attempts', 1)
                        and report.get('returncode') in spec.get('retry_exit_codes', [])):
                    return report
                continue
            attempt.mkdir(mode=0o700)
            atomic(attempt / 'spec.json', spec)
            _detach(spec, attempt, token, stamp)
            return release_status(attempt, token, stamp)
    raise ValueError('Invalid exhausted retry state')


def execute(attempt, token, stamp, lock_fd):
    spec = profile(load(attempt / 'spec.json', MAX_PROFILE))
    result = dict(token=token, attempt=stamp, status='failed', returncode=None)
    try:
        for value, expected in spec.get('inputs', {}).items():
            require(file_digest(Path(value)) == expected, 'Pinned remote input changed')
        command = [arg.replace('{run}', str(attempt)) for arg in spec['argv']]
        with (attempt / 'stdout.bin').open('xb') as out, (attempt / 'stderr.bin').open('xb') as err:
            with subprocess.Popen(command, cwd=spec['checkout'], stdout=out, stderr=err,
                                  stdin=subprocess.DEVNULL, start_new_session=True, pass_fds=(lock_fd,)) as proc:
                try:
                    result['returncode'] = proc.wait(timeout=spec['timeout_seconds'])
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    result['reason'] = 'native stage timed out; no retry without reconciliation'
        if result['returncode'] == 0:
            result['readiness_sha256'] = readiness(spec, attempt)
            result['status'] = 'completed'
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Keep host paths or captured errors in the worker's private log.
        print(str(error), file=sys.stderr)
        result.update(status='failed', returncode=None, reason='worker input, execution, or readiness verification failed')
    atomic(attempt / 'terminal.json', result)


def controller(spec, key):
    expected = identity(key, spec)
    encoded = base64.urlsafe_b64encode(canonical(spec)).decode('ascii')
    require(len(canonical(spec)) <= MAX_PROFILE, 'Remote profile is too large')
    argv = [spec['python'], '-B', '-m', 'tools.remote_stage', '_worker', '--key', key, '--payload', encoded]
    host = spec['host']
    if host:
        remote = ('cd ' + shlex.quote(spec['checkout']) +
                  ' && test "$(git rev-parse --verify HEAD)" = ' + shlex.quote(spec['worker_revision']) +
                  ' && git diff --quiet HEAD -- tools/remote_stage.py && exec ' + shlex.join(argv))
        argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '--', host, remote]
    try:
        reply = subprocess.run(argv, cwd=None if host else spec['checkout'], capture_output=True, timeout=35)
    except subprocess.TimeoutExpired:
        return dict(status='unreachable', reason='Worker status timed out; reservation not assumed absent')
    if reply.returncode == 255:
        return dict(status='unreachable', reason='SSH status unavailable; reservation not assumed absent')
    require(reply.returncode == 0 and len(reply.stdout) <= MAX_RECEIPT, 'Worker rejected the stage or returned oversized status')
    result = json.loads(reply.stdout)
    require(isinstance(result, dict) and {'token', 'attempt', 'status'} <= result.keys()
            <= {'token', 'attempt', 'status', 'returncode', 'readiness_sha256', 'reason'}
            and result['token'] == expected and type(result['attempt']) is int
            and 1 <= result['attempt'] <= spec.get('max_attempts', 1)
            and result['status'] in {'completed', 'running', 'failed', 'unknown'}, 'Unexpected worker receipt')
    if 'reason' in result:
        safe_reasons = {'reservation exists without a live worker or terminal receipt', 'incomplete worker reservation',
                        'prior stage identity lacks terminal evidence',
                        'native stage timed out; no retry without reconciliation',
                        'worker input, execution, or readiness verification failed'}
        require(result['reason'] in safe_reasons, 'Unexpected remote failure label')
    if result['status'] == 'completed':
        require(result.get('returncode') == 0 and (not spec.get('readiness') or
                isinstance(result.get('readiness_sha256'), str) and KEY.fullmatch(result['readiness_sha256'])),
                'Invalid terminal worker receipt')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('run')
    command.add_argument('--profile', type=Path, required=True)
    command.add_argument('--key', required=True)
    command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('_worker')
    command.add_argument('--payload', required=True)
    command.add_argument('--key', required=True)
    command = commands.add_parser('_execute')
    command.add_argument('--directory', type=Path, required=True)
    command.add_argument('--token', required=True)
    command.add_argument('--attempt', type=int, required=True)
    command.add_argument('--lock-fd', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == '_execute':
            execute(args.directory, args.token, args.attempt, args.lock_fd)
            return 0
        if args.command == '_worker':
            raw = base64.b64decode(args.payload, altchars=b'-_', validate=True)
            require(len(raw) <= MAX_PROFILE, 'Remote profile is too large')
            print(canonical(worker(profile(json.loads(raw)), args.key)).decode())
            return 0
        report = controller(profile(load(args.profile, MAX_PROFILE)), args.key)
        with args.output.open('xb') as stream:
            stream.write(canonical(report) + b'\n')
        print(canonical(report).decode())
        if report['status'] in ('running', 'unreachable'):
            return WAIT
        if report['status'] == 'unknown':
            return 2
        return 0 if report['status'] == 'completed' else 1
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(canonical(dict(status='error', reason=str(error))).decode(), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
