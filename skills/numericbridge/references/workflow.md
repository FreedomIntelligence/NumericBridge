# NumericBridge workflow

## Stages and handoffs

Run dependent commands through a [workflow plan](../../precision-compare/references/comparison.md#shared-tasks)
(`workflow.py init` creates one per task); it reuses verified stages, runs
independent sides in parallel and returns the next skill on failure.

```xml
<handoff>
  Carry the request, plan, workspace, runtime and workload identities, native
  job/process IDs, evidence, diagnosis.json, remaining budget and next action.
  Continue authorized work without pausing at progress updates; advance
  independent work when one path blocks. In a batch, prioritize actionable open
  issues over new cases within budget.
</handoff>

<delegation when="a case is delegated">
  One owner per case covers target and reference from comparison through
  diagnosis and original-workload verification; split independent cases for
  parallel work. Give the owner the request, grant, budget and checkpoint. The
  coordinator checks owner state on progress requests and worker returns,
  reconciles native runs before replacing a worker, and asks the user only for a
  missing decision or grant.
</delegation>

<issue_record>
  Record every finding that affects execution or results, including setup
  blockers, in the case's diagnosis.json (precision-repair diagnosis_state.py);
  render issues.md from it. Distinguish workarounds from fixes on the original path.
</issue_record>
```

## Request

Start from the [confirmed request](request.md); resolve routine setup, retries
and the listed autonomous decisions within its grant.

## Route community results

```xml
<entry>The target and task are confirmed.</entry>

<action>
  Call search_community with hardware, model, library and task (inference, sft or
  rl); add version only when pinned and model_size when needed. If MCP is
  unavailable, run the same query with the bundled CLI.
</action>

<output>
  Save the search result with its status, source and index identity; distinguish
  no match from unavailable lookup. Matching issues and solutions become the first
  candidates when triage receives the result (--community).
</output>

<next>Prepare the environment with or without a match; keep pinned versions.</next>
```

Bundled CLI: [community_search.py](../../precision-compare/scripts/community_search.py)
(`--model-size` for `model_size`).

## Prepare and compare

### Prepare the environment

Use [framework-adapt](../../framework-adapt/SKILL.md#execute) and its
[native preflight](../../framework-adapt/SKILL.md#native-preflight).

```xml
<entry>Community lookup finished or was unavailable.</entry>

<action>
  Prepare target and reference independently, reusing matching environments and
  receipts. Confirm device binding and preflight each unverified native path with
  its collectors.
</action>

<output>
  Side labels, execution JSON, runtime and resource receipts, workload,
  readiness, diagnosis-readiness fields and uncovered paths.
</output>

<next>
  Compare ready paths. On a failed preflight, recover an interrupted run first,
  then act on the receipt's failure category: correct setup_or_resource within
  scope, report unsupported capabilities, and send reproducible failures of a
  supported path to diagnosis with the native reproducer.
</next>
```

### Run the comparison

Use [precision-compare](../../precision-compare/SKILL.md#collect) and its task
recipe. Without a specified dataset, use the
[fixed 100-example batch](../../precision-compare/examples/precision-batch-v1.json)
in order on both sides ([task views](../../precision-compare/examples/minimal-precision-batch.md)).

```xml
<entry>
  Native capture is ready. Without a usable reference, run only standalone checks
  and state their limits.
</entry>

<action>
  Align inputs, initial state and coordinates; declare intended differences. For
  online RL, sample natively and pair only matching histories. Judge the reports
  with acceptance.py, passing exact checks and same-side repeat noise.
</action>

<output>
  Workload and side identities, paired observations by stage, coverage gaps,
  metrics with coordinates, and the acceptance verdict with its profile.
</output>

<next>
  Follow the verdict's route: pass or gap → deliver (gap offers optional
  precision optimization); fail → diagnosis with the acceptance report;
  noise_limited → stabilize the runs; incomplete → recover the capture.
</next>
```

### Choose a reference

```xml
<reference>
  Prefer executable access to both sides. With only the target accelerator,
  recompute suitable checkpoints on CPU (FP64 where appropriate) and report their
  coverage. Accuracy claims need an analytic result, a validated independent
  implementation or a higher-precision path; otherwise report observed
  differences, repeatability and invariant checks only.
</reference>
```

## Diagnose a difference

Use the [diagnosis loop](../../precision-repair/SKILL.md#diagnose).

```xml
<entry>
  A failed acceptance or a reproducible failure of a supported native path, with
  its evidence recorded.
</entry>

<action>
  Run triage with the acceptance report, comparator reports and the community
  result; start diagnosis.json; execute next checks cheapest first.
</action>

<output>diagnosis.json with labels, checked boundaries, ruled-out causes and evidence.</output>

<next>
  On config_mismatch, correct setup and rerun the comparison. Route a supported
  defect to repair. Stop at the loop's stop decision and deliver open issues with
  their blocker and next check; keep independent issues moving meanwhile.
</next>
```

## Repair and verify

Use the [repair method](../../precision-repair/SKILL.md#patch-and-verify).

```xml
<entry>
  Repair is authorized and the defect has evidence, a reproducer and a baseline.
</entry>

<action>
  Test one candidate at a time on the original small native case; extend only for
  missing coverage or an agreed requirement.
</action>

<output>
  Each candidate in diagnosis.json. Verified when the focused regression and the
  original-case acceptance both pass.
</output>

<next>
  After fixing a preflight blocker, re-enter comparison. Otherwise deliver; on
  failure, return to diagnosis or try the next candidate within budget.
</next>
```

## Local delivery

```xml
<entry>
  No required check remains actionable within authorization and budget; finish
  active in-scope runs first.
</entry>

<action>
  Reconcile execution, comparison, diagnosis and repair against the request and
  evidence (workflow state.json, acceptance report, diagnosis.json).
</action>

<output>
  Mark each stage complete, partial, blocked or not applicable (outside scope, or
  its trigger did not fire). A CPU slice does not complete an agreed dual-native
  comparison. Give the configuration, coverage, acceptance verdict and profile,
  runnable setup and evidence links; link issues.md for open issues. Provide
  reports, bundles or offline replay when requested.
</output>

<next>
  Output “NumericBridge 链路已完成” only when every required stage has completion
  evidence. Then, or after a shareable partial delivery, offer community
  contribution.
</next>
```

## Community contribution

Use the [community record guide](../../precision-compare/references/community-records.md).

```xml
<contribute when="local delivery has a shareable result or a contribution is being resumed">
  Honor a prior decline. Prepare an anonymized draft with minimum evidence and a PR
  draft for any verified patch intended for upstream. Fix findings from
  prepare_community_submission until it returns ready, show the exact preview and
  destination, then call submit_community with its draft ID and digest; that form
  is the approval. Decline or dismissal ends the offer. If submission is
  unavailable, keep the local draft and report its path. A receipt means pending
  review; call the result searchable only when the deployed index returns its IDs.
  Contribution status does not change local delivery status.
</contribute>
```

Without MCP, keep the anonymized draft and use the
[local preview](../../precision-compare/references/community-records.md#local-preview).
