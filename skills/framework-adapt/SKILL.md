---
name: framework-adapt
description: Establish native execution when a requested model, framework or hardware target needs deployment or backend adaptation.
---

# Framework adaptation

## Execute

Use the [shared execution tools](../precision-compare/references/comparison.md#wait-for-native-execution) for waiting and receipts.

- Reuse environments, adapters and receipts that match source, dependencies and target.
- For missing support, inspect the selected revision's support declarations and
  closest native example, then implement the model/backend integration.
- Run or reuse a [native preflight](#native-preflight) before capture. It checks
  readiness only; numerical agreement comes from comparison.
- Preserve native state transitions; record checkpoint conversions and tensor-layout changes.
- Keep implementations and environment declarations with the case; verify patches
  with [repair verification](../precision-repair/SKILL.md#patch-and-verify).

## Native preflight

Run the **smallest valid workload that reaches the declared native path and
produces its required evidence**. Fix the path and completion checks first, then
shrink; there is no fixed batch, sequence length or step count.

- **Keep the path.** Use the planned device, checkpoint and revision, framework
  and backend, dtype, launch environment, and the topology and settings that
  exercise the claimed kernels or collectives. A substitute model, CPU fallback or
  a topology that skips a required collective is a separate, narrower check.
- **Shrink within it.** Reduce requests, examples, lengths or iterations only
  while the workload stays valid and reaches the required boundaries. Read only
  the task recipe: [inference](../precision-compare/references/inference.md),
  [SFT](../precision-compare/references/sft.md) or [RL](../precision-compare/references/rl.md#native-preflight).
- **Pass conditions** (written to the receipt; formal capture requires them):
  native exit 0, the observed device and runtime path, and at each declared
  boundary a representative record matching the expected keys, shapes and sample
  mapping. Reuse a receipt only for its recorded scope, after checking the
  current device binding.
- **Diagnosis readiness** (same run, recorded in the receipt): for eager PyTorch
  paths, a [capture_boundaries](../precision-compare/scripts/capture_boundaries.py)
  run with `inventory=True` (module order, output shapes and bytes, hooks attach);
  whether the path runs under graph capture (hooks then need eager or msProbe);
  determinism settings; and one same-side repeat of the smallest capture so
  comparison has repeat noise.
- **Failures.** `record_run` and `workflow.py` report `failure_class` and
  `category`: correct `setup_or_resource` within scope, report `unsupported`, and
  route `reproducible_failure` of a supported path to diagnosis.

## Return

Hand the runnable command, execution JSON, inputs/state, readiness and gaps, and
the receipt to the next stage. In the actual worker, record device binding,
imported framework and backend versions/revisions, driver, runtime/toolkit (CANN
or CUDA) and the loaded collective library (HCCL or NCCL) with the
[runtime probe](../precision-compare/scripts/runtime_probe.py); keep unconfirmed
versions unknown.
