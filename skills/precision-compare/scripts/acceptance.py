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
  {"kind":"exact-checks","checks":{NAME:true|false}}  token IDs, masks, rewards,
    valid counts, inventories and other semantics that must match exactly.

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

Noise: --noise reports are same-side repeats of the same kind. A failing check
whose noise value also fails is noise_limited. Each check reports exceedance =
observed error / noise error (1-cosine for cosines) when noise is available.

User profile JSON: {"id":str, "limits":{CHECK:{"accept":X,"optimize":Y}}, optional
"min_top1_positions", "decisive_margin", "tensor_norm_floor"}. Limits are numbers
for max/min checks and [low, high] for ratio checks.
CLI: --report R (repeatable) [--array-report STAGE R ...] [--noise R ...] [--profile P.json] [--dtype bf16|fp16|fp32]
     [--optimizer adaptive|sgd] [--exit-zero] --output NEW.json
Exit: 0=passed (pass/gap), 1=fail/noise_limited/incomplete, 2=CLI/input error.
--exit-zero returns 0 whenever the report is written, so a workflow check on
/passed can route a failure to precision-repair.
"""

__all__ = ['evaluate']
import argparse
import copy
import json
import math
from pathlib import Path
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
            unavailable = report.get('reason', status)
        else:
            m = report['metrics']
            nll = max(abs(m['side_a_nll_nat_per_token']), abs(m['side_b_nll_nat_per_token']))
            values += [('target_logprob_mae', m['mae_nat'], None), ('target_logprob_p99', m['p99_nat'], None),
                       ('target_nll_rel', m['nll_abs_diff_nat_per_token'] / nll if nll else 0.0, None)]
    elif kind == 'distributions':
        agg = report.get('aggregate') or {}
        if status != 'no_thresholds' or 'tail_merged_js' not in agg:
            unavailable = agg.get('reason') or report.get('reason', status)
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
    elif kind is None and 'stage' in report and ('global' in report or 'exact_equal' in report):
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
        for name, ok in (report.get('checks') or {}).items():
            if type(ok) is not bool:
                raise ValueError(f'{label}: exact check {name} must be boolean')
            exact.append((name, ok, None))
    else:
        raise ValueError(f'{label}: unsupported report kind')
    return values, exact, unavailable


def evaluate(reports, profile=None, dtype='bf16', optimizer='adaptive', noise=()):
    """reports/noise: iterables of (label, report_dict). Returns the acceptance report."""
    if dtype not in ('bf16', 'fp16', 'fp32') or optimizer not in ('adaptive', 'sgd'):
        raise ValueError('dtype must be bf16|fp16|fp32 and optimizer adaptive|sgd')
    source = 'user' if profile else 'default'
    profile = {**DEFAULT_PROFILE, **(profile or {})}
    limits = _limits(profile, dtype)
    noise_values = {}
    for label, report in noise:
        for name, value, _ in _metrics(report, profile, optimizer, label)[0]:
            if name in KINDS and value is not None:
                noise_values[name] = max(noise_values.get(name, value), value, key=lambda v: _error(name, v))
    checks, exact_rows, gaps = [], [], []
    for label, report in reports:
        values, exact, unavailable = _metrics(report, profile, optimizer, label)
        if unavailable:
            gaps.append(dict(report=label, reason=unavailable))
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
            if name in noise_values:
                n = noise_values[name]
                row['noise'] = n
                err, nerr = _error(name, value), _error(name, n)
                row['exceedance'] = err / nerr if nerr else (None if err else 1.0)
                if row['result'] == 'fail' and not _within(KINDS[name], n, accept):
                    row['result'] = 'noise_limited'
            checks.append(row)
    results = {r['result'] for r in checks} | {r['result'] for r in exact_rows}
    if gaps:
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report', action='append', default=[])
    parser.add_argument('--array-report', action='append', nargs=2, default=[], metavar=('STAGE', 'PATH'))
    parser.add_argument('--noise', action='append', default=[])
    parser.add_argument('--profile')
    parser.add_argument('--dtype', default='bf16', choices=('bf16', 'fp16', 'fp32'))
    parser.add_argument('--optimizer', default='adaptive', choices=('adaptive', 'sgd'))
    parser.add_argument('--exit-zero', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if not args.report and not args.array_report:
            raise ValueError('supply --report or --array-report')
        reports = [(p, _load(p)) for p in args.report]
        reports += [(p, {**_load(p), 'stage': stage}) for stage, p in args.array_report]
        result = evaluate(reports, _load(args.profile) if args.profile else None,
                          args.dtype, args.optimizer, [(p, _load(p)) for p in args.noise])
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
