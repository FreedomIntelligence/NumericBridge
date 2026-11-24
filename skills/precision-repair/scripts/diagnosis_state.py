#!/usr/bin/env python3
"""Track diagnosis hypotheses, checks and stop conditions in diagnosis.json; render issues.md.
Call: python scripts/diagnosis_state.py init|add|next|record|conclude|decide|audit|decisions|render --state diagnosis.json ...
Input: acceptance/triage reports, hypotheses and check results (label, finding, evidence).
Output: Durable state, the next open check or a stop decision, and an issues.md rendering.
Requires: Standard library; atomic rewrites; ruled-out hypotheses are never proposed again.

Details:
init --state S --acceptance A.json [--triage T.json] [--case NAME] [--max-checks N]
  Creates S (refuses to overwrite). Triage candidates become hypotheses H1..Hn in
  triage order with their check, cost and evidence.
add --state S --statement TEXT --check TEXT [--cost cpu|replay|native]
  [--source manual|community|localize|replay] [--parent H]  -> prints the new ID.
next --state S -> {"action":"check",hypothesis} for the first open hypothesis;
  {"action":"refine",supported:[...]} when only supported-unconfirmed hypotheses
  without open children remain (add a narrower child or run interventions);
  {"action":"stop",reason} when a primary cause is confirmed, a config mismatch
  was found (rerun comparison after correcting setup), max checks are spent, or
  nothing actionable remains.
record --state S --hypothesis H --label LABEL --finding TEXT [--evidence P ...]
  [--control TEXT] [--run-command TEXT] [--status STATUS] [--blocker access|budget|no_actionable_check]
  Label -> status: not_reproduced/ruled_out -> ruled_out; primary_cause_confirmed,
  secondary_factor_confirmed -> confirmed; unresolved -> blocked; replay_invalid ->
  open; other labels -> supported. --status overrides. A blocked hypothesis needs
  --blocker; until one is recorded, next keeps returning it as a check.
conclude --state S --classification setup_capture|unsupported|expected_policy|
  inherent_difference|supported_defect|unresolved --summary TEXT [--next-check TEXT]
decide --state S --hypothesis H --ownership OWNER --decision DECISION --reason REASON
  [--repository R] [--component C] [--target-branch B] [--upstream-main ST]
  [--upstream-release ST] [--upstream-pinned ST] [--pinned-ref REF] [--fixed-in VERSION]
  [--fixed-commit SHA] [--upgrade-blocker TEXT] [--draft PATH] [--check-item NAME=STATE ...]
  One contribution decision per confirmed or blocked hypothesis (replaces an earlier one).
  OWNER: library_defect|numerical_policy|environment|adapter|unlocalized.
  DECISION: pr_draft_main|backport_draft|record_only|no_action.
  REASON: still_present|fixed_upstream_backport|fixed_upstream_no_target|reference_semantics|
  benefit_measured|no_measured_benefit|matches_reference|environment_only|adapter_only|
  not_reproduced|unlocalized. ST: present|fixed|absent|unknown.
  PR-draft checks (NAME=pass|fail|na): applies_cleanly, repo_style_test,
  native_on_target, performance, template_body; a missing or failed check makes
  the draft incomplete.
audit --state S -> {"status":"ok"|"incomplete", problems:[...], drafts:"complete"|"incomplete"|"none",
  incomplete_drafts:[...]}; status is incomplete only for problems: a confirmed/blocked
  hypothesis without a decision, a blocked one without a blocker, a draft decision without
  repository/branch/draft, or no conclusion. A draft with missing checks does not fail
  the audit: it is listed in incomplete_drafts and submitted marked incomplete.
decisions --state S --output result/contribution-decisions.json  (derived; overwritten)
render --state S [--output issues.md]  (derived file; overwritten)
Labels: config_mismatch, replay_invalid, local_source_candidate, propagation_candidate,
amplification_supported, local_source_confirmed, primary_cause_confirmed,
secondary_factor_confirmed, not_reproduced, ruled_out, unresolved.
Exit: 0=ok (including next returning stop; read "action"), 1=audit found problems, 2=input error.
"""

__all__ = ['init', 'add', 'next_action', 'record', 'conclude', 'decide', 'audit', 'render']
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

LABELS = ('config_mismatch', 'replay_invalid', 'local_source_candidate', 'propagation_candidate',
          'amplification_supported', 'local_source_confirmed', 'primary_cause_confirmed',
          'secondary_factor_confirmed', 'not_reproduced', 'ruled_out', 'unresolved')
STATUS = {'not_reproduced': 'ruled_out', 'ruled_out': 'ruled_out', 'primary_cause_confirmed': 'confirmed',
          'secondary_factor_confirmed': 'confirmed', 'unresolved': 'blocked', 'replay_invalid': 'open'}
STATUSES = ('open', 'supported', 'confirmed', 'ruled_out', 'blocked')
CLASSES = ('setup_capture', 'unsupported', 'expected_policy', 'inherent_difference', 'supported_defect', 'unresolved')
COSTS = ('cpu', 'replay', 'native')
BLOCKERS = ('access', 'budget', 'no_actionable_check')
OWNERS = ('library_defect', 'numerical_policy', 'environment', 'adapter', 'unlocalized')
DECISIONS = ('pr_draft_main', 'backport_draft', 'record_only', 'no_action')
REASONS = ('still_present', 'fixed_upstream_backport', 'fixed_upstream_no_target', 'reference_semantics',
           'benefit_measured', 'no_measured_benefit', 'matches_reference', 'environment_only', 'adapter_only',
           'not_reproduced', 'unlocalized')
UPSTREAM = ('present', 'fixed', 'absent', 'unknown')
DRAFT_CHECKS = ('applies_cleanly', 'repo_style_test', 'native_on_target', 'performance', 'template_body')
DRAFT_DECISIONS = ('pr_draft_main', 'backport_draft')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _read(path):
    with open(path) as stream:
        state = json.load(stream)
    if state.get('kind') != 'diagnosis' or state.get('schema_version') != 1:
        raise ValueError('not a diagnosis state file')
    return state


def _write(path, state):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    tmp.replace(path)


def _hypothesis(state, hid):
    for h in state['hypotheses']:
        if h['id'] == hid:
            return h
    raise ValueError('unknown hypothesis: ' + hid)


def init(path, acceptance, triage=None, case=None, max_checks=None):
    if Path(path).exists():
        raise ValueError('state exists: ' + str(path))
    state = dict(kind='diagnosis', schema_version=1, case=case, created=_now(), max_checks=max_checks,
                 trigger=dict(verdict=acceptance.get('verdict'), profile=acceptance.get('profile'),
                              failing=acceptance.get('failing', []), route=acceptance.get('route')),
                 hypotheses=[], checks=[], conclusion=None)
    for c in (triage or {}).get('candidates', []):
        add(state, c['cause'], c['check'], c.get('cost', 'native'), 'triage', evidence=c.get('evidence'))
    _write(path, state)
    return state


def add(state, statement, check, cost='native', source='manual', parent=None, evidence=None):
    if cost not in COSTS:
        raise ValueError('cost must be cpu|replay|native')
    if parent is not None:
        _hypothesis(state, parent)
    hid = f"H{len(state['hypotheses']) + 1}"
    state['hypotheses'].append(dict(id=hid, statement=statement, check=check, cost=cost, source=source,
                                    parent=parent, status='open', label=None,
                                    **({'evidence': evidence} if evidence is not None else {})))
    return hid


def next_action(state):
    hyps = state['hypotheses']
    if any(h['label'] == 'config_mismatch' for h in hyps):
        return dict(action='stop', reason='config_mismatch: correct setup or capture, then rerun the comparison')
    primary = [h['id'] for h in hyps if h['label'] == 'primary_cause_confirmed']
    if primary:
        return dict(action='stop', reason='primary cause confirmed', hypotheses=primary)
    if state.get('max_checks') and len(state['checks']) >= state['max_checks']:
        return dict(action='stop', reason='check budget spent')
    open_ = [h for h in hyps if h['status'] == 'open']
    if open_:
        return dict(action='check', hypothesis=open_[0])
    unblocked = [h for h in hyps if h['status'] == 'blocked' and not h.get('blocker')]
    if unblocked:
        return dict(action='check', hypothesis=unblocked[0],
                    reason='blocked without a recorded blocker: run its next check, or record access, budget '
                           'or no_actionable_check')
    children = {h['parent'] for h in hyps if h['parent']}
    supported = [h for h in hyps if h['status'] == 'supported' and h['id'] not in children]
    if supported:
        return dict(action='refine', supported=supported,
                    reason='add a narrower child hypothesis or run interventions for a supported hypothesis')
    secondary = [h['id'] for h in hyps if h['status'] == 'confirmed']
    return dict(action='stop', reason=('secondary factors confirmed; unless acceptance is restored, open a child '
                                       'hypothesis for the remaining difference before delivering')
                if secondary else 'no actionable hypothesis remains',
                hypotheses=secondary)


def record(state, hid, label, finding, evidence=(), control=None, command=None, status=None, blocker=None):
    if label not in LABELS:
        raise ValueError('unknown label: ' + label)
    h = _hypothesis(state, hid)
    status = status or STATUS.get(label, 'supported')
    if status not in STATUSES:
        raise ValueError('unknown status: ' + status)
    if blocker is not None and blocker not in BLOCKERS:
        raise ValueError('blocker must be one of ' + '|'.join(BLOCKERS))
    if status == 'blocked':
        h['blocker'] = blocker
    else:
        h.pop('blocker', None)
    cid = f"C{len(state['checks']) + 1}"
    state['checks'].append(dict(id=cid, hypothesis=hid, at=_now(), label=label, finding=finding,
                                evidence=list(evidence), control=control, command=command))
    h.update(status=status, label=label)
    return cid


def conclude(state, classification, summary, next_check=None):
    if classification not in CLASSES:
        raise ValueError('classification must be one of ' + '|'.join(CLASSES))
    state['conclusion'] = dict(classification=classification, summary=summary, next_check=next_check, at=_now())


def decide(state, hid, ownership, decision, reason, repository=None, component=None, target_branch=None,
           upstream=None, pinned_ref=None, fixed_in=None, fixed_commit=None, upgrade_blocker=None,
           draft=None, checks=None):
    h = _hypothesis(state, hid)
    for value, allowed, name in ((ownership, OWNERS, 'ownership'), (decision, DECISIONS, 'decision'),
                                 (reason, REASONS, 'reason')):
        if value not in allowed:
            raise ValueError(f'{name} must be one of ' + '|'.join(allowed))
    upstream = dict(upstream or {})
    if set(upstream) - {'main', 'latest_release', 'pinned'} or not set(upstream.values()) <= set(UPSTREAM):
        raise ValueError('upstream states use main, latest_release, pinned with ' + '|'.join(UPSTREAM))
    checks = dict(checks or {})
    if set(checks) - set(DRAFT_CHECKS) or not set(checks.values()) <= {'pass', 'fail', 'na'}:
        raise ValueError('draft checks use ' + ', '.join(DRAFT_CHECKS) + ' with pass|fail|na')
    if fixed_commit is not None and not (len(fixed_commit) == 40 and all(c in '0123456789abcdef' for c in fixed_commit)):
        raise ValueError('fixed commit must be a full lowercase SHA-1')
    entry = dict(hypothesis=hid, statement=h['statement'], status=h['status'], ownership=ownership,
                 decision=decision, reason=reason, repository=repository, component=component,
                 target_branch=target_branch, upstream=upstream, pinned_ref=pinned_ref, fixed_in=fixed_in,
                 fixed_commit=fixed_commit, upgrade_blocker=upgrade_blocker, draft=draft, checks=checks, at=_now())
    if decision in DRAFT_DECISIONS:
        entry['draft_status'] = 'complete' if all(checks.get(k) in ('pass', 'na') for k in DRAFT_CHECKS) else 'incomplete'
    state.setdefault('decisions', {})[hid] = entry
    return entry


def audit(state):
    problems, incomplete = [], []
    decisions = state.get('decisions', {})
    for h in state['hypotheses']:
        if h['status'] == 'blocked' and not h.get('blocker'):
            problems.append(f"{h['id']}: blocked without a blocker; run its next check or record why none is possible")
        if h['status'] in ('confirmed', 'blocked') and h['id'] not in decisions:
            problems.append(f"{h['id']}: no contribution decision")
    for hid, d in decisions.items():
        if d['decision'] in DRAFT_DECISIONS:
            missing = [k for k in ('repository', 'target_branch', 'draft') if not d.get(k)]
            if missing:
                problems.append(f"{hid}: {d['decision']} needs " + ', '.join(missing))
            if d.get('draft_status') != 'complete':
                incomplete.append(dict(hypothesis=hid, missing=[k for k in DRAFT_CHECKS
                                                                if d['checks'].get(k) not in ('pass', 'na')]))
        if d['ownership'] in ('library_defect', 'numerical_policy') and not d.get('repository'):
            problems.append(f'{hid}: library ownership needs a repository')
        if d['reason'] in ('fixed_upstream_backport', 'fixed_upstream_no_target') and not (d.get('fixed_in') or d.get('fixed_commit')):
            problems.append(f'{hid}: fixed upstream needs fixed_in or fixed_commit')
    if not state.get('conclusion'):
        problems.append('no conclusion recorded')
    drafts = [d for d in decisions.values() if d['decision'] in DRAFT_DECISIONS]
    return dict(status='ok' if not problems else 'incomplete', problems=problems,
                drafts='none' if not drafts else 'incomplete' if incomplete else 'complete',
                incomplete_drafts=incomplete)


def export_decisions(state):
    return dict(kind='contribution-decisions', schema_version=1, case=state.get('case'),
                decisions=[state.get('decisions', {})[h['id']] for h in state['hypotheses']
                           if h['id'] in state.get('decisions', {})],
                audit=audit(state))


def render(state):
    t = state['trigger']
    lines = [f"# Issue: {state.get('case') or 'diagnosis'}", '',
             f"- Trigger: acceptance `{t.get('verdict')}` ({t.get('profile')}), failing: {', '.join(t.get('failing') or []) or 'none'}"]
    c = state.get('conclusion')
    if c:
        lines += [f"- Conclusion: **{c['classification']}** — {c['summary']}"]
        if c.get('next_check'):
            lines.append(f"- Next check: {c['next_check']}")
    nxt = next_action(state)
    lines += [f"- Workflow state: {nxt['action']}" + (f" ({nxt.get('reason')})" if nxt.get('reason') else ''), '',
              '## Hypotheses', '', '| ID | Status | Label | Statement | Check | Cost |', '|---|---|---|---|---|---|']
    for h in state['hypotheses']:
        statement = h['statement'] + (f" (child of {h['parent']})" if h['parent'] else '')
        status = h['status'] + (f" ({h['blocker']})" if h.get('blocker') else '')
        lines.append(f"| {h['id']} | {status} | {h['label'] or ''} | {statement} | {h['check']} | {h['cost']} |")
    lines += ['', '## Checks', '']
    for k in state['checks']:
        ev = ', '.join(f'[{Path(p).name}]({p})' for p in k['evidence'])
        lines.append(f"- {k['id']} ({k['at']}) {k['hypothesis']} → `{k['label']}`: {k['finding']}"
                     + (f" Control: {k['control']}." if k.get('control') else '') + (f" Evidence: {ev}" if ev else ''))
    decisions = state.get('decisions', {})
    if decisions:
        lines += ['', '## Contribution decisions', '', '| ID | Ownership | Decision | Reason | Target | Draft |',
                  '|---|---|---|---|---|---|']
        for hid, d in decisions.items():
            target = ' '.join(x for x in (d.get('repository'), d.get('target_branch')) if x)
            lines.append(f"| {hid} | {d['ownership']} | {d['decision']} | {d['reason']} | {target} | "
                         f"{d.get('draft_status', '')} |")
    ruled = [h for h in state['hypotheses'] if h['status'] == 'ruled_out']
    if ruled:
        lines += ['', '## Ruled out', ''] + [f"- {h['id']}: {h['statement']}" for h in ruled]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('init', 'add', 'next', 'record', 'conclude', 'decide', 'audit',
                                             'decisions', 'render'))
    parser.add_argument('--state', required=True)
    for name in ('acceptance', 'triage', 'case', 'statement', 'check', 'source', 'parent', 'hypothesis',
                 'label', 'finding', 'control', 'status', 'classification', 'summary', 'next-check', 'output',
                 'blocker', 'ownership', 'decision', 'reason', 'repository', 'component', 'target-branch',
                 'upstream-main', 'upstream-release', 'upstream-pinned', 'pinned-ref', 'fixed-in', 'fixed-commit',
                 'upgrade-blocker', 'draft'):
        parser.add_argument('--' + name)
    parser.add_argument('--run-command', dest='run_command')
    parser.add_argument('--cost', default='native')
    parser.add_argument('--max-checks', type=int)
    parser.add_argument('--evidence', action='append', default=[])
    parser.add_argument('--check-item', '--check-state', dest='check_items', action='append', default=[],
                        help='PR-draft check NAME=pass|fail|na (decide)')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            load = lambda p: json.loads(Path(p).read_text())
            init(args.state, load(args.acceptance), load(args.triage) if args.triage else None, args.case, args.max_checks)
            print(json.dumps(next_action(_read(args.state)), ensure_ascii=False))
            return 0
        state = _read(args.state)
        if args.command == 'add':
            print(add(state, args.statement, args.check, args.cost, args.source or 'manual', args.parent))
        elif args.command == 'record':
            print(record(state, args.hypothesis, args.label, args.finding, args.evidence, args.control,
                         args.run_command, args.status, args.blocker))
        elif args.command == 'decide':
            upstream = {k: v for k, v in (('main', args.upstream_main), ('latest_release', args.upstream_release),
                                          ('pinned', args.upstream_pinned)) if v}
            checks = dict(item.split('=', 1) for item in args.check_items)
            print(json.dumps(decide(state, args.hypothesis, args.ownership, args.decision, args.reason,
                                    args.repository, args.component, args.target_branch, upstream, args.pinned_ref,
                                    args.fixed_in, args.fixed_commit, args.upgrade_blocker, args.draft, checks),
                             ensure_ascii=False))
        elif args.command == 'audit':
            result = audit(state)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['status'] == 'ok' else 1
        elif args.command == 'decisions':
            text = json.dumps(export_decisions(state), indent=2, ensure_ascii=False) + '\n'
            if args.output:
                Path(args.output).write_text(text)
            else:
                sys.stdout.write(text)
            return 0
        elif args.command == 'conclude':
            conclude(state, args.classification, args.summary, args.next_check)
        elif args.command == 'next':
            result = next_action(state)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        elif args.command == 'render':
            text = render(state)
            if args.output:
                Path(args.output).write_text(text)
            else:
                sys.stdout.write(text)
            return 0
        _write(args.state, state)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
