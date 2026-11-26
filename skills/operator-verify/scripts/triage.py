#!/usr/bin/env python3
"""Classify an operator verification target and emit verify-request.json.

Call: triage(...); CLI: python scripts/triage.py --operator NAME --category resource_capacity --output verify-request.json
Input: operator identity (directly or via an operator-optimize handoff.json), semantic category
or free-text description, backend, and optional kernel source path.
Output: verify-request.json recording eligibility, route, and recommended Lean model modules.
Requires: Python standard library only.
"""
__all__ = ['triage', 'CATEGORIES']

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


CATEGORIES = {
    'index_and_mask': {
        'eligibility': 'lean_only',
        'route': 'operator-verify',
        'model_modules': ['AscendLean.UBModel', 'AscendLean.TritonModel'],
    },
    'resource_capacity': {
        'eligibility': 'lean_only',
        'route': 'operator-verify',
        'model_modules': ['AscendLean.UBModel'],
    },
    'pipeline_sync': {
        'eligibility': 'model_calibration',
        'route': 'operator-verify',
        'model_modules': ['AscendLean.PipeModel'],
    },
    'semantic_equivalence': {
        'eligibility': 'lean_only',
        'route': 'operator-verify',
        'model_modules': ['AscendLean.TritonModel'],
    },
    'numeric_rounding': {
        'eligibility': 'out_of_scope_numeric',
        'route': 'precision-compare',
        'model_modules': [],
    },
}

# Word-boundary regex patterns to prevent substring collisions (e.g. "race" in "trace", "tail" in "detail", "192" in "head_dim 192").
_CATEGORY_PATTERNS = (
    ('pipeline_sync', re.compile(
        r'\b(?:setflag|waitflag|hardevent|ibwait|ibset|mte2|mte3|pipe_[a-z0-9]+|data\s+race|race\s+condition|deadlock|pipebarrier|syncall)\b',
        re.I,
    )),
    ('resource_capacity', re.compile(
        r'\b(?:unified\s+buffer|ub\s+(?:overflow|overrun|budget|limit|alias\w*)|tbuf|ubcastsafe|'
        r'in-place\s+(?:[a-z0-9_:-]+\s+){0,3}(?:cast|alias\w*)|cast\s+in-place|'
        r'byte[- ]vs[- ]element|multibuffer|192\s*k(?:i?b)?|248\s*k(?:i?b)?|32b\s*align|sram|silent\s+(?:host-side\s+)?tile\s+clamp\w*)\b',
        re.I,
    )),
    # Specific semantic-equivalence phrases that contain the word "mask" must precede generic index_and_mask `\bmask\b`.
    ('semantic_equivalence', re.compile(
        r'\b(?:inverted\s+bool(?:ean)?(?:\s+[a-z0-9_]+){0,2}\s+mask|welford|ldl\^?t|log2e)\b',
        re.I,
    )),
    ('index_and_mask', re.compile(
        r'\b(?:mask|masked\s+load|boundary_check|tail\s+(?:block|tile|lane|padding|slot)|padding_option|tiling|int32\s+overflow|oob|out-of-bounds|updatemask|counter|unconditional\s+store|stride)\b',
        re.I,
    )),
    ('semantic_equivalence', re.compile(
        r'(?:-inf\b|\b(?:bitvec|bitwise|torch\.compile|lowering|fusion|megakernel|reduction|equivalence|nan|online\s+softmax|softmax|poison|transpose|inverted\s+bool(?:ean)?|silent\s+truncat\w*)\b)',
        re.I,
    )),
    ('numeric_rounding', re.compile(
        r'\b(?:rounding|bf16|fp16|fp64|ulp|rmse|cosine|mantissa|accumulation\s+order)\b',
        re.I,
    )),
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _infer_category(description):
    for name, pattern in _CATEGORY_PATTERNS:
        if pattern.search(description):
            return name
    return None


def triage(operator=None, category=None, description='', backend='unknown',
           source=None, handoff=None, claim_kind='universal_proof', mode='standalone'):
    """Build a schema_version=1 verify-request.json payload."""
    handoff_data = None
    handoff_path = None
    if handoff is not None:
        handoff_path = str(handoff)
        handoff_data = json.loads(Path(handoff).read_text(encoding='utf-8'))
        if not isinstance(handoff_data, dict):
            raise ValueError(f'handoff file must contain a JSON object: {handoff}')
        if not operator:
            op_info = handoff_data.get('operator')
            if isinstance(op_info, dict):
                operator = op_info.get('name')
            elif isinstance(op_info, str):
                operator = op_info

    if not operator:
        raise ValueError('operator name is required (pass --operator or --handoff)')

    resolved_cat = category.lower() if category else _infer_category(description)
    if resolved_cat not in CATEGORIES:
        choices = ', '.join(CATEGORIES)
        raise ValueError(f'unable to classify target; pass --category one of {choices}')

    source_str = None
    source_hash = None
    if source is not None:
        src_path = Path(source).resolve()
        if not src_path.is_file():
            raise FileNotFoundError(f'kernel source not found: {source}')
        source_str = str(src_path)
        source_hash = _sha256(src_path)

    meta = CATEGORIES[resolved_cat]
    target = {
        'operator': operator,
        'backend': backend,
        'source': source_str,
        'source_sha256': source_hash,
    }
    if handoff_data and isinstance(handoff_data.get('operator'), dict):
        if handoff_data['operator'].get('call_site'):
            target['call_site'] = handoff_data['operator']['call_site']

    return {
        'schema_version': 1,
        'mode': 'numericbridge_companion' if handoff_path else mode,
        'target': target,
        'category': resolved_cat,
        'eligibility': meta['eligibility'],
        'route': meta['route'],
        'claim_kind': claim_kind,
        'upstream_handoff': handoff_path,
        'upstream_issue': handoff_data.get('issue') if isinstance(handoff_data, dict) else None,
        'model_modules': list(meta['model_modules']),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--operator', help='Operator or kernel name')
    parser.add_argument('--category', choices=list(CATEGORIES), help='Explicit semantic category')
    parser.add_argument('--description', default='', help='Short issue description used when --category is omitted')
    parser.add_argument('--backend', default='unknown',
                        choices=('triton-ascend', 'ascendc', 'triton', 'cuda', 'unknown'))
    parser.add_argument('--source', type=Path, help='Optional kernel source file path')
    parser.add_argument('--handoff', type=Path, help='Optional operator-optimize handoff.json path')
    parser.add_argument('--mode', default='standalone', choices=('standalone', 'numericbridge_companion'),
                        help='Workflow entry mode (auto-set to numericbridge_companion when --handoff is passed)')
    parser.add_argument('--claim-kind', default='universal_proof',
                        choices=('universal_proof', 'refutation', 'equivalence'))
    parser.add_argument('--output', type=Path, help='Write verify-request.json to this path (else print JSON)')
    args = parser.parse_args(argv)

    try:
        payload = triage(
            operator=args.operator,
            category=args.category,
            description=args.description,
            backend=args.backend,
            source=args.source,
            handoff=args.handoff,
            claim_kind=args.claim_kind,
            mode=args.mode,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + '\n'
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
