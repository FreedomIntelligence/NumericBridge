---
name: framework-adapt
description: Establish native execution for the requested model, framework and hardware.
---

# Framework adaptation

## Execute

Reuse matching environments and launch commands. Check missing pinned artifacts
on the hosts that need them. For public Hugging Face weights, [try an allowed
mirror or ModelScope on connectivity failure](references/public-model-download.md),
with the same artifacts verified against the official revision. If integration is
missing, use the pinned revision's
support notes and nearest native example. Adapt within the case, preserve native
state transitions and record any checkpoint or layout conversion. Use
[preflight_env.py](../precision-compare/scripts/preflight_env.py) only for suspected
dependency or launch issues, with the launch interpreter.

## Native preflight

Before launching a compiler or vLLM, verify the case work mount and an
[authorized node-local scratch root](../precision-compare/references/automation.md#native-vllm-worker)
independently. Prefer scheduler-provided local scratch; use a fresh `0700`
child for compiler temp/cache and IPC, not for models, logs or evidence. Check
free bytes, inodes and known quota limits. `ENOTEMPTY` during shared-directory
cleanup is not proof of a full disk; reconcile the former run, then retry only
the affected side on verified local scratch if the grant permits.

Reuse valid evidence if the device binding still matches. Otherwise check the
first formal batch: keep valid samples; stop on the wrong path or missing data.
For costly or risky launches, run the smallest valid preflight instead. Keep the
checkpoint, dtype, device, backend and topology needed by the target path. Use the
[inference](../precision-compare/references/inference.md),
[SFT](../precision-compare/references/sft.md) or
[RL](../precision-compare/references/rl.md#native-preflight) recipe as needed.

Set `ready: true` only if the native run succeeds on the target path and produces
the required keys, shapes and sample mapping. No CPU fallback. Debug hooks are
optional; measure repeat noise during formal capture.

[Recover interrupted jobs](../precision-compare/references/comparison.md#recover-an-interrupted-stage)
before retrying. Failure labels are clues: fix setup issues in scope, report
unsupported paths, and send reproducible failures on supported paths to diagnosis.

## Return

Hand off the launch command, execution profile, inputs/state, readiness and gaps.
Use the [runtime probe](../precision-compare/scripts/runtime_probe.py) in the worker
to record the device, loaded framework/backend, driver, toolkit, collective
library and image digest or install source. Distinguish unknown from not applicable;
use the actual source commit when the release version is ambiguous.
