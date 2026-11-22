---
name: precision-repair
description: Diagnose reproducible failures of supported native paths and numerical defects; verify fixes on the original workload.
---

# Precision repair

Run tools from this skill directory. Each tool's `--help` gives its interface.

| Step | Tool | Output |
|---|---|---|
| Rank causes from the comparison | [triage](scripts/triage.py) | Ordered candidates with their cheapest check |
| Find where the difference appears | [localize](scripts/localize.py) | Boundary growth ranking and the next capture |
| Separate local source, propagation and amplification | [replay](scripts/replay.py) | Cross-replay label, perturbation gains, intervention verdicts |
| Track hypotheses and checks | [diagnosis_state](scripts/diagnosis_state.py) | `diagnosis.json`, next open check, rendered `issues.md` |

## Diagnose

Start a state file from the trigger, then loop on `next` until it reports a stop:

```sh
python scripts/triage.py --acceptance acceptance.json --report scores.json [--community search.json] --output triage.json
python scripts/diagnosis_state.py init --state diagnosis.json --acceptance acceptance.json --triage triage.json
python scripts/diagnosis_state.py next --state diagnosis.json
python scripts/diagnosis_state.py record --state diagnosis.json --hypothesis H1 --label LABEL --finding TEXT --evidence PATH
```

1. **Check alignment.** Exact-check failures (inputs, masks, rewards, counts,
   events) are `config_mismatch`: correct setup or capture and rerun the
   comparison. For a preflight failure, confirm support on the tested revision and
   keep the failing command, configuration and earliest failing operation.
2. **Run the cheapest candidate.** Triage orders checks CPU recomputation first,
   then replay, then native capture. Scaling, reduction, mask and optimizer
   signatures are usually settled by an FP64 recomputation of the objective,
   gradient or update from captured operands, without a native rerun.
3. **Localize broad differences.** Capture block boundaries for a token subset on
   both sides plus one same-side repeat with
   [capture_boundaries.py](../precision-compare/scripts/capture_boundaries.py)
   ([procedure](../precision-compare/references/layerwise.md)), rank them with
   `localize.py`, then capture the children it names. Several candidate intervals
   can share one capture.
4. **Cross-replay the leaf.** Save the leaf's call on both sides and run each
   side's module on both calls with `replay_module`
   ([example](examples/causal-replay.md)); include the incoming gradient for a
   backward boundary. Add an FP64 reference output when available to tell which
   side deviates. `replay.py` assigns the label and next check.

| Label | Meaning | Next check |
|---|---|---|
| `config_mismatch` | Inputs, state or settings differ | Correct setup, rerun comparison |
| `replay_invalid` | Self-replay does not reproduce the capture | Check capture, state restoration, dispatch |
| `local_source_candidate` | Difference persists on matched inputs | Narrow to a child op; compare with reference or invariant |
| `propagation_candidate` | Matched-input difference is within noise | Trace the differing input upstream |
| `amplification_supported` | Output difference grows faster than the input difference | Perturb along the real input difference; check upstream source too |
| `local_source_confirmed` | Child op deviates from reference or invariant | Intervention test |
| `primary_cause_confirmed` / `secondary_factor_confirmed` | Intervention verdict | Repair or deliver |
| `not_reproduced` / `unresolved` | Trigger absent / no actionable check left | Deliver with the blocker and next check |

A local improvement counts only when the task endpoint also improves. Keep
injected state and diagnostic constraints out of original-workload verification.

**Stop** when a hypothesis reaches a confirmed label, the budget is spent, or no
open hypothesis has an actionable check. Ruled-out hypotheses are not retried.
Classify the conclusion as setup/capture, unsupported capability, expected
numerical policy, supported defect or unresolved; `diagnosis_state.py render`
writes `issues.md` with checked boundaries, ruled-out explanations and evidence.

## Patch and verify

- Test one patch, upgrade or configuration change at a time, ordered by cost:
  recorded community fix, configuration switch, upstream fix, new patch. Keep
  case-specific patches with the case.
- Build the focused regression from the captured operands with an FP64 or
  analytic expected value; show it fails before and passes after.
- Run the intervention set on the original small native case: baseline, change,
  revert (difference returns), an extra sample; for two interacting factors also
  run A, B and A+B. `replay.py interventions` judges each factor against repeat
  noise and labels primary or secondary causes and interactions.
- For an execution defect, verify the original failing path and its required
  evidence; report numerical coverage separately. Extend beyond the small case
  only for missing coverage or an agreed requirement; measure runtime and memory
  when relevant.

A fix is verified when the focused regression and the original-case
[acceptance](../precision-compare/references/acceptance.md) both pass. Record each
candidate, configuration, before/after values and evidence with
`diagnosis_state.py record`. Use [precision-compare](../precision-compare/SKILL.md#collect)
for missing captures and [shared execution](../precision-compare/references/comparison.md#recover-an-interrupted-stage)
for resumed runs.
