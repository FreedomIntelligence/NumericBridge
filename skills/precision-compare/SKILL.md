---
name: precision-compare
description: Collect and compare numerical observations for inference, SFT and RL against an aligned reference or another configuration, and judge them with a versioned acceptance profile.
---

# Precision comparison

Start from the workload, reference, observation points and available evidence.

## Collect

Read only the recipe for the requested workload:

- [Inference](references/inference.md): scores, distributions, cache stages.
- [SFT, full / LoRA](references/sft.md): objectives, gradients, stored updates.
- [RL](references/rl.md): trajectories, objectives, updates.

Read [comparison](references/comparison.md) for tools, shared execution and
metric definitions, and [layerwise](references/layerwise.md) only when the scope
includes internal observations. Run independent captures concurrently when
resources and isolation allow. Preserve native operators, state transitions and
dtype identity.

## Judge

Judge every formal comparison with [acceptance](references/acceptance.md) and
follow its route: deliver, diagnose, stabilize or
recover the capture.

## Tool use

Install with the repository exporter to bundle the shared tools. Use Python 3.10+
and NumPy for array tools; native execution uses the task's runtime. Select a
[comparator](references/comparison.md#tools) and keep task-specific coordinates,
masks, shifts and state in adapters. Read [tool help](scripts/tool_help.py) for
interface details. Recompute affected results when their evidence changes.
