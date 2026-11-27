#!/usr/bin/env python3
"""Classify a boundary by cross replay, input perturbation and intervention results.
Call: cross_replay(spec), perturbation(spec), interventions(spec); CLI: python scripts/replay.py cross|perturb|interventions --spec S.json --output NEW.json
Input: Output arrays of both implementations on both captured inputs (+ self-replay, optional FP64 reference), alpha sweeps, or endpoint metrics per intervention run.
Output: Directional metrics, a replay label with the next check, amplification gains, or measured intervention effects.
Requires: NumPy for cross/perturb (NPY/NPZ arrays); interventions uses the standard library.

Details:
Notation: implementations T (side a) and I (side b); captured inputs xT, xI.
Distance d(u, v) = 1 - mean per-token cosine (token axis 0, other axes flattened;
optional boolean mask [tokens]); a 1-D array is one token.

cross spec: {"outputs":{"T(xT)":P,"T(xI)":P,"I(xT)":P,"I(xI)":P},
  "inputs":{"xT":P,"xI":P}, "self":{"T":[replay_P,captured_P],"I":[...]},
  optional "reference":{"xT":P,"xI":P} (FP64/analytic outputs), "mask":P, "k":3}
P is a .npy path or "file.npz:array"; relative paths resolve from the spec.
Metrics: R_T, R_I = self-replay distance; S_T = d(T(xT),I(xT)), S_I = d(T(xI),I(xI));
P_T = d(T(xT),T(xI)), P_I = d(I(xT),I(xI)); C_in = d(xT,xI); C_natural = d(T(xT),I(xI));
gain_T, gain_I = P / C_in. Noise floor n = max(R_T, R_I).
Labels, in order: replay_invalid if max(R) > 0.5*C_natural and C_natural > 0
(self-replay error comparable to the effect); not_reproduced if C_natural <= k*n;
local_source_candidate if max(S) > k*n and max(S) >= 0.5*C_natural;
propagation_candidate otherwise. amplification_supported is added when a gain > 2.
With a reference, deviation_T/deviation_I = d(output, reference) name the side
farther from the reference on each input.

perturb spec: {"alphas":[0,0.25,0.5,1], "T":[P per alpha], "I":[P per alpha],
  optional "mask", "k"}; outputs at x_alpha = xT + alpha*(xI - xT). Reports
S_alpha = d(T(x_a), I(x_a)) and P_alpha = d(T(x_a), T(x_0)). Flat S_alpha (max/min
<= 2) means an input-independent implementation difference; rising S_alpha means
the input direction triggers it; the P_alpha slope gives the amplification gain.

interventions spec: {"metric":str, "lower_is_better":true, "baseline":x, "target":x,
  "noise":sigma, "runs":{"A":x, "revert:A":x, "extra:A":x, "B":x, "A+B":x}}
Effect is signed: positive means better, negative means worse. Direction uses
3*noise as a descriptive band, not a significance test. `revert:F` checks return
to baseline; `extra:F` is recorded only (another sample needs its own baseline).
For A+B, `interaction` is the combined effect minus the sum of single effects.
Missing singles leave `interaction` unknown, not zero. The optional target is
context only. Report v2 does not assign causality, local correctness or acceptance.
CLI exit: 0=report written, 2=input error.
"""

__all__ = ['cross_replay', 'perturbation', 'interventions']
import argparse
import json
import math
from pathlib import Path
import sys


def _array(ref, base):
    import numpy as np
    path, name = str(ref), ''
    if '.npz:' in path:
        path, name = path.rsplit(':', 1)
    path = Path(path) if Path(path).is_absolute() else Path(base) / path
    if path.suffix == '.npz':
        with np.load(path) as data:
            return np.asarray(data[name] if name else data[data.files[0]])
    return np.load(path)


def distance(u, v, mask=None):
    """1 - mean per-token cosine over tokens with nonzero norm on both sides."""
    import numpy as np
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    if u.shape != v.shape:
        raise ValueError(f'shape mismatch {u.shape} vs {v.shape}')
    u = u.reshape(1, -1) if u.ndim == 1 else u.reshape(u.shape[0], -1)
    v = v.reshape(1, -1) if v.ndim == 1 else v.reshape(v.shape[0], -1)
    if mask is not None:
        u, v = u[np.asarray(mask, dtype=bool)], v[np.asarray(mask, dtype=bool)]
    nu, nv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
    ok = (nu > 0) & (nv > 0)
    if not ok.any():
        both_zero = (nu == 0) & (nv == 0)
        if both_zero.all():
            return 0.0
        raise ValueError('no tokens with nonzero norm on both sides')
    cos = np.clip((u[ok] * v[ok]).sum(axis=1) / (nu[ok] * nv[ok]), -1, 1)
    return float(max(0.0, 1.0 - cos.mean()))


def cross_replay(spec, base='.'):
    get = lambda ref: _array(ref, base)
    mask = get(spec['mask']).astype(bool) if spec.get('mask') else None
    k = spec.get('k', 3)
    o = {key: get(spec['outputs'][key]) for key in ('T(xT)', 'T(xI)', 'I(xT)', 'I(xI)')}
    d = lambda a, b: distance(a, b, mask)
    m = dict(S_T=d(o['T(xT)'], o['I(xT)']), S_I=d(o['T(xI)'], o['I(xI)']),
             P_T=d(o['T(xT)'], o['T(xI)']), P_I=d(o['I(xT)'], o['I(xI)']),
             C_natural=d(o['T(xT)'], o['I(xI)']))
    if spec.get('inputs'):
        m['C_in'] = distance(get(spec['inputs']['xT']), get(spec['inputs']['xI']), mask)
    for side in ('T', 'I'):
        pair = (spec.get('self') or {}).get(side)
        m['R_' + side] = d(get(pair[0]), get(pair[1])) if pair else None
    noise = max([m[r] for r in ('R_T', 'R_I') if m[r] is not None], default=0.0)
    S, nat = max(m['S_T'], m['S_I']), m['C_natural']
    if m['R_T'] is not None and m['R_I'] is not None and nat > 0 and noise > 0.5 * nat:
        label, nxt = 'replay_invalid', 'check capture boundary, state restoration and runtime dispatch on the side with larger R'
    elif nat <= k * noise:
        label, nxt = 'not_reproduced', 'the natural difference is within self-replay noise at this boundary; check upstream boundaries'
    elif S > k * noise and S >= 0.5 * nat:
        label, nxt = 'local_source_candidate', 'narrow to a child operation and compare it with an FP64 reference or invariant'
    else:
        label, nxt = 'propagation_candidate', 'the implementations agree on matched inputs; trace the differing input upstream'
    labels = [label]
    gains = {}
    if m.get('C_in'):
        gains = {side: m['P_' + side] / m['C_in'] for side in ('T', 'I')}
        if max(gains.values()) > 2:
            labels.append('amplification_supported')
    result = dict(kind='cross_replay', schema_version=1, metrics=m, noise_floor=noise, k=k, gains=gains,
                  label=label, labels=labels, next=nxt)
    if not (m['R_T'] is not None and m['R_I'] is not None):
        result['warning'] = 'self-replay missing: noise floor assumed 0'
    if spec.get('reference'):
        ref = {x: get(spec['reference'][x]) for x in ('xT', 'xI') if x in spec['reference']}
        dev = {}
        for x, r in ref.items():
            dev[x] = dict(T=d(o['T(' + x + ')'], r), I=d(o['I(' + x + ')'], r))
            dev[x]['farther'] = 'T' if dev[x]['T'] > dev[x]['I'] else 'I'
        result['reference_deviation'] = dev
    return result


def perturbation(spec, base='.'):
    get = lambda ref: _array(ref, base)
    mask = get(spec['mask']).astype(bool) if spec.get('mask') else None
    alphas = spec['alphas']
    if len(alphas) < 2 or len(spec['T']) != len(alphas) or len(spec['I']) != len(alphas) or alphas[0] != 0:
        raise ValueError('alphas start at 0 and match T/I output counts')
    T, I = [get(p) for p in spec['T']], [get(p) for p in spec['I']]
    s = [distance(t, i, mask) for t, i in zip(T, I)]
    p = [distance(t, T[0], mask) for t in T]
    positive = [x for x in s if x > 0]
    flat = bool(positive) and max(positive) / min(positive) <= 2 and len(positive) == len(s)
    slope = p[-1] / alphas[-1] if alphas[-1] else None
    if flat:
        reading = 'implementation difference independent of the input direction (local source)'
    elif s[-1] > 2 * max(s[0], 1e-300):
        reading = 'the real input difference triggers the implementation difference (input-dependent source)'
    else:
        reading = 'matched-input difference is small along the input direction (propagation or amplification)'
    return dict(kind='perturbation', schema_version=1, alphas=alphas, S_alpha=s, P_alpha=p,
                response_slope=slope, flat_S=flat, reading=reading)


def interventions(spec):
    """Summarize endpoint effects without deciding whether a fix or cause is verified."""
    def number(value, name):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'{name} must be a finite number')
        return float(value)

    lower = spec.get('lower_is_better', True)
    if type(lower) is not bool:
        raise ValueError('lower_is_better must be boolean')
    base, noise = (number(spec[key], key) for key in ('baseline', 'noise'))
    if noise < 0:
        raise ValueError('noise must be nonnegative')
    band = number(3 * noise, 'repeat band')
    target = spec.get('target')
    if target is not None:
        target = number(target, 'target')
    supplied = spec['runs']
    if not isinstance(supplied, dict) or any(not isinstance(k, str) or not k.strip() for k in supplied):
        raise ValueError('runs must be an object keyed by change name')
    runs = {k: number(v, k) for k, v in supplied.items()}

    def measure(name):
        value = runs[name]
        effect = number((base - value) if lower else (value - base), f'{name} effect')
        direction = 'improved' if effect > band else 'worsened' if effect < -band else 'within_repeat_band'
        row = dict(value=value, effect=effect, direction=direction, revert_status='not_run')
        revert = runs.get(f'revert:{name}')
        if revert is not None:
            row['revert_value'] = revert
            row['revert_status'] = 'matches_baseline' if abs(revert - base) <= band else 'differs_from_baseline'
        if f'extra:{name}' in runs:
            row['extra_value'] = runs[f'extra:{name}']
        return row

    factors = {name: measure(name) for name in runs if ':' not in name and '+' not in name}
    combos = {}
    for name in runs:
        if ':' in name or '+' not in name:
            continue
        parts = name.split('+')
        if any(not part.strip() for part in parts) or len(set(parts)) != len(parts):
            raise ValueError(f'{name}: combination must list distinct change names')
        row = measure(name)
        missing = [part for part in parts if part not in factors]
        interaction = None if missing else number(
            row['effect'] - math.fsum(factors[part]['effect'] for part in parts), f'{name} interaction')
        combos[name] = dict(row, factors=parts, missing_factors=missing, interaction=interaction,
                            interaction_beyond_repeat_band=None if missing else abs(interaction) > band)
    return dict(kind='interventions', schema_version=2, metric=spec.get('metric'), baseline=base, noise=noise,
                lower_is_better=lower, repeat_band=band, target=target, factors=factors, combinations=combos)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('cross', 'perturb', 'interventions'))
    parser.add_argument('--spec', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.spec) as stream:
            spec = json.load(stream)
        base = Path(args.spec).resolve().parent
        result = (cross_replay(spec, base) if args.command == 'cross' else
                  perturbation(spec, base) if args.command == 'perturb' else interventions(spec))
        with Path(args.output).open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    if args.command == 'interventions':
        summary = {group: {name: row['direction'] for name, row in result[group].items()}
                   for group in ('factors', 'combinations')}
    else:
        summary = {k: result[k] for k in ('label', 'labels', 'next', 'reading') if k in result}
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
