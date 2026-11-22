#!/usr/bin/env python3
"""Rank boundaries where a cross-side difference first appears or grows, and plan the next capture.
Call: localize(boundaries, t=3.0, noise=None) or from_arrays(side_a, side_b, order, mask=None); CLI: python scripts/localize.py (--boundaries B.json | --side-a A.npz --side-b B.npz --order O.json) --output NEW.json
Input: Ordered boundaries with input/output agreement (cosine), or two NPZ captures keyed by boundary name.
Output: per-boundary C_in, C_out, growth G, per-token cosine; ranked candidates; next action (refine children or cross replay).
Requires: Standard library for --boundaries; NumPy for NPZ captures.

Details:
Boundaries JSON: {"boundaries":[{"name":str, "c_out":float, optional "c_in":float,
"noise":float (same-side repeat 1-cosine at this boundary), "children":[module paths]}]}
in execution order (backward order for gradients). Missing c_in takes the previous
boundary's c_out (1.0 for the first). C is a cosine agreement in [-1, 1]; with
NPZ input it is the mean per-token cosine over valid tokens.

Growth G = C_in - C_out (signed). Candidates:
  largest_growth  boundaries with G > 0 ordered by G (only G > noise when noise is given)
  first_drop      first boundary k with >= 3 predecessors and
                  C_out[k] < mean(C_out[:k]) - t * stdev(C_out[:k]) (t=3; predecessors only)
Error is not assumed monotone; there is no bisection. Next action: when the top
candidate declares children, capture those children (feed module paths to
msprobe_capture.plan_modules); otherwise cross-replay that boundary (replay.py).
After a refined capture, rerun on the children; rank repeated measurements by the
median G.

NPZ input: arrays named by boundary, shape [tokens, ...] (token axis 0, remaining
axes flattened per token), e.g. from precision-compare capture_boundaries.py.
order JSON: ["boundary", ...], the boundaries JSON without c values, or a
capture_boundaries inventory (its "order" carries module children). mask: NPZ/NPY
boolean [tokens] of valid positions. Both NPZ files must hold the same __tokens__
selection when present. --noise-a/--noise-b: same-side repeat captures; each
boundary's noise is the larger 1 - per-token cosine between a side and its repeat.
Reports the per-token cosine mean, P1, minimum and the worst token index.
CLI exit: 0=report written, 2=input error.
"""

__all__ = ['localize', 'from_arrays']
import argparse
import json
import math
from pathlib import Path
import statistics
import sys


def _load(path):
    with open(path) as stream:
        return json.load(stream)


def localize(boundaries, t=3.0, noise=None):
    """boundaries: ordered list of dicts with name, c_out, optional c_in/noise/children/per_token."""
    if not isinstance(boundaries, list) or not boundaries:
        raise ValueError('ordered nonempty boundaries required')
    rows, previous = [], 1.0
    names = set()
    for b in boundaries:
        name = b.get('name')
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('unique boundary names required')
        names.add(name)
        c_out = b.get('c_out')
        c_in = b.get('c_in', previous)
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (c_in, c_out)):
            raise ValueError(name + ': finite c_in/c_out required')
        row = dict(name=name, c_in=float(c_in), c_out=float(c_out), growth=float(c_in) - float(c_out))
        for key in ('noise', 'children', 'per_token'):
            if key in b:
                row[key] = b[key]
        if noise and name in noise:
            row['noise'] = noise[name]
        rows.append(row)
        previous = float(c_out)
    growth = sorted((r for r in rows if r['growth'] > r.get('noise', 0.0)), key=lambda r: -r['growth'])
    candidates = [dict(name=r['name'], reason='largest_growth', growth=r['growth']) for r in growth[:5]]
    first_drop = None
    for k in range(3, len(rows)):
        prior = [r['c_out'] for r in rows[:k]]
        bound = statistics.fmean(prior) - t * statistics.stdev(prior)
        if rows[k]['c_out'] < bound:
            first_drop = dict(name=rows[k]['name'], reason='first_drop', c_out=rows[k]['c_out'], bound=bound)
            break
    if first_drop:
        if candidates and candidates[0]['name'] == first_drop['name']:
            candidates[0]['reason'] = 'largest_growth+first_drop'
        else:
            candidates = [c for c in candidates if c['name'] != first_drop['name']]
            candidates.insert(1 if candidates else 0, first_drop)
    by_name = {r['name']: r for r in rows}
    if not candidates:
        nxt = dict(action='none', reason='no boundary grows the difference beyond noise; check inputs upstream of the first boundary')
    else:
        top = by_name[candidates[0]['name']]
        if top.get('children'):
            capture = list(top['children'])
            if len(candidates) > 1 and by_name[candidates[1]['name']].get('children'):
                capture += by_name[candidates[1]['name']]['children']
            nxt = dict(action='refine', boundary=top['name'], capture=capture)
        else:
            nxt = dict(action='cross_replay', boundary=top['name'],
                       reason='leaf boundary: replay both implementations on both captured inputs (replay.py)')
    return dict(kind='localization', schema_version=1, t=t, boundaries=rows, candidates=candidates, next=nxt)


def _token_cosines(a, b, mask):
    import numpy as np
    a = np.asarray(a, dtype=np.float64).reshape(a.shape[0], -1)
    b = np.asarray(b, dtype=np.float64).reshape(b.shape[0], -1)
    if a.shape != b.shape:
        raise ValueError('boundary shapes differ')
    if mask is not None:
        index = np.flatnonzero(mask)
        a, b = a[index], b[index]
    else:
        index = np.arange(a.shape[0])
    na, nb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    valid = (na > 0) & (nb > 0)
    if not valid.any():
        raise ValueError('no tokens with nonzero norm')
    cos = np.clip((a[valid] * b[valid]).sum(axis=1) / (na[valid] * nb[valid]), -1, 1)
    worst = int(np.argmin(cos))
    return dict(mean=float(cos.mean()), p1=float(np.quantile(cos, 0.01)), min=float(cos[worst]),
                worst_token=int(index[valid][worst]), tokens=int(valid.sum()), zero_norm_tokens=int((~valid).sum()))


def from_arrays(side_a, side_b, order, mask=None, t=3.0, repeats=()):
    """side_a/side_b: mappings boundary -> array [tokens, ...]; order: names or boundary dicts.

    repeats: (side_arrays, repeat_arrays) pairs giving per-boundary repeat noise.
    """
    import numpy as np
    if '__tokens__' in side_a and '__tokens__' in side_b and not np.array_equal(side_a['__tokens__'], side_b['__tokens__']):
        raise ValueError('sides captured different token selections')
    entries = [o if isinstance(o, dict) else dict(name=o) for o in order]
    boundaries = []
    for entry in entries:
        name = entry['name']
        if name not in side_a or name not in side_b:
            raise ValueError('missing boundary array: ' + name)
        per_token = _token_cosines(side_a[name], side_b[name], mask)
        row = {**{k: v for k, v in entry.items() if k in ('name', 'children', 'noise')},
               'c_out': per_token['mean'], 'per_token': per_token}
        noise = [1 - _token_cosines(base[name], repeat[name], mask)['mean'] for base, repeat in repeats
                 if name in base and name in repeat]
        if noise:
            row['noise'] = max(0.0, max(noise))
        boundaries.append(row)
    return localize(boundaries, t)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--boundaries')
    parser.add_argument('--side-a')
    parser.add_argument('--side-b')
    parser.add_argument('--order')
    parser.add_argument('--mask')
    parser.add_argument('--noise-a')
    parser.add_argument('--noise-b')
    parser.add_argument('--t', type=float, default=3.0)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.boundaries:
            result = localize(_load(args.boundaries)['boundaries'], args.t)
        elif args.side_a and args.side_b and args.order:
            import numpy as np
            order = _load(args.order)
            if isinstance(order, dict):
                order = order['order'] if order.get('kind') == 'boundary-capture' else order['boundaries']
            mask = None
            if args.mask:
                loaded = np.load(args.mask)
                mask = (loaded[loaded.files[0]] if hasattr(loaded, 'files') else loaded).astype(bool)
            load = lambda p: dict(np.load(p)) if p else None
            a, b = load(args.side_a), load(args.side_b)
            repeats = [(base, load(path)) for base, path in ((a, args.noise_a), (b, args.noise_b)) if path]
            result = from_arrays(a, b, order, mask, args.t, repeats)
        else:
            raise ValueError('use --boundaries, or --side-a, --side-b and --order')
        with Path(args.output).open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(dict(candidates=[c['name'] for c in result['candidates']], next=result['next'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
