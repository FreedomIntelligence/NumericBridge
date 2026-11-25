#!/usr/bin/env python3
"""Statically audit Triton-Ascend (.py) and AscendC (.h/.hpp/.cpp/.asc) kernels for discrete verification candidates.

Call: scan_path(path); CLI: python scripts/audit_scan.py PATH [PATH ...] [--output findings.json]
Input: one or more kernel source files or directories (Triton Python and/or AscendC C++).
Output: schema_version=1 JSON report listing candidate defects mapped to operator-verify categories
(`index_and_mask`, `resource_capacity`, `pipeline_sync`, `semantic_equivalence`) for triage and Lean 4 verification.
Requires: Python standard library only.
"""
__all__ = ['scan_file', 'scan_path', 'RULES']

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys


RULES = {
    'online_softmax_neginf': {
        'category': 'semantic_equivalence',
        'backend': 'triton-ascend',
        'model_modules': ['AscendLean.TritonModel'],
    },
    'block_ptr_unpadded_load': {
        'category': 'index_and_mask',
        'backend': 'triton-ascend',
        'model_modules': ['AscendLean.TritonModel'],
    },
    'masked_load_missing_other': {
        'category': 'index_and_mask',
        'backend': 'triton-ascend',
        'model_modules': ['AscendLean.TritonModel'],
    },
    'unmasked_mapping_index_load': {
        'category': 'index_and_mask',
        'backend': 'triton-ascend',
        'model_modules': ['AscendLean.TritonModel'],
    },
    'int32_pointer_mul_overflow': {
        'category': 'index_and_mask',
        'backend': 'triton-ascend',
        'model_modules': ['AscendLean.UBModel'],
    },
    'hardcoded_multibuffer_np2': {
        'category': 'resource_capacity',
        'backend': 'triton-ascend',
        'model_modules': ['AscendLean.UBModel'],
    },
    'ascendc_hardevent_imbalance': {
        'category': 'pipeline_sync',
        'backend': 'ascendc',
        'model_modules': ['AscendLean.PipeModel'],
    },
}

_TRITON_EXTS = {'.py'}
_ASCENDC_EXTS = {'.h', '.hpp', '.cpp', '.cc', '.asc'}
_NEGINF_RE = re.compile(r"(?:float\(\s*['\"]-inf['\"]\s*\)|-\s*float\(\s*['\"]inf['\"]\s*\))")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _is_triton_jit(fn):
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        text = ast.unparse(target)
        if text == 'jit' or text.endswith('.jit'):
            return True
    return False


def _has_autotune_multibuffer(fn):
    for dec in fn.decorator_list:
        text = ast.unparse(dec)
        if 'autotune' in text and 'multibuffer' in text:
            return True
    return False


def _make_finding(path, file_hash, rule_id, line, symbol, summary):
    meta = RULES[rule_id]
    return {
        'rule_id': rule_id,
        'category': meta['category'],
        'backend': meta['backend'],
        'file': str(path),
        'sha256': file_hash,
        'line': line,
        'symbol': symbol,
        'summary': summary,
        'model_modules': list(meta['model_modules']),
    }


def _count_mul_ops(expr):
    return sum(1 for n in ast.walk(expr) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult))


def _is_int64_cast(expr):
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == 'to'
        and any('int64' in ast.unparse(a) for a in expr.args)
    )


def _is_tl_load(expr):
    return isinstance(expr, ast.Call) and ast.unparse(expr.func) == 'tl.load'


def _strip_cpp_comments(text):
    text = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group(0).count('\n'), text, flags=re.S)
    text = re.sub(r'//[^\n]*', '', text)
    return text


def _scan_triton_python(path, text, file_hash):
    findings = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return findings

    jit_fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and _is_triton_jit(n)]
    if not jit_fns:
        return findings

    for fn in jit_fns:
        fn_src = ast.unparse(fn)
        has_reduction = any(tok in fn_src for tok in ('tl.sum(', 'tl.dot(', 'tl.max(', 'tl.min('))

        # 1. Online softmax (-inf) - (-inf) = NaN poisoning:
        # Track variables explicitly initialized to -inf (not finite sinks or tl.where masked scores).
        neginf_vars = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                val_s = ast.unparse(node.value)
                if _NEGINF_RE.search(val_s) and 'tl.where(' not in val_s:
                    neginf_vars.add(node.targets[0].id)

        has_safe_m_guard = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == 'tl.where' and len(node.args) >= 3:
                cond_s = ast.unparse(node.args[0])
                if _NEGINF_RE.search(cond_s) and any(op in cond_s for op in ('>', '!=', '==')):
                    has_safe_m_guard = True
                    break

        if neginf_vars and not has_safe_m_guard:
            for node in ast.walk(fn):
                if isinstance(node, ast.For):
                    loop_s = ast.unparse(node)
                    if 'tl.maximum(' not in loop_s or 'tl.exp(' not in loop_s:
                        continue
                    flagged_line = None
                    for call in ast.walk(node):
                        if isinstance(call, ast.Call) and ast.unparse(call.func) == 'tl.exp' and call.args:
                            arg_s = ast.unparse(call.args[0])
                            if '-' in arg_s and any(re.search(rf'\b{re.escape(v)}\b', arg_s) for v in neginf_vars):
                                flagged_line = call.lineno
                                break
                    if flagged_line is not None:
                        findings.append(_make_finding(
                            path, file_hash, 'online_softmax_neginf', flagged_line, fn.name,
                            'Online softmax loop initializes running max to -inf and evaluates tl.exp(m - new_m) '
                            'without guarding new_m == -inf; leading all-(-inf) tiles trigger (-inf) - (-inf) = NaN.',
                        ))
                        break

        # 2. tl.load(block_ptr, boundary_check=...) without padding_option="zero"
        # 3. tl.load(..., mask=...) without other= in a kernel with reductions
        # 4. Unmasked indexed load using a request mapping variable (can be -1 in padded graph rows)
        mapping_vars = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                val_s = ast.unparse(node.value)
                if 'tl.load(' in val_s and 'mapping' in val_s.lower():
                    mapping_vars.add(node.targets[0].id)

        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and ast.unparse(node.func) == 'tl.load'):
                continue
            kw_names = {kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg}
            arg0 = ast.unparse(node.args[0]) if node.args else ''

            if 'boundary_check' in kw_names:
                pad_opt = kw_names.get('padding_option', '')
                if 'zero' not in pad_opt:
                    findings.append(_make_finding(
                        path, file_hash, 'block_ptr_unpadded_load', node.lineno, fn.name,
                        'tl.load with boundary_check omits padding_option="zero"; '
                        'out-of-bounds lanes retain undefined UB garbage.',
                    ))

            has_mask = 'mask' in kw_names or len(node.args) >= 2
            has_other = 'other' in kw_names or len(node.args) >= 3
            if has_mask and not has_other and has_reduction:
                findings.append(_make_finding(
                    path, file_hash, 'masked_load_missing_other', node.lineno, fn.name,
                    'Masked tl.load omits explicit `other=` fill value inside a kernel with reductions; '
                    'uninitialized UB padding lanes can contaminate tl.sum/tl.dot.',
                ))

            if not has_mask and mapping_vars:
                for mv in mapping_vars:
                    if re.search(rf'\+\s*{re.escape(mv)}\b', arg0):
                        findings.append(_make_finding(
                            path, file_hash, 'unmasked_mapping_index_load', node.lineno, fn.name,
                            f'Unmasked tl.load indexed by mapping variable `{mv}` without `{mv} >= 0` guard; '
                            'padded graph rows with sentinel -1 perform out-of-bounds negative-offset loads.',
                        ))
                        break

        # 5. Potential int32 pointer offset overflow:
        # (a) `(a * b * c).to(tl.int64)` where widening occurs AFTER int32 multiplications, OR
        # (b) multi-factor offset expression (>=3 `*`) with no inner int64 cast.
        int64_vars = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if 'int64' in ast.unparse(node.value):
                    int64_vars.add(node.targets[0].id)

        for node in ast.walk(fn):
            val = None
            tgt = None
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
                tgt = node.target.id
                val = node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                tgt = node.targets[0].id
                val = node.value
            if val is None or tgt is None:
                continue

            if _is_int64_cast(val) and isinstance(val.func, ast.Attribute):
                inner = val.func.value
                inner_s = ast.unparse(inner)
                if not _is_tl_load(inner) and _count_mul_ops(inner) >= 2 and 'int64' not in inner_s:
                    if not any(re.search(rf'\b{re.escape(v)}\b', inner_s) for v in int64_vars):
                        findings.append(_make_finding(
                            path, file_hash, 'int32_pointer_mul_overflow', node.lineno, fn.name,
                            f'Offset `{tgt}` widens to int64 AFTER int32 multiplication (`{ast.unparse(val)}`); '
                            'product can overflow signed int32 before .to(tl.int64).',
                        ))
            elif 'offset' in tgt.lower():
                val_s = ast.unparse(val)
                if _count_mul_ops(val) >= 3 and 'int64' not in val_s:
                    if not any(re.search(rf'\b{re.escape(v)}\b', val_s) for v in int64_vars):
                        findings.append(_make_finding(
                            path, file_hash, 'int32_pointer_mul_overflow', node.lineno, fn.name,
                            f'Multi-factor pointer offset `{tgt} = {val_s}` computed without int64 cast; '
                            'can overflow signed int32 (2^31 - 1) on long-context or large-batch workloads.',
                        ))

    # 6. Hardcoded multibuffer=True with next_power_of_2 and no @triton.autotune
    if 'multibuffer=True' in text and 'next_power_of_2' in text:
        if not any(_has_autotune_multibuffer(fn) for fn in jit_fns):
            line_no = next((i for i, l in enumerate(text.splitlines(), 1) if 'multibuffer=True' in l), 1)
            findings.append(_make_finding(
                path, file_hash, 'hardcoded_multibuffer_np2', line_no, jit_fns[0].name,
                'Kernel launch hardcodes multibuffer=True with next_power_of_2 tiling and no multibuffer autotune; '
                'large/non-power-of-2 shapes risk Ascend UB overflow.',
            ))

    return findings


def _scan_ascendc(path, text, file_hash):
    findings = []
    cleaned = _strip_cpp_comments(text)
    sets = Counter(re.findall(r'SetFlag<(?:AscendC::)?HardEvent::([A-Z0-9_]+)>', cleaned))
    waits = Counter(re.findall(r'WaitFlag<(?:AscendC::)?HardEvent::([A-Z0-9_]+)>', cleaned))
    for ev in sorted(set(sets) | set(waits)):
        if sets[ev] != waits[ev]:
            m = re.search(rf'(?:Set|Wait)Flag<(?:AscendC::)?HardEvent::{ev}>', cleaned)
            line_no = cleaned[:m.start()].count('\n') + 1 if m else 1
            findings.append(_make_finding(
                path, file_hash, 'ascendc_hardevent_imbalance', line_no, ev,
                f'Static SetFlag/WaitFlag count mismatch for HardEvent::{ev} '
                f'(SetFlag={sets[ev]}, WaitFlag={waits[ev]}); requires control-flow sync verification.',
            ))
    return findings


def scan_file(path):
    """Scan one Triton-Ascend or AscendC source file and return candidate findings."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f'source file not found: {path}')
    suffix = p.suffix.lower()
    if suffix not in _TRITON_EXTS and suffix not in _ASCENDC_EXTS:
        return []
    text = p.read_text(encoding='utf-8', errors='replace')
    file_hash = _sha256(p)
    if suffix in _TRITON_EXTS:
        return _scan_triton_python(p, text, file_hash)
    return _scan_ascendc(p, text, file_hash)


def scan_path(paths):
    """Scan one or more files or directories and return a schema_version=1 audit report."""
    findings = []
    scanned_files = 0
    for raw in paths:
        p = Path(raw).resolve()
        if p.is_file():
            scanned_files += 1
            findings.extend(scan_file(p))
        elif p.is_dir():
            for child in sorted(p.rglob('*')):
                if child.is_file() and child.suffix.lower() in (_TRITON_EXTS | _ASCENDC_EXTS):
                    if '.lake' in child.parts or '__pycache__' in child.parts:
                        continue
                    scanned_files += 1
                    findings.extend(scan_file(child))
        else:
            raise FileNotFoundError(f'path not found: {raw}')

    by_category = Counter(f['category'] for f in findings)
    return {
        'schema_version': 1,
        'scanned_files': scanned_files,
        'finding_count': len(findings),
        'by_category': dict(sorted(by_category.items())),
        'findings': findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('paths', nargs='+', type=Path, help='Kernel source files or directories to audit')
    parser.add_argument('--output', type=Path, help='Write audit-findings.json to this path (else print JSON)')
    args = parser.parse_args(argv)

    try:
        report = scan_path(args.paths)
    except OSError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
