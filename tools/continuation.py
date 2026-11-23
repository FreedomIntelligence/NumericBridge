"""Continue a NumericBridge case through a caller-configured model client (POSIX).
Call: continue_case(config_path, workspace)
Input: JSON with plan, agent_argv (literal argv with a {prompt} placeholder),
agent_stages (allowlisted model-action stages), completion_stage, max_calls and
budget_seconds. Paths are relative to the config. The trusted plan owns all
verification checks. The client must support noninteractive invocation; use
its resume command when continuing an existing conversation. No provider is
configured, no desktop chat is addressed, and no automation is installed.
Output: Durable model-call receipts and a status with a verified stop reason.
CLI: python -B -m tools.continuation --config CONFIG --workspace WORKSPACE
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from .conditions import load_json
from .record_run import record_run
from .workflow import _plan, _positive, _save, run_workflow, workspace_lock

__all__ = ['continue_case']

DONE = 'NumericBridge 链路已完成'


def continue_case(config_path, workspace):
    config_path = Path(config_path).resolve()
    raw = config_path.read_bytes()
    cfg = json.loads(raw)
    plan = (config_path.parent / cfg['plan']).resolve()
    plan_bytes = plan.read_bytes()
    _, root, stages = _plan(plan)
    by_id = {s['id']: s for s in stages}
    gate = by_id[cfg['completion_stage']]
    covered = {gate['id']}
    for stage in reversed(stages):
        if stage['id'] in covered:
            covered.update(stage['depends_on'])
    argv = cfg['agent_argv']
    allowed = cfg['agent_stages']
    if (not isinstance(argv, list) or not all(isinstance(a, str) and '\0' not in a for a in argv)
            or '{prompt}' not in argv or not argv[0] or argv[0] == '{prompt}'
            or not isinstance(allowed, list) or not all(isinstance(a, str) and a in by_id for a in allowed)
            or type(cfg['max_calls']) is not int or cfg['max_calls'] < 1
            or not _positive(cfg['budget_seconds']) or not gate['checks'] or covered != set(by_id)):
        raise ValueError('invalid continuation configuration or completion stage does not check the complete plan')

    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    binding = dict(config=str(config_path), plan=str(plan), config_sha=hashlib.sha256(raw).hexdigest(),
                   plan_sha=hashlib.sha256(plan_bytes).hexdigest())
    with workspace_lock(workspace):
        state_path = workspace / 'state.json'
        state = load_json(state_path) if state_path.exists() else dict(binding=binding, history=[], spent=0)
        if state['binding'] != binding:
            raise ValueError('continuation inputs changed; use a new controller workspace')
        history = state['history']
        start = time.monotonic()
        prior = state['spent']
        if state.get('operation_started') is not None:
            prior += max(0, time.time() - state['operation_started'])
            state['operation_started'] = time.time()

        def checkpoint():
            state['spent'] = prior + time.monotonic() - start
            if state.get('operation'):
                state['operation_started'] = time.time()
            else:
                state.pop('operation_started', None)
            _save(workspace, state)

        def result(status, reason, report=None):
            checkpoint()
            return dict(status=status, reason=reason, calls=len(history), workflow=report,
                        message=DONE if status == 'completed' else None)

        if state.get('operation') == 'agent':
            receipts = list((workspace / 'agents').glob(f'compare-continue-{len(history)}-*/command.json'))
            if len(receipts) != 1:
                return result('blocked', 'previous model call has no unique terminal receipt')
            command = load_json(receipts[0])
            if not command.get('end') or command['status'] not in ('completed', 'failed'):
                return result('blocked', 'previous model owner requires reconciliation')
            history[-1].update(run=str(receipts[0].parent), status=command['status'])
            state.pop('operation')
            checkpoint()
        if history and history[-1]['status'] in ('interrupted', 'timed_out'):
            return result('blocked', 'previous model owner requires reconciliation')
        previous = None
        while True:
            if plan.read_bytes() != plan_bytes or config_path.read_bytes() != raw:
                return result('blocked', 'trusted plan or continuation configuration changed')
            remaining = cfg['budget_seconds'] - prior - (time.monotonic() - start)
            if remaining <= 0:
                return result('budget_exhausted', 'controller time budget exhausted', previous)
            state['operation'] = 'native'
            checkpoint()
            try:
                report = run_workflow(plan, workspace / 'native', time_budget_seconds=remaining)
            except ValueError as exc:
                return result('blocked', str(exc), previous)
            state.pop('operation')
            checkpoint()
            previous = report
            if plan.read_bytes() != plan_bytes or config_path.read_bytes() != raw:
                return result('blocked', 'trusted plan or continuation configuration changed', report)
            if report['status'] == 'completed':
                return result('completed', 'all declared checks verified', report)
            actions = report['blocked_actions']
            if any(a['state'] in ('observe', 'interrupted') for a in actions):
                return result('blocked', 'previous work requires owner reconciliation', report)
            eligible = [a for a in actions if a['state'] == 'action_required' and a['stage'] in allowed]
            if not eligible:
                return result('blocked', 'no authorized model action is ready', report)
            if report['spent_seconds'] >= report['budget_seconds']:
                return result('budget_exhausted', 'native command budget exhausted', report)
            # One request plus one nudge per unchanged action. Run paths, attempt
            # counters and free-text model replies do not count as progress.
            verified = [[s['id'], {k: v['identity'] for k, v in s['outputs'].items()}]
                        for s in report['stages'] if s['id'] != gate['id']]
            for action in eligible:
                identity = dict(stage=action['stage'], reason=action['reason'], verified=verified)
                if sum(a['identity'] == identity for a in history) < 2:
                    break
            else:
                return result('blocked', 'no verified progress after one continuation', report)
            if len(history) >= cfg['max_calls']:
                return result('budget_exhausted', 'model call budget exhausted', report)
            remaining = cfg['budget_seconds'] - prior - (time.monotonic() - start)
            if remaining <= 0:
                return result('budget_exhausted', 'controller time budget exhausted', report)
            instruction = 'Continue from the checkpoint.' if history else 'Perform the authorized next action.'
            prompt = ('<action>\n' + instruction + '\n' + json.dumps({
                'stage': action['stage'], 'skill': action['skill'], 'reason': action['reason'], 'plan': str(plan),
                'run': action.get('run'), 'inputs': action['inputs'], 'workspace': str(workspace / 'native')
            }, ensure_ascii=False) + '\n</action>\n'
                      '<handoff>Preserve the trusted plan and existing receipts. Work only on this action. '
                      'The controller owns native launches; do not launch native jobs from this turn. '
                      'Record the evidence or exact blocker and return for verification. '
                      'The controller alone reports NumericBridge 链路已完成.</handoff>')
            history.append(dict(identity=identity, status='running'))
            state['operation'] = 'agent'
            checkpoint()
            folder, receipt = record_run(workspace / 'agents', 'compare', f'continue-{len(history)}', remaining,
                                         [prompt if arg == '{prompt}' else arg for arg in argv], cwd=root)
            history[-1].update(run=str(folder), status=receipt['status'])
            state.pop('operation')
            checkpoint()
            if receipt['status'] not in ('completed', 'failed'):
                return result('blocked', 'model client did not exit successfully; inspect its receipt', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    try:
        report = continue_case(args.config, args.workspace)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc)), ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
