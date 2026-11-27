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
| Separate local source, propagation and amplification | [replay](scripts/replay.py) | Cross-replay label, perturbation gains, measured intervention effects |
| Track hypotheses and checks | [diagnosis_state](scripts/diagnosis_state.py) | `diagnosis.json`, next open check, rendered `issues.md` |
| Decide whether to run, resume or deliver | [diagnostic_round](scripts/diagnostic_round.py) | Budget, existing job receipts, reuse, bounded outcome and probe gate |
| Say which side deviates at an operator | [operator_verdict](scripts/operator_verdict.py) | Per-side error vs FP64, the stated bound, reading and patch direction |

## Diagnose

Run triage on the evidence you already have. Start a state file from the
acceptance report or a [reproduced native failure](references/diagnostic-round.md#plan-spend-and-resume),
then use `next` to choose the next check:

```sh
python scripts/triage.py --acceptance acceptance.json --report scores.json [--community search.json] --output triage.json
python scripts/diagnosis_state.py init --state diagnosis.json --acceptance acceptance.json --triage triage.json
python scripts/diagnosis_state.py next --state diagnosis.json
python scripts/diagnosis_state.py record --state diagnosis.json --hypothesis H1 --label LABEL --finding TEXT --evidence PATH
```

For a native failure, pass its run summary to triage with `--report` and use
`--native-failure` to initialize the state instead of `--acceptance`.

1. **Check comparability.** Verify inputs, state, effective settings and readout
   coordinates. For a native fault, record the tested revision, failing command
   and earliest failing operation. Test exact or related community cases against
   this workload before reusing their fixes.
2. **Narrow the boundary.** Use existing captures to find the nearest upstream
   agreement, first observed difference and later error growth. Prefer offline
   checks; sample [internal boundaries](../precision-compare/references/layerwise.md)
   only if that can distinguish plausible causes. Reuse a valid same-side repeat.
3. **Test the cause.** Reproduce each side's own result, then replay both with
   the same captured operands and state (including incoming gradients).
   [Cross-replay](examples/causal-replay.md) on the other side's inputs when it
   can change the result. Attribute with an invariant, analytic result or FP64
   recomputation from those operands (`operator_verdict.py`).

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
  <output>In issues.md, record the discrepancy, coordinates, coverage, gaps and checked boundaries.</output>
  <probe>Record hypothesis, one-factor control, finding and next check. Cite evidence, revision and code when known.</probe>
  <classify>Setup or capture problem, numerical-policy difference, supported defect or unresolved.</classify>
  <continue>Stage localization is a checkpoint unless the scope ends there. Reuse the case and run the smallest check that separates the remaining explanations.</continue>
  <blocked>Once no check or pending result can help within the grant, record the narrowest proven interval, ruled-out causes, blocker and next check.</blocked>
  <repair when="supported defect and repair in scope">Hand off a candidate patch and focused regression.</repair>
</diagnosis_handoff>
```

For broad differences, rank a small paired boundary capture with `localize.py`
and narrow only the relevant intervals. Recompute scaling, loss or updates from
captured operands before launching another native run. Use same-hardware stack
controls only when changing the stack can distinguish a hypothesis. A failed
control shows stack sensitivity; it does not rule out a hardware contribution.

| Label | Meaning | Next check |
|---|---|---|
| `config_mismatch` | Inputs, state or settings differ | Correct setup, rerun comparison |
| `replay_invalid` | Self-replay does not reproduce the capture | Check capture, state restoration, dispatch |
| `local_source_candidate` | Difference persists on matched inputs | Narrow to a child op; compare with reference or invariant |
| `propagation_candidate` | Matched-input difference is within noise | Trace the differing input upstream |
| `amplification_supported` | Output difference grows faster than the input difference | Perturb along the real input difference; check upstream source too |
| `local_source_confirmed` | Child op deviates from reference or invariant | Intervention test |
| `primary_cause_confirmed` / `secondary_factor_confirmed` | Controlled evidence ties a cause to the original failure | Repair; keep any unexplained part open |
| `not_reproduced` / `unresolved` | Trigger absent / no actionable check left | Deliver with the blocker and next check |

A local improvement does not establish original-task acceptance. Keep injected
state and diagnostic constraints out of original-workload verification.

For multi-check or resumed cases, reconcile the
[diagnostic round ledger](references/diagnostic-round.md) before allocating more
native resources. `diagnosis_state.py next` routes hypotheses; it does not prove
acceptance or account for remaining accelerator time. Use only usable probe
results for attribution. To investigate the probe itself, wait for its terminal
result with `after`; that follow-up is diagnostic-only.

- `recompare`: correct the active setup or capture mismatch and rerun a comparable
  comparison. Record `--label config_corrected --evidence RERUN` on that hypothesis
  once the rerun demonstrates the correction, even if the discrepancy persists.
- `check` / `refine`: continue a discriminating check within the grant. Keep any
  `confirmed_primary` findings while investigating the remaining difference.
- `repair`: hand off the confirmed cause only if repair is authorized. This is
  not a pass or an instruction to exceed limits.
- `stop`: no further diagnosis is proposed. If acceptance has not been restored,
  deliver the proved boundary, its limits and the condition that would reopen it.

Do not retry ruled-out or inconclusive checks without a material change in
inputs, runtime, coverage or method.
Record a hypothesis as blocked only with `--blocker access|budget|no_actionable_check`;
`next` keeps returning a blocked hypothesis without one, and a finding that names
a next check is not `no_actionable_check`. An execution error met
in an optional or diagnostic branch (crash, out-of-range index, ignored setting)
opens its own hypothesis with a minimal reproducer instead of skipping the branch;
setup and adapter mistakes that do not affect results go in the case log.
Classify the conclusion as setup/capture, unsupported capability, expected
policy, inherent difference, supported defect or unresolved. An in-bound
operator alone does not establish an inherent difference; explain the original
failure with evidence or keep it unresolved.
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

Tools: [intervention effects](scripts/replay.py),
[acceptance and measured identity](../precision-compare/references/acceptance.md#repair-completion-evidence),
[interrupted run recovery](../precision-compare/references/comparison.md#recover-an-interrupted-stage).

```xml
<repair>
  <candidate>Either side can be repaired. Try the lowest-cost applicable known fix, setting, upgrade or patch.</candidate>
  <baseline>Reuse a comparable baseline. Change one thing at a time.</baseline>
  <check when="execution defect">The original failing path must produce its required outputs.</check>
  <check when="semantic defect">Demonstrate that the same regression fails before and passes after.</check>
  <check when="precision change">Use the same operands and an analytic or FP64 reference. Separate improvement from meeting the bound; check the model's reference precision.</check>
  <effect>Report changes to the task metric separately. A wider A/B gap alone does not distinguish exposed errors from a new regression.</effect>
  <control>Attribute effects with a controlled comparison, not the size of the improvement. Record revert controls when available.</control>
  <combine when="interaction could explain the result">Reuse baseline and single-change results. Measure the combined effect and its departure from their sum.</combine>
  <extend when="coverage or regression risk warrants it">Add samples. Measure runtime and memory when relevant.</extend>
  <verify>Require focused checks and independent acceptance on the exact final candidate or combination.</verify>
  <route when="execution restored">Return to comparison. Report execution recovery separately from the first acceptance verdict if none existed.</route>
  <output>Record candidate identity, paired metrics, coverage and both sets of evidence with diagnosis_state.py record. Reuse unaffected captures.</output>
</repair>
```

For a changed final candidate, put `focused_report` in the checkpoint candidate
entry and include that path in its `evidence`. Save a JSON record for the same
focused test before and after the change:

```json
{"kind":"focused-verification","schema_version":1,"defect_id":"mask-offset",
 "base_sha256":"<base digest>","change_sha256":"<final change digest>",
 "before":{"verdict":"fail","evidence":"evidence/focused-before.log"},
 "after":{"verdict":"pass","evidence":"evidence/focused-after.log"}}
```

Use `request.defect_id` for a specified defect. Keep the distinct execution
artifacts and a separate original-workload acceptance report.

Draft tools: [upstream status](../numericbridge/references/workflow.md#stages-and-handoffs),
[upstream_status.py](../precision-compare/scripts/upstream_status.py),
[pr_draft.py](../precision-compare/scripts/pr_draft.py).

```xml
<draft_precheck>
  <refs>Check merged code on main, latest release and the pinned ref. Verify backports on the pinned version.</refs>
  <template>Use pr_draft.py inspect --repo to check the target template and contribution guide, including DCO and required checklist items.</template>
  <fallback when="no template">Use Problem / Change / Validation / Patch and record fallback_used: true.</fallback>
</draft_precheck>
```

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
