---
name: precision-repair
description: Diagnose reproducible failures of supported native paths and numerical defects; verify fixes on the original workload.
---

# Precision repair

## Diagnose

1. **Reproduce.** Reuse the native trigger and evidence. For a preflight failure,
   confirm support on the tested revision, then retain the failing command,
   configuration and earliest failing operation. For a numerical difference,
   check input/state, settings and coordinate alignment before replaying it.
   Repeat only to resolve reproducibility or variation.
2. **Choose a boundary.** Keep the earliest observed divergence or failure and
   the closest checked upstream boundary that passed. For numerical differences,
   also consider later boundaries where the task-relevant error grows. Use
   [internal observations](../precision-compare/references/layerwise.md) only where
   they distinguish candidates. Error growth helps choose a probe; it does not
   identify the source.
3. **Test one explanation.** Inspect the source and vary one factor. For a
   numerical replay, first reproduce each side's own capture within justified
   tolerance. Then [replay the same operands and state](examples/causal-replay.md)
   on both sides, including the incoming gradient for a backward check. Try the
   other side's captured operands if input dependence could change the conclusion.

| Replay result | Next check |
| --- | --- |
| Self-replay fails | Check capture, state restoration and runtime dispatch. |
| A difference persists on matched inputs | Narrow to a child operation and check it against the intended algorithm, an invariant or an independent reference. |
| The difference falls within expected variation on matched inputs | Trace the differing inputs upstream. To test amplification, hold the implementation fixed and vary the suspect input. |
| A local check improves after a change | Check the original task endpoint. If it does not improve, retain the local finding and revisit the other candidates. |

An operation can introduce and amplify error. Use the task's existing metrics;
keep injected state and diagnostic constraints out of original-workload verification.

```xml
<diagnosis_handoff>
  In issues.md, classify the issue as setup/capture, unsupported capability,
  expected numerical policy, supported defect or unresolved. Link source and
  evidence, checked boundaries and ruled-out explanations. For each probe,
  record its hypothesis, control, finding and next check. Continue beyond
  localization while a focused check can advance the cause within scope and
  budget. If blocked, leave the concrete blocker and next check. For a supported
  defect, hand off the trigger and focused regression to authorized repair.
</diagnosis_handoff>
```

## Patch and verify

- Test one patch, upgrade or configuration change at a time. Keep case-specific
  patches with the case and reuse matching before evidence, inputs and settings.
- Show the focused regression fail before and pass after, then verify the
  original small native case. If run variation could explain an endpoint gain,
  restore the baseline and recheck. For an execution defect, verify the original
  failing path and its required evidence; report numerical coverage separately.
  Reuse the regression run when it covers both checks. Extend only for missing
  coverage or agreed scope.
- Use the agreed numerical tolerance or justify the regression tolerance from
  the computation and dtype. Measure runtime and memory when relevant.

Use [precision-compare](../precision-compare/SKILL.md#collect) for missing captures
or metrics, and [shared execution](../precision-compare/references/comparison.md#recover-an-interrupted-stage)
for resumed runs.

## Return

Update the same issues.md entry with each candidate, tested configuration,
before/after observations, regression and workload outcomes, evidence and
remaining checks. Mark verified only after both checks pass.
