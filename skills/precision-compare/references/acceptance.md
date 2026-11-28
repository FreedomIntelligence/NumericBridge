# Acceptance

Use [acceptance.py](../scripts/acceptance.py) with `nb-default-v1`. Fix the
profile before running and record it in the report. Users do not set thresholds.

```sh
python scripts/acceptance.py --report scores.json --exact-report exact.json \
  --noise-for scores.json repeat-scores.json --sides A B \
  --dtype bf16 --output acceptance.json
```

| Verdict | Meaning | Route |
|---|---|---|
| `pass` | All checks within the optimize target | Deliver |
| `gap` | Within acceptance, outside an optimize target | Deliver; report the optimize-target gap, or enter [precision optimization](../../operator-optimize/SKILL.md) once an operator is localized and `operator_optimize.enabled` |
| `fail` | An acceptance or exact check failed | [Diagnose](../../precision-repair/SKILL.md#diagnose) |
| `noise_limited` | Each failing check also fails on a same-side repeat | Fix nondeterminism or compare against repeat noise |
| `incomplete` | A required report is unavailable or the observed sides are not the requested pair | Recover the capture |

A same-capture comparison is useful as a self-check, not as A/B evidence. Bind
repeat noise to the corresponding comparison report with `--noise-for`; it applies
only to the same check and logical objective or tensor. Use unchanged execution
conditions for the repeat.

## Repair completion evidence

Save a `tested-identity.json` with the supported native capture, based on what
actually ran:

- `workload_sha256`: hash the original workload declaration, including the case's
  model, input, starting-state and observation identities.
- `base_sha256`: hash the pinned runtime/base identity for the comparison, before
  any candidate changes.
- `change_sha256`: hash the exact patch bytes, or the immutable change manifest
  for a settings change, upgrade or combination. Use `null` for an unchanged run.

Use stable serialized manifests or pinned files so the same workload and base
have the same digests across attempts. Pass `--tested-identity tested-identity.json`
when judging that capture. In a workflow plan, declare the capture's identity as
an output and pass it by artifact reference from a declared dependency. Produce
new evidence for a changed version. Do not attach a new identity to old metrics.

For standard A/B evaluation or another acceptance completion goal, freeze
`request.json`'s `original_contract` before formal
capture. Use the workload and base digests above and `criterion_sha256` from
`acceptance.py --criterion-only --dtype DTYPE --optimizer OPTIMIZER`. Use those
same settings and `--exact-report` for the formal verdict. Do not copy settings
or digests back from a successful candidate to change the original target.

In the final candidate entry, record `base_sha256` and `patch_sha256` (or
`change_sha256`). Cite the same acceptance JSON in `original.evidence` and
`original_workload.evidence`. Keep the unchanged baseline report, or record an
original execution failure as `baseline.native_failure` with `verdict: not_run`
and links to the initial failure and its reproduction. Paths are relative to the
case directory unless absolute. The validator checks identity and evidence links;
it does not attest runners or rerun the workload.

## Default limits (BF16, same checkpoint)

| Check | Accept | Optimize |
|---|---|---|
| Target logprob MAE / P99 | ≤ 0.1 / ≤ 0.5 | ≤ 0.02 / ≤ 0.1 |
| Target NLL relative difference | ≤ 1% | ≤ 0.2% |
| Tail-merged JS mean / P99 (nats) | ≤ 1e-3 / ≤ 1e-2 | ≤ 1e-4 / ≤ 1e-3 |
| Top-1 agreement (≥ 100 positions) | ≥ 0.95 | ≥ 0.99 |
| Decisive top-1 flips (both margins ≥ 0.1) | 0 | 0 |
| Objective relative difference | ≤ 1% | ≤ 0.1% |
| Global gradient cosine / norm ratio | ≥ 0.99 / [0.95, 1.05] | ≥ 0.999 / [0.99, 1.01] |
| Worst tensor gradient cosine / norm ratio | ≥ 0.95 / [0.9, 1.1] | ≥ 0.99 / [0.98, 1.02] |
| Update cosine, SGD / Adam-family | ≥ 0.99 / ≥ 0.8 | ≥ 0.999 / ≥ 0.99 |
| Optimizer recomputation relative error | ≤ 1e-3 | ≤ 1e-5 |
| Advantage max abs (rewards equal) | ≤ 1e-3 | ≤ 1e-5 |

Pass `--dtype` as the lower-precision compute dtype of the two sides. FP32 paths
use the tighter `FP32_LIMITS` in the tool; FP16 uses BF16 limits. Give
`compare_arrays`/`compare_trees` reports as `--array-report STAGE PATH`.
The profile does not cover quantized paths (FP8, INT8, weight-only). Report
measured metrics and coverage without claiming a formal pass. Norm ratios catch
scaling and denominator errors that leave cosine near 1. Adam-family updates
amplify sign flips of near-zero gradients, so pair their loose update cosine
with each side's optimizer recomputation from its own gradient and state.

## Exact checks

Check task-required exact values: tokens, positions, masks, rule-based rewards,
valid counts, inventory, gradient presence and optimizer events as applicable.
Build the report from the same captures and pass it with `--exact-report`:

```json
{"kind":"exact-checks","checks":{"token-ids":true,"loss-mask":false},"missing":[]}
```

List required observations not captured in `missing`; do not mark them `false`.
An observed exact mismatch fails even with noise or missing data. Otherwise
missing coverage is `incomplete`. The report does not replace numerical metrics.

Limits judge agreement between the two sides. A semantic defect can stay within
them (for example a disabled attention window measured 0.007 logprob MAE), so
keep reference and invariant checks where the task defines them.

## Agreement and accuracy

`nb-default-v1` judges agreement between A and B. It does not say which side is
more accurate: a patch that moves one side closer to FP64 can widen the A/B gap
when the two sides' errors previously cancelled. Report accuracy separately, per
localized operator: the RMSE and maximum error of A and of B against an FP64
recomputation from the same captured operands, before and after any patch.

| A vs FP64 | B vs FP64 | Reading |
|---|---|---|
| near | far | B deviates at this operator |
| far | near | A deviates at this operator |
| far | far | Shared or dtype-inherent policy; neither side is defective here |
| near | near | Not the source; trace the differing input upstream |

"Near" is the error expected for the dtype and reduction length; state the
bound used. Operator verdicts do not attribute the end-to-end difference.
