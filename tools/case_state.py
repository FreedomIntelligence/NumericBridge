#!/usr/bin/env python3
"""Validate a case checkpoint (case-state.json) against the fixed status and field set.
Call: validate(state); CLI: python scripts/case_state.py check|template --state case-state.json
Input: case-state.json written at each stage change.
Output: {"status":"ok"|"invalid", "problems":[...]} (check) or a new template (template).
Requires: Standard library.

Details:
status: in_progress|needs_human|done|partial. Required fields: status, stage, next_action,
running_jobs (list of {host, job_or_pid, check_command}), sessions (int >= 1).
needs_human requires access_blocker (what the user must provide); done and partial require
delivery (report path); partial also requires open_items (non-empty list). in_progress
with an empty running_jobs requires next_action. Other fields are allowed but may not
replace these names (e.g. completed, active_jobs, waiting_jobs are rejected).
CLI exit: 0=ok, 1=invalid, 2=input error.
"""

__all__ = ['validate', 'template']
import argparse
import json
from pathlib import Path
import sys

STATUSES = ('in_progress', 'needs_human', 'done', 'partial')
REQUIRED = ('status', 'stage', 'next_action', 'running_jobs', 'sessions')
REJECTED = {'active_jobs': 'running_jobs', 'waiting_jobs': 'running_jobs', 'native_jobs': 'running_jobs',
            'completed': 'status=done', 'finished': 'status=done'}


def validate(state):
    problems = []
    if not isinstance(state, dict):
        return dict(status='invalid', problems=['case state must be a JSON object'])
    for key in REQUIRED:
        if key not in state:
            problems.append('missing field: ' + key)
    for key, replacement in REJECTED.items():
        if key in state:
            problems.append(f'use {replacement} instead of {key}')
    status = state.get('status')
    if status not in STATUSES:
        problems.append('status must be one of ' + '|'.join(STATUSES) + f', got {status!r}')
    jobs = state.get('running_jobs', [])
    if not isinstance(jobs, list) or not all(
            isinstance(j, dict) and {'host', 'job_or_pid', 'check_command'} <= set(j) for j in jobs):
        problems.append('running_jobs entries need host, job_or_pid and check_command')
    sessions = state.get('sessions')
    if 'sessions' in state and not (type(sessions) is int and sessions >= 1):
        problems.append('sessions must be a positive integer')
    if status == 'needs_human' and not state.get('access_blocker'):
        problems.append('needs_human requires access_blocker naming what the user must provide')
    if status in ('done', 'partial') and not state.get('delivery'):
        problems.append(f'{status} requires delivery (report path)')
    if status == 'partial' and not (isinstance(state.get('open_items'), list) and state['open_items']):
        problems.append('partial requires open_items with the missing evidence or input')
    if status == 'in_progress' and not jobs and not state.get('next_action'):
        problems.append('in_progress without running jobs needs next_action')
    return dict(status='ok' if not problems else 'invalid', problems=problems)


def template():
    return dict(status='in_progress', stage='request', next_action='community retrieval', running_jobs=[],
                sessions=1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('check', 'template'))
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'template':
            with args.state.open('x') as stream:
                json.dump(template(), stream, indent=2)
                stream.write('\n')
            return 0
        result = validate(json.loads(args.state.read_text()))
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
