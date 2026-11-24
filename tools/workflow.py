#!/usr/bin/env python3
"""Run and resume dependency-linked adaptation/comparison/repair commands (POSIX, stdlib).
Call: run_workflow(plan_path, workspace, refresh=(), time_budget_seconds=None, jobs=1); inspect_workflow(plan_path, workspace); init_plan(task, sides)
Input: Dependency plan with source/runtime inputs, outputs, checks and cumulative budget.
Output: Verified receipts, next_action, runnable_actions and blocked_actions.
Requires: Standard library/POSIX; status launches nothing; unchanged failures are retained; --refresh reruns selected stages and descendants.

Details:
CLI: workflow.py run|status --plan P --workspace W [--refresh STAGE ...]
     [--time-budget-seconds S] [--jobs N];  workflow.py init --task inference|sft|rl
     --sides A,B --output NEW.json [--id ID]. Exit 0=all stages completed,
1=next action/budget required, 2=invalid plan or concurrent runner.

Plan JSON: schema_version=1, id, root (relative to plan, default '.'), workload
(optional object), budget_seconds (cumulative child wall time incl. retries),
stages=[{id, skill:framework-adapt|precision-compare|precision-repair|operator-optimize,
argv:[literal,...], inputs:[path,...], outputs:{name:relative_path},
depends_on:[stage,...], timeout, side (default 'compare'), cache (default true),
execution (record_run profile path or {artifact:STAGE:NAME}), env_keys:[name,...],
checks:[{output, pointer:'/JSON/pointer', equals:V | min:X | max:Y | min+max}],
on_check_failure:skill}]. argv substitutes {run} (fresh stage directory), {root} and
{artifact:STAGE:NAME} from a direct dependency, without a shell. Arguments starting
with TODO are rejected so templates must be completed.

Reuse identity covers declared inputs, dependency outputs, executables, the
execution profile, selected environment variables, the workload and these engine
files; undeclared changes on remote hosts or in installed packages are not seen.
Successful unchanged stages are reused after their output hashes match; cache:false
stages always run. A matching failure returns its continuation without rerunning;
change its inputs or pass --refresh. Missing outputs, failed checks, changing inputs
and unsuccessful commands are never reused. Checks express task-owned readiness or
acceptance; a failed check routes to on_check_failure (e.g. precision-repair).

--jobs N runs up to N ready stages concurrently; independent branches keep
running after a failure. Budget reserves each running stage's timeout.
next_action gives the stage, the skill to load, the reason and, for a run, its
logs, declared outputs and record_run failure_class/category. Commands use
record_run (logs, timeout, process-group cleanup). A process lock prevents
concurrent runners; after a hard-killed runner, a still-live recorded child blocks
duplicate launch.
argv substitutes {run} (fresh stage directory), {root}, and
{artifact:STAGE:NAME} (a declared dependency output), without shell evaluation.
Outputs must be files/directories inside that run. Missing outputs, failed JSON
checks, changing inputs and unsuccessful commands are never successful cache hits.
No precision tolerance is supplied: checks express task-owned validation or
correctness evidence; comparator exit 0 establishes metrics, not user acceptance.

Workspace state.json contains attempts, content identities and artifact paths.
Successful unchanged stages are reused after checking output hashes. A matching
failure yields its continuation without rerunning; change its inputs or request
--refresh. Refresh reruns the stage and descendants. cache:false stages execute
on each run (e.g. a live environment probe); unchanged receipts permit downstream
reuse. Failures route to the stage skill; a failed task-owned check may route to
on_check_failure. The agent performs the indicated adaptation/repair, then calls
run again to verify the affected dependency chain. No model service is invoked.

Commands use record_run for literal argv, logs, timeout and process-group cleanup.
Existing run bytes are preserved. A process lock prevents concurrent execution.
Independent branches continue when another branch is blocked. After a hard-killed
runner, a still-live recorded child blocks duplicate launch. A matching terminal
command receipt is revalidated against current inputs and declared output checks.
A local SSH exit is not remote terminal evidence; remote adapters must verify the
exact remote owner before launch and provide checked outputs for remote completion.
"""

__all__ = ['run_workflow', 'inspect_workflow', 'init_plan']
import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time

from .conditions import canonical, load_json
from .record_run import load_execution, record_run, summarize_run

SKILLS = {'framework-adapt', 'precision-compare', 'precision-repair', 'operator-optimize'}
ID = re.compile(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z')
TOKEN = re.compile(r'\{artifact:([^:{}]+):([^:{}]+)\}')
ENGINE_FILES = tuple(Path(__file__).with_name(name) for name in ('workflow.py', 'record_run.py', 'conditions.py'))
ENV_KEYS = ('PATH', 'PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH',
            'CUDA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER', 'NVIDIA_VISIBLE_DEVICES',
            'ASCEND_RT_VISIBLE_DEVICES', 'ASCEND_VISIBLE_DEVICES',
            'ASCEND_HOME_PATH', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def fingerprint(path, symlinks=True, ancestors=frozenset()):
    """Content identity for selected local files/directories, detecting changing files."""
    path = Path(path)
    _require(symlinks or not path.is_symlink(), 'output identities require literal files')
    _require(path.exists(), 'missing input/output: ' + str(path))
    if path.is_dir():
        real = path.resolve()
        _require(real not in ancestors, 'directory identity contains a symbolic-link cycle')
        entries = [(p.name, fingerprint(p, symlinks, ancestors | {real}))
                   for p in sorted(path.iterdir())]
        return dict(kind='directory', sha256=_digest(entries),
                    files=sum(v.get('files', 1) for _, v in entries),
                    bytes=sum(v['bytes'] for _, v in entries))
    _require(path.is_file(), 'expected regular file: ' + str(path))
    before = path.stat()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    after = path.stat()
    stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    _require(stamp(before) == stamp(after), 'file changed while hashing: ' + str(path))
    return dict(kind='file', sha256=h.hexdigest(), bytes=after.st_size)


def _plan(path):
    path = Path(path).resolve()
    spec = load_json(path)
    _require(isinstance(spec, dict) and spec.get('schema_version') == 1, 'plan schema_version must be 1')
    _require(isinstance(spec.get('id'), str) and ID.fullmatch(spec['id']), 'invalid workflow id')
    _require(_positive(spec.get('budget_seconds')), 'positive budget_seconds required')
    _require(isinstance(spec.get('workload', {}), dict), 'workload must be an object')
    root = (path.parent / spec.get('root', '.')).resolve()
    _require(root.is_dir(), 'workflow root must exist')
    stages = spec.get('stages')
    _require(isinstance(stages, list) and stages, 'nonempty stages required')
    by_id = {}
    for raw in stages:
        _require(isinstance(raw, dict), 'stage must be an object')
        stage = dict(raw)
        name = stage.get('id')
        _require(isinstance(name, str) and ID.fullmatch(name) and name not in by_id, 'unique stage IDs required')
        _require(stage.get('skill') in SKILLS, 'unknown stage skill')
        _require(stage.get('on_check_failure', stage['skill']) in SKILLS, 'unknown check-failure skill')
        argv = stage.get('argv')
        _require(isinstance(argv, list) and argv and all(isinstance(x, str) and '\0' not in x for x in argv) and argv[0], 'literal argv required')
        _require(not any(x.startswith('TODO') for x in argv), 'stage ' + str(name) + ' argv contains a TODO placeholder')
        _require(_positive(stage.get('timeout')), 'positive stage timeout required')
        _require('execution' not in stage or isinstance(stage['execution'], str) and stage['execution'],
                 'execution must name a profile file')
        for field in ('inputs', 'depends_on', 'env_keys'):
            stage.setdefault(field, [])
            _require(isinstance(stage[field], list) and all(isinstance(x, str) and x for x in stage[field])
                     and len(set(stage[field])) == len(stage[field]), field + ' must contain unique strings')
        stage.setdefault('side', 'compare')
        _require(isinstance(stage['side'], str) and ID.fullmatch(stage['side']), 'invalid stage side')
        stage.setdefault('cache', True)
        _require(type(stage['cache']) is bool, 'cache must be boolean')
        outputs = stage.get('outputs')
        _require(isinstance(outputs, dict) and outputs, 'named outputs required')
        for key, value in outputs.items():
            _require(isinstance(key, str) and ID.fullmatch(key), 'invalid output name')
            _require(isinstance(value, str) and value and not Path(value).is_absolute()
                     and '..' not in Path(value).parts and value != '.', 'output must be a relative run path')
        _require(len(set(outputs.values())) == len(outputs), 'output paths must be distinct')
        stage.setdefault('checks', [])
        _require(isinstance(stage['checks'], list), 'checks must be a list')
        for check in stage['checks']:
            predicate = set(check) - {'output', 'pointer'} if isinstance(check, dict) else set()
            _require(isinstance(check, dict) and {'output', 'pointer'} <= set(check)
                     and (predicate == {'equals'} or (predicate and predicate <= {'min', 'max'}
                          and all(_number(check[k]) for k in predicate)))
                     and check['output'] in outputs and isinstance(check['pointer'], str)
                     and (not check['pointer'] or check['pointer'].startswith('/')), 'invalid JSON output check')
        by_id[name] = stage
    for name, stage in by_id.items():
        _require(all(dep in by_id and dep != name for dep in stage['depends_on']), 'unknown/self dependency')
        for arg in stage['argv'] + ([stage['execution']] if 'execution' in stage else []):
            for dep, output in TOKEN.findall(arg):
                _require(dep in stage['depends_on'] and output in by_id[dep]['outputs'], 'artifact must name a direct dependency output')
    ordered, visiting, visited = [], set(), set()
    def visit(name):
        _require(name not in visiting, 'cyclic stage dependency')
        if name in visited:
            return
        visiting.add(name)
        for dep in by_id[name]['depends_on']:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
        ordered.append(by_id[name])
    for name in by_id:
        visit(name)
    return spec, root, ordered


def _state(workspace, spec, root):
    path = workspace / 'state.json'
    if not path.exists():
        return dict(schema_version=1, id=spec['id'], root=str(root), history=[])
    state = load_json(path)
    _require(state.get('schema_version') == 1 and state.get('id') == spec['id']
             and state.get('root') == str(root) and isinstance(state.get('history'), list),
             'workspace belongs to a different workflow/root')
    return state


def _save(workspace, state):
    temporary = workspace / 'state.json.tmp'
    temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + '\n')
    temporary.replace(workspace / 'state.json')


@contextmanager
def workspace_lock(workspace):
    """Exclusively lock an existing POSIX workspace without waiting or unlink races."""
    _require(os.name == 'posix', 'workflow command runner requires POSIX process locking')
    import fcntl
    with (workspace / 'runner.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('workflow already has an active runner') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _signature(stage, root, completed, workload):
    inputs = {name: fingerprint(root / name) for name in stage['inputs']}
    deps = {}
    for name in stage['depends_on']:
        outputs = {}
        for key, value in completed[name]['outputs'].items():
            actual = fingerprint(value['path'], symlinks=False)
            _require(actual == value['identity'], 'dependency output changed: ' + name + ':' + key)
            outputs[key] = actual
        deps[name] = dict(key=completed[name]['key'], outputs=outputs)
    execution = _execution(stage, root, completed)
    profile, child_env = load_execution(execution)
    programs = dict(command=stage['argv'][0])
    if profile.get('argv_prefix'):
        programs['launcher'] = profile['argv_prefix'][0]
    executables = {}
    for name, program in programs.items():
        program = TOKEN.sub(lambda m: completed[m[1]]['outputs'][m[2]]['path'], program).replace('{root}', str(root))
        candidate = root / program
        resolved = (str(candidate) if candidate.is_file() else None) if '/' in program else shutil.which(program, path=child_env.get('PATH', os.defpath))
        selected = {p.resolve() for p in (candidate, Path(resolved) if resolved else candidate) if p.is_file()}
        executables[name] = {str(p): fingerprint(p) for p in sorted(selected)}
    if execution is not None:
        inputs['execution:' + str(execution)] = fingerprint(execution)
    keys = sorted(set(ENV_KEYS) | set(stage['env_keys']))
    environment = _digest({k: child_env.get(k) for k in keys})
    return dict(stage=stage, root=str(root), workload=workload, inputs=inputs, dependencies=deps,
                execution_tools={p.name: fingerprint(p) for p in ENGINE_FILES},
                executables=executables, environment=environment)


def _valid(receipt):
    try:
        return receipt['status'] == 'completed' and all(
            not Path(v['path']).is_symlink()
            and Path(v['path']).resolve().is_relative_to(Path(receipt['run']).resolve())
            and fingerprint(v['path'], symlinks=False) == v['identity'] for v in receipt['outputs'].values())
    except (KeyError, OSError, ValueError):
        return False


def _latest(state, name):
    return next((r for r in reversed(state['history']) if r['stage'] == name), None)


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _recover(workspace, state, *, inspect=False):
    for receipt in state['history']:
        if receipt['status'] != 'running':
            continue
        folders = list((workspace / 'runs').glob(receipt['run_prefix'] + '*'))
        _require(len(folders) <= 1, 'ambiguous interrupted run: ' + receipt['run_prefix'])
        if folders and (command := folders[0] / 'command.json').is_file():
            metadata = load_json(command)
            if metadata['status'] == 'running' and _alive(metadata.get('child_pid')):
                _require(inspect, 'previous native child may still be running: ' + str(command))
                continue
            wall = metadata.get('wall_seconds')
            receipt.update(run=str(folders[0]), wall_seconds=wall if _positive(wall) else receipt['timeout'])
            if (metadata['status'] == 'completed' and metadata.get('returncode') == 0
                    and metadata.get('end') and _positive(wall)):
                receipt.update(status='pending_validation', returncode=0)
                continue
        receipt.update(status='interrupted', reason='previous runner ended before recording verified outputs')


def _execution(stage, root, completed):
    value = stage.get('execution')
    if value is None:
        return None
    value = TOKEN.sub(lambda m: completed[m[1]]['outputs'][m[2]]['path'], value)
    return (root / value).resolve()


def _argv(stage, root, run, completed):
    def replace(match):
        return completed[match[1]]['outputs'][match[2]]['path']
    return [TOKEN.sub(replace, arg).replace('{root}', str(root)).replace('{run}', str(run))
            for arg in stage['argv']]


def _pointer(document, pointer):
    for part in pointer.split('/')[1:] if pointer else []:
        _require(not re.search(r'~(?![01])', part), 'invalid JSON pointer escape')
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(document, list):
            _require(bool(re.fullmatch(r'0|[1-9][0-9]*', part)), 'invalid JSON pointer array index')
            document = document[int(part)]
        else:
            document = document[part]
    return document


def _outputs(stage, run):
    result = {}
    for name, relative in stage['outputs'].items():
        path = run / relative
        _require(not path.is_symlink() and path.resolve().is_relative_to(run.resolve()), 'output escaped stage run')
        result[name] = dict(path=str(path.resolve()), identity=fingerprint(path, symlinks=False))
    return result


class CheckFailed(ValueError):
    """A well-formed task-owned predicate disagrees with a captured value."""


def _checks(stage, outputs):
    for check in stage['checks']:
        actual = _pointer(load_json(outputs[check['output']]['path']), check['pointer'])
        where = check['output'] + check['pointer']
        if 'equals' in check:
            if canonical(actual) != canonical(check['equals']):
                raise CheckFailed('output check failed: ' + where)
        elif not _number(actual):
            raise CheckFailed('output check needs a finite number: ' + where)
        elif actual < check.get('min', actual) or actual > check.get('max', actual):
            raise CheckFailed(f'output check failed: {where}={actual} outside '
                              f"[{check.get('min', '-inf')}, {check.get('max', 'inf')}]")


def _report(spec, state, entries, actions):
    ready = [a for a in actions if a['state'] == 'runnable']
    blocked = [a for a in actions if a['state'] != 'runnable']
    return dict(id=spec['id'], workload=spec.get('workload', {}), status='needs_action' if actions else 'completed',
                spent_seconds=sum(x.get('wall_seconds', 0) for x in state['history']),
                budget_seconds=spec['budget_seconds'], attempts=len(state['history']),
                stages=entries, next_action=(ready or blocked or [None])[0],
                runnable_actions=ready, blocked_actions=blocked)


def _action(stage, reason, receipt=None, *, state="action_required"):
    result = dict(stage=stage['id'], skill=(receipt or {}).get('next_skill', stage['skill']),
                  reason=reason, inputs=stage['inputs'], depends_on=stage['depends_on'], state=state)
    if receipt and receipt.get('run'):
        result.update(run=receipt['run'], stdout=str(Path(receipt['run']) / 'stdout.bin'),
                      stderr=str(Path(receipt['run']) / 'stderr.bin'),
                      outputs=receipt.get('outputs', {}), declared_outputs=stage['outputs'])
        command = Path(receipt['run']) / 'command.json'
        if command.exists():
            result['execution'] = summarize_run(receipt['run'], include_tails=True)
            for key in ('failure_class', 'category'):
                if key in result['execution']:
                    result[key] = result['execution'][key]
    return result


def _inline(function, *args, **kwargs):
    """Run synchronously behind the Future interface (jobs=1 keeps interrupts in the caller)."""
    future = Future()
    try:
        future.set_result(function(*args, **kwargs))
    except Exception as exc:
        future.set_exception(exc)
    return future


def _validate_outputs(receipt, stage, folder, key, root, completed, workload):
    try:
        receipt['outputs'] = _outputs(stage, folder)
        _require(_digest(_signature(stage, root, completed, workload)) == key,
                 'declared inputs changed during execution')
        try:
            _checks(stage, receipt['outputs'])
        except CheckFailed as exc:
            receipt['next_skill'] = stage.get('on_check_failure', stage['skill'])
            raise ValueError(str(exc)) from exc
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        receipt.update(status='invalid_outputs', reason=str(exc))


def _finish(root, stage, receipt, key, completed, folder, command, workload):
    receipt.update(run=str(folder), status=command['status'], wall_seconds=command['wall_seconds'],
                   returncode=command['returncode'])
    if command['status'] == 'completed':
        _validate_outputs(receipt, stage, folder, key, root, completed, workload)
    else:
        receipt['reason'] = 'native command ' + command['status']


def _prepare(spec, root, stage, workspace, state, completed, entries, execute, forced, reserved, deadline):
    """Reuse or recover a ready stage, or return an action dict or a launch (key, identity, timeout)."""
    name, workload = stage['id'], spec.get('workload', {})
    latest = _latest(state, name)
    if latest and name not in forced and latest['status'] in ('running', 'interrupted'):
        return _action(stage, latest.get('reason', latest['status']), latest,
                       state='observe' if latest['status'] == 'running' else 'interrupted')
    try:
        identity = _signature(stage, root, completed, workload)
        key = _digest(identity)
    except (OSError, ValueError) as exc:
        return _action(stage, str(exc))
    same = latest and latest['key'] == key
    recovered = False
    if latest and name not in forced and latest['status'] == 'pending_validation' and not same:
        return _action(stage, 'terminal inputs changed; reconcile prior run', latest, state='interrupted')
    if same and name not in forced and latest['status'] == 'pending_validation':
        if not execute:
            return _action(stage, 'validate terminal outputs', latest, state='runnable')
        folder = Path(latest['run'])
        command = load_json(folder / 'command.json')
        profile, _ = load_execution(_execution(stage, root, completed))
        if command.get('cwd') != str(root) or command.get('argv') != profile.get('argv_prefix', []) + _argv(stage, root, folder, completed):
            latest.update(status='invalid_outputs', reason='terminal command does not match reserved stage')
        else:
            latest['status'] = 'completed'
            _validate_outputs(latest, stage, folder, key, root, completed, workload)
        _save(workspace, state)
        recovered = True
    if (stage['cache'] or recovered or not execute) and name not in forced and same and _valid(latest):
        completed[name] = latest
        entries.append(dict(id=name, skill=stage['skill'], reused=True, run=latest['run'], outputs=latest['outputs']))
        return None
    if same and name not in forced and latest['status'] != 'completed':
        return _action(stage, latest.get('reason', latest['status']), latest)
    spent = sum(x.get('wall_seconds', 0) for x in state['history'])
    remaining = spec['budget_seconds'] - spent - reserved
    if deadline is not None:
        remaining = min(remaining, deadline - time.monotonic())
    if remaining <= 0:
        return _action(stage, 'cumulative command budget exhausted', latest, state='budget')
    if not execute:
        return _action(stage, 'stage needs execution' if not same else 'cached outputs unavailable',
                       latest, state='runnable')
    return key, identity, min(stage['timeout'], remaining)


def _walk(spec, root, stages, workspace, state, execute, refresh, deadline=None, jobs=1):
    completed, entries, actions = {}, [], []
    workload = spec.get('workload', {})
    forced = set(refresh)
    _require(forced <= {s['id'] for s in stages}, 'unknown refresh stage')
    for stage in stages:
        if any(dep in forced for dep in stage['depends_on']):
            forced.add(stage['id'])
    pending, running = list(stages), {}
    pool = ThreadPoolExecutor(max_workers=jobs) if execute and jobs > 1 else None
    try:
        while True:
            progressed = True
            while progressed and len(running) < jobs:
                progressed = False
                for stage in list(pending):
                    if len(running) >= jobs:
                        break
                    if not all(dep in completed for dep in stage['depends_on']):
                        continue
                    reserved = sum(r['timeout'] for _, r, _ in running.values())
                    outcome = _prepare(spec, root, stage, workspace, state, completed, entries,
                                       execute, forced, reserved, deadline)
                    if isinstance(outcome, dict) and outcome['state'] == 'budget' and running:
                        break  # wait for running stages to release their reserved budget
                    pending.remove(stage)
                    progressed = True
                    if isinstance(outcome, dict):
                        actions.append(outcome)
                        continue
                    if outcome is None:
                        continue
                    key, identity, timeout = outcome
                    name = stage['id']
                    task = f"{spec['id']}-{name}-attempt{len(state['history']) + 1}"
                    receipt = dict(stage=name, skill=stage['skill'], key=key, identity=identity, status='running',
                                   outputs={}, run_prefix=f"{stage['side']}-{task}-", timeout=timeout, wall_seconds=0)
                    state['history'].append(receipt)
                    _save(workspace, state)
                    argv = lambda run, stage=stage: _argv(stage, root, run, completed)
                    submit = pool.submit if pool else _inline
                    future = submit(record_run, workspace / 'runs', stage['side'], task, timeout, argv, root,
                                    execution=_execution(stage, root, completed))
                    running[future] = (stage, receipt, key)
            if not running:
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for future in done:
                stage, receipt, key = running.pop(future)
                folder, command = future.result()
                _finish(root, stage, receipt, key, completed, folder, command, workload)
                _save(workspace, state)
                if receipt['status'] != 'completed':
                    actions.append(_action(stage, receipt.get('reason', receipt['status']), receipt))
                else:
                    completed[stage['id']] = receipt
                    entries.append(dict(id=stage['id'], skill=stage['skill'], reused=False, run=receipt['run'],
                                        outputs=receipt['outputs']))
    finally:
        if pool:
            pool.shutdown(wait=True)
    for stage in pending:
        missing = [dep for dep in stage['depends_on'] if dep not in completed]
        actions.append(_action(stage, 'waiting for dependencies: ' + ', '.join(missing), state='dependency'))
    return _report(spec, state, entries, actions)


def run_workflow(plan_path, workspace, refresh=(), time_budget_seconds=None, jobs=1):
    """Execute missing stages and return their reusable outputs or the next skill action."""
    _require(time_budget_seconds is None or _positive(time_budget_seconds),
             'time_budget_seconds must be positive')
    _require(type(jobs) is int and jobs >= 1, 'jobs must be a positive integer')
    deadline = None if time_budget_seconds is None else time.monotonic() + time_budget_seconds
    spec, root, stages = _plan(plan_path)
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with workspace_lock(workspace):
        state = _state(workspace, spec, root)
        _recover(workspace, state)
        result = _walk(spec, root, stages, workspace, state, True, refresh, deadline, jobs)
        _save(workspace, state)
        return result


def inspect_workflow(plan_path, workspace):
    """Inspect reuse and the next required action without executing or creating files."""
    spec, root, stages = _plan(plan_path)
    workspace = Path(workspace).resolve()
    state = _state(workspace, spec, root)
    # Inspect terminal receipts without rewriting state or claiming an active child.
    _recover(workspace, state, inspect=True)
    return _walk(spec, root, stages, workspace, state, False, ())


def _compare_scripts():
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, here.parent / 'skills/precision-compare/scripts'):
        if (candidate / 'acceptance.py').is_file():
            return candidate
    return Path('PRECISION_COMPARE_SCRIPTS')


def init_plan(task, sides, plan_id=None):
    """Return a plan skeleton: per-side preflight and capture, a same-side repeat of the
    first side, task comparisons with repeat-noise comparisons, and acceptance.

    TODO arguments mark case-specific commands; the plan is rejected until they are replaced.
    The repeat capture must label its manifests with its own side ID (e.g. A-repeat).
    """
    _require(task in ('inference', 'sft', 'rl'), 'task must be inference|sft|rl')
    _require(len(sides) == 2 and len(set(sides)) == 2 and all(ID.fullmatch(s) for s in sides), 'two distinct side IDs required')
    scripts = _compare_scripts()
    py = lambda name: [sys.executable, str(scripts / name)]
    stages = []
    for side in sides:
        stages.append(dict(id=f'preflight-{side}', skill='framework-adapt', side=side,
                           argv=['TODO-preflight-command', '--receipt', '{run}/receipt.json'],
                           execution=f'environments/{side}.json', inputs=[], outputs=dict(receipt='receipt.json'),
                           checks=[dict(output='receipt', pointer='/ready', equals=True)], timeout=1800))
        stages.append(dict(id=f'capture-{side}', skill='precision-compare', side=side, depends_on=[f'preflight-{side}'],
                           argv=['TODO-capture-command', '--output', '{run}/capture'],
                           execution=f'environments/{side}.json', inputs=['inputs'],
                           outputs=dict(scores='capture/scores.json', tensors='capture/tensors.json'), timeout=3600))
    a, b = sides
    repeat = dict(stages[1], id=f'capture-{a}-repeat', cache=False,
                  argv=['TODO-capture-command', '--side-id', f'{a}-repeat', '--output', '{run}/capture'])
    stages.append(repeat)
    def compare(name, tool, contract, output, left=f'capture-{a}', right=f'capture-{b}'):
        return dict(id=f'compare-{name}', skill='precision-compare', depends_on=[left, right],
                    argv=py(tool) + ['--contract', '{root}/' + contract, '--side-a', f'{{artifact:{left}:{output}}}',
                                     '--side-b', f'{{artifact:{right}:{output}}}', '--output', '{run}/report.json'],
                    inputs=[contract], outputs=dict(report='report.json'), timeout=1800)
    kinds = [('scores', 'compare_target_scores.py', 'contracts/scores.json', 'scores')]
    if task == 'inference':
        kinds.append(('distributions', 'compare_distributions.py', 'contracts/distributions.json', 'tensors'))
    else:
        kinds.append(('gradients', 'compare_tensors.py', 'contracts/gradients.json', 'tensors'))
    comparisons = [compare(*k) for k in kinds]
    noise = [compare(k[0] + '-noise', *k[1:], right=repeat['id']) for k in kinds]
    stages += comparisons + noise
    reports = []
    for c in comparisons:
        reports += ['--report', f"{{artifact:{c['id']}:report}}"]
    for c in noise:
        reports += ['--noise', f"{{artifact:{c['id']}:report}}"]
    stages.append(dict(id='acceptance', skill='precision-compare', depends_on=[c['id'] for c in comparisons + noise],
                       argv=py('acceptance.py') + reports + ['--dtype', 'bf16', '--exit-zero', '--output', '{run}/acceptance.json'],
                       outputs=dict(acceptance='acceptance.json'), timeout=600,
                       checks=[dict(output='acceptance', pointer='/passed', equals=True)],
                       on_check_failure='precision-repair'))
    return dict(schema_version=1, id=plan_id or f'{task}-{a}-{b}', root='.', budget_seconds=4 * 3600,
                workload=dict(task=task, sides=list(sides)), stages=stages)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('run', 'status', 'init'))
    parser.add_argument('--plan')
    parser.add_argument('--workspace')
    parser.add_argument('--refresh', action='append', default=[])
    parser.add_argument('--time-budget-seconds', type=float)
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--task')
    parser.add_argument('--sides')
    parser.add_argument('--id')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            _require(args.task and args.sides and args.output, 'init requires --task, --sides A,B and --output')
            plan = init_plan(args.task, args.sides.split(','), args.id)
            with Path(args.output).open('x') as stream:
                json.dump(plan, stream, indent=2)
                stream.write('\n')
            print(json.dumps(dict(plan=str(Path(args.output).resolve()), todo=[s['id'] for s in plan['stages']
                                  if any(x.startswith('TODO') for x in s['argv'])])))
            return 0
        _require(args.plan and args.workspace, 'run/status require --plan and --workspace')
        _require(args.command == 'run' or not (args.refresh or args.time_budget_seconds is not None or args.jobs != 1),
                 '--refresh, --time-budget-seconds and --jobs apply to run')
        report = (run_workflow(args.plan, args.workspace, args.refresh, args.time_budget_seconds, args.jobs)
                  if args.command == 'run' else inspect_workflow(args.plan, args.workspace))
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
