#!/usr/bin/env python3
"""Judge comparison reports against a versioned acceptance profile (standard library).
Call: evaluate(reports, profile=None, dtype='bf16', optimizer='adaptive', noise=()); CLI: python scripts/acceptance.py --report R.json [--report ...] --output NEW.json
Input: Comparator reports (target scores, distributions, gradients/updates), optional scalar/exact checks and same-side noise reports.
Output: verdict pass|gap|fail|noise_limited|incomplete, route, per-check values, limits and noise exceedance.
Requires: Standard library; default profile nb-default-v1; a user profile replaces selected limits and is labeled source=user.

Details:
Verdicts: pass = every applicable check within the optimize target; gap = within
acceptance but outside an optimize target (route optimize_optional); fail = an
acceptance or exact check failed (route diagnose); noise_limited = every failing
check also fails on a same-side repeat (route stabilize: fix nondeterminism or
compare against repeat noise); incomplete = a report is unavailable or not
covered (route recover_capture). passed is true for pass and gap.

Report kinds read: target-scores (compare_target_scores), distributions
(compare_distributions), tensor reports with stage gradients|postclip-gradients|
updates (compare_tensors, metrics mode), and compare_arrays/compare_trees reports
given with a stage (--array-report STAGE PATH; API: add "stage" to the dict).
Observation-stage tensor reports are diagnostic and not judged. Adapter-produced inputs:
  {"kind":"scalar-metrics","metrics":{CHECK:value},"objective_pairs":{NAME:[a,b]}}
    CHECK is a profile check (e.g. advantage_max_abs, optimizer_recompute_rel);
    each objective pair adds objective_rel = |a-b|/max(|a|,|b|).
  {"kind":"exact-checks","checks":{NAME:true|false},"missing":[NAME,...]}
    Compare required exact values and list required observations not captured.
    Formal plans pass this report with --exact-report (requires missing, even []).

Checks (BF16 accept / optimize): target_logprob_mae 0.1/0.02, target_logprob_p99
0.5/0.1, target_nll_rel 0.01/0.002, js_mean 1e-3/1e-4, js_p99 1e-2/1e-3,
top1_agreement >=0.95/0.99 (only with >=100 positions), decisive_top1_flips 0
(top-1 differs while both sides' top-1/top-2 probability margins >= 0.1),
objective_rel 0.01/0.001, gradient_cosine >=0.99/0.999, gradient_norm_ratio
[0.95,1.05]/[0.99,1.01], tensor_gradient_cosine >=0.95/0.99 and
tensor_gradient_norm_ratio [0.9,1.1]/[0.98,1.02] for tensors whose side_a norm is
>= 1% of the median, update_cosine_sgd >=0.99/0.999, update_cosine_adaptive
>=0.8/0.99 (Adam-family steps amplify near-zero gradient sign flips; pair with
optimizer_recompute_rel <=1e-3/1e-5), advantage_max_abs 1e-3/1e-5. dtype=fp32
selects tighter limits (FP32_LIMITS); fp16 uses BF16 limits. Calibrated on 419
cross-hardware community comparisons: 308 pass, 99 gap, 12 fail.

Noise: --noise-for PRIMARY REPEAT binds a same-side repeat to one --report or
--array-report path. Match kind, stage, metric and logical objective or tensor.
A failing check whose own repeat also fails is noise_limited. A/B inputs may
not be self-comparisons. --sides LEFT RIGHT checks observed capture side IDs.
Exceedance is observed error / repeat error (1-cosine for cosines).

User profile JSON: {"id":str, "limits":{CHECK:{"accept":X,"optimize":Y}}, optional
"min_top1_positions", "decisive_margin", "tensor_norm_floor"}. Limits are numbers
for max/min checks and [low, high] for ratio checks.
CLI: --report R (repeatable) [--array-report STAGE R ...] [--noise-for R REPEAT ...]
     [--sides LEFT RIGHT] [--profile P.json] [--dtype bf16|fp16|fp32]
     [--optimizer adaptive|sgd] [--tested-identity RUN-IDENTITY.json] [--exit-zero] --output NEW.json
Use --criterion-only with the planned --dtype and --optimizer to print the
formal criterion SHA-256 before observing results; it assumes --exact-report.
For repair closure, --tested-identity embeds the measured capture's workload_sha256,
base_sha256 and change_sha256 (null if unchanged). It does not judge provenance.
Exit: 0=passed (pass/gap), 1=fail/noise_limited/incomplete, 2=CLI/input error.
--exit-zero returns 0 whenever the report is written, so a workflow check on
/passed can route a failure to precision-repair.
"""

__all__ = ['evaluate', 'criterion_sha256']
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

# kind: max = value <= limit, min = value >= limit, range = low <= value <= high
KINDS = {
    'target_logprob_mae': 'max', 'target_logprob_p99': 'max', 'target_nll_rel': 'max',
    'js_mean': 'max', 'js_p99': 'max', 'top1_agreement': 'min', 'decisive_top1_flips': 'max',
    'objective_rel': 'max', 'gradient_cosine': 'min', 'gradient_norm_ratio': 'range',
    'tensor_gradient_cosine': 'min', 'tensor_gradient_norm_ratio': 'range',
    'update_cosine_sgd': 'min', 'update_cosine_adaptive': 'min',
    'optimizer_recompute_rel': 'max', 'advantage_max_abs': 'max',
}
BF16_LIMITS = {
    'target_logprob_mae': (0.1, 0.02), 'target_logprob_p99': (0.5, 0.1), 'target_nll_rel': (0.01, 0.002),
    'js_mean': (1e-3, 1e-4), 'js_p99': (1e-2, 1e-3), 'top1_agreement': (0.95, 0.99),
    'decisive_top1_flips': (0, 0), 'objective_rel': (0.01, 0.001),
    'gradient_cosine': (0.99, 0.999), 'gradient_norm_ratio': ([0.95, 1.05], [0.99, 1.01]),
    'tensor_gradient_cosine': (0.95, 0.99), 'tensor_gradient_norm_ratio': ([0.9, 1.1], [0.98, 1.02]),
    'update_cosine_sgd': (0.99, 0.999), 'update_cosine_adaptive': (0.8, 0.99),
    'optimizer_recompute_rel': (1e-3, 1e-5), 'advantage_max_abs': (1e-3, 1e-5),
}
FP32_LIMITS = {
    'target_logprob_mae': (1e-3, 1e-4), 'target_logprob_p99': (1e-2, 1e-3), 'target_nll_rel': (1e-4, 1e-5),
    'js_mean': (1e-6, 1e-7), 'js_p99': (1e-5, 1e-6), 'top1_agreement': (0.99, 0.999),
    'objective_rel': (1e-4, 1e-5), 'gradient_cosine': (0.9999, 0.99999),
    'gradient_norm_ratio': ([0.999, 1.001], [0.9999, 1.0001]),
    'tensor_gradient_cosine': (0.999, 0.9999), 'tensor_gradient_norm_ratio': ([0.99, 1.01], [0.999, 1.001]),
    'update_cosine_sgd': (0.9999, 0.99999), 'update_cosine_adaptive': (0.99, 0.999),
    'optimizer_recompute_rel': (1e-5, 1e-6), 'advantage_max_abs': (1e-5, 1e-6),
}
DEFAULT_PROFILE = dict(id='nb-default-v1', min_top1_positions=100, decisive_margin=0.1, tensor_norm_floor=0.01)
ROUTES = dict(pass_='deliver', gap='optimize_optional', fail='diagnose',
              noise_limited='stabilize', incomplete='recover_capture')


def _load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    with open(path) as stream:
        return json.load(stream, object_pairs_hook=pairs)


def _number(value):
    if isinstance(value, dict):
        value = value.get('value')
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _limits(profile, dtype):
    table = {k: tuple(copy.deepcopy(v)) for k, v in BF16_LIMITS.items()}
    if dtype == 'fp32':
        table.update({k: tuple(copy.deepcopy(v)) for k, v in FP32_LIMITS.items()})
    for name, limit in (profile.get('limits') or {}).items():
        if name not in KINDS or not isinstance(limit, dict) or set(limit) - {'accept', 'optimize'}:
            raise ValueError('invalid profile limit: ' + str(name))
        accept = limit.get('accept', table[name][0])
        table[name] = (accept, limit.get('optimize', accept))
    return table


def _within(kind, value, limit):
    if kind == 'max':
        return value <= limit
    if kind == 'min':
        return value >= limit
    return limit[0] <= value <= limit[1]


def _error(name, value):
    """Error magnitude used for noise exceedance (larger is worse)."""
    if KINDS[name] == 'min':
        return 1 - value
    if KINDS[name] == 'range':
        return abs(value - 1)
    return value


def _metrics(report, profile, optimizer, label):
    """Yield (check, value, note) and exact findings from one report."""
    kind, values, exact, unavailable = report.get('kind'), [], [], None
    status = report.get('status')
    if kind == 'target-scores':
        if status != 'no_thresholds':
            unavailable = report.get('reason') or status or 'missing score status'
        else:
            m = report['metrics']
            nll = max(abs(m['side_a_nll_nat_per_token']), abs(m['side_b_nll_nat_per_token']))
            values += [('target_logprob_mae', m['mae_nat'], None), ('target_logprob_p99', m['p99_nat'], None),
                       ('target_nll_rel', m['nll_abs_diff_nat_per_token'] / nll if nll else 0.0, None)]
    elif kind == 'distributions':
        agg = report.get('aggregate') or {}
        if status != 'no_thresholds' or 'tail_merged_js' not in agg:
            unavailable = agg.get('reason') or report.get('reason') or status or 'missing distribution status'
        else:
            js = agg['tail_merged_js']
            values += [('js_mean', js['mean'], None), ('js_p99', js['p99'], None)]
            margin = profile['decisive_margin']
            flips = [p for p in report.get('positions', []) if 'top1_mismatch' in p
                     and p['top1_mismatch']['side_a_margin'] >= margin and p['top1_mismatch']['side_b_margin'] >= margin]
            values.append(('decisive_top1_flips', len(flips),
                           [dict(sample=p['sample'], position=p['position']) for p in flips[:20]] or None))
            top1 = agg['top1_agreement']
            if top1['total'] >= profile['min_top1_positions']:
                values.append(('top1_agreement', top1['fraction'], None))
            else:
                values.append(('top1_agreement', None, f"{top1['count']}/{top1['total']} positions; fraction judged only with >= {profile['min_top1_positions']}"))
    elif kind == 'tensors' and status != 'no_thresholds':
        unavailable = report.get('reason') or (report.get('global') or {}).get('reason') or status or 'missing tensor status'
    elif kind == 'tensors' or (kind is None and 'stage' in report and 'exact_equal' in report):
        stage = report['stage']
        array_report = 'global' not in report
        g = (report.get('metrics') or {}) if array_report else report['global']
        missing = report.get('coverage', {}).get('missing', {}) if array_report else {}
        if stage == 'observations':
            values.append(('observations', None, 'diagnostic stage; not judged'))
        elif array_report and (status != 'completed' or not g or report.get('metrics_unavailable') or any(missing.values())):
            unavailable = report.get('metrics_unavailable') or report.get('reason') or f'array comparison {status}, missing {missing}'
        elif not array_report and status not in (None, 'no_thresholds') and g.get('status') == 'not_covered':
            unavailable = g.get('reason', status)
        else:
            cos, a, b = _number(g.get('cosine')), _number(g.get('side_a_norm')), _number(g.get('side_b_norm'))
            if stage in ('gradients', 'postclip-gradients'):
                values.append(('gradient_cosine', cos, None))
                values.append(('gradient_norm_ratio', b / a if a else None, None))
                norms = {n: _number(t.get('side_a_norm')) for n, t in report.get('per_tensor', {}).items()}
                norms = {n: v for n, v in norms.items() if v is not None}
                if norms:
                    floor = profile['tensor_norm_floor'] * statistics.median(norms.values())
                    worst_cos = worst_ratio = None
                    for name, norm in sorted(norms.items()):
                        if norm < floor or norm == 0:
                            continue
                        t = report['per_tensor'][name]
                        tc, tb = _number(t.get('cosine')), _number(t.get('side_b_norm'))
                        if tc is not None and (worst_cos is None or tc < worst_cos[1]):
                            worst_cos = (name, tc)
                        if tb is not None and (worst_ratio is None or abs(tb / norm - 1) > abs(worst_ratio[1] - 1)):
                            worst_ratio = (name, tb / norm)
                    for check, worst in (('tensor_gradient_cosine', worst_cos), ('tensor_gradient_norm_ratio', worst_ratio)):
                        if worst:
                            values.append((check, worst[1], dict(tensor=worst[0])))
            elif stage == 'updates':
                values.append(('update_cosine_adaptive' if optimizer == 'adaptive' else 'update_cosine_sgd', cos, None))
                events = report.get('events')
                if events and events.get('side_a') != events.get('side_b'):
                    exact.append(('optimizer_events', False, events))
    elif kind == 'scalar-metrics':
        for name, value in (report.get('metrics') or {}).items():
            if name not in KINDS:
                raise ValueError(f'{label}: unknown scalar check {name}')
            values.append((name, value, None))
        for name, pair in (report.get('objective_pairs') or {}).items():
            a, b = (float(x) for x in pair)
            scale = max(abs(a), abs(b))
            values.append(('objective_rel', abs(a - b) / scale if scale else 0.0, dict(objective=name)))
    elif kind == 'exact-checks':
        findings, missing = report.get('checks'), report.get('missing', [])
        if (not isinstance(findings, dict) or not isinstance(missing, list)
                or any(not isinstance(name, str) or not name.strip() for name in missing)):
            raise ValueError(f'{label}: exact checks require a checks object and a list of missing check names')
        for name, ok in findings.items():
            if type(ok) is not bool:
                raise ValueError(f'{label}: exact check {name} must be boolean')
            exact.append((name, ok, None))
        if missing:
            unavailable = 'missing required observations: ' + ', '.join(missing)
        elif not findings:
            unavailable = 'no exact checks reported'
    else:
        raise ValueError(f'{label}: unsupported report kind')
    return values, exact, unavailable


_NATIVE_PAIRS = ('target-scores', 'distributions', 'tensors')
_SIDE_KEYS = ('side_a', 'side_b')


def _pair(report):
    sides = report.get('sides')
    if isinstance(sides, dict):
        values = tuple(sides.get(key) for key in _SIDE_KEYS)
        if all(isinstance(side, str) and side.strip() for side in values):
            return values
    return None


def _pair_problem(report, expected_sides=None):
    pair = _pair(report)
    sources = report.get('sources')
    same_source = (isinstance(sources, dict) and sources.get('side_a') is not None
                   and sources.get('side_a') == sources.get('side_b'))
    if report.get('self_comparison') is True or same_source or (pair and pair[0] == pair[1]):
        return 'self-comparison is not independent paired evidence'
    if 'sides' in report and pair is None:
        return 'invalid side IDs in comparison report'
    if expected_sides and pair is not None and pair != expected_sides:
        return 'observed side IDs do not match the requested A/B pair'
    if expected_sides and pair is None and report.get('kind') in _NATIVE_PAIRS and report.get('status') == 'no_thresholds':
        return 'comparison report has no observed side IDs'
    return None


def _repeat_problem(primary, repeat):
    if (primary.get('kind'), primary.get('stage')) != (repeat.get('kind'), repeat.get('stage')):
        return 'repeat and primary comparison have different kinds or stages'
    if reason := _pair_problem(repeat):
        return reason
    left, right = _pair(primary), _pair(repeat)
    if left is None and right is None:
        return None  # Adapter-supplied metrics use the caller's explicit pairing.
    if left is None or right is None or len(set(left) & set(right)) != 1:
        return 'repeat must share one observed side with the primary comparison'
    shared = next(iter(set(left) & set(right)))
    anchor, repeat_anchor = left.index(shared), right.index(shared)
    primary_sources, repeat_sources = primary.get('sources'), repeat.get('sources')
    if isinstance(primary_sources, dict) and isinstance(repeat_sources, dict):
        contract, repeat_contract = primary_sources.get('contract'), repeat_sources.get('contract')
        if contract is not None and repeat_contract is not None and contract != repeat_contract:
            return 'repeat uses a different comparison contract'
        begin = primary_sources.get(_SIDE_KEYS[anchor])
        repeated_from = repeat_sources.get(_SIDE_KEYS[repeat_anchor])
        if begin is not None and repeated_from is not None and begin != repeated_from:
            return 'repeat does not use the matching primary capture'
    conditions = repeat.get('conditions')
    if isinstance(conditions, dict):
        same_side = [conditions.get(key) for key in _SIDE_KEYS]
        if all(isinstance(row, dict) for row in same_side) and same_side[0] != same_side[1]:
            return 'repeat changes the measured side conditions or rank coverage'
    return None


def _noise_key(label, name, detail):
    coordinates = tuple((key, detail[key]) for key in ('objective', 'tensor') if isinstance(detail, dict) and key in detail)
    return label, name, coordinates


def evaluate(reports, profile=None, dtype='bf16', optimizer='adaptive', noise=(), *,
             require_numeric=False, expected_sides=None):
    """reports: (label, document); noise: (primary_label, repeat_label, document)."""
    if dtype not in ('bf16', 'fp16', 'fp32') or optimizer not in ('adaptive', 'sgd'):
        raise ValueError('dtype must be bf16|fp16|fp32 and optimizer adaptive|sgd')
    if expected_sides is not None:
        expected_sides = tuple(expected_sides)
        if len(expected_sides) != 2 or any(not isinstance(side, str) or not side.strip() for side in expected_sides) or expected_sides[0] == expected_sides[1]:
            raise ValueError('expected_sides needs two distinct side IDs')
    source = 'user' if profile else 'default'
    profile = {**DEFAULT_PROFILE, **(profile or {})}
    limits = _limits(profile, dtype)
    reports = list(reports)
    by_label = dict(reports)
    if len(reports) != len(by_label):
        raise ValueError('comparison report labels must be unique')
    noise_values, checks, exact_rows, gaps = {}, [], [], []
    for target, label, report in noise:
        if target not in by_label or label in by_label:
            raise ValueError('each repeat needs a primary report and a separate repeat report')
        if reason := _repeat_problem(by_label[target], report):
            gaps.append(dict(report=label, reason=reason))
            continue
        values, _, unavailable = _metrics(report, profile, optimizer, label)
        comparable = [(name, value, note) for name, value, note in values if name in KINDS and value is not None]
        if require_numeric and (unavailable or not comparable):
            gaps.append(dict(report=label, reason=unavailable or 'no comparable repeat metrics'))
        for name, value, note in comparable:
            key = _noise_key(target, name, note)
            noise_values[key] = max(noise_values.get(key, value), value, key=lambda v: _error(name, v))
    invalid_pair = False
    for label, report in reports:
        if reason := _pair_problem(report, expected_sides):
            invalid_pair = True
            gaps.append(dict(report=label, reason=reason))
            continue
        values, exact, unavailable = _metrics(report, profile, optimizer, label)
        if unavailable:
            gaps.append(dict(report=label, reason=unavailable))
        elif require_numeric and report.get('kind') != 'exact-checks' and not any(
                name in KINDS and value is not None for name, value, _ in values):
            gaps.append(dict(report=label, reason='no comparable numerical metrics'))
        for name, ok, detail in exact:
            exact_rows.append(dict(report=label, name=name, result='pass' if ok else 'fail', **({'detail': detail} if detail else {})))
        for name, value, note in values:
            row = dict(report=label, name=name, value=value)
            if note is not None:
                row['detail'] = note
            if name not in KINDS or value is None:
                row['result'] = 'not_applicable'
                checks.append(row)
                continue
            accept, optimize = limits[name]
            row.update(kind=KINDS[name], accept=accept, optimize=optimize)
            row['result'] = 'pass' if _within(KINDS[name], value, optimize) else 'gap' if _within(KINDS[name], value, accept) else 'fail'
            if (key := _noise_key(label, name, note)) in noise_values:
                n = noise_values[key]
                row['noise'] = n
                err, nerr = _error(name, value), _error(name, n)
                row['exceedance'] = err / nerr if nerr else (None if err else 1.0)
                if row['result'] == 'fail' and not _within(KINDS[name], n, accept):
                    row['result'] = 'noise_limited'
            checks.append(row)
    if require_numeric and not any(row['result'] != 'not_applicable' for row in checks):
        gaps.append(dict(reason='no comparable numerical evidence'))
    results = {r['result'] for r in checks} | {r['result'] for r in exact_rows}
    if invalid_pair:
        verdict = 'incomplete'
    elif any(row['result'] == 'fail' for row in exact_rows):
        verdict = 'fail'
    elif gaps:
        verdict = 'incomplete'
    elif 'fail' in results:
        verdict = 'fail'
    elif 'noise_limited' in results:
        verdict = 'noise_limited'
    elif 'gap' in results:
        verdict = 'gap'
    elif results - {'not_applicable'}:
        verdict = 'pass'
    else:
        verdict = 'incomplete'
        gaps.append(dict(reason='no judgeable checks'))
    return dict(kind='acceptance', schema_version=1, profile=profile['id'], source=source, dtype=dtype,
                optimizer=optimizer, verdict=verdict, passed=verdict in ('pass', 'gap'),
                route=ROUTES['pass_' if verdict == 'pass' else verdict],
                failing=[r['name'] for r in checks + exact_rows if r['result'] in ('fail', 'noise_limited')],
                checks=checks, exact=exact_rows, unavailable=gaps)


def criterion_sha256(profile=None, dtype='bf16', optimizer='adaptive', *, require_numeric=True):
    """Hash the chosen acceptance criterion before the first observation."""
    settings = {**DEFAULT_PROFILE, **(profile or {})}
    criterion = dict(profile=settings, limits=_limits(settings, dtype), source='user' if profile else 'default',
                     dtype=dtype, optimizer=optimizer, require_numeric=require_numeric)
    return hashlib.sha256(json.dumps(criterion, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def _tested_identity(value):
    fields = ('workload_sha256', 'base_sha256', 'change_sha256')
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('tested identity requires workload_sha256, base_sha256 and change_sha256')
    for key in fields:
        digest = value[key]
        if key == 'change_sha256' and digest is None:
            continue
        if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise ValueError(f'invalid tested identity {key}')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report', action='append', default=[])
    parser.add_argument('--array-report', action='append', nargs=2, default=[], metavar=('STAGE', 'PATH'))
    parser.add_argument('--exact-report', help='required exact checks and missing coverage for a formal plan')
    parser.add_argument('--noise-for', action='append', nargs=2, default=[], metavar=('PRIMARY', 'REPEAT'))
    parser.add_argument('--sides', nargs=2, metavar=('LEFT', 'RIGHT'))
    parser.add_argument('--profile')
    parser.add_argument('--dtype', default='bf16', choices=('bf16', 'fp16', 'fp32'))
    parser.add_argument('--optimizer', default='adaptive', choices=('adaptive', 'sgd'))
    parser.add_argument('--tested-identity', type=Path, help='capture identity JSON to embed in the report')
    parser.add_argument('--exit-zero', action='store_true')
    parser.add_argument('--criterion-only', action='store_true', help='print the formal criterion digest without judging results')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    try:
        profile = _load(args.profile) if args.profile else None
        if args.criterion_only:
            if any((args.report, args.array_report, args.exact_report, args.noise_for, args.tested_identity, args.output)):
                raise ValueError('--criterion-only does not take reports or an output path')
            print(json.dumps(dict(criterion_sha256=criterion_sha256(profile, args.dtype, args.optimizer))))
            return 0
        if not args.output:
            raise ValueError('supply --output when judging reports')
        if not args.report and not args.array_report:
            raise ValueError('supply --report or --array-report')
        reports = [(str(Path(p).resolve()), _load(p)) for p in args.report]
        reports += [(str(Path(p).resolve()), {**_load(p), 'stage': stage}) for stage, p in args.array_report]
        stages = {str(Path(p).resolve()): stage for stage, p in args.array_report}
        repeats = []
        for target, path in args.noise_for:
            label = str(Path(target).resolve())
            repeat = _load(path)
            if label in stages and 'stage' not in repeat:
                repeat = {**repeat, 'stage': stages[label]}
            repeats.append((label, str(Path(path).resolve()), repeat))
        if args.exact_report:
            exact = _load(args.exact_report)
            if not isinstance(exact, dict) or exact.get('kind') != 'exact-checks' or 'missing' not in exact:
                raise ValueError('--exact-report needs an exact-checks object with explicit missing coverage')
            reports.append((str(Path(args.exact_report).resolve()), exact))
        result = evaluate(reports, profile, args.dtype, args.optimizer, repeats,
                          require_numeric=bool(args.exact_report), expected_sides=args.sides)
        if args.tested_identity:
            result['tested_identity'] = _tested_identity(_load(args.tested_identity))
            result['criterion_sha256'] = criterion_sha256(profile, args.dtype, args.optimizer,
                                                          require_numeric=bool(args.exact_report))
        with Path(args.output).open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(dict(verdict=result['verdict'], route=result['route'], failing=result['failing'])))
    return 0 if result['passed'] or args.exit_zero else 1


if __name__ == '__main__':
    sys.exit(main())
