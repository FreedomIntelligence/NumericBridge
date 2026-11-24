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
| `not_the_source` and not enabled | Do not enter; diagnosis delivers an inherent difference |

An acceptance `gap` verdict with `operator_optimize.enabled` enters the same way
after diagnosis has localized an operator.

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
4. **Check every variant** with `kernel_check.py` on the captured shapes plus
   random and edge shapes (odd lengths, the largest reduction, extreme values),
   two or more repeats for reproducibility, and paired timing: the baseline and
   each variant timed on one host in one session, with warmup, a fixed sample
   count and the same inputs.
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
  Do not discard a variant for being slower: keep every variant that is within
  the bound, plus the baseline, and report precision and speed for each.
  Drop only variants that are worse in both precision and speed than another
  kept variant on the same shapes.
  Install kernels and toolchains only in case-private environments; load them as
  operator overrides. Do not change shared images, drivers or system packages.
  A missing toolchain that cannot be installed case-private is an access blocker.
  When attempts or budget run out, deliver the best variant with its remaining
  distance to the bound.
</rules>
```

## Deliver

Write `result/optimize/<operator>/` with each variant's source or patch, the
`kernel_check` spec and report, timing conditions, end-to-end results and the
handoff. Record each variant in diagnosis.json with `diagnosis_state.py record`
and give the issue its contribution decision as precision-repair describes; a
kernel against an upstream library is a PR draft there, with the measured speed
in the draft. Report all variants in the community record as described in the
[community guide](../precision-compare/references/community-records.md#operator-variants).
Measure and report; precision tiers and recommendations are assigned on the
community side, not here.
