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
  latest release and the user's pinned version or distribution branch. Where a
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

Use the public search MCP at `https://numericbridge.com/mcp` (Streamable HTTP,
no credential or SSH tunnel required). The bundled CLI is the fallback if MCP is
unavailable.

```xml
<entry>The target and task are confirmed.</entry>

<action>
  Search the NumericBridge community: call search_community with hardware,
  model, library and task (inference, sft or rl); add version only when pinned and model_size when needed. If MCP is
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
  Each candidate in diagnosis.json. Verified when the focused regression and the
  original-case acceptance both pass.
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
  it ends at local delivery and community submission with a met standard
  (acceptance restored, an inherent difference shown, or an operator confirmed
  outside its FP64 bound with a verified fix that follows the reference), and
  its PR drafts follow from that evidence. Precision optimization is a
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
  follow it.
</action>

<output>
  Mark each stage complete, partial, blocked or not applicable (outside scope, or
  its trigger did not fire). A CPU slice does not complete an agreed dual-native
  comparison. A required item whose inputs are unavailable (for example a
  reproduction whose original token histories were never published) stays
  partial with the missing input named, even when every run finished. Give the
  configuration, coverage, acceptance verdict and profile,
  runnable setup and evidence links; link issues.md for open issues. Provide
  reports, bundles or offline replay when requested.
</output>

<next>
  Output “NumericBridge 链路已完成” only when every required stage has completion
  evidence. Then, or after a shareable partial delivery, execute community
  contribution if `request.json` records `community_contribution.consent: "share"`
  (reporting the receipt and preview path without re-asking), or offer it once
  when no intake choice was recorded.
</next>
```

## Community contribution

Use the [community record guide](../../precision-compare/references/community-records.md).
The public search MCP is read-only. For submissions and receipt status, use the
configured local contribution MCP (stdio) with intake at
`https://numericbridge.com/v1/submissions` and an operator-issued upload token
file. No SSH tunnel is required. Keep credentials out of prompts and logs. If
the configured intake URL changes, prepare a new preview and confirm it before
sending.

```xml
<contribute when="local delivery has a shareable result or a contribution is being resumed">
  Honor a prior decline. Prepare an anonymized draft with minimum evidence and a
  PR or backport draft for each pr_draft_main or backport_draft decision; the
  record's issues and drafts match result/contribution-decisions.json. Run
  `anonymize_scan.py` over the draft, evidence and PR bodies (adding host aliases and account names with
  --term) and fix every finding before preparing. Use the credit recorded in
  request.json's community_contribution. Fix findings from
  prepare_community_submission until it returns ready. Use the configured
  NumericBridge contribution MCP tools directly (do not manually invoke local
  repo submission scripts with another Python environment). With consent
  recorded at intake, call submit_community with the draft ID, digest and
  consent_record (the request.json path); it sends without a form and the inbox
  re-checks the records and PR drafts. Then tell the user the receipt and preview
  path; no further confirmation is needed. Without recorded consent,
  submit_community asks through the client; a recorded or later decline ends the
  offer. If submission is
  unavailable, keep the local draft and report its path. A receipt means pending
  review; call the result searchable only when the deployed index returns its IDs.
  When decisions change after a receipt, submit an updated draft that names the
  earlier receipt (supersedes), and read review progress with
  community_submission_status. Contribution status does not change local
  delivery status.
</contribute>
```

Scan before preparing with [anonymize_scan.py](../../precision-compare/scripts/anonymize_scan.py).
Without MCP, keep the anonymized draft and use the
[local preview](../../precision-compare/references/community-records.md#local-preview).
