#!/usr/bin/env python3
"""Scaffold an upstream PR or backport draft from one contribution decision, and check a filled draft.
Call: scaffold(decision, sections, upstream=None, guidance_urls=()), check(document);
CLI: python scripts/pr_draft.py scaffold --decisions result/contribution-decisions.json --hypothesis H
       --sections SECTIONS.json [--upstream UPSTREAM-STATUS.json] [--guidance-url URL ...] --output NEW.json
     python scripts/pr_draft.py check --draft DRAFT.json
Input: a pr_draft_main or backport_draft entry from diagnosis_state.py decisions, the draft's
sections (title, problem, change, reproducer, validation, patch) and optionally upstream_status.py output.
Output: the `upstream_pr_draft` evidence document for evidence/submissions/*.json (repository,
base_branch, title, body_markdown, guidance_urls, pending_checks) and a check report.
Requires: Standard library.

Details:
SECTIONS: {"title":TEXT, "problem":MD, "change":MD, "reproducer":MD, "validation":MD,
  "patch":MD (a fenced diff), optional "performance":MD, "affected":MD,
  "template":[[HEADING, [KEY...] or TEXT], ...]}.
With "template", the body uses the repository template's headings in order; each heading
holds the listed section keys (problem, change, validation, patch) or literal text (for
example "No." under a user-facing-change heading). Sections no heading holds are appended
under their own heading. Without it: Problem / Change / Validation / Patch. A backport body also states where the defect was fixed upstream
(fixed_in, fixed_commit) and the upgrade blocker. base_branch is the decision's target_branch.
pending_checks lists every draft check that is not pass or na (applies_cleanly, repo_style_test,
native_on_target, performance, template_body); an incomplete draft is still a valid draft.
Upstream rows cite only merged commits; references to unmerged pull requests are rejected.
check: required fields present, no TODO/TBD placeholders, no pull-request URL in the body
other than merged-commit links, and body sections non-empty.
CLI exit: 0=written or check ok, 1=check found problems, 2=input error.
"""

__all__ = ['scaffold', 'check']
import argparse
import json
from pathlib import Path
import re
import sys

DRAFT_CHECKS = ('applies_cleanly', 'repo_style_test', 'native_on_target', 'performance', 'template_body')
DEFAULT_HEADINGS = ('Problem', 'Change', 'Validation', 'Patch')
SECTION_KEYS = ('problem', 'change', 'reproducer', 'validation', 'performance', 'patch')
PLACEHOLDER = re.compile(r'\b(?:TODO|TBD|FIXME|XXX)\b|<fill[^>]*>', re.IGNORECASE)
PULL_URL = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+')
FIELDS = ('repository', 'base_branch', 'title', 'body_markdown', 'guidance_urls', 'pending_checks')


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


def scaffold(decision, sections, upstream=None, guidance_urls=()):
    if decision.get('decision') not in ('pr_draft_main', 'backport_draft'):
        raise ValueError('only pr_draft_main or backport_draft decisions get a PR draft')
    for key in ('repository', 'target_branch'):
        if not decision.get(key):
            raise ValueError(f'decision needs {key}')
    for key in ('title', 'problem', 'change', 'reproducer', 'validation', 'patch'):
        if not str(sections.get(key, '')).strip():
            raise ValueError(f'sections need {key}')
    blocks = dict(sections)
    context = _upstream_lines(decision, upstream)
    if sections.get('affected'):
        context.insert(0, sections['affected'])
    blocks['problem'] = '\n\n'.join([sections['problem'].strip(), *(['\n'.join(context)] if context else [])])
    blocks['validation'] = '\n\n'.join(p for p in (sections['reproducer'].strip(), sections['validation'].strip(),
                                                   str(sections.get('performance', '')).strip()) if p)
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
    return dict(kind='upstream_pr_draft', repository=decision['repository'], base_branch=decision['target_branch'],
                title=sections['title'].strip(), body_markdown='\n'.join(body).rstrip() + '\n',
                guidance_urls=list(guidance_urls), pending_checks=pending,
                decision=dict(hypothesis=decision.get('hypothesis'), decision=decision['decision'],
                              reason=decision.get('reason'), draft_status='incomplete' if pending else 'complete'))


def check(document):
    problems = []
    for key in FIELDS:
        if key not in document:
            problems.append('missing field: ' + key)
    body = document.get('body_markdown', '')
    for text in (document.get('title', ''), body):
        for match in PLACEHOLDER.finditer(text):
            problems.append(f'placeholder left: {match.group(0)!r}')
    for match in PULL_URL.finditer(body):
        problems.append(f'pull request reference {match.group(0)}: cite merged commits only')
    sections = re.split(r'^## ', body, flags=re.MULTILINE)[1:]
    for section in sections:
        heading, _, content = section.partition('\n')
        if not content.strip():
            problems.append(f'empty section: {heading.strip()}')
    if not isinstance(document.get('pending_checks', []), list):
        problems.append('pending_checks must be a list')
    return dict(status='ok' if not problems else 'problems', problems=problems,
                pending_checks=document.get('pending_checks', []))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('scaffold')
    make.add_argument('--decisions', required=True, type=Path)
    make.add_argument('--hypothesis', required=True)
    make.add_argument('--sections', required=True, type=Path)
    make.add_argument('--upstream', type=Path)
    make.add_argument('--guidance-url', action='append', default=[])
    make.add_argument('--output', required=True, type=Path)
    review = commands.add_parser('check')
    review.add_argument('--draft', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            result = check(json.loads(args.draft.read_text()))
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['status'] == 'ok' else 1
        decisions = json.loads(args.decisions.read_text())
        entry = next((d for d in decisions.get('decisions', []) if d.get('hypothesis') == args.hypothesis), None)
        if entry is None:
            raise ValueError(f'no decision for {args.hypothesis}')
        upstream = json.loads(args.upstream.read_text()) if args.upstream else None
        document = scaffold(entry, json.loads(args.sections.read_text()), upstream, args.guidance_url)
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
