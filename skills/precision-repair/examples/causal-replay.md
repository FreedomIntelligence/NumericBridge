# Causal replay examples

## Same-state replay of a training step

Scalar SGD, learning rate `0.1`, `w=0`, targets `[1, 3]` in two microbatches.
The intended objective `L(w) = mean((w-y)**2)/2` has gradient `w-2`.

| Step | Native `w` before | Native gradient | Expected at native `w` | Correct-trajectory `w` | Correct gradient |
|---|---:|---:|---:|---:|---:|
| 1 | 0 | -4 | -2 | 0 | -2 |
| 2 | 0.4 | -3.2 | -1.6 | 0.2 | -1.8 |

At step 2, `-3.2` versus `-1.8` mixes the defect with the changed state.
Evaluating the expected gradient at the native state isolates a constant ratio
of 2: microbatch means were summed without window normalization. `triage.py`
reports this as a norm ratio near `2/1` with cosine 1, and the cheapest check is
the FP64 recomputation of the objective denominator. The regression against the
analytic gradient fails before and passes after the fix; rerunning from `w=0`
gives gradients `[-2, -1.8]` and parameters `[0.2, 0.38]`.

## Cross replay at a leaf boundary

Capture the boundary input on both sides (`xT`, `xI`), run each implementation on
both inputs, and replay each side on its own input:

```json
{"outputs": {"T(xT)": "t_on_t.npy", "T(xI)": "t_on_i.npy", "I(xT)": "i_on_t.npy", "I(xI)": "i_on_i.npy"},
 "inputs": {"xT": "x_t.npy", "xI": "x_i.npy"},
 "self": {"T": ["t_replay.npy", "t_captured.npy"], "I": ["i_replay.npy", "i_captured.npy"]},
 "reference": {"xT": "fp64_on_t.npy"}}
```

```sh
python scripts/replay.py cross --spec cross.json --output cross-report.json
```

| Result (distances = 1 − per-token cosine) | Label |
|---|---|
| `R` ≈ 1e-7, `S` ≈ 4e-4, `C_natural` ≈ 5e-4 | `local_source_candidate`; the reference names the deviating side |
| `R` ≈ 1e-7, `S` ≈ 2e-7, `P` ≈ `C_natural` | `propagation_candidate`; move upstream |
| `P / C_in` > 2 on either side | adds `amplification_supported`; perturb along `xI − xT` |
| `R` ≈ `C_natural` | `replay_invalid`; fix capture or state restoration first |

For a backward boundary, replay with the same incoming gradient on both sides.

## RL at a rollout boundary

When two online rollouts choose different tokens, later scores have different
histories. Compare up to the last shared history and keep later online behavior
as separate observations. To test the trainer, replay the same trajectories into
each trainer from matched weights and optimizer state; if loss inputs agree but
gradients diverge, localize the backward path. The fixed replay does not verify
the online rollout or policy handoff.
