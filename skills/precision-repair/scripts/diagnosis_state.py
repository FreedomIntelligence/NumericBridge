#!/usr/bin/env python3
"""Track diagnosis hypotheses, checks and stop conditions in diagnosis.json; render issues.md.
Call: python scripts/diagnosis_state.py init|add|next|record|conclude|render --state diagnosis.json ...
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
  [--control TEXT] [--run-command TEXT] [--status STATUS]
  Label -> status: not_reproduced/ruled_out -> ruled_out; primary_cause_confirmed,
  secondary_factor_confirmed -> confirmed; unresolved -> blocked; replay_invalid ->
  open; other labels -> supported. --status overrides.
conclude --state S --classification setup_capture|unsupported|expected_policy|
  supported_defect|unresolved --summary TEXT [--next-check TEXT]
render --state S [--output issues.md]  (derived file; overwritten)
Labels: config_mismatch, replay_invalid, local_source_candidate, propagation_candidate,
amplification_supported, local_source_confirmed, primary_cause_confirmed,
secondary_factor_confirmed, not_reproduced, ruled_out, unresolved.
Exit: 0=ok (next: check/refine), 1=next returned stop, 2=input error.
"""

__all__ = ['init', 'add', 'next_action', 'record', 'conclude', 'render']
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
CLASSES = ('setup_capture', 'unsupported', 'expected_policy', 'supported_defect', 'unresolved')
COSTS = ('cpu', 'replay', 'native')


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
    children = {h['parent'] for h in hyps if h['parent']}
    supported = [h for h in hyps if h['status'] == 'supported' and h['id'] not in children]
    if supported:
        return dict(action='refine', supported=supported,
                    reason='add a narrower child hypothesis or run interventions for a supported hypothesis')
    secondary = [h['id'] for h in hyps if h['status'] == 'confirmed']
    return dict(action='stop', reason='secondary factors confirmed' if secondary else 'no actionable hypothesis remains',
                hypotheses=secondary)


def record(state, hid, label, finding, evidence=(), control=None, command=None, status=None):
    if label not in LABELS:
        raise ValueError('unknown label: ' + label)
    h = _hypothesis(state, hid)
    status = status or STATUS.get(label, 'supported')
    if status not in STATUSES:
        raise ValueError('unknown status: ' + status)
    cid = f"C{len(state['checks']) + 1}"
    state['checks'].append(dict(id=cid, hypothesis=hid, at=_now(), label=label, finding=finding,
                                evidence=list(evidence), control=control, command=command))
    h.update(status=status, label=label)
    return cid


def conclude(state, classification, summary, next_check=None):
    if classification not in CLASSES:
        raise ValueError('classification must be one of ' + '|'.join(CLASSES))
    state['conclusion'] = dict(classification=classification, summary=summary, next_check=next_check, at=_now())


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
        lines.append(f"| {h['id']} | {h['status']} | {h['label'] or ''} | {statement} | {h['check']} | {h['cost']} |")
    lines += ['', '## Checks', '']
    for k in state['checks']:
        ev = ', '.join(f'[{Path(p).name}]({p})' for p in k['evidence'])
        lines.append(f"- {k['id']} ({k['at']}) {k['hypothesis']} → `{k['label']}`: {k['finding']}"
                     + (f" Control: {k['control']}." if k.get('control') else '') + (f" Evidence: {ev}" if ev else ''))
    ruled = [h for h in state['hypotheses'] if h['status'] == 'ruled_out']
    if ruled:
        lines += ['', '## Ruled out', ''] + [f"- {h['id']}: {h['statement']}" for h in ruled]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('init', 'add', 'next', 'record', 'conclude', 'render'))
    parser.add_argument('--state', required=True)
    for name in ('acceptance', 'triage', 'case', 'statement', 'check', 'source', 'parent', 'hypothesis',
                 'label', 'finding', 'control', 'status', 'classification', 'summary', 'next-check', 'output'):
        parser.add_argument('--' + name)
    parser.add_argument('--run-command', dest='run_command')
    parser.add_argument('--cost', default='native')
    parser.add_argument('--max-checks', type=int)
    parser.add_argument('--evidence', action='append', default=[])
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
                         args.run_command, args.status))
        elif args.command == 'conclude':
            conclude(state, args.classification, args.summary, args.next_check)
        elif args.command == 'next':
            result = next_action(state)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result['action'] == 'stop' else 0
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
