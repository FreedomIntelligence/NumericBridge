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
| Say which side deviates at an operator | [operator_verdict](scripts/operator_verdict.py) | Per-side error vs FP64, the stated bound, reading and patch direction |

## Diagnose

Start a state file from the trigger, then loop on `next` until it reports a stop:

```sh
python scripts/triage.py --acceptance acceptance.json --report scores.json [--community search.json] --output triage.json
python scripts/diagnosis_state.py init --state diagnosis.json --acceptance acceptance.json --triage triage.json
python scripts/diagnosis_state.py next --state diagnosis.json
python scripts/diagnosis_state.py record --state diagnosis.json --hypothesis H1 --label LABEL --finding TEXT --evidence PATH
```

Principles for each check:

1. **Reproduce the observation.** Reuse the native small case, identities,
   collectors and captures. Check that inputs, initial state, effective settings
   and observed coordinates are comparable; resolve capture or configuration
   mismatches first. Repeat only when needed to distinguish a stable discrepancy
   from run-to-run variation. Preserve the intended execution semantics.
2. **Choose the next boundary.** Start with existing stage captures. Keep the
   first observed divergence and any later boundary that substantially increases
   the error relevant to the task. Add [internal observations](../precision-compare/references/layerwise.md)
   only where they distinguish these candidates. Record the nearest upstream
   agreement and gaps in coverage; a large difference ranks a candidate but
   does not identify its cause.
3. **Run a discriminating replay.** First reproduce each side's own captured
   result within the justified tolerance. At the chosen boundary, give both
   implementations the same actual operands and state, including the incoming
   gradient for a backward check. Use the other side's captured operands if
   input dependence could change the conclusion. See [causal replay](examples/causal-replay.md).

| Replay result | Next check |
| --- | --- |
| A native self-replay fails | Check capture, state and dispatch before attributing a cause. |
| A difference persists with matched inputs | Narrow to a child operation; compare with the intended algorithm, an invariant or an independent reference. |
| The difference falls within expected variation with matched inputs | Trace back to the differing inputs. If amplification matters, hold the implementation fixed and vary only the suspect input. |
| A single change improves the local check | Check its effect on the original task endpoint. If that endpoint does not improve, keep the local finding and revisit the remaining candidates. |

An operation can introduce and amplify error at the same time. Keep injected
state and diagnostic constraints out of original workload verification. Use
the task's existing metrics and comparable observations.

```xml
<diagnosis_handoff>
  Update the case's local issues.md with the discrepancy, paired coordinates,
  coverage and checked boundaries, including gaps. For each probe, record the
  hypothesis, one-factor control, finding and next check. Cite supporting evidence
  and the source revision and code when identified. Classify the discrepancy
  as a capture or setup problem, a numerical-policy difference, a supported
  defect, or unresolved.
  Stage localization is a checkpoint unless it meets an explicit scope limit.
  Otherwise, reuse the triggering case to narrow the interval until evidence
  establishes a cause or no focused check or pending result can advance diagnosis
  within the authorized scope and budget. Run the smallest check that can
  distinguish the remaining explanations. If blocked, report the narrowest
  supported interval, what was ruled out, the concrete blocker and the next check.
  For a supported defect, propose a patch and pass a focused regression check
  to verification when repair is in scope.
</diagnosis_handoff>
```

Loop steps:

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
   backward boundary. Recompute the leaf in FP64 on CPU from the same operands
   to tell which side deviates ([agreement and accuracy](../precision-compare/references/acceptance.md#agreement-and-accuracy));
   `operator_verdict.py` applies that table with a stated dtype bound. Either
   side may be the one to repair. `replay.py` assigns the label and next check.

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

A local improvement repairs the task only when the task endpoint also improves;
otherwise keep it as an operator finding. Keep
injected state and diagnostic constraints out of original-workload verification.

**Stop** when acceptance is restored, the budget is spent, or no open hypothesis
has an actionable check. A confirmed secondary factor does not stop diagnosis
while the trigger remains unexplained: open a child for the remaining
difference. Ruled-out hypotheses are not retried.
Record a hypothesis as blocked only with `--blocker access|budget|no_actionable_check`;
`next` keeps returning a blocked hypothesis without one, and a finding that names
a next check is not `no_actionable_check`. An execution error met
in an optional or diagnostic branch (crash, out-of-range index, ignored setting)
opens its own hypothesis with a minimal reproducer instead of skipping the branch;
setup and adapter mistakes that do not affect results go in the case log.
Mark an issue stack-sensitive when a same-hardware stack control also fails; its
target is an operator fix closer to FP64 with no original-case regression.
Classify the conclusion as setup/capture, unsupported capability, expected
numerical policy / inherent difference (every localized operator within its
FP64 bound; recorded via `diagnosis_state.py conclude --classification
expected_policy` or `inherent_difference`), supported defect, or unresolved;
`diagnosis_state.py render` writes `issues.md` with checked boundaries,
ruled-out explanations and evidence.

## Hand off to optimization

For each localized operator judged with `operator_verdict.py`, write
`result/optimize/<operator>/handoff.json` in the
[handoff format](../operator-optimize/references/handoff.md) and route it by the
reading: outside the bound requires [operator optimization](../operator-optimize/SKILL.md);
within the bound enters it only when `operator_optimize.enabled`. Include the
slow fix when one exists and the operator's end-to-end contribution with only
that operator changed. Keep diagnosing the remaining difference meanwhile.

## Patch and verify

- Either side can be patched. Classify each candidate as a semantic or
  execution defect (wrong function, out-of-bounds index or capacity, ignored
  setting) or a numerical-policy difference (rounding point, accumulation order).
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

- For a numerical-policy difference, compare the library with the model's
  reference implementation (for example its Transformers modeling code) before
  deciding on a draft; record whether the reference keeps higher precision.
- Verify a numerical-policy patch at the operator: it moves the patched side
  closer to FP64 on the captured operands (`operator_verdict.py` with `patched`
  reports `closer`). On the original case it must not
  regress the task endpoint beyond repeat noise without an explanation; when
  repeat noise is zero (deterministic runs), judge regression against the
  acceptance profile's limit for that metric instead. A wider
  A/B gap alone does not reject it, since the sides' errors can cancel; report
  the agreement change as a secondary result.
- Before a PR draft, record the [upstream status](../numericbridge/references/workflow.md#stages-and-handoffs)
  at main, the latest release and the pinned ref (Compare's
  [upstream_status.py](../precision-compare/scripts/upstream_status.py) reads
  merged code from a local clone) and verify any backport on the user's pinned
  version. Inspect the target repository's PR template and contribution guide
  with [pr_draft.py](../precision-compare/scripts/pr_draft.py) `inspect --repo`
  (for example DCO/Signed-off-by, Megatron/vLLM checklists, or template
  headings); when the repository defines none, `pr_draft.py` applies the
  pre-agreed NumericBridge fallback format (`Problem / Change / Validation /
  Patch`) and records `fallback_used: true`.

A defect fix is verified when the focused regression and the original-case
[acceptance](../precision-compare/references/acceptance.md) both pass (for an
execution defect, the original failing path and its required evidence). A
numerical-policy patch is verified by the operator rule above; record whether
original-case acceptance changed. Record each
candidate, configuration, before/after values and evidence with
`diagnosis_state.py record`. Use [precision-compare](../precision-compare/SKILL.md#collect)
for missing captures and [shared execution](../precision-compare/references/comparison.md#recover-an-interrupted-stage)
for resumed runs.

## Declare contribution decisions

Before local delivery, give every confirmed or blocked hypothesis one decision
and pass the audit:

```sh
python scripts/diagnosis_state.py decide --state diagnosis.json --hypothesis H3 \
  --ownership library_defect --decision pr_draft_main --reason still_present \
  --repository OWNER/REPO --target-branch main --upstream-main present \
  --upstream-release present --upstream-pinned present --draft result/upstream/H3/ \
  --check-item applies_cleanly=pass --check-item repo_style_test=pass \
  --check-item native_on_target=fail --check-item performance=na --check-item template_body=pass
python scripts/diagnosis_state.py audit --state diagnosis.json
python scripts/diagnosis_state.py decisions --state diagnosis.json --output result/contribution-decisions.json
```

- **Ownership** names who changes the code: `library_defect`, `numerical_policy`,
  `environment` (a launch, dependency or host hazard that does not reproduce in a
  clean documented environment), `adapter` (the case's own integration) or
  `unlocalized`. `environment` needs that clean-environment evidence or the
  recorded image name and digest; without either, use `unlocalized`.
- **Decision** follows the upstream state: present on main → `pr_draft_main`;
  fixed on main but present on the pinned ref → `backport_draft` for the
  distribution the user installs; environment, adapter or matching-reference
  results → `record_only`; nothing to share → `no_action`.
- **Numerical policy** is decided by the reference first: when the model's
  reference implementation keeps higher precision than the library and the patch
  moves the operator closer to FP64, the decision is a draft with
  `reference_semantics`; the end-to-end change goes into the draft as a
  secondary result and does not veto it. An operator confirmed outside its FP64
  bound gets a draft on that evidence alone; whether the original case passes
  acceptance does not decide it. `benefit_measured` covers a patch closer
  to FP64 without a reference preference. `matches_reference` or
  `no_measured_benefit` (record only) apply only when the reference does not keep
  higher precision.
- Decide each issue from its current evidence and these rules; re-derive a
  decision carried over from an earlier session instead of copying it. Treat
  issues with the same evidence pattern the same way.
- A draft with a failed or missing check is still submitted, marked incomplete
  with that check named.
