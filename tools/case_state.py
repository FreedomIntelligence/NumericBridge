#!/usr/bin/env python3
"""Validate a case checkpoint (case-state.json) against the fixed status and field set.
Call: validate(state, request=None, case_dir=None); CLI: python scripts/case_state.py check|template --state case-state.json
Use check --request request.json to verify the confirmed goal and sharing decision.
Local completion reads the report and evidence relative to the checkpoint directory (case_dir).
Use a confirmed request for file-backed completion. Without case_dir, validation is structural only.
Input: case-state.json written at each stage change.
Output: {"status":"ok"|"invalid", "problems":[...]} (check) or a new template (template).
Requires: Standard library.

Details:
status: in_progress|needs_human|done|partial. Required fields: status, stage, next_action,
running_jobs (list of {host, job_or_pid, check_command}), sessions (int >= 1), delivery_summary.
needs_human requires access_blocker (what the user must provide); done and partial require
delivery (report path); partial also requires open_items (non-empty list). Every in_progress
checkpoint needs a concrete next_action. Job entries need a host, a job ID or positive PID,
and a status-check command. Done requires next_action=null. When the request records share
consent, resolve community_contribution before closing. Pending review does not block completion.
Other fields may not replace these names (e.g. completed, active_jobs, waiting_jobs are rejected).
delivery_summary schema_version 1:
- original: {verdict, verification, evidence}. Verdicts follow acceptance.py
  (pass|gap|fail|noise_limited|incomplete), plus not_run. Verification is
  supported_native for an observed original-workload verdict, not_run otherwise.
  evidence is a list of report references. An incomplete verdict can cite partial
  native evidence; not_run cannot claim verification or acceptance evidence.
  With not_run, original.native_failure may carry the diagnosis trigger
  {kind: "native_failure", evidence: REF, reproduction: DISTINCT_REF}.
- delivery: {outcome, evidence}. Outcomes are acceptance_passed,
  acceptance_restored, acceptance_retested, bounded_attribution and partial.
  Pass and gap both meet acceptance. Restored and retested require original.baseline
  with an earlier nonpassing supported-native verdict and separate evidence.
- candidates: [{id, focused, original_relation, evidence, ...}]. Focused status
  pass|fail|not_run concerns only the candidate's targeted regression. The
  relationship to the original is independent|unproven|contributing_cause|
  verified_fix. Independent means assessed apart from the original, not proof
  of absence of causation. Contributing cause needs relationship_evidence;
  verified_fix needs focused pass and candidate-specific supported-native
  original_workload evidence of pass or gap, not reused focused evidence.
  target_commit and patch_sha256 are optional together but required for receipts.
- community (optional): [{receipt_binding, review_state}] for current candidate
  versions only. A binding must match its receipt digest and every listed
  candidate's target commit and patch SHA-256. An empty candidates list binds
  only the draft, not a patch. Receipts do not change any numerical verdict.
  End the contribution stage with a receipt matching community_draft_sha256,
  or community_local_only={draft_sha256, reason}. Use a null digest only if no
  draft was prepared. This check does not contact intake or wait for review.
- completion_goal (top level, optional): restore_original_acceptance or
  repair_defect_and_restore_acceptance. For a changed result set delivery.candidate_id
  to the exact verified_fix with matching original_workload evidence. Its base_sha256
  and patch_sha256 (or change_sha256) must match the tested_identity in the acceptance
  report and request.original_contract. A changed result needs focused_report
  for the exact change, with distinct fail-before and pass-after artifacts and
  the requested defect_id if specified. For restore_original_acceptance only,
  an unchanged accepted run can close; use acceptance_retested after a failure.

Done or partial delivery requires reconciled jobs. Done cannot claim
outcome=partial or carry required open_items. Partial names the resume condition.
`template` initializes the delivery summary.
CLI exit: 0=ok, 1=invalid, 2=input error.
"""

__all__ = ['validate', 'template']
import argparse
import json
from pathlib import Path
import sys

from tools.contribution_contract import COMMIT as _COMMIT, DIGEST as _DIGEST, SLUG as _SLUG, intake_mode

STATUSES = ('in_progress', 'needs_human', 'done', 'partial')
STAGES = ('request', 'community_lookup', 'environment', 'comparison', 'diagnosis', 'repair',
          'operator_optimization', 'delivery', 'community_contribution')
REQUIRED = ('status', 'stage', 'next_action', 'running_jobs', 'sessions', 'delivery_summary')
REJECTED = {'active_jobs': 'running_jobs', 'waiting_jobs': 'running_jobs', 'native_jobs': 'running_jobs',
            'completed': 'status=done', 'finished': 'status=done'}


_RESTORE_ACCEPTANCE = 'restore_original_acceptance'
_REPAIR_DEFECT = 'repair_defect_and_restore_acceptance'
_COMPLETION_GOALS = (_RESTORE_ACCEPTANCE, _REPAIR_DEFECT)
_ACCEPTED = ('pass', 'gap')
_NONPASSING = ('fail', 'noise_limited')
_ACCEPTED_OUTCOMES = ('acceptance_passed', 'acceptance_restored', 'acceptance_retested')
_OUTCOMES = _ACCEPTED_OUTCOMES + ('bounded_attribution', 'partial')
_FOCUSED = ('pass', 'fail', 'not_run')
_RELATIONS = ('independent', 'unproven', 'contributing_cause', 'verified_fix')
_REVIEW_STATES = ('pending_review', 'needs_information', 'accepted', 'declined', 'published')


def _matches(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _checkable_job(row):
    if not isinstance(row, dict):
        return False
    job = row.get('job_or_pid')
    return (_nonblank(row.get('host')) and _nonblank(row.get('check_command'))
            and (_nonblank(job) or (type(job) is int and job > 0)))


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
        if not isinstance(bound, list):
            problems.append(f'{name}.candidates must be a list (empty for a draft-only receipt)')
            continue
        ids = set()
        for j, row in enumerate(bound):
            item = f'{name}.candidates[{j}]'
            if not isinstance(row, dict):
                problems.append(f'{item} must be an object')
                continue
            cid = row.get('candidate_id')
            version = _version(row, item, problems, required=True)
            if not _matches(_SLUG, cid) or len(cid) > 120:
                problems.append(f'{item}.candidate_id must be a candidate slug')
            elif cid in ids:
                problems.append(f'{item}.candidate_id must be unique within the receipt')
            elif cid not in current:
                problems.append(f'{item}.candidate_id must identify a current candidate')
            elif version is not None and current[cid] != version:
                problems.append(f'{item} target_commit and patch_sha256 must match the current candidate')
            if isinstance(cid, str):
                ids.add(cid)
            tested, tested_patch = row.get('tested_commit'), row.get('tested_patch_sha256')
            if (tested is not None or tested_patch is not None) and (
                    not _matches(_COMMIT, tested) or not _matches(_DIGEST, tested_patch)):
                problems.append(f'{item} requires both tested_commit and tested_patch_sha256, or neither')


def _native_failure(trigger, path, problems):
    if not isinstance(trigger, dict) or trigger.get('kind') != 'native_failure' or 'verdict' in trigger:
        problems.append(f'{path} must be a native_failure trigger without an acceptance verdict')
        return False
    refs = (trigger.get('evidence'), trigger.get('reproduction'))
    if not all(isinstance(ref, str) and ref.strip() for ref in refs) or refs[0].strip() == refs[1].strip():
        problems.append(f'{path} requires separate first-failure and reproduction evidence')
        return False
    return True


def _local_file(case_dir, ref, path, problems):
    if not _nonblank(ref):
        problems.append(f'{path} must name a local evidence file')
        return None
    try:
        saved = Path(case_dir) / ref
        if not saved.is_file() or not saved.stat().st_size:
            raise ValueError('file is missing or empty')
    except (OSError, ValueError):
        problems.append(f'{path}: missing or empty local file {ref!r}')
        return None
    return saved


def _acceptance_report(case_dir, ref, verdict, path, problems):
    try:
        report = json.loads((Path(case_dir) / ref).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        problems.append(f'{path} cannot read acceptance report {ref!r}: {exc}')
        return None
    if (not isinstance(report, dict) or report.get('kind') != 'acceptance'
            or type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('verdict') != verdict or report.get('passed') is not (verdict in _ACCEPTED)):
        problems.append(f'{path} {ref!r} must be an acceptance report matching the recorded verdict')
        return None
    return report


def _tested_acceptance(refs, verdict, case_dir, path, problems):
    """Read the measured report, not an identity copied into the checkpoint."""
    if not refs:
        return None
    if case_dir is None:
        problems.append(f'{path} needs a case directory to read acceptance reports')
        return None
    contexts = set()
    for ref in refs:
        report = _acceptance_report(case_dir, ref, verdict, path, problems)
        if report is None:
            continue
        identity = report.get('tested_identity')
        if (not isinstance(identity, dict) or set(identity) != {'workload_sha256', 'base_sha256', 'change_sha256'}
                or not all(_matches(_DIGEST, identity[key]) for key in ('workload_sha256', 'base_sha256'))
                or (identity['change_sha256'] is not None and not _matches(_DIGEST, identity['change_sha256']))):
            problems.append(f'{path} {ref!r} lacks a tested workload, base and change identity')
            continue
        criterion = report.get('criterion_sha256')
        if not _matches(_DIGEST, criterion):
            problems.append(f'{path} {ref!r} lacks the measured acceptance criterion')
            continue
        contexts.add((identity['workload_sha256'], identity['base_sha256'], criterion, identity['change_sha256']))
    if len(contexts) > 1:
        problems.append(f'{path} acceptance reports disagree on measured identity or criterion')
        return None
    return next(iter(contexts), None)


_CONTRACT_KEYS = ('workload_sha256', 'base_sha256', 'criterion_sha256')


def _focused_report(final, base, change, defect_id, case_dir, path, problems):
    ref = final.get('focused_report')
    if not isinstance(final.get('evidence'), list) or ref not in final['evidence']:
        problems.append(f'{path}.focused_report must cite focused regression evidence')
        return
    receipt = _local_file(case_dir, ref, path + '.focused_report', problems)
    if receipt is None:
        return
    try:
        report = json.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        problems.append(f'{path}.focused_report cannot be read: {exc}')
        return
    if (not isinstance(report, dict) or report.get('kind') != 'focused-verification'
            or type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('base_sha256') != base or report.get('change_sha256') != change
            or not _nonblank(report.get('defect_id')) or (defect_id is not None and report['defect_id'] != defect_id)):
        problems.append(f'{path}.focused_report must match the requested defect (if any) and final tested base and change')
        return
    refs = []
    for key, verdict in (('before', 'fail'), ('after', 'pass')):
        finding = report.get(key)
        if not isinstance(finding, dict) or finding.get('verdict') != verdict:
            problems.append(f'{path}.focused_report needs a {key} {verdict} result')
        elif artifact := _local_file(case_dir, finding.get('evidence'), f'{path}.focused_report.{key}', problems):
            refs.append(artifact)
    if len(refs) == 2 and (refs[0].samefile(refs[1]) or any(artifact.samefile(receipt) for artifact in refs)):
        problems.append(f'{path}.focused_report needs distinct before and after execution artifacts')


def _verify_repair_delivery(original, verdict, refs, delivery, candidates, current, goal, request, case_dir, path, problems):
    outcome = delivery.get('outcome') if isinstance(delivery, dict) else None
    cid = delivery.get('candidate_id') if isinstance(delivery, dict) else None
    if outcome not in _ACCEPTED_OUTCOMES:
        problems.append('repair done requires an accepted original outcome, not a bounded finding')
    contract = request.get('original_contract') if isinstance(request, dict) else None
    locked = None
    if not isinstance(contract, dict) or not all(_matches(_DIGEST, contract.get(key)) for key in _CONTRACT_KEYS):
        problems.append('repair done requires the confirmed request.original_contract workload, base and criterion digests')
    else:
        locked = tuple(contract[key] for key in _CONTRACT_KEYS)
    defect_id = request.get('defect_id') if isinstance(request, dict) else None
    if goal == _REPAIR_DEFECT and not _nonblank(defect_id):
        problems.append('specified defect repair requires request.defect_id')
    baseline = original.get('baseline') if isinstance(original, dict) else None
    if cid is None and outcome in ('acceptance_passed', 'acceptance_retested') and goal == _RESTORE_ACCEPTANCE:
        if outcome == 'acceptance_passed' and isinstance(baseline, dict) and baseline.get('verdict') in _NONPASSING:
            problems.append('use acceptance_retested to record an unchanged result after a failed baseline')
        measured = _tested_acceptance(refs, verdict, case_dir, path + '.original', problems)
        if measured is not None and measured[-1] is not None:
            problems.append('unchanged acceptance requires a measured report with no change_sha256')
    elif not isinstance(cid, str) or cid not in current:
        problems.append('repair done requires delivery_summary.delivery.candidate_id for the final candidate')
        return
    else:
        final = next(row for row in candidates if isinstance(row, dict) and row.get('id') == cid)
        workload = final.get('original_workload')
        if final.get('original_relation') != 'verified_fix' or final.get('focused') != 'pass':
            problems.append('repair done requires the final candidate to be a verified_fix with focused pass')
        if not isinstance(workload, dict) or workload.get('verdict') != verdict or verdict not in _ACCEPTED or not refs:
            problems.append('repair done requires the final candidate to match the accepted original verdict')
            return
        candidate_refs = _evidence(workload, 'evidence', path + '.delivery candidate original_workload', problems)
        if set(candidate_refs) != set(refs):
            problems.append('repair done requires the same original-workload evidence for the final candidate and original verdict')
            return
        measured = _tested_acceptance(refs, verdict, case_dir, path + '.original', problems)
        base = final.get('base_sha256')
        change = final.get('patch_sha256', final.get('change_sha256'))
        if not _matches(_DIGEST, base) or not _matches(_DIGEST, change):
            problems.append('final candidate requires base_sha256 and patch_sha256 or change_sha256')
        else:
            if measured is not None and (measured[1], measured[-1]) != (base, change):
                problems.append('final candidate does not match the base and change measured in the original acceptance report')
            if case_dir is not None:
                _focused_report(final, base, change, defect_id, case_dir, path + '.delivery', problems)
        if outcome != 'acceptance_restored' and not (isinstance(baseline, dict)
                and (baseline.get('native_failure') or baseline.get('verdict') in _ACCEPTED)):
            problems.append('changed repair requires an explicit acceptance or reproduced native failure baseline')
    if measured is not None and locked is not None and measured[:-1] != locked:
        problems.append('measured acceptance does not match the confirmed original workload, base and criterion')
    if isinstance(baseline, dict) and baseline.get('verdict') in _NONPASSING + _ACCEPTED:
        previous_refs = _evidence(baseline, 'evidence', path + '.original.baseline', problems, required=True)
        previous = _tested_acceptance(previous_refs, baseline['verdict'], case_dir, path + '.original.baseline', problems)
        if previous is not None and (previous[-1] is not None or (measured is not None and previous[:-1] != measured[:-1])):
            problems.append('baseline acceptance must be unchanged and match the current workload, base and criterion')
    elif isinstance(baseline, dict) and baseline.get('native_failure') and case_dir is not None:
        trigger = baseline['native_failure']
        if isinstance(trigger, dict):
            for key in ('evidence', 'reproduction'):
                _local_file(case_dir, trigger.get(key), path + f'.original.baseline.native_failure.{key}', problems)


def _delivery_summary(summary, problems, *, done, goal, request, case_dir):
    path = 'delivery_summary'
    if not isinstance(summary, dict):
        problems.append(f'{path} must be an object')
        return
    if type(summary.get('schema_version')) is not int or summary['schema_version'] != 1:
        problems.append(f'{path}.schema_version must be 1')
        return
    original = summary.get('original')
    verdict, refs = _workload(original, path + '.original', problems)
    native_failure = False
    if isinstance(original, dict) and 'native_failure' in original:
        native_failure = _native_failure(original['native_failure'], path + '.original.native_failure', problems)
        if verdict != 'not_run':
            problems.append(f'{path}.original.native_failure requires acceptance verdict not_run')
    baseline_verdict = None
    if isinstance(original, dict) and 'baseline' in original:
        baseline = original['baseline']
        baseline_verdict, baseline_refs = _workload(baseline, path + '.original.baseline', problems)
        if isinstance(baseline, dict) and 'native_failure' in baseline:
            _native_failure(baseline['native_failure'], path + '.original.baseline.native_failure', problems)
            if baseline_verdict != 'not_run':
                problems.append(f'{path}.original.baseline.native_failure requires acceptance verdict not_run')
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
        if outcome in _ACCEPTED_OUTCOMES and verdict not in _ACCEPTED:
            problems.append(f'{path}.delivery {outcome} requires original pass or gap')
        if outcome in ('acceptance_restored', 'acceptance_retested') and baseline_verdict not in _NONPASSING:
            problems.append(f'{path}.delivery {outcome} requires an earlier nonpassing original.baseline')
        if outcome == 'acceptance_retested' and (goal != _RESTORE_ACCEPTANCE or delivery.get('candidate_id') is not None):
            problems.append('acceptance_retested is for the unchanged workload and an acceptance-only goal')
        if outcome == 'bounded_attribution' and not (verdict in _NONPASSING or (verdict == 'not_run' and native_failure)):
            problems.append(f'{path}.delivery bounded_attribution requires original fail or noise_limited, '
                            'or not_run with reproduced native failure')
        if done and outcome == 'partial':
            problems.append(f'{path}.delivery outcome partial is not a done delivery')
    candidates = summary.get('candidates')
    current = _candidates(candidates, path + '.candidates', problems)
    if done and case_dir is not None:
        if isinstance(delivery, dict):
            for ref in _evidence(delivery, 'evidence', path + '.delivery', problems):
                _local_file(case_dir, ref, path + '.delivery.evidence', problems)
        if verdict == 'not_run' and native_failure:
            for key, ref in original['native_failure'].items():
                if key in ('evidence', 'reproduction'):
                    _local_file(case_dir, ref, path + f'.original.native_failure.{key}', problems)
        if goal not in _COMPLETION_GOALS and verdict != 'not_run':
            for ref in refs:
                _acceptance_report(case_dir, ref, verdict, path + '.original', problems)
    if done and goal in _COMPLETION_GOALS:
        _verify_repair_delivery(original, verdict, refs, delivery, candidates, current, goal, request, case_dir, path, problems)
    if 'community' in summary:
        _community(summary['community'], current, path + '.community', problems)


def _community_completion(state, summary, problems):
    digest = state.get('community_draft_sha256')
    if digest is not None and not _matches(_DIGEST, digest):
        problems.append('community_draft_sha256 must identify the exact reviewed draft')
    if not isinstance(summary, dict):
        return
    rows = summary.get('community')
    matched = isinstance(rows, list) and _matches(_DIGEST, digest) and any(
        isinstance(row, dict) and isinstance(row.get('receipt_binding'), dict)
        and row['receipt_binding'].get('draft_sha256') == digest for row in rows)
    local = summary.get('community_local_only')
    local_ok = (isinstance(local, dict) and 'draft_sha256' in local
                and local['draft_sha256'] == digest and _nonblank(local.get('reason')))
    if local is not None and not local_ok:
        problems.append('community_local_only must record a reason for the current draft (null digest if none was prepared)')
    if state.get('status') == 'done' and not (matched or local_ok):
        problems.append('community completion requires a receipt for the current draft or a matching community_local_only reason')
    if matched and local_ok:
        problems.append('the current draft cannot be both received and local-only')
    if state.get('stage') == 'community_contribution':
        delivery = summary.get('delivery')
        if state.get('status') == 'partial' or (isinstance(delivery, dict) and delivery.get('outcome') == 'partial'):
            problems.append('finish required local work before entering community contribution')


def validate(state, *, request=None, case_dir=None):
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
    if state.get('stage') not in STAGES:
        problems.append('stage must name one of the nine workflow stages')
    jobs = state.get('running_jobs', [])
    if not isinstance(jobs, list) or not all(_checkable_job(job) for job in jobs):
        problems.append('running_jobs entries need a non-empty host and check_command and a job ID or positive PID')
    sessions = state.get('sessions')
    if 'sessions' in state and not (type(sessions) is int and sessions >= 1):
        problems.append('sessions must be a positive integer')
    if status == 'needs_human' and not state.get('access_blocker'):
        problems.append('needs_human requires access_blocker naming what the user must provide')
    community_stage = state.get('stage') == 'community_contribution'
    local_complete = status == 'done' or community_stage
    if local_complete or status == 'partial':
        if not _nonblank(state.get('delivery')):
            problems.append(f'{status} requires delivery (report path)')
        elif case_dir is not None:
            _local_file(case_dir, state['delivery'], 'delivery', problems)
    if local_complete and case_dir is not None and request is None:
        problems.append('local completion must be validated against the confirmed request')
    if status == 'partial' and not (isinstance(state.get('open_items'), list) and state['open_items']):
        problems.append('partial requires open_items with the missing evidence or input')
    next_action = state.get('next_action')
    if status == 'in_progress' and not _nonblank(next_action):
        problems.append('in_progress requires a concrete next_action even with running jobs')
    if status == 'done':
        if state.get('stage') not in ('delivery', 'community_contribution'):
            problems.append('done requires the delivery or community_contribution stage')
        if next_action is not None:
            problems.append('done requires next_action=null; finish or resolve the pending action first')
    completion_goal = state.get('completion_goal')
    valid_goals = '|'.join(_COMPLETION_GOALS)
    if 'completion_goal' in state and completion_goal not in _COMPLETION_GOALS:
        problems.append(f'completion_goal must be {valid_goals} when supplied')
    if request is not None:
        if not isinstance(request, dict):
            problems.append('request must be a JSON object')
        elif 'completion_goal' in request and request['completion_goal'] not in _COMPLETION_GOALS:
            problems.append(f'request.completion_goal must be {valid_goals} when supplied')
        elif completion_goal != request.get('completion_goal'):
            problems.append('checkpoint completion_goal must match the confirmed request')
    sharing = request.get('community_contribution') if isinstance(request, dict) else None
    summary = state.get('delivery_summary')
    share_requested = isinstance(sharing, dict) and sharing.get('consent') == 'share'
    if status == 'done' and share_requested and not community_stage:
        problems.append('done with recorded share consent requires resolving the community_contribution stage')
    if community_stage or (status == 'done' and share_requested):
        _community_completion(state, summary, problems)
    repair = completion_goal in _COMPLETION_GOALS
    label = 'repair' if repair else 'case'
    if (local_complete or status == 'partial') and jobs:
        problems.append(f'{label} cannot be delivered with running jobs; reconcile them first')
    if local_complete and state.get('open_items'):
        problems.append('local completion cannot have required open_items')
    if status == 'partial' and not _nonblank(state.get('resume_when')):
        problems.append(f'partial {label} requires resume_when')
    if 'delivery_summary' in state:
        _delivery_summary(summary, problems, done=local_complete, goal=completion_goal, request=request, case_dir=case_dir)
    return dict(status='ok' if not problems else 'invalid', problems=problems)


def template():
    summary = dict(schema_version=1, original=dict(verdict='not_run', verification='not_run', evidence=[]),
                   delivery=dict(outcome='partial', evidence=[]), candidates=[])
    return dict(status='in_progress', stage='request', next_action='Confirm scope, grants and requested outcome', running_jobs=[],
                sessions=1, delivery_summary=summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('check', 'template'))
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--request', type=Path, help='check against the confirmed request (check only)')
    args = parser.parse_args(argv)
    if args.request and args.command != 'check':
        parser.error('--request is only valid with check')
    try:
        if args.command == 'template':
            with args.state.open('x') as stream:
                json.dump(template(), stream, indent=2)
                stream.write('\n')
            return 0
        request = json.loads(args.request.read_text()) if args.request else None
        if args.request and not isinstance(request, dict):
            raise ValueError('request must be a JSON object')
        result = validate(json.loads(args.state.read_text()), request=request, case_dir=args.state.resolve().parent)
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
