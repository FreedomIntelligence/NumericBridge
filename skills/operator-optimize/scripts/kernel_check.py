#!/usr/bin/env python3
"""Measure operator variants (baseline, slow fix, kernels) against an FP64 reference and each other.
Call: check(spec); CLI: python scripts/kernel_check.py --spec S.json --output NEW.json
Input: per test case, the FP64 (or analytic) reference recomputed on CPU and each variant's output
on the same operands (one array, or a list of repeated runs); optional paired timing samples.
Output: per variant and case the relative RMSE and max error vs FP64, the same errors for the
reference correctly rounded to the dtype (the floor a kernel in that dtype can reach), repeat
reproducibility, comparison with the baseline and slow fix, and timing statistics.
Requires: NumPy (NPY/NPZ arrays).

Details:
spec: {"operator":TEXT, "dtype":"bf16|fp16|fp32|fp64", "reduction":K, optional "bound":x, "c":1.0,
  "tolerance":0.05, "baseline":"baseline", "slow_fix":"slow_fix", "reference_semantics":TEXT,
  "cases":[{"id":TEXT, "shape_class":"captured|random|edge", "reference":P, optional "mask":P,
            "variants":{NAME: P | [P, P, ...]}}],
  optional "timing":{"unit":"ms", "conditions":{...}, "variants":{NAME:[samples]}}}
P is a .npy path or "file.npz:array"; relative paths resolve from the spec. A list of paths is
repeated runs on identical inputs; the first is scored, all are compared bitwise.
error = rmse(variant - ref) / rms(ref); max_error = max|variant - ref| / max|ref|.
bound = spec bound, else c * u(dtype) * sqrt(K) (u = 2^-8 bf16, 2^-11 fp16, 2^-24 fp32, 2^-53 fp64).
rounded_reference: ref rounded to the dtype (bf16 via fp32, round-to-nearest-even) and scored the same.
Checks per variant: within_bound (all cases finite and error <= bound), reproducible (true, false,
or not_measured without repeats), vs_slow_fix (no case error above (1 + tolerance) * slow fix error),
vs_baseline per case (closer | farther | unchanged at the same tolerance).
Timing: median, min, max, interquartile range and sample count per variant, and median ratio to the
baseline. Timing samples must come from one host and session (paired); the tool reports raw values
and ratios and judges no performance limit. Missing measurements are reported as not_measured.
CLI exit: 0=report written, 2=input error.
"""

__all__ = ['check', 'round_to_dtype']
import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

UNIT_ROUNDOFF = {'bf16': 2.0 ** -8, 'fp16': 2.0 ** -11, 'fp32': 2.0 ** -24, 'fp64': 2.0 ** -53}
ALIASES = {'bfloat16': 'bf16', 'float16': 'fp16', 'half': 'fp16', 'float32': 'fp32', 'float64': 'fp64'}


def _dtype(name):
    name = ALIASES.get(str(name).lower(), str(name).lower())
    if name not in UNIT_ROUNDOFF:
        raise ValueError('dtype must be bf16, fp16, fp32 or fp64')
    return name


def round_to_dtype(values, dtype):
    """Round float64 values to the dtype and return them as float64."""
    values = np.asarray(values, dtype=np.float64)
    dtype = _dtype(dtype)
    if dtype == 'fp64':
        return values.copy()
    if dtype == 'fp32':
        return values.astype(np.float32).astype(np.float64)
    if dtype == 'fp16':
        return values.astype(np.float16).astype(np.float64)
    bits = values.astype(np.float32).view(np.uint32)
    bits = (bits + (((bits >> 16) & 1) + np.uint32(0x7FFF))) & np.uint32(0xFFFF0000)
    return bits.view(np.float32).astype(np.float64)


def _load(ref, base):
    path, _, key = str(ref).partition(':')
    path = Path(path)
    if not path.is_absolute():
        path = base / path
    data = np.load(path, allow_pickle=False)
    if key:
        return np.asarray(data[key])
    if hasattr(data, 'files'):
        raise ValueError(f'{ref}: name the array as file.npz:array')
    return np.asarray(data)


def _errors(value, reference, mask):
    value = np.asarray(value, dtype=np.float64)
    if value.shape != reference.shape:
        raise ValueError(f'shape {value.shape} differs from reference {reference.shape}')
    if mask is not None:
        value, reference = value[mask], reference[mask]
    if not np.all(np.isfinite(value)):
        return dict(error=math.inf, max_error=math.inf, nonfinite=int(value.size - np.isfinite(value).sum()))
    diff = value - reference
    scale = float(np.sqrt(np.mean(reference * reference))) or 1.0
    peak = float(np.max(np.abs(reference))) or 1.0
    return dict(error=float(np.sqrt(np.mean(diff * diff))) / scale,
                max_error=float(np.max(np.abs(diff))) / peak, nonfinite=0)


def _change(after, before, tolerance):
    if after < (1 - tolerance) * before:
        return 'closer'
    if after > (1 + tolerance) * before:
        return 'farther'
    return 'unchanged'


def _timing(samples):
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError('timing samples must be a non-empty list of positive numbers')
    q1, median, q3 = (float(v) for v in np.percentile(values, [25, 50, 75]))
    return dict(samples=int(values.size), median=median, min=float(values.min()), max=float(values.max()),
                iqr=q3 - q1)


def check(spec, base=Path('.')):
    dtype = _dtype(spec.get('dtype', ''))
    if 'bound' in spec:
        bound, basis = float(spec['bound']), 'given'
    else:
        if int(spec.get('reduction', 0)) < 1:
            raise ValueError('give bound, or reduction >= 1')
        bound = float(spec.get('c', 1.0)) * UNIT_ROUNDOFF[dtype] * math.sqrt(int(spec['reduction']))
        basis = f"c*u({dtype})*sqrt({int(spec['reduction'])})"
    tolerance = float(spec.get('tolerance', 0.05))
    baseline, slow_fix = spec.get('baseline', 'baseline'), spec.get('slow_fix', 'slow_fix')
    cases = spec.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a non-empty list')
    names = list(dict.fromkeys(name for case in cases for name in case.get('variants', {})))
    if baseline not in names:
        raise ValueError(f'every spec needs the baseline variant {baseline!r}')
    rows, floors = {name: {} for name in names}, {}
    for case in cases:
        cid = case['id']
        reference = np.asarray(_load(case['reference'], base), dtype=np.float64)
        if not np.all(np.isfinite(reference)):
            raise ValueError(f'{cid}: the FP64 reference has non-finite values')
        mask = None
        if case.get('mask') is not None:
            mask = np.asarray(_load(case['mask'], base), dtype=bool)
            mask = np.broadcast_to(mask.reshape(mask.shape + (1,) * (reference.ndim - mask.ndim)), reference.shape)
        floor = _errors(round_to_dtype(reference, dtype), reference, mask)
        floors[cid] = dict(floor, shape=list(reference.shape), shape_class=case.get('shape_class'))
        for name, refs in case['variants'].items():
            runs = refs if isinstance(refs, list) else [refs]
            if not runs:
                raise ValueError(f'{cid}/{name}: no output')
            first = _load(runs[0], base)
            row = _errors(first, reference, mask)
            row['within_bound'] = row['error'] <= bound
            row['to_floor'] = row['error'] / floor['error'] if floor['error'] > 0 else None
            if len(runs) > 1:
                row['reproducible'] = all(np.array_equal(first, _load(r, base), equal_nan=True) for r in runs[1:])
                row['repeats'] = len(runs)
            else:
                row['reproducible'] = 'not_measured'
            rows[name][cid] = row
    variants = {}
    for name in names:
        per_case = rows[name]
        result = dict(cases=per_case, missing_cases=[c['id'] for c in cases if c['id'] not in per_case])
        result['within_bound'] = not result['missing_cases'] and all(r['within_bound'] for r in per_case.values())
        flags = [r['reproducible'] for r in per_case.values()]
        result['reproducible'] = ('not_measured' if all(f == 'not_measured' for f in flags)
                                  else all(f is True for f in flags if f != 'not_measured'))
        if name != baseline:
            result['vs_baseline'] = {cid: _change(r['error'], rows[baseline][cid]['error'], tolerance)
                                     for cid, r in per_case.items() if cid in rows[baseline]}
        if slow_fix in rows and name != slow_fix:
            shared = [cid for cid in per_case if cid in rows[slow_fix]]
            result['vs_slow_fix'] = ('not_measured' if not shared else all(
                per_case[cid]['error'] <= (1 + tolerance) * rows[slow_fix][cid]['error'] for cid in shared))
        variants[name] = result
    timing = spec.get('timing')
    performance = dict(status='not_measured', reason='no timing samples in the spec')
    if timing:
        measured = {name: _timing(samples) for name, samples in (timing.get('variants') or {}).items()}
        performance = dict(status='measured', unit=timing.get('unit', 'ms'), conditions=timing.get('conditions'),
                           pairing='samples given in one spec are treated as one host and session',
                           variants=measured, not_measured=[n for n in names if n not in measured])
        if baseline in measured:
            for name, row in measured.items():
                row['median_ratio_to_baseline'] = row['median'] / measured[baseline]['median']
    return dict(kind='kernel-check', schema_version=1, operator=spec.get('operator'), dtype=dtype,
                reduction=spec.get('reduction'), reference_semantics=spec.get('reference_semantics'),
                bound=bound, bound_basis=basis, tolerance=tolerance, baseline=baseline,
                slow_fix=slow_fix if slow_fix in rows else None, rounded_reference=floors,
                variants=variants, performance=performance,
                scope='these operands and shapes on the measured host; end-to-end effects are measured separately')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text())
        result = check(spec, args.spec.resolve().parent)
        text = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=True) + '\n'
        if args.output:
            with args.output.open('x') as stream:
                stream.write(text)
        print(text, end='')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
