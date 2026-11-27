"""Start a bounded watch controller that survives a chat/SSH client disconnect.

Only start a plan whose case-specific resource grants allow detached execution.
An active controller is identified by an inherited file lock, not by its PID.
Do not put submission commands or credentials in the unattended execution plan.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from .workflow import _plan, _positive, watch_workflow
from .remote_stage import atomic


def start(plan, workspace, *, seconds, interval=15, jobs=1):
    plan, workspace = Path(plan).resolve(), Path(workspace).resolve()
    if not _positive(seconds) or seconds > 86400 or not _positive(interval) or interval < 1 or type(jobs) is not int or jobs < 1:
        raise ValueError('Choose an explicit watch budget (up to 24 hours), interval >= 1, and a positive job count')
    spec, _, _ = _plan(plan)
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = workspace.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Detached controller workspace must be private and owned (0700)')
    lock = os.open(workspace / 'controller.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'controller_active', 'workspace': str(workspace)}
        run = workspace / ('compare-watch-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        run.mkdir(mode=0o700)
        cmd = [sys.executable, '-B', '-m', 'tools.workflow_controller', '_watch', '--plan', str(plan),
               '--workspace', str(workspace), '--seconds', str(seconds), '--interval', str(interval),
               '--jobs', str(jobs), '--result', str(run / 'result.json'), '--lock-fd', str(lock)]
        with (run / 'controller.log').open('xb') as log:
            process = subprocess.Popen(cmd, cwd=Path(__file__).resolve().parents[1], start_new_session=True,
                                       pass_fds=(lock,), stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        return {'status': 'controller_started', 'id': spec['id'], 'pid': process.pid, 'workspace': str(workspace),
                'report': str(run / 'result.json'), 'log': str(run / 'controller.log')}
    finally:
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start', '_watch'))
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--seconds', required=True, type=float)
    parser.add_argument('--interval', type=float, default=15)
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--lock-fd', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command == '_watch':
            if args.lock_fd is None or args.result is None:
                raise ValueError('Missing controller start reservation')
            with os.fdopen(args.lock_fd, 'r+b'):
                try:
                    result = watch_workflow(args.plan, args.workspace, seconds=args.seconds,
                                            interval=args.interval, jobs=args.jobs)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    result = {'status': 'error', 'reason': str(error)}
                atomic(args.result, result)
            return 0 if result['status'] == 'completed' else 1
        print(json.dumps(start(args.plan, args.workspace, seconds=args.seconds, interval=args.interval, jobs=args.jobs)))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'error', 'reason': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
