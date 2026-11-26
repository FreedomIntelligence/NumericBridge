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

<continuous_run>
  Run the case in one continuous session until local delivery or an access
  blocker (request.md). Waiting is not a reason to end the turn: after launching
  a native job, follow it to its terminal status with a bounded blocking wait or
  by checking every few minutes, and advance independent work meanwhile. On a
  failure, read failure_class and the log tail, correct it within the grant and
  retry; route only unsupported or reproducible failures onward. An execution
  error in an optional or diagnostic branch (a crash, an out-of-range index, an
  ignored setting) is a finding, not a skipped branch: open a hypothesis with its
  minimal reproducer. A user follow-up within the case (a new question, check or
  draft) is continuous-run work under the same grant. Write the case state and
  handoff at each stage change as a checkpoint, validate the state with
  `case_state.py check`, then continue. Set needs_human only for an access
  blocker, after retrying with backoff (about every 10 minutes for up to 2 hours)
  and finishing independent work.
</continuous_run>

<delegation when="a case is delegated">
  One owner per case covers both sides from comparison through
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

<upstream_status when="a defect or policy difference is confirmed in a library">
  Check the owning repository's current main and latest release, including its
  pull requests, but count only merged code: treat open issues and unmerged PRs
  as absent: do not skip, narrow or reshape a draft to avoid overlap with them.
  They may be cited as related work. Check
  three refs from a local clone with `upstream_status.py`: current main, the
  latest release and the user's pinned version or distribution branch. Prefer
  full authoritative refs (for example `refs/remotes/origin/main`) and pass the
  resolved target commit to PR template inspection; a local checkout or PR ref
  is not an upstream version for build or adoption. Where a
  defect was found (the tested version) is not where it is
  delivered: fixed on main but present on the pinned ref → upgrade plus a
  backport draft against the distribution the user installs (for example a
  plugin release branch), verified on that version; present on main → a PR draft
  against main. Name the first fixed release and merge commit, and record why an
  upgrade is not possible when a dependency pins the version.
</upstream_status>
```

Case tools in Compare: [case_state.py](../../precision-compare/scripts/case_state.py)
(checkpoint fields and statuses) and [upstream_status.py](../../precision-compare/scripts/upstream_status.py)
(per-ref present/fixed state from merged code).

## Request

Start from the [confirmed request](request.md); resolve routine setup, retries
and the listed autonomous decisions within its grant.

## Route community results

Community lookup is optional. Use the configured public search MCP at
`https://numericbridge.com/mcp` (Streamable HTTP; no credential required), or
query an explicitly provided snapshot with the bundled CLI. No records or index
are bundled, and the workflow does not fetch an index automatically.

```xml
<entry>The target and task are confirmed.</entry>

<action>
  If community lookup is available, call search_community with hardware,
  model, library and task (inference, sft or rl); add version only when pinned
  and model_size when needed. If MCP is unavailable, use the bundled CLI only
  with a supplied index. Otherwise record lookup as unavailable and continue.
</action>

<output>
  When a lookup succeeds, save its status, source and index identity. A
  successful empty lookup is not the same as unavailable lookup. Matching
  issues and solutions become the first candidates when triage receives the
  result (--community); omit that input when lookup was unavailable.
</output>

<next>Prepare the environment with or without a match; keep pinned versions.</next>
```

Bundled CLI: [community_search.py](../../precision-compare/scripts/community_search.py)
(required `--index`: local snapshot or explicitly selected HTTPS URL; optional
`--sha256`: expected digest; `--model-size` for `model_size`).

## Prepare and compare

### Prepare the environment

Use [framework-adapt](../../framework-adapt/SKILL.md#execute) and its
[native preflight](../../framework-adapt/SKILL.md#native-preflight).

```xml
<entry>Community lookup finished or was unavailable.</entry>

<action>
  Prepare both sides independently, reusing matching environments and
  receipts. Confirm device binding and preflight each unverified native path with
  its collectors. In each preflight worker, collect the environment receipt
  (`precision-compare/scripts/environment_receipt.py` or `tools.environment_receipt`) after the workload reaches its runtime path:
  framework, backend plugin, runtime, driver and launch image, keeping unknown
  distinct from not applicable.
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
  Native capture is ready. With only one executable side, run only standalone checks
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
  Follow the verdict's route: pass → deliver; gap → deliver, or diagnose toward
  precision optimization when operator_optimize.enabled; fail → diagnosis with
  the acceptance report;
  noise_limited → stabilize the runs; incomplete → recover the capture.
</next>
```

### Sides and ground truth

```xml
<sides>
  Label the two stacks A and B. Neither is assumed correct and either can be
  repaired. The end-to-end comparison measures agreement only and does not
  decide which side deviates. Attribute a deviation only against a ground truth:
  an analytic result, an invariant, or an operator recomputed in FP64 on CPU
  from the captured operands (cheap and needs no third device; see
  acceptance.md#agreement-and-accuracy). A whole-model higher-precision or
  third-stack reference is optional and must not share kernels with either side.
  When a cross-side fail persists, run one same-hardware stack control (one side
  with another engine or toolkit version) and report its difference beside the
  cross-side verdict; it shows how much the software stack alone moves the
  result. When the stack control also fails, mark the issue stack-sensitive: the
  difference is not tied to the hardware, so the goal is an operator-level fix
  (closer to FP64 on the captured operands) with no regression of the original
  case, not closing the A/B gap. With only one executable side, recompute
  suitable checkpoints on CPU and report their coverage.
</sides>
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
  result; start diagnosis.json. Run the reproduction checks triage lists for
  matching and related NumericBridge community issues first, then the remaining
  checks cheapest first. At each localized operator, judge both sides against
  FP64 on the captured operands with operator_verdict.py.
</action>

<output>diagnosis.json with labels, checked boundaries, ruled-out causes and evidence.</output>

<next>
  On config_mismatch, correct setup and rerun the comparison. Route a supported
  defect to repair and a localized operator to precision optimization by its
  FP64 reading. Diagnosis ends in one of three ways: acceptance restored; an
  inherent difference (each localized operator within its FP64 bound and no
  actionable check left); or optimization entered for a localized operator,
  after which diagnosis continues for the remaining unexplained difference.
  "Blocked" is a stop reason only with a recorded blocker (access, budget or
  no_actionable_check). A hypothesis whose finding names a next check is not
  no_actionable_check: run that check, or open it as a child hypothesis.
  Diagnosis stays open while any hypothesis has an actionable check, including
  issues found after delivery. Setup, dependency and adapter fixes go in the
  case log, not in diagnosis hypotheses, unless they affect results.
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
  Record each candidate in diagnosis.json and keep two results separate: a
  focused regression can verify an independent defect without restoring the
  original acceptance. Mark a candidate as a verified fix for the original case
  only after both its focused regression and independent supported-native
  acceptance on the original workload pass.
</output>

<next>
  After fixing a preflight blocker, re-enter comparison. Otherwise deliver; on
  failure, return to diagnosis or try the next candidate within budget.
</next>
```

## Precision optimization

Use [operator-optimize](../../operator-optimize/SKILL.md#optimize).

```xml
<tracks>
  Two tracks with different ends. Diagnosis and repair is a deliverable task:
  it ends at local delivery (acceptance restored, an inherent difference shown,
  or bounded attribution of a non-passing original). An independently verified
  operator fix can be delivered alongside that outcome without changing it.
  Any community submission and PR drafts follow the evidence and consent. Precision optimization is a
  continuing task: each round delivers the variants measured so far with their
  precision and speed, records what remains open, and can be resumed in a later
  session or by another contributor. An optimization round never holds back
  the diagnosis delivery.
</tracks>
```

```xml
<entry>
  Diagnosis localized an operator, judged both sides against FP64 on the captured
  operands with `operator_verdict.py`, and wrote `result/optimize/<operator>/handoff.json`.
  `entry` in `handoff.json` comes solely from that FP64 reading: a reading
  outside the bound (`a_deviates`, `b_deviates`, `shared_policy`) sets
  `entry="required"` whatever `request.json`'s `operator_optimize.enabled` says;
  a reading within the bound (`not_the_source`) sets `entry="optional"` and enters
  only when `operator_optimize.enabled` is true (if the user requests optional
  optimization during follow-up, record `operator_optimize.enabled: true` in
  `request.json`; otherwise diagnosis delivers an inherent difference).
  A `gap` verdict enters the same way once an operator is localized, and is
  otherwise delivered with the remaining distance to the optimize target.
</entry>

<action>
  Try configuration, backend and upstream variants and the slow fix first, then
  write a kernel. Measure every variant against FP64 and the dtype floor with
  `kernel_check.py`, collect paired timing on one host in one session, and rerun
  the original workload with each kept variant.

  Case generation and measurement rules:
  - Test `captured`, `random`, and `edge` shape classes using genuinely constructed
    inputs and real kernel/reference outputs on the target path; never slice
    `captured` arrays to fabricate `random`/`edge` cases, and never duplicate a
    single run's array to fabricate repeat runs (`repeats >= 2` must come from
    actual repeated executions on identical inputs).
  - Do not hardcode an approximate or custom `bound` in `kernel-spec.json` unless
    explicitly justified; omit `bound` so `kernel_check.py` computes
    `c*u(dtype)*sqrt(reduction)` consistently with `operator_verdict.py`.
  - Distinguish a full optimization round from a partial/pre-benchmark handoff:
    every variant run so far must be measured with `kernel_check.py` (producing
    `kernel-report.json`), and any unmeasured cases (`random`/`edge` on target
    hardware, paired `timing`, or un-run variants) must stay explicitly absent or
    `not_measured` rather than filled with offline proxies.
</action>

<output>
  `result/optimize/<operator>/` with the input `handoff.json`, each tried
  variant's source, `kernel-spec.json`, `kernel-report.json`, timing conditions,
  end-to-end precision and speed, and `open.json`; each kept or tested variant
  recorded in `diagnosis.json`.

  Round state (`open.json`) and handoff link:
  - `open.json` tracks `round_status` (`"handoff_open"` when work remains for a
    later round), `entry` (`"required"` or `"optional"`), `remaining_distance`
    (`relative_rmse`, `bound`, `excess`), `variants_not_yet_tried`, and
    `next_step` so another session can resume cleanly.
  - When `slow_fix` has a `kernel_check.py` report, reference it via
    `slow_fix.report` in `handoff.json` and add the `kernel_check` command under
    `reproduce`.
</output>

<next>
  Submit the round with all kept variants (all variants within the bound plus
  baseline), their precision/speed tradeoffs, and the open items in `open.json`.
  Only a kept variant that improves toward the reference semantics, applies to
  the target upstream repository, and has verified source evidence gets a PR
  draft (marking any unverified `repo_style_test` / `native_on_target` /
  `performance` items in `pending_checks` so the draft is explicitly flagged
  `incomplete`). A variant that still fails the bound or has unmeasured
  shapes/timing does not block round delivery: record its `kernel-report.json`
  distance and mark the operator `round_status: "handoff_open"` in `open.json`
  with `variants_not_yet_tried` and `next_step` for the next session.

  Community record boundary: an operator that has completed an optimization
  round (paired timing and end-to-end checks on kept variants) adds one
  `candidate` entity per kept variant as described in
  [community-records.md](../../precision-compare/references/community-records.md#operator-variants);
  an operator with only a pre-round `handoff_open` (or captured-only
  `kernel_check` awaiting target random/edge/timing) records its handoff and
  `open.json` state without fabricating full-round `candidate` benchmark
  claims. Package either state using `operator_optimization.py` (`handoff_open`
  or `round_delivered`) under `evidence/submissions/*.json`; keep server-side
  tiers and recommendations out of client payloads. Return to diagnosis for the next localized operator while the
  acceptance failure remains unexplained.
</next>
```

## Local delivery

```xml
<entry>
  No required check remains actionable within authorization and budget; finish
  active in-scope runs first. `diagnosis_state.py audit` returns ok: every
  confirmed or blocked hypothesis has a contribution decision and every blocked
  one a blocker. Drafts with missing checks do not block delivery; they are
  listed in incomplete_drafts and submitted marked incomplete.
</entry>

<action>
  Reconcile execution, comparison, diagnosis and repair against the request and
  evidence (workflow state.json, acceptance report, diagnosis.json). Declare one
  contribution decision per issue with `diagnosis_state.py decide` (ownership,
  upstream state at main, latest release and pinned ref, decision, reason, and
  draft checks), then write `result/contribution-decisions.json` with
  `diagnosis_state.py decisions`; the report, community record and PR drafts
  follow it. In case-state.json, fill and validate the v1 delivery_summary with
  the original verdict, local outcome and each candidate's focused regression
  and relationship to the original. Use case_state.py check; a new checkpoint
  can start from template --with-delivery-summary.
</action>

<output>
  Report: stage status (complete/partial/blocked/N/A) and blockers; original
  verdict and criterion; versions, coverage and key stats; each fix's scoped
  red/green result and before/after metrics. Link setup, issues and requested evidence.
  Missing required inputs stay partial and named. CPU is not dual-native proof.
  Say “NumericBridge 链路已完成” only with evidence for every required stage.
</output>

<next>
  After a shareable delivery, follow Community contribution even for a PASS
  with no issue or patch. A scoped fix or receipt never changes acceptance.
</next>
```

## Community contribution

**Only submit to NumericBridge.** Upstream is read-only: never open or update
PRs/MRs, fork publicly, or push for submission by any means. Do not give runners
upstream write credentials. Maintainers handle upstream filing; an existing PR
is not a community receipt.

1. **Check route and consent.** Use `community_contribution_capabilities`.
   Honor declines. Public intake is new anonymous drafts only; named credit and
   `supersedes` require authenticated intake. Keep unsupported submissions local.
2. **Prepare.** Scan with [anonymize_scan.py](../../precision-compare/scripts/anonymize_scan.py)
   using local aliases. Follow the [archive checklist](../../precision-compare/references/community-records.md)
   and use `prepare_contribution`. For patches, inspect the pinned merged template
   and confirm the exact tested diff binding. On a template blocker, don't bypass
   validation. Review both visibility lanes and the consented attribution.
3. **Send only the reviewed draft.** Pass ID and digest to `submit_community`.
   Use `consent_record` for recorded `share`; otherwise offer once through client
   confirmation. If blocked, keep and report the draft. Changes require a new
   preview; retry an unchanged draft with its original ID and digest.
4. **Record the result.** Save `receipt_binding` and `review_state` in
   `delivery_summary.community`; run `case_state.py check`. Report the receipt
   and preview path. Status proof uses receipt ID **and** draft SHA. Received
   means pending review, not published; verify discoverability in the deployed
   index. Preserve old receipts; never bind a changed patch to an old receipt.
