#!/usr/bin/env python3
"""Rank likely causes of a failed comparison from its signature, before any new native run.
Call: triage(acceptance, reports=(), community=None); CLI: python scripts/triage.py [--acceptance A.json] [--report R.json ...] [--community S.json] --output NEW.json
Input: acceptance.py report (optional for a failed run), comparator reports (scores, distributions, tensors, nonfinite),
record_run/workflow run summaries carrying failure_class, and community_search output.
Output: ordered candidates {cause, evidence, check, cost, tool}; first candidate is the cheapest discriminating check.
Requires: Standard library; reads JSON only.

Details:
Signatures (evaluated in this order; cost cpu < replay < native):
  incomplete acceptance          -> recover capture before diagnosis
  noise_limited acceptance       -> nondeterminism: repeat each side, pin seeds/deterministic kernels;
                                    repeats that still differ when deterministic suggest a race or uninitialized memory
  run with failure_class index_out_of_bounds (record_run summary)
                                 -> index or capacity overflow: buffer size vs real index domain, int32 near 2^31
  exact check failed             -> semantic mismatch named by the check (inputs, masks, rewards, counts, events;
                                    setting_effect=false: a changed setting left outputs bit-identical)
  community matches              -> known issue: compare the recorded trigger/setting, apply its tested fix
  nonfinite report               -> overflow or dtype narrowing: find the first nonfinite boundary
  norm ratio fails, cosine passes -> scaling or reduction denominator; ratio near a small rational is reported
  objective fails, scores pass   -> loss reduction, mask or coefficient: FP64 recompute of the objective
  update fails, gradients pass   -> optimizer: recompute each side's update from its own gradient and state
  gradients fail, objective passes -> backward path: localize gradients with the incoming gradient fixed
  sparse error (P99/max >> MAE, decisive flips, error concentrated in few positions or tensors)
                                 -> masks, shifts, padding, special tokens or one module at the named coordinates
  error grows with position      -> accumulation, cache dtype, RoPE or chunking
  broad error, or any failure      -> localize boundaries (localize.py), then cross replay (replay.py);
                                    always last, so ruling out specific causes leaves a next step
Concentration: share of total JS (distributions) or squared error (tensors) held by
the top 1% of positions / top tensor. Position trend: Spearman rank correlation
between position and per-position JS. Community input is the JSON printed by
community_search; matches with issue or repair categories become candidates.
CLI exit: 0=candidates written, 2=input error.
"""

__all__ = ['triage']
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import sys

COST = {'cpu': 0, 'replay': 1, 'native': 2}
EXACT = {
    'token_ids': 'input token IDs differ: check tokenizer, template, BOS/EOS and truncation',
    'positions': 'position IDs differ: check packing, padding side and cache offsets',
    'masks': 'masks differ: check attention/loss/action masks and label shift',
    'rewards': 'rewards differ: check reward function inputs, parsing and stripping',
    'valid_counts': 'valid counts differ: check filtering, masking and group construction',
    'optimizer_events': 'optimizer step skipped or applied differently: check overflow, loss scaling and accumulation boundaries',
    'inventory': 'parameter or gradient inventory differs: check trainable flags, tied weights and name mapping',
    'coverage': 'required observations missing: recover capture before diagnosis',
    'setting_effect': 'a changed setting left outputs bit-identical: check that the argument reaches the kernel '
                      '(ignored, overwritten or defaulted parameter)',
}


def _load(path):
    with open(path) as stream:
        return json.load(stream)


def _value(v):
    return v.get('value') if isinstance(v, dict) else v


def _rational(ratio):
    """Return 'p/q' when ratio is within 1% of a fraction with small terms."""
    if not ratio or ratio <= 0:
        return None
    frac = Fraction(ratio).limit_denominator(32)
    if frac != 1 and frac.numerator <= 64 and abs(float(frac) - ratio) <= 0.01 * ratio:
        return f'{frac.numerator}/{frac.denominator}'
    return None


def _spearman(xs, ys):
    def ranks(values):
        order = sorted(range(len(values)), key=values.__getitem__)
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                result[order[k]] = (i + j) / 2
            i = j + 1
        return result
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _top_share(values, fraction=0.01):
    values = sorted((v for v in values if v is not None and v >= 0), reverse=True)
    total = math.fsum(values)
    if not values or not total:
        return None
    k = max(1, int(len(values) * fraction))
    return math.fsum(values[:k]) / total


def _add(out, cause, evidence, check, cost, tool=None, priority=0):
    out.append(dict(cause=cause, evidence=evidence, check=check, cost=cost, **({'tool': tool} if tool else {}),
                    _key=(priority, COST[cost], len(out))))


def triage(acceptance, reports=(), community=None):
    """acceptance: dict; reports: iterable of (label, dict); community: community_search output dict."""
    checks = {c['name']: c for c in acceptance.get('checks', [])}
    failing = set(acceptance.get('failing', []))
    verdict = acceptance.get('verdict')
    out = []
    if verdict == 'incomplete':
        _add(out, 'missing or unavailable comparison', acceptance.get('unavailable'),
             'recover the named captures and rerun the comparison', 'native', priority=-3)
    if verdict == 'noise_limited':
        noisy = [n for n in failing if checks.get(n, {}).get('result') == 'noise_limited']
        _add(out, 'run-to-run nondeterminism', dict(checks=noisy),
             'repeat each side with pinned seeds and deterministic kernels; if repeats still differ, capture boundaries '
             'on two same-side repeats: the first differing boundary points to a data race or uninitialized memory',
             'native', priority=-2)
    for row in acceptance.get('exact', []):
        if row['result'] == 'fail':
            _add(out, 'semantic mismatch: ' + row['name'], row,
                 EXACT.get(row['name'], 'locate the first differing exact quantity'), 'cpu', priority=-1)
    for match in (community or {}).get('matches', []):
        category = str(match.get('category', ''))
        if any(k in category for k in ('repair', 'issue', 'investigation', 'solution')):
            title = (match.get('content', {}).get('title', {}) or {}).get('en') or match.get('title')
            _add(out, 'known community issue: ' + str(title), dict(id=match.get('id'), scope=match.get('scope')),
                 "compare this workload with the case's trigger and settings; apply its tested setting or fix", 'cpu')
    def failed(*names):
        return [n for n in names if n in failing]
    def ok(*names):
        return all(n in checks and n not in failing for n in names)
    for label, report in reports:
        if report.get('failure_class') == 'index_out_of_bounds':
            _add(out, 'index or capacity overflow', dict(report=label, evidence=report.get('evidence')),
                 'compare the failing index with the size of the buffer it addresses; check the capacity formula '
                 'against the real index domain (e.g. global vs per-group slots) and int32 offsets or counts near 2^31',
                 'cpu', priority=-1)
    for label, report in reports:
        if report.get('kind') == 'nonfinite_tensors':
            _add(out, 'overflow or dtype narrowing (nonfinite values)', dict(report=label, samples=report.get('global', {}).get('samples')),
                 'capture boundary statistics and find the first boundary with NaN/Inf; check its dtype and scaling', 'replay',
                 'localize.py', priority=-1)
    ratio_fail = failed('gradient_norm_ratio', 'tensor_gradient_norm_ratio')
    if ratio_fail and not failed('gradient_cosine', 'tensor_gradient_cosine'):
        ratio = checks[ratio_fail[0]]['value']
        _add(out, 'scaling or reduction denominator', dict(norm_ratio=ratio, near=_rational(ratio), checks=ratio_fail),
             'recompute the objective and its denominators (tokens, microbatches, accumulation window, world size) in FP64 on CPU',
             'cpu')
    if failed('objective_rel') and not failed('target_logprob_mae', 'target_logprob_p99'):
        _add(out, 'loss reduction, mask or coefficient', dict(objective_rel=checks['objective_rel']['value']),
             'recompute each side\'s native objective from its own captured scores and masks in FP64', 'cpu')
    upd = failed('update_cosine_adaptive', 'update_cosine_sgd', 'optimizer_recompute_rel')
    if upd and not failed('gradient_cosine', 'gradient_norm_ratio'):
        _add(out, 'optimizer update', dict(checks=upd),
             'recompute each side\'s update from its own gradient, state and hyperparameters (eps, bias correction, weight decay, master copy)', 'cpu')
    if failed('gradient_cosine', 'tensor_gradient_cosine') and (ok('objective_rel') or 'objective_rel' not in checks):
        worst = checks.get('tensor_gradient_cosine', {}).get('detail')
        _add(out, 'backward path', dict(worst_tensor=worst),
             'localize gradient boundaries in backward order; replay with the same incoming gradient on both sides', 'replay',
             'localize.py')
    sparse, trend = [], None
    mae, p99 = checks.get('target_logprob_mae', {}).get('value'), checks.get('target_logprob_p99', {}).get('value')
    if mae and p99 and p99 > 10 * mae and failed('target_logprob_p99'):
        sparse.append(dict(p99_over_mae=p99 / mae))
    if 'decisive_top1_flips' in failing:
        sparse.append(dict(decisive_flips=checks['decisive_top1_flips'].get('detail')))
    for label, report in reports:
        if report.get('kind') == 'target-scores' and report.get('metrics'):
            if sparse:
                sparse.append(dict(report=label, max_coordinate=report['metrics'].get('max_coordinate')))
        elif report.get('kind') == 'distributions' and report.get('positions'):
            rows = [p for p in report['positions'] if isinstance(p.get('tail_merged_js'), dict)]
            js = [_value(p['tail_merged_js']) for p in rows]
            share = _top_share(js)
            if share is not None and share > 0.5 and len(js) >= 20:
                worst = max(rows, key=lambda p: _value(p['tail_merged_js']))
                sparse.append(dict(report=label, top1pct_js_share=share, worst=dict(sample=worst['sample'], position=worst['position'])))
            rho = _spearman([p['position'] for p in rows], js)
            if rho is not None and rho > 0.5 and len(js) >= 10:
                trend = dict(report=label, spearman_position_js=rho)
        elif 'per_tensor' in report and report.get('stage') in ('gradients', 'postclip-gradients', 'updates', 'observations'):
            errors = {n: _value(t.get('error_norm')) for n, t in report['per_tensor'].items()}
            errors = {n: e for n, e in errors.items() if e is not None}
            total = math.fsum(e * e for e in errors.values())
            if total and len(errors) >= 4:
                name, worst = max(errors.items(), key=lambda kv: kv[1])
                if worst * worst / total > 0.5:
                    sparse.append(dict(report=label, tensor=name, squared_error_share=worst * worst / total))
    if sparse:
        _add(out, 'localized error (mask, shift, padding, special token or one module)', sparse,
             'inspect the named coordinates/tensor: compare inputs, masks and positions there, then replay that module', 'cpu')
    if trend:
        _add(out, 'error grows with position (accumulation, cache dtype, RoPE, chunking)', trend,
             'compare short and long contexts; vary cache dtype, RoPE precision or chunk size one at a time', 'replay')
    numeric = failed('target_logprob_mae', 'target_logprob_p99', 'target_nll_rel', 'js_mean', 'js_p99', 'top1_agreement',
                     'gradient_cosine', 'tensor_gradient_cosine', 'update_cosine_adaptive', 'update_cosine_sgd')
    if numeric or verdict == 'fail':
        _add(out, 'broad numerical difference', dict(checks=numeric),
             'capture block-boundary statistics, rank boundaries with localize.py, then cross-replay the top candidate', 'native',
             'localize.py', priority=1)
    out.sort(key=lambda c: c.pop('_key'))
    return dict(kind='triage', schema_version=1, verdict=verdict, route=acceptance.get('route'),
                candidates=out, next=out[0] if out else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--acceptance')
    parser.add_argument('--report', action='append', default=[])
    parser.add_argument('--community')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        result = triage(_load(args.acceptance) if args.acceptance else {}, [(p, _load(p)) for p in args.report],
                        _load(args.community) if args.community else None)
        with Path(args.output).open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(dict(candidates=len(result['candidates']), next=result['next'] and result['next']['cause'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
