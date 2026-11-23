#!/usr/bin/env python3
"""Judge which side deviates at one localized operator against an FP64 recomputation.
Call: verdict(spec); CLI: python scripts/operator_verdict.py --spec S.json --output NEW.json
Input: the operator's FP64 (or analytic) output recomputed on CPU from captured operands, each
side's output on the same operands, the dtype and reduction length, optional patched outputs.
Output: per side relative RMSE and max error vs FP64, near/far against a stated bound, the
acceptance-table reading, and for each patch whether it moves that side closer to FP64.
Requires: NumPy (NPY/NPZ arrays).

Details:
spec: {"operator":TEXT, "dtype":"bf16|fp16|fp32|fp64", "reduction":K, "reference":P,
  "A":P, "B":P, optional "patched":{"A":P,"B":P}, "mask":P, "bound":x, "c":1.0, "tolerance":0.05}
P is a .npy path or "file.npz:array"; relative paths resolve from the spec. Arrays are compared
in float64 on the masked elements (mask broadcasts on the leading axis).
error(side) = rmse(side - ref) / rms(ref); max_error = max|side - ref| / max|ref|.
bound = spec bound, else c * u(dtype) * sqrt(K) with unit roundoff u = 2^-8 (bf16),
2^-11 (fp16), 2^-24 (fp32), 2^-53 (fp64); the bound used is always reported.
near when error <= bound. Reading (acceptance.md#agreement-and-accuracy):
A near, B far -> b_deviates; A far, B near -> a_deviates; both far -> shared_policy;
both near -> not_the_source. A patch is closer when its error is below
(1 - tolerance) * the unpatched error, farther above (1 + tolerance) *, unchanged otherwise.
The verdict attributes this operator only, never the end-to-end difference.
CLI exit: 0=report written, 2=input error.
"""

__all__ = ['verdict']
import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

UNIT_ROUNDOFF = {'bf16': 2.0 ** -8, 'bfloat16': 2.0 ** -8, 'fp16': 2.0 ** -11, 'float16': 2.0 ** -11,
                 'fp32': 2.0 ** -24, 'float32': 2.0 ** -24, 'fp64': 2.0 ** -53, 'float64': 2.0 ** -53}
READING = {(True, False): 'b_deviates', (False, True): 'a_deviates', (False, False): 'shared_policy',
           (True, True): 'not_the_source'}


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
        value, ref = value[mask], reference[mask]
    else:
        ref = reference
    if not np.all(np.isfinite(value)):
        return dict(error=math.inf, max_error=math.inf, nonfinite=int(np.size(value) - np.isfinite(value).sum()))
    diff = value - ref
    scale = float(np.sqrt(np.mean(ref * ref))) or 1.0
    peak = float(np.max(np.abs(ref))) or 1.0
    return dict(error=float(np.sqrt(np.mean(diff * diff))) / scale, max_error=float(np.max(np.abs(diff))) / peak,
                nonfinite=0)


def verdict(spec, base=Path('.')):
    dtype = str(spec.get('dtype', '')).lower()
    if 'bound' in spec:
        bound, basis = float(spec['bound']), 'given'
    else:
        if dtype not in UNIT_ROUNDOFF or int(spec.get('reduction', 0)) < 1:
            raise ValueError('give bound, or dtype (bf16|fp16|fp32|fp64) and reduction >= 1')
        bound = float(spec.get('c', 1.0)) * UNIT_ROUNDOFF[dtype] * math.sqrt(int(spec['reduction']))
        basis = f"c*u({dtype})*sqrt({int(spec['reduction'])})"
    tolerance = float(spec.get('tolerance', 0.05))
    reference = np.asarray(_load(spec['reference'], base), dtype=np.float64)
    if not np.all(np.isfinite(reference)):
        raise ValueError('the FP64 reference has non-finite values')
    mask = None
    if spec.get('mask') is not None:
        mask = np.asarray(_load(spec['mask'], base), dtype=bool)
        mask = mask.reshape(mask.shape + (1,) * (reference.ndim - mask.ndim))
        mask = np.broadcast_to(mask, reference.shape)
    sides = {}
    for side in ('A', 'B'):
        row = _errors(_load(spec[side], base), reference, mask)
        row['near'] = row['error'] <= bound
        sides[side] = row
    result = dict(kind='operator-verdict', schema_version=1, operator=spec.get('operator'), dtype=dtype or None,
                  reduction=spec.get('reduction'), bound=bound, bound_basis=basis, sides=sides,
                  reading=READING[(sides['A']['near'], sides['B']['near'])],
                  scope='this operator on the captured operands; not an end-to-end attribution')
    patches = {}
    for side, ref in (spec.get('patched') or {}).items():
        if side not in sides:
            raise ValueError('patched keys must be A or B')
        row = _errors(_load(ref, base), reference, mask)
        before = sides[side]['error']
        if row['error'] < (1 - tolerance) * before:
            row['change'] = 'closer'
        elif row['error'] > (1 + tolerance) * before:
            row['change'] = 'farther'
        else:
            row['change'] = 'unchanged'
        row['near'] = row['error'] <= bound
        row['before'] = before
        patches[side] = row
    if patches:
        result['patched'] = patches
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text())
        result = verdict(spec, args.spec.resolve().parent)
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
