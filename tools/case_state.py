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
Optional delivery_summary schema_version 1 (historical checkpoints may omit it):
- original: {verdict, verification, evidence}. Verdicts follow acceptance.py
  (pass|gap|fail|noise_limited|incomplete), plus not_run. Verification is
  supported_native for an observed original-workload verdict, not_run otherwise.
  evidence is a list of report references. An incomplete verdict can cite partial
  native evidence; not_run cannot claim verification or acceptance evidence.
- delivery: {outcome, evidence}. Outcomes are acceptance_passed,
  acceptance_restored, bounded_attribution and partial. Pass and gap both meet
  acceptance. An acceptance_restored outcome also needs original.baseline with
  an earlier nonpassing supported-native verdict and separate evidence.
- candidates: [{id, focused, original_relation, evidence, ...}]. Focused status
  pass|fail|not_run concerns only the candidate's targeted regression. The
  relationship to the original is independent|unproven|contributing_cause|
  verified_fix. Independent means assessed apart from the original, not proof
  of absence of causation. Contributing cause needs relationship_evidence;
  verified_fix needs focused pass and candidate-specific supported-native
  original_workload evidence of pass or gap, not reused focused evidence.
  target_commit and patch_sha256 are optional together but required for receipts.
- community (optional): [{receipt_binding, review_state}] for current candidate
  versions only. A binding must match its receipt digest and every candidate's
  target commit and patch SHA-256. It does not change any numerical verdict.
  This validator checks structure and consistency, not evidence bytes or intake.

`template --with-delivery-summary` includes a neutral summary to fill in.
CLI exit: 0=ok, 1=invalid, 2=input error.
"""

__all__ = ['validate', 'template']
import argparse
import json
from pathlib import Path
import sys

from tools.contribution_contract import COMMIT as _COMMIT, DIGEST as _DIGEST, SLUG as _SLUG, intake_mode

STATUSES = ('in_progress', 'needs_human', 'done', 'partial')
REQUIRED = ('status', 'stage', 'next_action', 'running_jobs', 'sessions')
REJECTED = {'active_jobs': 'running_jobs', 'waiting_jobs': 'running_jobs', 'native_jobs': 'running_jobs',
            'completed': 'status=done', 'finished': 'status=done'}


_ACCEPTED = ('pass', 'gap')
_NONPASSING = ('fail', 'noise_limited')
_OUTCOMES = ('acceptance_passed', 'acceptance_restored', 'bounded_attribution', 'partial')
_FOCUSED = ('pass', 'fail', 'not_run')
_RELATIONS = ('independent', 'unproven', 'contributing_cause', 'verified_fix')
_REVIEW_STATES = ('pending_review', 'needs_information', 'accepted', 'declined', 'published')


def _matches(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _evidence(row, key, path, problems, *, required=False):
    refs = row.get(key, [])
    if not isinstance(refs, list) or not all(isinstance(ref, str) and ref.strip() == ref and ref for ref in refs):
        problems.append(f'{path}.{key} must be a list of non-empty evidence references')
        return []
    if required and not refs:
        problems.append(f'{path}.{key} must include evidence')
    return refs


def _workload(row, path, problems):
    if not isinstance(row, dict):
        problems.append(f'{path} must be an object')
        return None, []
    verdict = row.get('verdict')
    if verdict not in _ACCEPTED + _NONPASSING + ('incomplete', 'not_run'):
        problems.append(f'{path}.verdict must be an acceptance verdict or not_run')
    verification = row.get('verification')
    if verification not in ('supported_native', 'not_run'):
        problems.append(f'{path}.verification must be supported_native or not_run')
    observed = verdict in _ACCEPTED + _NONPASSING
    refs = _evidence(row, 'evidence', path, problems, required=observed or verification == 'supported_native')
    if observed and verification != 'supported_native':
        problems.append(f'{path} observed verdict requires supported_native verification')
    if verdict == 'not_run' and (verification != 'not_run' or refs):
        problems.append(f'{path} not_run cannot claim verification or acceptance evidence')
    if verification == 'not_run' and refs and verdict != 'not_run':
        problems.append(f'{path} cannot cite acceptance evidence with not_run verification')
    return verdict, refs


def _version(row, path, problems, *, required=False):
    target, patch = row.get('target_commit'), row.get('patch_sha256')
    if required or 'target_commit' in row or 'patch_sha256' in row:
        if not _matches(_COMMIT, target) or not _matches(_DIGEST, patch):
            problems.append(f'{path} requires a full target_commit and patch_sha256 together')
            return None
        return target, patch
    return None


def _candidates(rows, path, problems):
    if not isinstance(rows, list):
        problems.append(f'{path} must be a list')
        return {}
    current = {}
    for i, candidate in enumerate(rows):
        name = f'{path}[{i}]'
        if not isinstance(candidate, dict):
            problems.append(f'{name} must be an object')
            continue
        cid = candidate.get('id')
        version = _version(candidate, name, problems)
        if not _matches(_SLUG, cid) or len(cid) > 120 or cid in current:
            problems.append(f'{name}.id must be a unique candidate slug')
        else:
            current[cid] = version
        focused = candidate.get('focused')
        if focused not in _FOCUSED:
            problems.append(f'{name}.focused must be one of ' + '|'.join(_FOCUSED))
        refs = _evidence(candidate, 'evidence', name, problems, required=focused in ('pass', 'fail'))
        if focused == 'not_run' and refs:
            problems.append(f'{name} focused not_run cannot claim focused test evidence')
        relation = candidate.get('original_relation')
        if relation not in _RELATIONS:
            problems.append(f'{name}.original_relation must be one of ' + '|'.join(_RELATIONS))
        causal_refs = _evidence(candidate, 'relationship_evidence', name, problems,
                                required=relation == 'contributing_cause')
        if relation == 'contributing_cause' and set(causal_refs) & set(refs):
            problems.append(f'{name}.relationship_evidence must not reuse focused regression evidence')
        end_to_end = None
        if 'original_workload' in candidate:
            end_to_end, original_refs = _workload(candidate['original_workload'], name + '.original_workload', problems)
            if set(original_refs) & set(refs):
                problems.append(f'{name}.original_workload must not reuse focused regression evidence')
        if relation == 'verified_fix' and (focused != 'pass' or end_to_end not in _ACCEPTED):
            problems.append(f'{name} verified_fix requires focused pass and candidate-specific original_workload pass or gap')
        if relation in ('independent', 'unproven') and end_to_end in _ACCEPTED:
            problems.append(f'{name} focused-only relationship cannot claim an original_workload pass or gap')
    return current


def _is_intake(destination):
    try:
        return intake_mode(destination) != 'local_only'
    except ValueError:
        return False


def _community(rows, current, path, problems):
    if not isinstance(rows, list):
        problems.append(f'{path} must be a list')
        return
    receipts = set()
    for i, receipt in enumerate(rows):
        name = f'{path}[{i}]'
        if not isinstance(receipt, dict) or not isinstance(receipt.get('receipt_binding'), dict):
            problems.append(f'{name} needs a receipt_binding object')
            continue
        if receipt.get('review_state') not in _REVIEW_STATES:
            problems.append(f'{name}.review_state must be one of ' + '|'.join(_REVIEW_STATES))
        binding = receipt['receipt_binding']
        name += '.receipt_binding'
        if type(binding.get('schema_version')) is not int or binding['schema_version'] != 1:
            problems.append(f'{name}.schema_version must be 1')
        if not _is_intake(binding.get('destination')):
            problems.append(f'{name}.destination must be a NumericBridge intake URL')
        digest, receipt_id = binding.get('draft_sha256'), binding.get('receipt_id')
        if not _matches(_DIGEST, digest) or receipt_id != f'submission-{digest}':
            problems.append(f'{name}.receipt_id must match draft_sha256')
        elif receipt_id in receipts:
            problems.append(f'{name}.receipt_id must be unique')
        else:
            receipts.add(receipt_id)
        bound = binding.get('candidates')
        if not isinstance(bound, list) or not bound:
            problems.append(f'{name}.candidates must bind at least one current candidate version')
            continue
        ids = set()
        for j, row in enumerate(bound):
            item = f'{name}.candidates[{j}]'
            if not isinstance(row, dict):
                problems.append(f'{item} must be an object')
                continue
            cid = row.get('candidate_id')
            version = _version(row, item, problems, required=True)
            if not isinstance(cid, str) or cid not in current:
                problems.append(f'{item}.candidate_id must identify a current candidate')
            elif cid in ids:
                problems.append(f'{item}.candidate_id must be unique within the receipt')
            elif version is not None and current[cid] != version:
                problems.append(f'{item} target_commit and patch_sha256 must match the current candidate')
            if isinstance(cid, str):
                ids.add(cid)
            tested, tested_patch = row.get('tested_commit'), row.get('tested_patch_sha256')
            if (tested is not None or tested_patch is not None) and (
                    not _matches(_COMMIT, tested) or not _matches(_DIGEST, tested_patch)):
                problems.append(f'{item} requires both tested_commit and tested_patch_sha256, or neither')


def _delivery_summary(summary, problems):
    path = 'delivery_summary'
    if not isinstance(summary, dict):
        problems.append(f'{path} must be an object')
        return
    if type(summary.get('schema_version')) is not int or summary['schema_version'] != 1:
        problems.append(f'{path}.schema_version must be 1')
        return
    original = summary.get('original')
    verdict, refs = _workload(original, path + '.original', problems)
    baseline_verdict = None
    if isinstance(original, dict) and 'baseline' in original:
        baseline_verdict, baseline_refs = _workload(original['baseline'], path + '.original.baseline', problems)
        if set(refs) & set(baseline_refs):
            problems.append(f'{path}.original.baseline and current verdict need separate evidence')
    delivery = summary.get('delivery')
    if not isinstance(delivery, dict):
        problems.append(f'{path}.delivery must be an object')
    else:
        outcome = delivery.get('outcome')
        if outcome not in _OUTCOMES:
            problems.append(f'{path}.delivery.outcome must be one of ' + '|'.join(_OUTCOMES))
        _evidence(delivery, 'evidence', path + '.delivery', problems, required=outcome in _OUTCOMES[:-1])
        if outcome in ('acceptance_passed', 'acceptance_restored') and verdict not in _ACCEPTED:
            problems.append(f'{path}.delivery {outcome} requires original pass or gap')
        if outcome == 'acceptance_restored' and baseline_verdict not in _NONPASSING:
            problems.append(f'{path}.delivery acceptance_restored requires an earlier nonpassing original.baseline')
        if outcome == 'bounded_attribution' and verdict not in _NONPASSING:
            problems.append(f'{path}.delivery bounded_attribution requires original fail or noise_limited')
    current = _candidates(summary.get('candidates'), path + '.candidates', problems)
    if 'community' in summary:
        _community(summary['community'], current, path + '.community', problems)


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
    if 'delivery_summary' in state:
        _delivery_summary(state['delivery_summary'], problems)
    return dict(status='ok' if not problems else 'invalid', problems=problems)


def template(*, with_delivery_summary=False):
    state = dict(status='in_progress', stage='request', next_action='community retrieval', running_jobs=[],
                 sessions=1)
    if with_delivery_summary:
        state['delivery_summary'] = dict(schema_version=1,
                                         original=dict(verdict='not_run', verification='not_run', evidence=[]),
                                         delivery=dict(outcome='partial', evidence=[]), candidates=[])
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('check', 'template'))
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--with-delivery-summary', action='store_true', help='include a v1 summary in a new template')
    args = parser.parse_args(argv)
    if args.with_delivery_summary and args.command != 'template':
        parser.error('--with-delivery-summary is only valid with template')
    try:
        if args.command == 'template':
            with args.state.open('x') as stream:
                json.dump(template(with_delivery_summary=args.with_delivery_summary), stream, indent=2)
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
