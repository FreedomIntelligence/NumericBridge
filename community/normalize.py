"""Normalize accepted inbox submissions and aggregate server-side operator optimization rounds."""

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from tools.operator_optimization import check as check_operator_optimization
from tools.pr_draft import check as check_pr_draft
from .records.catalog import Catalog
from .records.search import compile_index
from .records.submission import COLLECTIONS, DIGEST, encode


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def normalize_acceptance(value):
    """Normalize string or dict acceptance into (verdict, passed) without altering source values."""
    if value is None:
        return None, False
    if isinstance(value, str):
        verdict = value.strip().lower()
        return verdict, verdict in ('passed', 'pass', 'gap')
    if isinstance(value, dict):
        verdict = value.get('verdict')
        verdict_norm = verdict.strip().lower() if isinstance(verdict, str) else None
        passed = value.get('passed')
        if type(passed) is bool:
            return verdict_norm, passed
        return verdict_norm, verdict_norm in ('pass', 'passed', 'gap')
    return None, False


def _grade_variant(variant_report, performance_variants=None, name=None):
    cases = variant_report.get('cases') or {}
    within_bound = bool(variant_report.get('within_bound'))
    reproducible = variant_report.get('reproducible')
    ratios = [c.get('to_floor') for c in cases.values() if isinstance(c, dict) and _finite(c.get('to_floor'))]
    max_to_floor = max(ratios) if ratios else None
    errors = [c.get('error') for c in cases.values() if isinstance(c, dict) and _finite(c.get('error'))]
    max_error = max(errors) if errors else None
    shape_classes = sorted({c.get('shape_class') for c in cases.values() if isinstance(c, dict) and c.get('shape_class')})
    if not shape_classes:
        shape_classes = sorted(cases)

    missing_cases = list(variant_report.get('missing_cases') or [])
    has_timing = bool(isinstance(performance_variants, dict) and name and isinstance(performance_variants.get(name), dict))
    timing = performance_variants.get(name) if has_timing else None

    if not within_bound:
        tier = 'out_of_bound'
    elif reproducible is False:
        tier = 'unstable'
    elif max_to_floor is not None and max_to_floor <= 1.05 and not missing_cases and has_timing:
        tier = 'floor_matched'
    elif not missing_cases and has_timing:
        tier = 'within_bound_verified'
    else:
        tier = 'within_bound_partial'

    return {
        'tier': tier,
        'within_bound': within_bound,
        'reproducible': reproducible,
        'max_to_floor': max_to_floor,
        'max_error': max_error,
        'shape_classes': shape_classes,
        'missing_cases': missing_cases,
        'timing_measured': has_timing,
        'median_time': timing.get('median') if isinstance(timing, dict) else None,
        'median_ratio_to_baseline': timing.get('median_ratio_to_baseline') if isinstance(timing, dict) else None,
    }


def _recommend_variant(variant_rows):
    eligible = [v for v in variant_rows if v['within_bound'] and v['reproducible'] is not False]
    if not eligible:
        return {
            'status': 'no_within_bound_variant',
            'recommended_variant': None,
            'reason': 'No variant is reproducible and within the operator FP64 bound.',
        }

    def rank(row):
        tier_rank = {'floor_matched': 0, 'within_bound_verified': 1, 'within_bound_partial': 2}.get(row['tier'], 3)
        ratio = row['median_ratio_to_baseline']
        speed_key = ratio if _finite(ratio) else math.inf
        floor_key = row['max_to_floor'] if _finite(row['max_to_floor']) else math.inf
        err_key = row['max_error'] if _finite(row['max_error']) else math.inf
        return (tier_rank, len(row['missing_cases']), 0 if row['timing_measured'] else 1,
                round(floor_key, 9), speed_key, err_key, row['name'])

    best = min(eligible, key=rank)
    return {
        'status': 'recommended',
        'recommended_variant': best['name'],
        'candidate_id': best.get('candidate_id'),
        'tier': best['tier'],
        'median_ratio_to_baseline': best.get('median_ratio_to_baseline'),
        'max_to_floor': best.get('max_to_floor'),
    }


def assess_operator_optimization(payload, *, receipt_id=None):
    """Compute server-side tiers and recommendations for one operator_optimization payload."""
    check = check_operator_optimization(payload)
    if check['status'] != 'ok':
        raise ValueError('Invalid operator_optimization payload: ' + '; '.join(check['problems']))
    round_status = payload['round_status']
    kernel_check = payload.get('kernel_check') or {}
    perf = kernel_check.get('performance') or {}
    perf_variants = perf.get('variants') if perf.get('status') == 'measured' else None
    kc_variants = kernel_check.get('variants') or {}
    declared = {v['name']: v for v in (payload.get('variants') or []) if isinstance(v, dict) and v.get('name')}

    variant_rows = []
    for name in sorted(set(kc_variants) | set(declared)):
        report = kc_variants.get(name)
        meta = declared.get(name, {})
        if report is not None:
            graded = _grade_variant(report, perf_variants, name=name)
        else:
            graded = {
                'tier': 'unmeasured',
                'within_bound': bool(meta.get('within_bound')),
                'reproducible': None,
                'max_to_floor': None,
                'max_error': None,
                'shape_classes': [],
                'missing_cases': ['kernel_check'],
                'timing_measured': False,
                'median_time': None,
                'median_ratio_to_baseline': None,
            }
        variant_rows.append({
            'name': name,
            'candidate_id': meta.get('candidate_id'),
            'end_to_end': meta.get('end_to_end'),
            **graded,
        })

    recommendation = (_recommend_variant(variant_rows) if kc_variants
                      else {'status': 'handoff_open', 'recommended_variant': None,
                            'reason': 'No kernel_check measurements attached yet.'})
    return {
        'schema_version': 1,
        'receipt_id': receipt_id,
        'issue': payload['issue'],
        'operator': payload['operator'],
        'entry': payload['entry'],
        'reading': payload['reading'],
        'round_status': round_status,
        'bound': kernel_check.get('bound'),
        'dtype': kernel_check.get('dtype'),
        'baseline': kernel_check.get('baseline'),
        'end_to_end': payload.get('end_to_end'),
        'open_items': payload.get('open_items'),
        'variants': variant_rows,
        'recommendation': recommendation,
    }


def aggregate_operator_rounds(rounds):
    """Aggregate multiple operator_optimization rounds for one issue/operator on the server."""
    ordered = list(rounds)
    if not ordered:
        raise ValueError('At least one operator optimization round is required')
    assessed = [assess_operator_optimization(item['payload'], receipt_id=item.get('receipt_id'))
                if isinstance(item, dict) and 'payload' in item
                else assess_operator_optimization(item)
                for item in ordered]
    latest = assessed[-1]
    merged_variants = {}
    for row in assessed:
        for variant in row['variants']:
            merged_variants[variant['name']] = dict(variant, receipt_id=row.get('receipt_id'))
    variant_list = [merged_variants[name] for name in sorted(merged_variants)]
    return {
        'schema_version': 1,
        'issue': latest['issue'],
        'operator': latest['operator'],
        'entry': latest['entry'],
        'reading': latest['reading'],
        'round_count': len(assessed),
        'latest_round_status': latest['round_status'],
        'open_items': latest.get('open_items'),
        'variants': variant_list,
        'recommendation': _recommend_variant(variant_list) if any(v['tier'] != 'unmeasured' for v in variant_list) else latest['recommendation'],
        'rounds': assessed,
    }


def normalize_draft(draft, *, receipt_id=None):
    """Produce a standardized maintainer review summary from a validated inbox submission."""
    additions = draft.get('additions') or {}
    evaluations = (additions.get('evaluations') or {}).get('evaluations') or []
    repairs = (additions.get('repairs') or {}).get('repairs') or []
    library_groups = (additions.get('repairs') or {}).get('library_groups') or []
    relations = additions.get('relations') or {}
    entities = relations.get('entities') or []
    links = relations.get('links') or []
    evidence = additions.get('evidence') or {}

    cases = [e for e in entities if e.get('kind') == 'case']
    issues = [e for e in entities if e.get('kind') == 'issue']
    candidates = [e for e in entities if e.get('kind') == 'candidate']
    verifications = [e for e in entities if e.get('kind') == 'verification']
    workloads = [e for e in entities if e.get('kind') == 'workload']

    eval_summaries = []
    for ev in evaluations:
        verdict, passed = normalize_acceptance(ev.get('acceptance'))
        sides = []
        for s in ev.get('sides') or []:
            settings = s.get('settings') or {}
            sides.append({
                'id': s.get('id'),
                'hardware': s.get('hardware'),
                'frameworks': s.get('frameworks') or [],
                'libraries': [{'name': l.get('name'), 'version': l.get('version'), 'commit': l.get('commit')}
                              for l in (s.get('libraries') or [])],
                'has_runtime_environment': 'runtime_environment' in settings,
            })
        eval_summaries.append({
            'id': ev.get('id'),
            'date': ev.get('date'),
            'task': ev.get('task'),
            'status': ev.get('status'),
            'model': ev.get('model'),
            'acceptance_raw': ev.get('acceptance'),
            'acceptance_verdict': verdict,
            'acceptance_passed': passed,
            'sides': sides,
            'comparison_ids': [c.get('id') for c in (ev.get('comparisons') or [])],
        })

    pr_drafts = []
    operator_optimizations = []
    other_evidence = []
    for path in sorted(evidence):
        doc = evidence[path]
        if isinstance(doc, dict) and 'upstream_pr_draft' in doc:
            pr = doc['upstream_pr_draft']
            check = check_pr_draft(pr)
            pr_drafts.append({
                'evidence_path': path,
                'repository': pr.get('repository'),
                'base_branch': pr.get('base_branch'),
                'title': pr.get('title'),
                'pending_checks': list(pr.get('pending_checks') or []),
                'template_source': pr.get('template_source'),
                'fallback_used': pr.get('fallback_used'),
                'decision': pr.get('decision'),
                'check_status': check['status'],
                'check_problems': check['problems'],
            })
        elif isinstance(doc, dict) and doc.get('kind') == 'upstream_pr_draft':
            # Historical/flat PR draft evidence submitted directly as top-level document.
            check = check_pr_draft(doc)
            pr_drafts.append({
                'evidence_path': path,
                'repository': doc.get('repository'),
                'base_branch': doc.get('base_branch'),
                'title': doc.get('title'),
                'pending_checks': list(doc.get('pending_checks') or []),
                'template_source': doc.get('template_source'),
                'fallback_used': doc.get('fallback_used'),
                'decision': doc.get('decision'),
                'check_status': check['status'],
                'check_problems': check['problems'],
                'legacy_flat_wrapper': True,
            })
        elif isinstance(doc, dict) and 'operator_optimization' in doc:
            assessed = assess_operator_optimization(doc['operator_optimization'], receipt_id=receipt_id)
            assessed['evidence_path'] = path
            operator_optimizations.append(assessed)
        elif isinstance(doc, dict) and doc.get('kind') == 'operator-optimization':
            assessed = assess_operator_optimization(doc, receipt_id=receipt_id)
            assessed['evidence_path'] = path
            assessed['legacy_flat_wrapper'] = True
            operator_optimizations.append(assessed)
        else:
            if isinstance(doc, dict) and isinstance(doc.get('operator_optimization_handoff'), dict):
                handoff_doc = doc['operator_optimization_handoff']
                for op in handoff_doc.get('operators') or []:
                    if not isinstance(op, dict) or not op.get('issue') or not op.get('verdict'):
                        continue
                    v = op['verdict']
                    legacy_payload = {
                        'kind': 'operator-optimization',
                        'schema_version': 1,
                        'issue': op['issue'],
                        'operator': {'name': op.get('name')},
                        'entry': op.get('entry') or 'optional',
                        'reading': v.get('reading') or 'not_the_source',
                        'round_status': 'handoff_open',
                        'shapes': op.get('shapes'),
                        'open_items': {
                            'remaining_distance': op.get('remaining_distance'),
                            'variants_not_yet_tried': op.get('variants_not_yet_tried') or [],
                            'next_step': op.get('next_step'),
                        },
                    }
                    assessed = assess_operator_optimization(legacy_payload, receipt_id=receipt_id)
                    assessed['evidence_path'] = path
                    assessed['legacy_handoff_wrapper'] = True
                    operator_optimizations.append(assessed)
            # Also inspect nested paired_operator_and_original_workload.kernel_check when present (pre-decoupling shape).
            diag = doc.get('diagnostic_evidence') if isinstance(doc, dict) else None
            if isinstance(diag, dict):
                for section_key, section in diag.items():
                    paired = section.get('paired_operator_and_original_workload') if isinstance(section, dict) else None
                    if isinstance(paired, dict) and isinstance(paired.get('kernel_check'), dict):
                        kc = paired['kernel_check']
                        legacy_payload = {
                            'kind': 'operator-optimization',
                            'schema_version': 1,
                            'issue': f'diagnostic:{section_key}',
                            'operator': {'name': kc.get('operator') or section_key},
                            'entry': 'optional',
                            'reading': 'not_the_source',
                            'round_status': 'round_delivered',
                            'kernel_check': kc,
                            'end_to_end': paired.get('paired_workload') or {'status': 'not_measured', 'reason': 'not recorded'},
                            'variants': [{'name': name, 'within_bound': bool(row.get('within_bound')), 'candidate_id': None}
                                         for name, row in (kc.get('variants') or {}).items()],
                        }
                        assessed = assess_operator_optimization(legacy_payload, receipt_id=receipt_id)
                        assessed['evidence_path'] = path
                        assessed['legacy_diagnostic_wrapper'] = True
                        operator_optimizations.append(assessed)
            other_evidence.append({
                'evidence_path': path,
                'keys': sorted(doc.keys()) if isinstance(doc, dict) else [],
                'has_operator_optimization_handoff': isinstance(doc, dict) and 'operator_optimization_handoff' in doc,
            })

    # Also detect unindexed records (declared outside any case.includes).
    included = {
        (l['to']['kind'], l['to']['id'])
        for l in links
        if l.get('relation') == 'includes' and (l.get('from') or {}).get('kind') == 'case'
    }
    declared_members = (
        [('comparison', e['id']) for e in evaluations]
        + [('repair', r['id']) for r in repairs]
        + [(e['kind'], e['id']) for e in entities if e.get('kind') != 'case']
    )
    unindexed = [f'{kind}:{identity}' for kind, identity in declared_members if (kind, identity) not in included]

    issue_by_classification = {}
    for item in issues:
        cls = (item.get('details') or {}).get('classification') or 'unknown'
        issue_by_classification[cls] = issue_by_classification.get(cls, 0) + 1

    return {
        'schema_version': 1,
        'receipt_id': receipt_id,
        'draft_schema_version': draft.get('schema_version'),
        'title': draft.get('title'),
        'attribution': draft.get('attribution'),
        'base_commit': draft.get('base_commit'),
        'supersedes': list(draft.get('supersedes') or []),
        'counts': {
            'evaluations': len(evaluations),
            'repairs': len(repairs),
            'library_groups': len(library_groups),
            'cases': len(cases),
            'issues': len(issues),
            'candidates': len(candidates),
            'verifications': len(verifications),
            'workloads': len(workloads),
            'links': len(links),
            'evidence_files': len(evidence),
            'upstream_pr_drafts': len(pr_drafts),
            'operator_optimizations': len(operator_optimizations),
        },
        'cases': [{'id': c.get('id'), 'category': (c.get('details') or {}).get('category'),
                   'request': (c.get('details') or {}).get('request')} for c in cases],
        'evaluations': eval_summaries,
        'repairs': [{'id': r.get('id'), 'status': r.get('status'), 'affected_library': r.get('affected_library'),
                     'library_group_id': r.get('library_group_id')} for r in repairs],
        'issue_classifications': issue_by_classification,
        'candidates': [{'id': c.get('id'), 'title': (c.get('details') or {}).get('title'),
                        'solution_type': (c.get('details') or {}).get('solution_type'),
                        'decision': (c.get('details') or {}).get('decision'),
                        'target': (c.get('details') or {}).get('target'),
                        'has_upstream': 'upstream' in (c.get('details') or {})} for c in candidates],
        'upstream_pr_drafts': pr_drafts,
        'operator_optimizations': operator_optimizations,
        'other_evidence': other_evidence,
        'unindexed_records': unindexed,
    }


def apply_receipt(repo, inbox, receipt_id, output_dir, *, base_ref='HEAD'):
    """Materialize an accepted/latest receipt onto a clean site directory and emit normalized metadata."""
    from .review import read_receipt

    if not re.fullmatch(r'submission-[0-9a-f]{64}', receipt_id or ''):
        raise ValueError('Invalid receipt ID')
    _, raw = read_receipt(Path(inbox), receipt_id)
    draft = json.loads(raw)
    additions = draft.get('additions') or {}

    repo = Path(repo).resolve()
    out = Path(output_dir).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Output directory must be empty or new: {out}')
    out.mkdir(parents=True, exist_ok=True)

    from .records.submission import Submissions
    sub = Submissions(repo, out / '.tmp-drafts', base_ref=base_ref)
    base = sub.preview(
        draft['title'],
        additions,
        attribution_mode=(draft.get('attribution') or {}).get('mode', 'anonymous'),
        attribution_name=(draft.get('attribution') or {}).get('name'),
        replaces=draft.get('supersedes') or (),
    )['base_commit']

    site_out = out / 'site'
    with sub.checkout(base) as root:
        files = sub.apply(root, additions)
        import shutil
        shutil.copytree(root / 'community/site', site_out)
        # Re-verify compiled index and catalog on materialized site.
        Catalog(site_out)
        compile_index(Catalog(site_out))

    normalized = normalize_draft(draft, receipt_id=receipt_id)
    normalized['materialized_base_commit'] = base
    normalized['changed_files'] = sorted(files)
    (out / 'normalized.json').write_bytes(encode(normalized))
    return normalized
