#!/usr/bin/env python3
"""Capture module-boundary tensors with PyTorch hooks for localization and cross replay.
Call: with BoundaryCapture(model, select, out_dir, side=...) as cap: model(...); cap.save(). replay_module(module, call_path, ...); default_boundaries(model); valid_tokens(mask)
Input: An eager PyTorch model, module paths (or "re:" patterns), optional token selection and input overrides.
Output: <side>-rank<r>.npz (arrays keyed by boundary) + .json inventory (order, children, shapes, dtypes, statistics, first nonfinite); call files for module replay.
Requires: PyTorch >= 2.0 and NumPy; eager execution (graph capture such as CUDA graphs/ACLGraph/torch.compile bypasses hooks).

Details:
BoundaryCapture(model, select, output_dir, *, side, rank=None, tokens=None,
  layout='bsh', directions=('forward',), io=('output',), invocations=(0,),
  statistics_only=False, inventory=False, save_calls=(), inject=None)
select: module paths; entries starting with "re:" are regular expressions over
  named_modules paths. default_boundaries(model) returns the children of the
  largest ModuleList (decoder blocks) as a first pass.
tokens: None (full tensors) or a token selection applied to every captured leaf:
  layout 'bsh' takes [(batch, position), ...] from tensors shaped [batch, seq, ...];
  layout 'th' takes [index, ...] from tensors shaped [tokens, ...]. Captured arrays
  are [n_tokens, ...]; leaves whose shape does not fit are recorded with
  statistics only and a reason. valid_tokens(attention_mask, limit, seed) picks an
  evenly spaced, deterministic subset of valid positions; use the same list on both
  sides (stored as __tokens__ and checked by localize.py).
directions: 'forward' and/or 'backward' (full backward hooks: io 'output' is the
  gradient arriving at the module output, 'input' the gradient it passes on).
io: 'output' and/or 'input' (forward inputs include tensor kwargs).
invocations: call indexes per module and direction to keep; None keeps all.
Boundary key: "<module>:<direction>:<io>.<leaf>#<invocation>", e.g.
  "model.layers.3:forward:output.0#0". Arrays are saved as float32 (float64 kept)
  with the native dtype in the inventory; statistics (mean, std, absmax, l2,
  nan/posinf/neginf counts) are computed on the full native tensor.
inventory=True also records every module's first-call output shapes and bytes
  (input for msprobe_capture.plan_modules and preflight diagnosis readiness).
Inventory order lists the primary leaf of each captured boundary in execution
  order with the module's direct children, so localize.py can rank boundaries and
  propose the next capture: localize.py --side-a A.npz --side-b B.npz --order A.json.
save_calls: module paths whose first forward call (args/kwargs) is saved with
  torch.save as <side>-rank<r>-<module>.call.pt for replay_module. Capture with the
  KV cache disabled (e.g. use_cache=False) unless the cache is the boundary under test.
inject: {module_path: call_path} replaces that module's forward args/kwargs with
  a saved call (moved to the module's device), e.g. to run this side's module on
  the other side's captured input.
replay_module(module, call_path, device=None, dtype=None, output=None,
  flatten_tokens=True): runs one module on a saved call (dtype=torch.float64 on CPU
  gives an FP64 reference) and returns/saves the primary output as [tokens, ...].
CLI: capture_boundaries.py summary INVENTORY.json  (order, bytes, nonfinite boundaries)
"""

__all__ = ['BoundaryCapture', 'replay_module', 'default_boundaries', 'valid_tokens']
import argparse
import json
import math
from pathlib import Path
import re
import sys


def _torch():
    import torch
    return torch


def default_boundaries(model):
    """Children of the largest ModuleList: the usual decoder-block boundaries."""
    torch = _torch()
    best = max(((name, m) for name, m in model.named_modules() if isinstance(m, torch.nn.ModuleList)),
               key=lambda item: len(item[1]), default=None)
    if best is None:
        raise ValueError('no ModuleList found; pass module paths explicitly')
    prefix = best[0] + '.' if best[0] else ''
    return [prefix + str(i) for i in range(len(best[1]))]


def valid_tokens(attention_mask, limit=None, seed=0):
    """Evenly spaced valid (batch, position) pairs from a [batch, seq] mask; deterministic."""
    import numpy as np
    mask = np.asarray(attention_mask.cpu() if hasattr(attention_mask, 'cpu') else attention_mask).astype(bool)
    pairs = [tuple(int(x) for x in p) for p in np.argwhere(mask)]
    if limit is None or limit >= len(pairs):
        return pairs
    rng = np.random.default_rng(seed)
    offset = int(rng.integers(0, max(1, len(pairs) // limit)))
    step = len(pairs) / limit
    return [pairs[min(len(pairs) - 1, offset + int(i * step))] for i in range(limit)]


def _leaves(obj, prefix):
    torch = _torch()
    if isinstance(obj, torch.Tensor):
        yield prefix, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _leaves(value, f'{prefix}.{key}')
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            yield from _leaves(value, f'{prefix}.{i}')


def _stats(t):
    torch = _torch()
    x = t.detach()
    if not (x.is_floating_point() or x.is_complex()):
        x = x.to(torch.float64)
    work = x.to(torch.float64) if x.dtype != torch.float64 else x
    finite = torch.isfinite(work)
    counts = dict(nan=int(torch.isnan(work).sum()), posinf=int(torch.isposinf(work).sum()),
                  neginf=int(torch.isneginf(work).sum()))
    values = work[finite]
    if values.numel() == 0:
        return dict(count=int(work.numel()), **counts)
    return dict(count=int(work.numel()), mean=float(values.mean()), std=float(values.std()) if values.numel() > 1 else 0.0,
                absmax=float(values.abs().max()), l2=float(values.norm()), **counts)


class BoundaryCapture:
    """Context manager registering capture hooks; see module docstring for options."""

    def __init__(self, model, select, output_dir, *, side, rank=None, tokens=None, layout='bsh',
                 directions=('forward',), io=('output',), invocations=(0,), statistics_only=False,
                 inventory=False, save_calls=(), inject=None):
        torch = _torch()
        if not re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', side or ''):
            raise ValueError('side must contain alphanumeric segments separated by hyphens')
        if layout not in ('bsh', 'th') or set(directions) - {'forward', 'backward'} or set(io) - {'input', 'output'}:
            raise ValueError("layout 'bsh'|'th', directions forward/backward, io input/output")
        self.model, self.side, self.layout = model, side, layout
        if rank is None:
            rank = torch.distributed.get_rank() if torch.distributed.is_available() and torch.distributed.is_initialized() else 0
        self.rank = rank
        self.output_dir = Path(output_dir)
        self.modules = dict(model.named_modules())
        paths = []
        for entry in select:
            if entry.startswith('re:'):
                pattern = re.compile(entry[3:])
                paths += [n for n in self.modules if n and pattern.fullmatch(n)]
            elif entry in self.modules:
                paths.append(entry)
            else:
                raise ValueError('unknown module path: ' + entry)
        self.paths = list(dict.fromkeys(paths))
        if not self.paths and not inventory:
            raise ValueError('no modules selected')
        self.tokens = None if tokens is None else [tuple(t) if isinstance(t, (list, tuple)) else int(t) for t in tokens]
        self.directions, self.io = tuple(directions), tuple(io)
        self.invocations = None if invocations is None else set(invocations)
        self.statistics_only, self.inventory = statistics_only, inventory
        self.save_calls, self.inject = set(save_calls), dict(inject or {})
        for path in list(self.save_calls) + list(self.inject):
            if path not in self.modules:
                raise ValueError('unknown module path: ' + path)
        self.arrays, self.records, self.order, self.module_inventory = {}, {}, [], {}
        self.calls, self.counts, self.handles = {}, {}, []

    # -- selection -------------------------------------------------------
    def _select(self, t):
        torch = _torch()
        if self.tokens is None:
            return t, None
        if self.layout == 'bsh':
            if t.dim() < 2 or any(b >= t.shape[0] or p >= t.shape[1] for b, p in self.tokens):
                return None, f'shape {tuple(t.shape)} does not fit (batch, position) tokens'
            b = torch.tensor([x[0] for x in self.tokens], device=t.device)
            p = torch.tensor([x[1] for x in self.tokens], device=t.device)
            return t[b, p], None
        if t.dim() < 1 or any(i >= t.shape[0] for i in self.tokens):
            return None, f'shape {tuple(t.shape)} does not fit token indexes'
        return t[torch.tensor(self.tokens, device=t.device)], None

    def _record(self, path, direction, io, value):
        torch = _torch()
        counter = (path, direction, io)
        invocation = self.counts.get(counter, 0)
        self.counts[counter] = invocation + 1
        if self.invocations is not None and invocation not in self.invocations:
            return
        primary = None
        for leaf, t in _leaves(value, io):
            if t is None or not isinstance(t, torch.Tensor):
                continue
            key = f'{path}:{direction}:{leaf}#{invocation}'
            info = dict(module=path, cls=type(self.modules[path]).__name__, direction=direction, leaf=leaf,
                        invocation=invocation, shape=list(t.shape), dtype=str(t.dtype).replace('torch.', ''),
                        bytes=t.numel() * t.element_size(), stats=_stats(t))
            if not self.statistics_only and not t.is_complex():
                selected, reason = self._select(t.detach())
                if selected is None:
                    info['skipped'] = reason
                else:
                    keep = torch.float64 if selected.dtype == torch.float64 else torch.float32
                    array = selected.to('cpu', dtype=keep if selected.is_floating_point() else selected.dtype, copy=True)
                    self.arrays[key] = array.numpy()
                    info['captured_shape'] = list(array.shape)
                    if primary is None and selected.is_floating_point() and selected.dim() >= 1:
                        primary = key
            self.records[key] = info
        if primary:
            prefix = path + '.'
            children = [n for n in self.modules if n.startswith(prefix) and '.' not in n[len(prefix):]]
            self.order.append(dict(name=primary, module=path, children=children))

    # -- hooks -----------------------------------------------------------
    def __enter__(self):
        for path in self.paths:
            module = self.modules[path]
            if 'forward' in self.directions:
                def forward_hook(mod, args, kwargs, output, path=path):
                    if 'input' in self.io:
                        self._record(path, 'forward', 'input', dict(args=args, kwargs=kwargs))
                    if 'output' in self.io:
                        self._record(path, 'forward', 'output', output)
                self.handles.append(module.register_forward_hook(forward_hook, with_kwargs=True))
            if 'backward' in self.directions:
                def backward_hook(mod, grad_input, grad_output, path=path):
                    if 'output' in self.io:
                        self._record(path, 'backward', 'output', grad_output)
                    if 'input' in self.io:
                        self._record(path, 'backward', 'input', grad_input)
                self.handles.append(module.register_full_backward_hook(backward_hook))
        torch = _torch()
        for path in self.save_calls:
            def save_hook(mod, args, kwargs, path=path):
                if path not in self.calls:
                    detach = lambda x: x.detach().to('cpu', copy=True) if isinstance(x, torch.Tensor) else x
                    self.calls[path] = dict(args=tuple(_map(args, detach)), kwargs=_map(kwargs, detach))
            self.handles.append(self.modules[path].register_forward_pre_hook(save_hook, with_kwargs=True))
        for path, call_path in self.inject.items():
            call = torch.load(call_path, weights_only=False)
            def inject_hook(mod, args, kwargs, call=call):
                device = next((p.device for p in mod.parameters()), None)
                move = lambda x: x.to(device) if isinstance(x, torch.Tensor) and device is not None else x
                return tuple(_map(call['args'], move)), _map(call['kwargs'], move)
            self.handles.append(self.modules[path].register_forward_pre_hook(inject_hook, with_kwargs=True, prepend=True))
        if self.inventory:
            for name, module in self.modules.items():
                if not name:
                    continue
                def inventory_hook(mod, args, output, name=name):
                    if name in self.module_inventory:
                        return
                    leaves = [t for _, t in _leaves(output, 'output') if isinstance(t, torch.Tensor)]
                    self.module_inventory[name] = dict(cls=type(mod).__name__, call_order=len(self.module_inventory),
                                                       output_shapes=[list(t.shape) for t in leaves],
                                                       output_bytes=sum(t.numel() * t.element_size() for t in leaves))
                self.handles.append(module.register_forward_hook(inventory_hook))
        return self

    def __exit__(self, *exc):
        for handle in self.handles:
            handle.remove()
        self.handles = []
        return False

    def save(self):
        """Write <side>-rank<r>.npz/.json (and call files); return the inventory path."""
        import numpy as np
        torch = _torch()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = self.output_dir / f'{self.side}-rank{self.rank}'
        arrays = dict(self.arrays)
        if self.tokens is not None:
            arrays['__tokens__'] = np.asarray(self.tokens, dtype=np.int64)
        with open(str(stem) + '.npz', 'xb') as stream:
            np.savez(stream, **arrays)
        calls = {}
        for path, call in self.calls.items():
            target = Path(f'{stem}-{path}.call.pt')
            with target.open('xb') as stream:
                torch.save(call, stream)
            calls[path] = target.name
        first_nonfinite = next((k for k, r in self.records.items()
                                if r['stats'].get('nan') or r['stats'].get('posinf') or r['stats'].get('neginf')), None)
        inventory = dict(kind='boundary-capture', schema_version=1, side=self.side, rank=self.rank,
                         model=type(self.model).__name__, tokens=self.tokens, layout=self.layout,
                         arrays=Path(str(stem) + '.npz').name, order=self.order, boundaries=self.records,
                         first_nonfinite=first_nonfinite, calls=calls,
                         **({'modules': self.module_inventory} if self.inventory else {}))
        path = Path(str(stem) + '.json')
        with path.open('x') as stream:
            json.dump(inventory, stream, indent=2, allow_nan=False)
            stream.write('\n')
        return path


def _map(obj, fn):
    if isinstance(obj, dict):
        return {k: _map(v, fn) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)) and not hasattr(obj, '_fields'):
        return type(obj)(_map(v, fn) for v in obj)
    return fn(obj)


def replay_module(module, call_path, device=None, dtype=None, output=None, flatten_tokens=True):
    """Run one module on a saved call; return the primary output as a NumPy array [tokens, ...]."""
    import numpy as np
    torch = _torch()
    call = torch.load(call_path, weights_only=False)
    if dtype is not None:
        module = module.to(dtype=dtype)
    if device is not None:
        module = module.to(device)
    target = next((p.device for p in module.parameters()), torch.device(device or 'cpu'))
    def move(x):
        if not isinstance(x, torch.Tensor):
            return x
        x = x.to(target)
        return x.to(dtype) if dtype is not None and x.is_floating_point() else x
    with torch.no_grad():
        result = module(*_map(call['args'], move), **_map(call['kwargs'], move))
    primary = next((t for _, t in _leaves(result, 'output') if t.is_floating_point()), None)
    if primary is None:
        raise ValueError('module returned no floating tensor')
    keep = torch.float64 if primary.dtype == torch.float64 else torch.float32
    array = primary.detach().to('cpu', dtype=keep).numpy()
    if flatten_tokens and array.ndim >= 3:
        array = array.reshape(-1, *array.shape[2:])
    if output is not None:
        with open(output, 'xb') as stream:
            np.save(stream, array)
    return array


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('summary',))
    parser.add_argument('inventory')
    args = parser.parse_args(argv)
    try:
        inv = json.loads(Path(args.inventory).read_text())
        rows = [dict(name=o['name'], children=len(o['children']),
                     bytes=inv['boundaries'][o['name']]['bytes'],
                     nonfinite=sum(inv['boundaries'][o['name']]['stats'].get(k, 0) for k in ('nan', 'posinf', 'neginf')))
                for o in inv['order']]
        total = sum(r['bytes'] for r in inv['boundaries'].values())
        print(json.dumps(dict(side=inv['side'], rank=inv['rank'], boundaries=len(rows), native_bytes=total,
                              first_nonfinite=inv['first_nonfinite'], order=rows), indent=1))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
