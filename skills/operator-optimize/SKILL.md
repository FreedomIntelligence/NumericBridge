---
name: operator-optimize
description: Bring a localized operator closer to its FP64 reference by configuration, backend choice or a new kernel (Triton, CUDA, Ascend C), and report every variant's precision and speed so users can choose their tradeoff.
---

# Operator optimization

Run tools from this skill directory. Each tool's `--help` gives its interface.

| Step | Tool | Output |
|---|---|---|
| Measure variants against FP64 and each other | [kernel_check](scripts/kernel_check.py) | Per-variant error vs FP64 and the dtype floor, reproducibility, timing |

Read the [handoff format](references/handoff.md) for the input and the
[backend guide](references/backends.md) for loading a kernel without changing
shared environments.

## Entry

Enter from diagnosis with a handoff for one localized operator, judged by
precision-repair's `operator_verdict.py` on the captured operands:

| Local FP64 reading | Optimization |
|---|---|
| `a_deviates`, `b_deviates` or `shared_policy` (fails the bound) | Required, whatever `operator_optimize.enabled` says |
| `not_the_source` (within the bound) and `operator_optimize.enabled` | Optional: bring both sides closer to FP64 |
| `not_the_source` and not enabled | Skip this operator. Continue diagnosing any unexplained original failure. |

An original-workload `gap` does not change these entry rules. Start only after
diagnosis localizes an operator and only within the confirmed resource grant.

## Optimize

1. **Reproduce.** Rerun `operator_verdict.py` on the handoff operands and confirm
   the reading. Time the original operator on the target host as the baseline.
2. **Try cheap variants first.** Backend or kernel selection, a configuration
   switch, an upstream version that already fixed it, then the framework-level
   slow fix from the handoff (composed operators at higher precision). Measure
   each as a variant.
3. **Write a kernel** when no cheap variant is within the bound, or when one is
   but costs speed. GPU: Triton first, CUDA when Triton cannot express it. NPU:
   triton-ascend when available, otherwise Ascend C or a torch_npu custom
   operator. Follow the model's reference semantics (reduction dtype, rounding
   point, epsilon placement, weight offset); higher precision than the reference
   is a separate variant, not a replacement.
4. **Measure in stages.** Screen each tried variant on captured operands. For
   kept variants and the baseline, measure captured, random and edge cases on
   the target path. Use actual repeat runs (at least two per case) and paired
   timings with the same inputs, warmup and sample count on one host and session.
   Omit `bound` to use the same computed bound as diagnosis unless an override
   is justified. Never substitute offline proxies or duplicate one output.
5. **Iterate** within `operator_optimize.max_attempts` and the accelerator-hour
   budget. Keep each attempt's source, spec and report.
6. **Verify end to end.** Load each kept variant into the original native case
   and rerun the original workload: record the acceptance metrics, the distance
   to the FP64 or FP32 reference where one exists, throughput (tokens/s or step
   time), TTFT/TPOT for serving, peak memory, and no regression on other paths.

```xml
<rules>
  The target is the FP64 reference, never the other side's output: a kernel
  tuned to match the other stack copies its rounding.
  A required optimization succeeds when a variant is within the bound and not
  worse than the slow fix on every checked shape; its end-to-end effect is
  measured and reported, and acceptance need not pass. An optional optimization
  succeeds when a variant moves its side closer to FP64 end to end.
  Keep the baseline and in-bound variants; being slower alone does not reject
  a precision improvement. If none meets the bound, report the best measured
  attempt and the remaining distance. Record screened-out attempts and reasons.
  Install kernels and toolchains only in case-private environments; load them as
  operator overrides. Do not change shared images, drivers or system packages.
  A missing toolchain that cannot be installed case-private is an access blocker.
  Optimization continues across rounds. A round ends when attempts or budget run
  out or the variants stop improving; deliver the best variant with its
  remaining distance to the bound and the next idea to try.
</rules>
```

## Deliver a round

Package with the [round validator](../precision-compare/scripts/operator_optimization.py).
For sharing and draft requirements, use the
[community guide](../precision-compare/references/community-records.md#operator-variants).

```xml
<round>
  <output>Under result/optimize/operator, save handoff, source, spec, report, timing and native results. Record attempts in diagnosis.json.</output>
  <state>In open.json record issue, round_status, entry, remaining_distance (relative_rmse, bound, excess), variants_not_yet_tried and next_step.</state>
  <reuse>If the slow fix was measured, link slow_fix.report and the reproduce.kernel_check command from the handoff.</reuse>
  <open when="coverage, timing or end-to-end results are missing">Use handoff_open. Keep measurements and name the next missing check.</open>
  <delivered when="required measurements are present">Use round_delivered. Include the baseline and kept changes. In each change's end_to_end record include status=measured, verdict, evidence and numeric metrics. Keep measured failures distinct from missing checks.</delivered>
  <draft>Draft only a kept change with verified source that improves toward the reference and applies to the target. Mark unverified draft checks pending.</draft>
  <rule>Deliver the current evidence without waiting for later rounds. Original acceptance is separate; precision tiers and recommendations belong to the server.</rule>
</round>
```
