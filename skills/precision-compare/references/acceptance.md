# Acceptance

Judge every formal comparison with [acceptance.py](../scripts/acceptance.py).
Without user criteria, use the default profile `nb-default-v1` and record it in
the delivery; user limits replace selected checks and are labeled `source=user`.

```sh
python scripts/acceptance.py --report scores.json --report gradients.json \
  [--noise repeat-a.json] [--dtype bf16|fp16|fp32] [--optimizer adaptive|sgd] --output acceptance.json
```

| Verdict | Meaning | Route |
|---|---|---|
| `pass` | All checks within the optimize target | Deliver |
| `gap` | Within acceptance, outside an optimize target | Deliver; report the optimize-target gap (precision optimization is a placeholder, not run) |
| `fail` | An acceptance or exact check failed | [Diagnose](../../precision-repair/SKILL.md#diagnose) |
| `noise_limited` | Each failing check also fails on a same-side repeat | Fix nondeterminism or compare against repeat noise |
| `incomplete` | A required report is unavailable or not covered | Recover the capture |

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
Quantized paths (FP8, INT8, weight-only) need user limits. Norm ratios catch
scaling and denominator errors that leave cosine near 1. Adam-family updates
amplify sign flips of near-zero gradients, so pair their loose update cosine
with each side's optimizer recomputation from its own gradient and state.

## Exact checks

Token IDs, positions, masks, rule-based rewards, valid target/action/group
counts, parameter inventory and gradient presence, optimizer events and required
observation coverage must match exactly. Pass them as an `exact-checks` report;
any mismatch fails before numerical limits apply.

Limits judge agreement between the two sides. A semantic defect can stay within
them (for example a disabled attention window measured 0.007 logprob MAE), so
keep reference and invariant checks where the task defines them.
