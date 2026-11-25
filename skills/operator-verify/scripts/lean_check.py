#!/usr/bin/env python3
"""Type-check a Lean 4 verification file, audit sorry/axioms, and emit verify-report.json.

Call: check_lean(target, ...); CLI: python scripts/lean_check.py TARGET.lean [--project DIR] [--output verify-report.json]
Input: a Lean 4 target file, optional Lake project root, optional kernel source files for SHA-256 provenance,
claim kind, preconditions, optional counterexample summary, and explicit TCB assumptions.
Output: verify-report.json with toolchain identity, file hashes, theorem list, sorry/admit/axiom/escape audit,
compiler status, and verdict (proved | refuted | passed_unclassified | inconclusive | not_run).
Exit codes: 0=passed, 1=failed or audit_failed, 2=input error, 3=toolchain_missing.
Requires: Python standard library only (invokes host `lake`/`lean` if available).
"""
__all__ = ['audit_lean_source', 'strip_lean_comments', 'check_lean']

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


_THEOREM_RE = re.compile(r'^\s*(?:(?:protected|noncomputable)\s+)*(?:theorem|lemma)\s+([A-Za-z0-9_.\']+)', re.M)
_AXIOM_RE = re.compile(r'\baxiom\s+([A-Za-z0-9_.\']+)')
_SORRY_RE = re.compile(r'\b(?:sorry|sorryAx)\b')
_ADMIT_RE = re.compile(r'\badmit\b')
_UNSAFE_ESCAPE_RE = re.compile(
    r'\b(?:native_decide|unsafe|opaque|implemented_by|extern|ofReduceBool|ofReduceNat|trustCompiler)\b'
    r'|@\[[^\]]*\b(?:implemented_by|extern)\b'
)
_COMPILER_SORRY_RE = re.compile(r"declaration uses ['`]?sorry(?:Ax)?['`]?", re.I)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def strip_lean_comments(text):
    """Remove Lean 4 line comments (`--`), nested block comments (`/- ... -/`), string literals, and char literals."""
    out = []
    i = 0
    n = len(text)
    block_depth = 0
    in_string = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if block_depth > 0:
            if ch == '/' and nxt == '-':
                block_depth += 1
                i += 2
            elif ch == '-' and nxt == '/':
                block_depth -= 1
                i += 2
            else:
                if ch == '\n':
                    out.append('\n')
                i += 1
            continue
        if in_string:
            if ch == '\\' and i + 1 < n:
                i += 2
            elif ch == '"':
                in_string = False
                i += 1
            else:
                if ch == '\n':
                    out.append('\n')
                i += 1
            continue
        if ch == '/' and nxt == '-':
            block_depth = 1
            i += 2
            continue
        if ch == '-' and nxt == '-':
            while i < n and text[i] != '\n':
                i += 1
            continue
        # Handle Lean char literals like '"' or '\'' so `'"'` never opens a string literal.
        # Avoid consuming identifier prime suffixes like `x'` by checking previous char is not ident-part.
        if ch == "'":
            prev = text[i - 1] if i > 0 else ' '
            if not (prev.isalnum() or prev in ('_', "'")):
                if i + 2 < n and text[i + 1] != '\\' and text[i + 1] != '\n' and text[i + 2] == "'":
                    out.append("''")
                    i += 3
                    continue
                if i + 3 < n and text[i + 1] == '\\' and text[i + 3] == "'":
                    out.append("''")
                    i += 4
                    continue
        if ch == '"':
            in_string = True
            out.append('""')
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def audit_lean_source(text):
    """Scan comment-stripped Lean source for theorems, sorry/admit/escape tokens, and custom axioms."""
    cleaned = strip_lean_comments(text)
    theorems = _THEOREM_RE.findall(cleaned)
    custom_axioms = _AXIOM_RE.findall(cleaned)
    sorry_count = len(_SORRY_RE.findall(cleaned))
    admit_count = len(_ADMIT_RE.findall(cleaned))
    unsafe_escapes = sorted(set(_UNSAFE_ESCAPE_RE.findall(cleaned)))
    no_theorems = len(theorems) == 0
    clean = (
        sorry_count == 0
        and admit_count == 0
        and not custom_axioms
        and not unsafe_escapes
        and not no_theorems
    )
    return {
        'theorems': theorems,
        'audit': {
            'sorry_count': sorry_count,
            'admit_count': admit_count,
            'custom_axioms': custom_axioms,
            'unsafe_escapes': unsafe_escapes,
            'has_theorems': not no_theorems,
            'compiler_sorry_warning': False,
            'clean': clean,
        },
    }


def _find_lake_project(target_path, explicit_project=None):
    if explicit_project is not None:
        return Path(explicit_project).resolve()
    for parent in Path(target_path).resolve().parents:
        if (parent / 'lakefile.toml').is_file() or (parent / 'lakefile.lean').is_file():
            return parent
    return None


def _probe_version(binary):
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ''
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def check_lean(target, project=None, build_lib=False, kernel_sources=None,
               claim_kind='unspecified', preconditions=None, tcb_assumptions=None,
               counterexample=None, timeout=120):
    """Run static audit and Lean 4 type-checking, returning a schema_version=1 verify-report dict."""
    target_path = Path(target).resolve()
    if not target_path.is_file():
        raise FileNotFoundError(f'Lean target not found: {target}')

    text = target_path.read_text(encoding='utf-8')
    scan = audit_lean_source(text)
    project_dir = _find_lake_project(target_path, project)

    sources = [{'role': 'lean_target', 'path': str(target_path), 'sha256': _sha256(target_path)}]
    for src in (kernel_sources or []):
        src_path = Path(src).resolve()
        if not src_path.is_file():
            raise FileNotFoundError(f'kernel source not found: {src}')
        sources.append({'role': 'kernel_source', 'path': str(src_path), 'sha256': _sha256(src_path)})

    lake_bin = shutil.which('lake')
    lean_bin = shutil.which('lean')
    toolchain_file = (project_dir / 'lean-toolchain') if project_dir is not None else None
    toolchain_pin = (
        toolchain_file.read_text(encoding='utf-8').strip()
        if toolchain_file is not None and toolchain_file.is_file()
        else None
    )
    toolchain = {
        'lean_version': _probe_version(lean_bin),
        'lake_version': _probe_version(lake_bin),
        'lean_toolchain': toolchain_pin,
    }

    base_report = {
        'schema_version': 1,
        'claim_kind': claim_kind,
        'project': str(project_dir) if project_dir is not None else None,
        'toolchain': toolchain,
        'sources': sources,
        'audit': dict(scan['audit']),
        'theorems': scan['theorems'],
        'preconditions': list(preconditions or []),
        'tcb_assumptions': list(tcb_assumptions or []),
        'counterexample': counterexample,
    }

    use_lake = project_dir is not None
    if (use_lake and not lake_bin) or (not use_lake and not lean_bin):
        return {
            **base_report,
            'status': 'toolchain_missing',
            'verdict': 'not_run',
            'command': None,
        }

    t0 = time.monotonic()
    cwd = project_dir if project_dir is not None else target_path.parent
    if build_lib and use_lake:
        build_proc = subprocess.run([lake_bin, 'build'], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if build_proc.returncode != 0:
            return {
                **base_report,
                'status': 'failed',
                'verdict': 'inconclusive',
                'command': {
                    'argv': ['lake', 'build'],
                    'exit_code': build_proc.returncode,
                    'duration_sec': round(time.monotonic() - t0, 3),
                    'stdout_tail': build_proc.stdout.strip()[-2000:],
                    'stderr_tail': build_proc.stderr.strip()[-2000:],
                },
            }

    cmd = [lake_bin, 'env', 'lean', str(target_path)] if use_lake else [lean_bin, str(target_path)]
    display_cmd = ['lake', 'env', 'lean', str(target_path)] if use_lake else ['lean', str(target_path)]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    duration = round(time.monotonic() - t0, 3)

    combined_output = f'{proc.stdout}\n{proc.stderr}'
    compiler_sorry = bool(_COMPILER_SORRY_RE.search(combined_output))
    audit = dict(scan['audit'])
    audit['compiler_sorry_warning'] = compiler_sorry
    audit['clean'] = audit['clean'] and not compiler_sorry
    base_report['audit'] = audit

    if proc.returncode != 0:
        status = 'failed'
        verdict = 'inconclusive'
    elif not audit['clean']:
        status = 'audit_failed'
        verdict = 'inconclusive'
    else:
        status = 'passed'
        if claim_kind == 'refutation':
            verdict = 'refuted'
        elif claim_kind in ('universal_proof', 'equivalence'):
            verdict = 'proved'
        else:
            verdict = 'passed_unclassified'

    return {
        **base_report,
        'status': status,
        'verdict': verdict,
        'command': {
            'argv': display_cmd,
            'exit_code': proc.returncode,
            'duration_sec': duration,
            'stdout_tail': proc.stdout.strip()[-2000:],
            'stderr_tail': proc.stderr.strip()[-2000:],
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('target', type=Path, help='Path to the Lean 4 (.lean) file to verify')
    parser.add_argument('--project', type=Path, help='Lake project directory (auto-detected from ancestors if omitted)')
    parser.add_argument('--build-lib', action='store_true', help='Run `lake build` before checking the target file')
    parser.add_argument('--source', action='append', default=[], type=Path,
                        help='Kernel source file to hash for provenance (repeatable)')
    parser.add_argument('--claim-kind', default='unspecified',
                        choices=('universal_proof', 'refutation', 'equivalence', 'unspecified'))
    parser.add_argument('--precondition', action='append', default=[],
                        help='Required or discovered precondition for the proved invariant (repeatable)')
    parser.add_argument('--tcb', action='append', default=[], help='Explicit TCB modeling assumption (repeatable)')
    parser.add_argument('--counterexample', help='Short witness/counterexample description for refutation claims')
    parser.add_argument('--timeout', type=int, default=120, help='Subprocess timeout in seconds (default: 120)')
    parser.add_argument('--output', type=Path, help='Write verify-report.json to this path (else print JSON)')
    args = parser.parse_args(argv)

    try:
        report = check_lean(
            target=args.target,
            project=args.project,
            build_lib=args.build_lib,
            kernel_sources=args.source,
            claim_kind=args.claim_kind,
            preconditions=args.precondition,
            tcb_assumptions=args.tcb,
            counterexample=args.counterexample,
            timeout=args.timeout,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        sys.stdout.write(rendered)

    if report['status'] == 'passed':
        return 0
    if report['status'] == 'toolchain_missing':
        return 3
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
