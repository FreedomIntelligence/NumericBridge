#!/usr/bin/env python3
"""Scaffold an upstream PR or backport draft from one contribution decision, and check a filled draft.
Call: inspect_repo(repo, ref='HEAD'), scaffold(decision, sections, upstream=None, guidance_urls=(), repo=None, ref='HEAD', attribution=None), check(document);
CLI: python scripts/pr_draft.py scaffold --decisions result/contribution-decisions.json --hypothesis H
       --sections SECTIONS.json [--upstream UPSTREAM-STATUS.json] [--repo CLONE] [--ref REF]
       [--attribution-mode anonymous|nickname|github --attribution-name NAME | --request REQUEST.json]
       [--guidance-url URL ...] --output NEW.json
     python scripts/pr_draft.py inspect --repo CLONE [--ref REF]
     python scripts/pr_draft.py check --draft DRAFT.json
Input: a pr_draft_main or backport_draft entry from diagnosis_state.py decisions, the draft's
sections (title, problem, change, reproducer, validation, patch) and optionally upstream_status.py output.
Output: the `upstream_pr_draft` evidence document for evidence/submissions/*.json (repository,
base_branch, title, body_markdown, guidance_urls, pending_checks, template_source,
contribution_requirements) and a check report.
Requires: Standard library.

Details:
SECTIONS: {"title":TEXT, "problem":MD, "change":MD, "reproducer":MD, "validation":MD,
  "patch":MD (a fenced diff), optional "performance":MD, "affected":MD,
  "template":[[HEADING, [KEY...] or TEXT], ...]}.
With "template", the body uses the repository template's headings in order; each heading
holds the listed section keys (problem, change, validation, patch) or literal text (for
example "No." under a user-facing-change heading). Sections no heading holds are appended
under their own heading. Without a repository PR template, `scaffold` uses the pre-agreed
fallback format: Problem / Change / Validation / Patch and records
`template_source="numericbridge_default"` and `fallback_used=True` so maintainers know no
repo-specific PR template existed. When `--repo` is supplied, `inspect_repo` reads
`.github/PULL_REQUEST_TEMPLATE*`, `.github/pull_request_template.md`, `CONTRIBUTING.md`,
and `docs/contributing*` at `ref` (default `decision["target_branch"]`), extracts headings,
DCO/Signed-off-by and checklist hints into `contribution_requirements`, and validates that
repository templates match the scaffolded body headings and required sign-off/guidance fields.
A backport body also states where the defect was fixed upstream (fixed_in, fixed_commit)
and the upgrade blocker. `base_branch` is the decision's `target_branch`.
pending_checks lists every draft check that is not pass or na (applies_cleanly, repo_style_test,
native_on_target, performance, template_body); an incomplete draft is still a valid draft.
Upstream rows (where the defect is present or fixed) come from merged commits only; the body may
cite related issues and pull requests, open or merged, as related work.
check: required fields present, no TODO/TBD placeholders, and body sections non-empty.
CLI exit: 0=written or check ok, 1=check found problems, 2=input error.
"""

__all__ = ['inspect_repo', 'scaffold', 'check']
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

DRAFT_CHECKS = ('applies_cleanly', 'repo_style_test', 'native_on_target', 'performance', 'template_body')
DEFAULT_HEADINGS = ('Problem', 'Change', 'Validation', 'Patch')
SECTION_KEYS = ('problem', 'change', 'reproducer', 'validation', 'performance', 'patch')
PLACEHOLDER = re.compile(r'\b(?:TODO|TBD|FIXME|XXX)\b|<fill[^>]*>', re.IGNORECASE)
FIELDS = ('repository', 'base_branch', 'title', 'body_markdown', 'guidance_urls', 'pending_checks')
NUMERICBRIDGE_REPO_URL = 'https://github.com/FreedomIntelligence/NumericBridge'
NUMERICBRIDGE_CASES_URL = 'https://freedomintelligence.github.io/NumericBridge/index.html#/cases'
PR_TEMPLATE_CANDIDATES = (
    '.github/PULL_REQUEST_TEMPLATE.md',
    '.github/pull_request_template.md',
    'PULL_REQUEST_TEMPLATE.md',
)
CONTRIBUTING_CANDIDATES = (
    'CONTRIBUTING.md',
    '.github/CONTRIBUTING.md',
    'docs/contributing/overview.md',
    'docs/CONTRIBUTING.md',
)


def _git_ls(repo, ref, prefix):
    result = subprocess.run(
        ['git', '-C', str(repo), 'ls-tree', '-r', '--name-only', ref, '--', prefix],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _git_show(repo, ref, path):
    result = subprocess.run(
        ['git', '-C', str(repo), 'show', f'{ref}:{path}'],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _resolve_ref(repo, ref):
    for candidate in (ref, f'origin/{ref}', f'refs/tags/{ref}'):
        out = subprocess.run(
            ['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', candidate + '^{commit}'],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            return candidate, out.stdout.strip()
    raise ValueError(f'ref not found in {repo}: {ref}')


def _extract_headings(markdown):
    headings = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r'^#{2,3}\s+(.+?)\s*#*$', line)
        if match:
            headings.append(match.group(1).strip())
    return headings


def inspect_repo(repo, ref='HEAD'):
    """Inspect a local target-repo clone at `ref` for PR templates and contribution guides."""
    repo = Path(repo)
    resolved_ref, commit = _resolve_ref(repo, ref)
    template_paths = []
    for path in PR_TEMPLATE_CANDIDATES:
        if _git_show(repo, resolved_ref, path) is not None and path not in template_paths:
            template_paths.append(path)
    for path in _git_ls(repo, resolved_ref, '.github/PULL_REQUEST_TEMPLATE'):
        if path.lower().endswith('.md') and path not in template_paths:
            template_paths.append(path)
    contributing_paths = []
    for path in CONTRIBUTING_CANDIDATES:
        if _git_show(repo, resolved_ref, path) is not None and path not in contributing_paths:
            contributing_paths.append(path)

    template_headings = []
    if template_paths:
        text = _git_show(repo, resolved_ref, template_paths[0]) or ''
        template_headings = _extract_headings(text)

    guide_text = '\n'.join(_git_show(repo, resolved_ref, p) or '' for p in (*template_paths, *contributing_paths))
    requires_dco = bool(re.search(r'(?:Signed-off-by|\bDCO\b|git commit -s)', guide_text, re.IGNORECASE))
    requires_issue_link = bool(re.search(r'(?:Fixes\s+#|related\s+issue|issue\s+link)', guide_text, re.IGNORECASE))
    requires_tests = bool(re.search(r'(?:unit\s+test|pytest|regression\s+test|test\s+plan)', guide_text, re.IGNORECASE))
    requires_checklist = '- [ ]' in guide_text or '- [x]' in guide_text

    return {
        'ref': ref,
        'commit': commit,
        'has_pr_template': bool(template_paths),
        'pr_templates': template_paths,
        'template_headings': template_headings,
        'contributing_files': contributing_paths,
        'requires_dco': requires_dco,
        'requires_issue_link': requires_issue_link,
        'requires_tests': requires_tests,
        'requires_checklist': requires_checklist,
        'template_source': 'repository_template' if template_paths else 'numericbridge_default',
        'fallback_used': not bool(template_paths),
        'fallback_headings': list(DEFAULT_HEADINGS),
    }


def _normalize_attribution(value):
    if value is None:
        return {'mode': 'anonymous'}
    if isinstance(value, dict):
        if 'community_contribution' in value and isinstance(value['community_contribution'], dict):
            value = value['community_contribution']
        mode = value.get('attribution_mode', value.get('mode', 'anonymous'))
        name = value.get('attribution_name', value.get('name'))
    else:
        raise ValueError('attribution must be an object or None')
    if mode == 'anonymous' or not mode:
        return {'mode': 'anonymous'}
    if mode == 'github':
        if not isinstance(name, str) or not re.fullmatch(r'@[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', name.strip()):
            raise ValueError('github attribution_name must look like @username')
        return {'mode': 'github', 'name': name.strip()}
    if mode == 'nickname':
        if not isinstance(name, str) or not name.strip():
            raise ValueError('nickname attribution_name must be non-empty')
        return {'mode': 'nickname', 'name': name.strip()}
    raise ValueError('attribution mode must be anonymous, nickname, or github')


def _provenance_footer(credit):
    parts = [
        f'Diagnosed and verified with [NumericBridge]({NUMERICBRIDGE_REPO_URL})',
        f'[Case Library]({NUMERICBRIDGE_CASES_URL})',
    ]
    if credit.get('mode') in ('github', 'nickname') and credit.get('name'):
        parts.append(f"Discovered by {credit['name']}")
    return '---\n<sub>' + ' · '.join(parts) + '</sub>\n'


def _upstream_lines(decision, upstream):
    lines = []
    states = decision.get('upstream') or {}
    if states:
        lines.append('Upstream status (merged code only): ' + ', '.join(f'{k} {v}' for k, v in states.items()) + '.')
    if upstream:
        for row in upstream.get('refs', []):
            if row.get('commit'):
                lines.append(f"- `{row['ref']}` at `{row['commit'][:12]}`: {row.get('state', 'unknown')}")
            for commit in row.get('merged_commits', [])[:3]:
                lines.append(f"  - merged `{commit['commit'][:12]}` ({commit.get('date')}): {commit.get('subject')}")
    if decision['decision'] == 'backport_draft':
        fixed = decision.get('fixed_in') or 'an unreleased main commit'
        commit = f" (merge commit `{decision['fixed_commit']}`)" if decision.get('fixed_commit') else ''
        lines.append(f'Fixed upstream in {fixed}{commit}; this backport targets `{decision.get("target_branch")}`.')
        if decision.get('upgrade_blocker'):
            lines.append(f"Users cannot upgrade because: {decision['upgrade_blocker']}.")
    return lines


def scaffold(decision, sections, upstream=None, guidance_urls=(), repo=None, ref=None,
             attribution=None):
    if decision.get('decision') not in ('pr_draft_main', 'backport_draft'):
        raise ValueError('only pr_draft_main or backport_draft decisions get a PR draft')
    for key in ('repository', 'target_branch'):
        if not decision.get(key):
            raise ValueError(f'decision needs {key}')
    for key in ('title', 'problem', 'change', 'reproducer', 'validation', 'patch'):
        if not str(sections.get(key, '')).strip():
            raise ValueError(f'sections need {key}')
    credit = _normalize_attribution(attribution)
    repo_info = inspect_repo(repo, ref or decision['target_branch']) if repo is not None else None
    if repo_info is not None and repo_info['has_pr_template'] and not sections.get('template'):
        raise ValueError(
            f"target repo has PR template {repo_info['pr_templates'][0]}; provide sections['template'] "
            f"matching its headings: {repo_info['template_headings']}"
        )
    blocks = dict(sections)
    context = _upstream_lines(decision, upstream)
    if sections.get('affected'):
        context.insert(0, sections['affected'])
    blocks['problem'] = '\n\n'.join([sections['problem'].strip(), *(['\n'.join(context)] if context else [])])
    blocks['validation'] = '\n\n'.join(p for p in (sections['reproducer'].strip(), sections['validation'].strip(),
                                                   str(sections.get('performance', '')).strip()) if p)
    has_custom_template = bool(sections.get('template'))
    template = sections.get('template') or [[h, [h.lower()]] for h in DEFAULT_HEADINGS]
    placed, body = set(), []
    for item in template:
        if not (isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str)):
            raise ValueError('template entries are [HEADING, [KEY...] or TEXT]')
        heading, content = item
        if isinstance(content, str):
            text = content.strip()
        else:
            keys = list(content)
            if set(keys) - {'problem', 'change', 'validation', 'patch'} or placed & set(keys):
                raise ValueError(f'template keys must be problem/change/validation/patch, each once: {heading}')
            text = '\n\n'.join(blocks[k].strip() for k in keys)
            placed.update(keys)
        body.append(f'## {heading}\n\n{text}\n')
    for key in ('problem', 'change', 'validation', 'patch'):
        if key not in placed:
            body.append(f'## {key.title()}\n\n{blocks[key].strip()}\n')
    checks = decision.get('checks') or {}
    pending = [name for name in DRAFT_CHECKS if checks.get(name) not in ('pass', 'na')]
    if repo_info is not None:
        template_source = 'repository_template' if repo_info['has_pr_template'] else 'numericbridge_default'
        fallback_used = not repo_info['has_pr_template']
        contribution_requirements = {
            'checked': True,
            'ref': repo_info['ref'],
            'commit': repo_info['commit'],
            'pr_templates': repo_info['pr_templates'],
            'template_headings': repo_info['template_headings'],
            'contributing_files': repo_info['contributing_files'],
            'requires_dco': repo_info['requires_dco'],
            'requires_issue_link': repo_info['requires_issue_link'],
            'requires_tests': repo_info['requires_tests'],
            'requires_checklist': repo_info['requires_checklist'],
            'fallback_format': None if repo_info['has_pr_template'] else list(DEFAULT_HEADINGS),
        }
    else:
        template_source = 'repository_template' if has_custom_template else 'numericbridge_default'
        fallback_used = not has_custom_template
        contribution_requirements = {
            'checked': bool(guidance_urls or has_custom_template),
            'pr_templates': [],
            'contributing_files': [],
            'fallback_format': None if has_custom_template else list(DEFAULT_HEADINGS),
        }
    if fallback_used and checks.get('template_body') not in ('pass', 'na'):
        # Without a repo-specific template, template_body follows NumericBridge's pre-agreed fallback.
        pass
    body_text = '\n'.join(body).rstrip() + '\n'
    if repo_info is not None and repo_info['requires_dco']:
        if 'Signed-off-by' not in body_text and 'dco' not in body_text.lower():
            body_text += '\n## DCO Sign-off\n\nRequires `git commit -s` (`Signed-off-by`) on upstream filing.\n'
    if NUMERICBRIDGE_REPO_URL not in body_text:
        body_text += '\n' + _provenance_footer(credit)
    return dict(
        kind='upstream_pr_draft',
        repository=decision['repository'],
        base_branch=decision['target_branch'],
        title=sections['title'].strip(),
        body_markdown=body_text,
        guidance_urls=list(guidance_urls),
        pending_checks=pending,
        template_source=template_source,
        fallback_used=fallback_used,
        contribution_requirements=contribution_requirements,
        attribution=credit,
        decision=dict(
            hypothesis=decision.get('hypothesis'),
            decision=decision['decision'],
            reason=decision.get('reason'),
            draft_status='incomplete' if pending else 'complete',
        ),
    )


def check(document):
    problems = []
    for key in FIELDS:
        if key not in document:
            problems.append('missing field: ' + key)
    body = document.get('body_markdown', '')
    for text in (document.get('title', ''), body):
        for match in PLACEHOLDER.finditer(text):
            problems.append(f'placeholder left: {match.group(0)!r}')
    sections = re.split(r'^## ', body, flags=re.MULTILINE)[1:]
    headings = []
    for section in sections:
        heading, _, content = section.partition('\n')
        headings.append(heading.strip())
        if not content.strip():
            problems.append(f'empty section: {heading.strip()}')
    if not isinstance(document.get('pending_checks', []), list):
        problems.append('pending_checks must be a list')
    template_source = document.get('template_source')
    if template_source is not None and template_source not in ('repository_template', 'numericbridge_default'):
        problems.append('template_source must be "repository_template" or "numericbridge_default"')
    if template_source == 'numericbridge_default':
        for heading in DEFAULT_HEADINGS:
            if heading not in headings:
                problems.append(f'numericbridge_default format missing heading: {heading}')
    reqs = document.get('contribution_requirements')
    if reqs is not None:
        if not isinstance(reqs, dict):
            problems.append('contribution_requirements must be an object')
        else:
            for expected in reqs.get('template_headings') or []:
                if expected not in headings:
                    problems.append(f'missing repository template heading: {expected}')
            if reqs.get('requires_dco') and 'Signed-off-by' not in body and 'dco' not in body.lower():
                problems.append('target repository requires DCO / Signed-off-by note')
    if template_source == 'repository_template' and not document.get('guidance_urls'):
        if isinstance(reqs, dict) and reqs.get('checked') and 'commit' in reqs and not (
                reqs.get('pr_templates') or reqs.get('contributing_files')):
            problems.append('repository_template draft needs guidance_urls or contribution_requirements files')
    if 'template_source' in document and NUMERICBRIDGE_REPO_URL not in body:
        problems.append('missing NumericBridge project attribution link in body_markdown')
    doc_credit = document.get('attribution')
    if isinstance(doc_credit, dict) and doc_credit.get('mode') in ('github', 'nickname'):
        name = doc_credit.get('name')
        if not isinstance(name, str) or not name.strip():
            problems.append('attribution name required for named credit')
        elif name.strip() not in body:
            problems.append(f'named discoverer attribution {name.strip()!r} missing from body_markdown')
    return dict(status='ok' if not problems else 'problems', problems=problems,
                pending_checks=document.get('pending_checks', []))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    probe = commands.add_parser('inspect')
    probe.add_argument('--repo', required=True, type=Path)
    probe.add_argument('--ref', default='HEAD')
    make = commands.add_parser('scaffold')
    make.add_argument('--decisions', required=True, type=Path)
    make.add_argument('--hypothesis', required=True)
    make.add_argument('--sections', required=True, type=Path)
    make.add_argument('--upstream', type=Path)
    make.add_argument('--repo', type=Path)
    make.add_argument('--ref')
    make.add_argument('--request', type=Path, help='optional request.json to read community_contribution attribution')
    make.add_argument('--attribution-mode', choices=('anonymous', 'nickname', 'github'))
    make.add_argument('--attribution-name')
    make.add_argument('--guidance-url', action='append', default=[])
    make.add_argument('--output', required=True, type=Path)
    review = commands.add_parser('check')
    review.add_argument('--draft', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            print(json.dumps(inspect_repo(args.repo, args.ref), indent=2, ensure_ascii=False))
            return 0
        if args.command == 'check':
            result = check(json.loads(args.draft.read_text()))
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['status'] == 'ok' else 1
        decisions = json.loads(args.decisions.read_text())
        entry = next((d for d in decisions.get('decisions', []) if d.get('hypothesis') == args.hypothesis), None)
        if entry is None:
            raise ValueError(f'no decision for {args.hypothesis}')
        upstream = json.loads(args.upstream.read_text()) if args.upstream else None
        credit = None
        if args.attribution_mode:
            credit = {'mode': args.attribution_mode, 'name': args.attribution_name}
        elif args.request:
            credit = json.loads(args.request.read_text())
        document = scaffold(entry, json.loads(args.sections.read_text()), upstream, args.guidance_url,
                            repo=args.repo, ref=args.ref, attribution=credit)
        with args.output.open('x') as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        print(json.dumps(dict(status='written', output=str(args.output), pending_checks=document['pending_checks'],
                              review=check(document)['status']), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
