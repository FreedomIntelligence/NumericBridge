#!/usr/bin/env python3
"""Decide the next bounded diagnosis action without launching anything.

Call: python scripts/diagnostic_round.py round|probe --input FILE
Inputs: a diagnostic round ledger or an exact native probe control (v1).
Output: a JSON action, budget and evidence guards. Exit 2 on invalid input.

The caller records actual use and evidence, then reruns the same ledger to
resume. depends_on consumes usable results; after waits for terminal results
only and marks the check diagnostic-only. A non-decisive or invalid result
remains recorded and charged, but is not reusable for attribution. Evidence
references describe verified artifacts; this tool compares receipt fields, not
binary tensors or native job state.
"""

import argparse
import json
from pathlib import Path
import re
import sys


_SHA256 = re.compile(r'[0-9a-f]{64}')
_IDENTITIES = ('side', 'workload_sha256', 'runtime_sha256', 'schedule_sha256', 'coordinates_sha256')
_PHASES = ('planned', 'running', 'finished', 'blocked')
_KINDS = ('offline', 'native', 'probe')


def _object(value, name):
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be an object')
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value


def _digest(value, name):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f'{name} must be a lowercase SHA-256')
    return value


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _evidence(values, name):
    if not isinstance(values, list) or not values:
        raise ValueError(f'{name} needs at least one evidence reference')
    return [_text(value, name) for value in values]


def _schema(document):
    _object(document, 'input')
    if type(document.get('schema_version')) is not int or document['schema_version'] != 1:
        raise ValueError('schema_version must be 1')


def probe_control(document):
    """Gate an attribution claim on an exact same-side tap-on/off pilot."""
    _schema(document)
    pair = []
    for name in ('untapped', 'tapped'):
        row = _object(document.get(name), name)
        _text(row.get('side'), f'{name}.side')
        _text(row.get('run_id'), f'{name}.run_id')
        for field in (*_IDENTITIES[1:], 'endpoint_sha256'):
            _digest(row.get(field), f'{name}.{field}')
        _text(row.get('evidence'), f'{name}.evidence')
        expected = _integer(row.get('expected'), f'{name}.expected', 1)
        observed = _integer(row.get('observed'), f'{name}.observed')
        if observed > expected:
            raise ValueError(f'{name}.observed cannot exceed expected')
        pair.append(row)
    base, tap = pair
    reasons = [f'{field}_mismatch' for field in _IDENTITIES if base[field] != tap[field]]
    if base['run_id'] == tap['run_id'] or base['evidence'] == tap['evidence']:
        reasons.append('not_independent_runs')
    if base['expected'] != tap['expected'] or any(row['observed'] != row['expected'] for row in pair):
        reasons.append('incomplete_coverage')
    if base['endpoint_sha256'] != tap['endpoint_sha256']:
        reasons.append('endpoint_changed')
    return dict(status='verified' if not reasons else 'unverified', usable_for_attribution=not reasons,
                reasons=reasons, evidence=[row['evidence'] for row in pair])


def _closure(document):
    data = document.get('bounded_delivery')
    if data is None:
        return None
    _object(data, 'bounded_delivery')
    return dict(established=_text(data.get('established'), 'bounded_delivery.established'),
                not_established=_text(data.get('not_established'), 'bounded_delivery.not_established'),
                reopen_when=_text(data.get('reopen_when'), 'bounded_delivery.reopen_when'),
                evidence=_evidence(data.get('evidence'), 'bounded_delivery.evidence'))


def _dependencies(row, key, earlier):
    value = row.get(key, [])
    if not isinstance(value, list) or any(not isinstance(dep, str) or dep not in earlier for dep in value):
        raise ValueError(f'{row["id"]}.{key} must list earlier check IDs')
    if len(set(value)) != len(value):
        raise ValueError(f'{row["id"]}.{key} must not repeat a check ID')
    return value


def _resolve_checks(dependencies, settled, prior):
    """Resolve usable evidence separately from terminal execution or planned work."""
    cache, visiting = {}, set()

    def waits(cid):
        requires, after = dependencies[cid]
        states = [(dep, result(dep)) for dep in requires]
        bad = [dep for dep, status in states if status == 'unusable']
        if bad:
            return dict(reason='unusable_dependency', dependencies=bad)
        if (any(status != 'usable' for _, status in states)
                or any(dep not in settled and result(dep) != 'usable' for dep in after)):
            return dict(reason='pending_dependency')
        return None

    def result(cid):
        if cid in cache:
            return cache[cid]
        if cid in visiting:
            return 'pending'  # an alias must not depend on the check whose result it would reuse
        visiting.add(cid)
        if cid in settled:
            row = settled[cid]
            waiting = waits(cid)
            if waiting is not None:
                row['decision'] = None
            row['diagnostic_only'] = bool(dependencies[cid][1])
            row['usable_result'] = row['decision'] is not None and not row['diagnostic_only']
            status = 'usable' if row['usable_result'] else 'unusable'
        elif cid in prior:
            source_status = result(prior[cid])
            waiting = waits(cid)
            if source_status == 'unusable' or (waiting and waiting['reason'] == 'unusable_dependency'):
                status = 'unusable'
            elif source_status == 'usable' and not waiting and not dependencies[cid][1]:
                status = 'usable'
            else:
                status = 'pending'
        else:
            status = 'pending'
        visiting.remove(cid)
        cache[cid] = status
        return status

    for cid in settled:
        result(cid)
    return result, waits


def next_action(document):
    """Reconcile resolved checks before routing a new check or bounded delivery."""
    _schema(document)
    trigger = _object(document.get('trigger'), 'trigger')
    trigger_kind = trigger.get('kind', 'acceptance')
    verdict = trigger.get('verdict')
    evidence = _text(trigger.get('evidence'), 'trigger.evidence')
    if trigger_kind == 'native_failure':
        reproduction = _text(trigger.get('reproduction'), 'trigger.reproduction')
        if 'verdict' in trigger or evidence.strip() == reproduction.strip():
            raise ValueError('native_failure needs separate reproduction evidence and no acceptance verdict')
    elif trigger_kind != 'acceptance' or verdict not in ('fail', 'noise_limited'):
        raise ValueError('trigger must be a native_failure or an acceptance fail/noise_limited')
    cap = _integer(document.get('accelerator_seconds_limit'), 'accelerator_seconds_limit')
    count_cap = _integer(document.get('check_limit'), 'check_limit')
    checks = document.get('checks')
    if not isinstance(checks, list):
        raise ValueError('checks must be a list')
    closure = _closure(document)
    dependencies, by_fingerprint = {}, {}
    queued, running, deferred, reusable = [], [], [], []
    settled = {}
    reserved, running_fingerprints = 0, set()
    spent = _integer(document.get('prior_accelerator_seconds', 0), 'prior_accelerator_seconds')
    finished = _integer(document.get('prior_checks', 0), 'prior_checks')
    for row in checks:
        _object(row, 'check')
        cid = _text(row.get('id'), 'check.id')
        if cid in dependencies:
            raise ValueError(f'duplicate check ID: {cid}')
        requires = _dependencies(row, 'depends_on', dependencies)
        after = _dependencies(row, 'after', dependencies)
        if set(requires) & set(after):
            raise ValueError(f'{cid}: a prerequisite cannot be both depends_on and after')
        dependencies[cid] = (requires, after)
        fingerprint = _digest(row.get('fingerprint'), f'{cid}.fingerprint')
        kind, phase = row.get('kind'), row.get('phase')
        if kind not in _KINDS or phase not in _PHASES:
            raise ValueError(f'{cid}: invalid check kind or phase')
        question = _text(row.get('question'), f'{cid}.question')
        on_match = _text(row.get('on_match'), f'{cid}.on_match')
        on_mismatch = _text(row.get('on_mismatch'), f'{cid}.on_mismatch')
        if on_match.strip() == on_mismatch.strip():
            raise ValueError(f'{cid}: both outcomes lead to the same decision')
        resource = row.get('resource', 'cpu' if kind == 'offline' else 'accelerator')
        if resource not in ('cpu', 'accelerator') or (kind == 'offline' and resource != 'cpu'):
            raise ValueError(f'{cid}: invalid resource for {kind} check')
        estimate = _integer(row.get('estimated_accelerator_seconds'), f'{cid}.estimated_accelerator_seconds',
                            minimum=int(resource == 'accelerator'))
        if resource == 'cpu' and estimate:
            raise ValueError(f'{cid}: CPU-only check cannot reserve accelerator time')
        signature = (kind, resource, question, on_match, on_mismatch, bool(after))
        meaning, versions = by_fingerprint.setdefault(fingerprint, (signature, []))
        if signature != meaning:
            raise ValueError(f'{cid}: changed check meaning requires a new fingerprint')
        versions.append((cid, row))
        if phase == 'planned':
            queued.append((cid, fingerprint, estimate))
        elif phase == 'running':
            reserved += estimate
            running_fingerprints.add(fingerprint)
            running.append(dict(id=cid, job_receipt=_text(row.get('job_receipt'), f'{cid}.job_receipt')))
        elif phase == 'blocked':
            blocker = row.get('blocker')
            if blocker not in ('access', 'resource_scope', 'missing_evidence'):
                raise ValueError(f'{cid}: use a concrete access, resource_scope or missing_evidence blocker')
            deferred.append(dict(id=cid, reason=blocker, detail=_text(row.get('detail'), f'{cid}.detail')))
        else:
            finished += 1
            actual = _integer(row.get('actual_accelerator_seconds'), f'{cid}.actual_accelerator_seconds')
            if resource == 'cpu' and actual:
                raise ValueError(f'{cid}: CPU-only check cannot charge accelerator time')
            spent += actual
            outcome = row.get('outcome')
            if outcome not in ('match', 'mismatch', 'inconclusive'):
                raise ValueError(f'{cid}: invalid outcome')
            refs = _evidence(row.get('evidence'), f'{cid}.evidence')
            gate = None
            if kind == 'probe':
                if 'probe_control' not in row and outcome == 'inconclusive':
                    gate = dict(status='unverified', usable_for_attribution=False,
                                reasons=['no_probe_receipts'], evidence=[])
                else:
                    gate = probe_control(row.get('probe_control'))
            decision = {'match': on_match, 'mismatch': on_mismatch}.get(outcome)
            if gate is not None and not gate['usable_for_attribution']:
                decision = None
            settled[cid] = dict(id=cid, outcome=outcome, decision=decision, evidence=refs, probe_control=gate)
    prior = {}
    for fingerprint, (_, versions) in by_fingerprint.items():
        launched = [(cid, row) for cid, row in versions if row['phase'] in ('finished', 'running')]
        if len(launched) > 1:
            raise ValueError(f'duplicate execution for {fingerprint}; put planned repeats in the same check')
        if launched and launched[0][0] in settled:
            prior.update({cid: launched[0][0] for cid, row in versions if row['phase'] == 'planned'})
    result, waits = _resolve_checks(dependencies, settled, prior)
    budget = dict(accelerator_seconds_limit=cap, accelerator_seconds_spent=spent,
                  accelerator_seconds_reserved=reserved, check_limit=count_cap, checks_finished=finished)
    output = dict(original_verdict=verdict, budget=budget, settled=list(settled.values()), reused=reusable, deferred=deferred)
    if trigger_kind == 'native_failure':
        output['trigger'] = dict(kind=trigger_kind, evidence=evidence, reproduction=reproduction)
    runnable = []
    for cid, fingerprint, estimate in queued:
        waiting = waits(cid)
        if waiting is not None:
            deferred.append(dict(id=cid, **waiting))
        elif cid in prior:
            if result(cid) == 'usable':
                reusable.append(dict(id=cid, source=prior[cid]))
            else:
                deferred.append(dict(id=cid, reason='unusable_prior_result', source=prior[cid]))
        elif fingerprint in running_fingerprints:
            deferred.append(dict(id=cid, reason='pending_dependency'))
        elif finished + len(running) < count_cap and (estimate == 0 or spent + reserved + estimate <= cap):
            runnable.append(cid)
        else:
            deferred.append(dict(id=cid, reason='budget'))
    if running:
        return dict(output, action='reconcile_running', jobs=running, independent_check=(runnable or [None])[0])
    if runnable:
        return dict(output, action='run_check', check_id=runnable[0])
    if closure is not None:
        for entry in settled.values():
            if entry['usable_result']:
                continue
            blocked_refs = set(entry['evidence'])
            gate = entry['probe_control']
            unverified_probe = gate is not None and not gate['usable_for_attribution']
            if unverified_probe:
                blocked_refs.update(gate['evidence'])
            if blocked_refs.intersection(closure['evidence']):
                cause = 'an unverified probe' if unverified_probe else 'an unusable result'
                raise ValueError(f'bounded_delivery.evidence cannot use {cause} to establish attribution')
        return dict(output, action='deliver_bounded', bounded_delivery=closure)
    return dict(output, action='summarize', reason='no executable check remains; record the evidence boundary or a concrete next check')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('round', 'probe'))
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding='utf-8'))
        report = (next_action if args.command == 'round' else probe_control)(document)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps(dict(status='invalid', reason=str(exc))))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
