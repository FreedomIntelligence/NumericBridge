#!/usr/bin/env python3
"""Scaffold and validate the decoupled operator_optimization wire payload for evidence/submissions/*.json.
Call: scaffold(handoff, open_state=None, kernel_report=None, end_to_end=None, variants=()),
      check(document, *, entities=());
CLI: python scripts/operator_optimization.py scaffold --handoff HANDOFF.json [--open OPEN.json]
       [--kernel-report REPORT.json] [--end-to-end E2E.json] [--variants VARIANTS.json] --output NEW.json
     python scripts/operator_optimization.py check --payload PAYLOAD.json
Input: operator-optimize artifacts (handoff.json, open.json, kernel-report.json, end-to-end summary).
Output: the `operator_optimization` block for evidence/submissions/*.json and a structural check report.
Requires: Standard library.

Details:
Client/server decoupling:
- The client packages and validates only factual measurements, handoff context, open-round state,
  and explicit `not_measured` markers with reasons.
- Precision tiers and user-facing recommendations are NOT assigned here; the server computes them
  independently from the validated payload.
- `round_status`:
  - `"handoff_open"`: optimization entered or handed off, full round not yet finished; allows
    missing `kernel_report` or missing `random`/`edge` shapes or `timing` without full-round
    `variants` claims.
  - `"round_delivered"`: full round measurements delivered; requires `kernel_report` (`kind ==
    "kernel-check"`), `variants` matching the kept variants (including baseline), and explicit
    end-to-end measurements or `{status: "not_measured", reason: ...}`. When `entities` are
    supplied to `check()`, every `candidate_id` in `variants` must match a `candidate` entity.
CLI exit: 0=written or check ok, 1=check found problems, 2=input error.
"""

__all__ = ['scaffold', 'check']
import argparse
import json
import math
from pathlib import Path
import re
import sys

ROUND_STATUSES = ('handoff_open', 'round_delivered')
ENTRIES = ('required', 'optional')
READINGS = ('a_deviates', 'b_deviates', 'shared_policy', 'not_the_source')
REQUIRED_FIELDS = ('kind', 'schema_version', 'issue', 'operator', 'entry', 'reading', 'round_status')
OPTIONAL_FIELDS = (
    'shapes', 'reference_semantics', 'slow_fix', 'kernel_check',
    'end_to_end', 'variants', 'open_items', 'reproduce',
)
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def _require_text(value, field, problems):
    if not isinstance(value, str) or not value.strip():
        problems.append(f'{field} must be a non-empty string')


def _check_finite(value, field, problems):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        problems.append(f'{field} must be a finite number')


def _check_kernel_check(report, problems):
    if not isinstance(report, dict):
        problems.append('kernel_check must be an object')
        return
    if report.get('kind') != 'kernel-check' or report.get('schema_version') != 1:
        problems.append('kernel_check must have kind="kernel-check" and schema_version=1')
    for key in ('dtype', 'baseline'):
        _require_text(report.get(key), f'kernel_check.{key}', problems)
    _check_finite(report.get('bound'), 'kernel_check.bound', problems)
    variants = report.get('variants')
    if not isinstance(variants, dict) or not variants:
        problems.append('kernel_check.variants must be a non-empty object')
    else:
        baseline = report.get('baseline')
        if isinstance(baseline, str) and baseline and baseline not in variants:
            problems.append(f'kernel_check.variants missing baseline {baseline!r}')
        for name, row in variants.items():
            if not isinstance(row, dict) or not isinstance(row.get('cases'), dict) or not row['cases']:
                problems.append(f'kernel_check.variants.{name}.cases must be a non-empty object')
                continue
            if 'within_bound' not in row or not isinstance(row['within_bound'], bool):
                problems.append(f'kernel_check.variants.{name}.within_bound must be a boolean')
    perf = report.get('performance')
    if not isinstance(perf, dict) or perf.get('status') not in ('measured', 'not_measured'):
        problems.append('kernel_check.performance.status must be "measured" or "not_measured"')
    elif perf['status'] == 'not_measured':
        _require_text(perf.get('reason'), 'kernel_check.performance.reason', problems)
    else:
        if not isinstance(perf.get('variants'), dict) or not perf['variants']:
            problems.append('kernel_check.performance.variants must be a non-empty object when measured')


def _check_variant(item, index, round_status, candidate_ids, problems):
    prefix = f'variants[{index}]'
    if not isinstance(item, dict):
        problems.append(f'{prefix} must be an object')
        return
    _require_text(item.get('name'), f'{prefix}.name', problems)
    if 'candidate_id' in item and item['candidate_id'] is not None:
        cid = item['candidate_id']
        if not isinstance(cid, str) or not SLUG.fullmatch(cid):
            problems.append(f'{prefix}.candidate_id must be a valid slug')
        elif candidate_ids is not None and cid not in candidate_ids:
            problems.append(f'{prefix}.candidate_id {cid!r} missing from relations.entities')
    elif round_status == 'round_delivered':
        problems.append(f'{prefix}.candidate_id is required for round_delivered')
    if 'within_bound' in item and not isinstance(item['within_bound'], bool):
        problems.append(f'{prefix}.within_bound must be a boolean')
    if 'end_to_end' in item and item['end_to_end'] is not None:
        e2e = item['end_to_end']
        if not isinstance(e2e, dict):
            problems.append(f'{prefix}.end_to_end must be an object')
        elif e2e.get('status') == 'not_measured':
            _require_text(e2e.get('reason'), f'{prefix}.end_to_end.reason', problems)
        elif not e2e:
            problems.append(f'{prefix}.end_to_end must not be empty')


def scaffold(handoff, open_state=None, kernel_report=None, end_to_end=None, variants=()):
    """Build an operator_optimization wire document from local operator-optimize artifacts."""
    if not isinstance(handoff, dict):
        raise ValueError('handoff must be an object')
    for key in ('issue', 'operator', 'entry', 'reading'):
        if not handoff.get(key):
            raise ValueError(f'handoff missing {key}')
    round_status = (open_state or {}).get('round_status')
    if not round_status:
        round_status = 'round_delivered' if (kernel_report is not None and variants) else 'handoff_open'
    if round_status not in ROUND_STATUSES:
        raise ValueError('round_status must be handoff_open or round_delivered')
    payload = {
        'kind': 'operator-optimization',
        'schema_version': 1,
        'issue': handoff['issue'],
        'operator': handoff['operator'],
        'entry': (open_state or {}).get('entry') or handoff['entry'],
        'reading': handoff['reading'],
        'round_status': round_status,
    }
    if handoff.get('shapes') is not None:
        payload['shapes'] = handoff['shapes']
    if handoff.get('reference_semantics') is not None:
        payload['reference_semantics'] = handoff['reference_semantics']
    if 'slow_fix' in handoff:
        payload['slow_fix'] = handoff['slow_fix']
    if kernel_report is not None:
        payload['kernel_check'] = kernel_report
    e2e_value = end_to_end if end_to_end is not None else handoff.get('end_to_end')
    if e2e_value is not None:
        if e2e_value == 'not_measured':
            e2e_value = {'status': 'not_measured', 'reason': 'end-to-end run not measured'}
        payload['end_to_end'] = e2e_value
    if variants:
        payload['variants'] = list(variants)
    if open_state is not None:
        payload['open_items'] = {
            k: open_state[k] for k in ('remaining_distance', 'variants_not_yet_tried', 'next_step')
            if k in open_state
        }
    elif round_status == 'handoff_open':
        payload['open_items'] = {'variants_not_yet_tried': [], 'next_step': None}
    if handoff.get('reproduce') is not None:
        payload['reproduce'] = handoff['reproduce']
    return payload


def check(document, *, entities=()):
    """Validate an operator_optimization wire document without computing tiers or recommendations."""
    problems = []
    if not isinstance(document, dict):
        return {'status': 'problems', 'problems': ['operator_optimization must be an object']}
    unknown = set(document) - (set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS))
    if unknown:
        problems.append('unknown fields: ' + ', '.join(sorted(unknown)))
    for key in REQUIRED_FIELDS:
        if key not in document:
            problems.append('missing field: ' + key)
    if document.get('kind') != 'operator-optimization':
        problems.append('kind must be "operator-optimization"')
    if document.get('schema_version') != 1:
        problems.append('schema_version must be 1')
    if 'issue' in document:
        _require_text(document.get('issue'), 'issue', problems)
    operator = document.get('operator')
    if isinstance(operator, dict):
        _require_text(operator.get('name'), 'operator.name', problems)
    elif operator is not None:
        _require_text(operator, 'operator', problems)
    if document.get('entry') not in ENTRIES:
        problems.append('entry must be "required" or "optional"')
    if document.get('reading') not in READINGS:
        problems.append('reading must be one of ' + ', '.join(READINGS))
    round_status = document.get('round_status')
    if round_status not in ROUND_STATUSES:
        problems.append('round_status must be "handoff_open" or "round_delivered"')
    if 'tier' in document or 'recommendation' in document:
        problems.append('precision tiers and recommendations are assigned on the community side')

    if 'kernel_check' in document and document['kernel_check'] is not None:
        _check_kernel_check(document['kernel_check'], problems)
    elif round_status == 'round_delivered':
        problems.append('round_delivered requires kernel_check')

    if 'end_to_end' in document and document['end_to_end'] is not None:
        e2e = document['end_to_end']
        if isinstance(e2e, str):
            if e2e == 'not_measured':
                problems.append('end_to_end "not_measured" must be an object with status and reason')
            else:
                problems.append('end_to_end must be an object')
        elif not isinstance(e2e, dict):
            problems.append('end_to_end must be an object')
        elif e2e.get('status') == 'not_measured':
            _require_text(e2e.get('reason'), 'end_to_end.reason', problems)
        elif not e2e:
            problems.append('end_to_end must not be empty')
    elif round_status == 'round_delivered':
        problems.append('round_delivered requires end_to_end (or explicit not_measured with reason)')

    candidate_ids = None
    if entities:
        candidate_ids = {
            e.get('id') for e in entities
            if isinstance(e, dict) and e.get('kind') == 'candidate' and isinstance(e.get('id'), str)
        }

    variants = document.get('variants')
    if variants is not None:
        if not isinstance(variants, list):
            problems.append('variants must be a list')
        else:
            if round_status == 'round_delivered' and not variants:
                problems.append('round_delivered requires non-empty variants')
            for idx, item in enumerate(variants):
                _check_variant(item, idx, round_status, candidate_ids, problems)
    elif round_status == 'round_delivered':
        problems.append('round_delivered requires variants')

    if 'open_items' in document and document['open_items'] is not None:
        open_items = document['open_items']
        if not isinstance(open_items, dict):
            problems.append('open_items must be an object')
        else:
            if 'variants_not_yet_tried' in open_items and not isinstance(open_items['variants_not_yet_tried'], list):
                problems.append('open_items.variants_not_yet_tried must be a list')
    elif round_status == 'handoff_open':
        problems.append('handoff_open requires open_items')

    return {
        'status': 'ok' if not problems else 'problems',
        'problems': problems,
        'round_status': round_status,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('scaffold')
    make.add_argument('--handoff', required=True, type=Path)
    make.add_argument('--open', dest='open_state', type=Path)
    make.add_argument('--kernel-report', type=Path)
    make.add_argument('--end-to-end', type=Path)
    make.add_argument('--variants', type=Path)
    make.add_argument('--output', required=True, type=Path)
    review = commands.add_parser('check')
    review.add_argument('--payload', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            result = check(json.loads(args.payload.read_text(encoding='utf-8')))
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['status'] == 'ok' else 1
        handoff = json.loads(args.handoff.read_text(encoding='utf-8'))
        open_state = json.loads(args.open_state.read_text(encoding='utf-8')) if args.open_state else None
        kernel_report = json.loads(args.kernel_report.read_text(encoding='utf-8')) if args.kernel_report else None
        end_to_end = json.loads(args.end_to_end.read_text(encoding='utf-8')) if args.end_to_end else None
        variants = json.loads(args.variants.read_text(encoding='utf-8')) if args.variants else ()
        document = scaffold(handoff, open_state=open_state, kernel_report=kernel_report,
                            end_to_end=end_to_end, variants=variants)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        print(json.dumps({'status': 'written', 'output': str(args.output),
                          'round_status': document['round_status'],
                          'review': check(document)['status']}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'error', 'reason': str(exc)}))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
