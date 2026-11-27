# NumericBridge workflow

## Stages and handoffs

Tools: [workflow plan](../../precision-compare/references/comparison.md#shared-tasks), [durable execution and minimal exchange](../../precision-compare/references/automation.md), [checkpoint](../../precision-compare/scripts/case_state.py), [upstream status](../../precision-compare/scripts/upstream_status.py).

```xml
<workflow_rules>
  <plan>Use workflow.py init per task. Reuse verified stages and run independent sides in parallel.</plan>
  <handoff>Carry request, plan, workspace, tested identities, job records, evidence, diagnosis, budget and next_action.</handoff>
  <scope>Continue through delivery and eligible contribution under the same grant.</scope>
  <priority>Advance independent work while blocked. Prioritize actionable open cases over new cases.</priority>
  <jobs>Track each job to terminal status. Record host, ID or PID, and status command.</jobs>
  <failure>Read failure_class and logs. Retry only in scope; record unverified branches and reproducers.</failure>

  <checkpoint file="case-state.json">
    <save at="stage change, job launch or exit, new evidence, before reply"/>
    <require when="in_progress">Set a concrete next_action.</require>
    <validate>Run case_state.py check --state case-state.json --request request.json.</validate>
    <rule>Do not weaken the confirmed goal to close the case.</rule>
  </checkpoint>

  <resume>
    <action>Validate the checkpoint first. Reconcile jobs, reuse evidence, and load only the current method.</action>
    <action when="verified adapters available">Bind the plan and run it. For long authorized remote stages use the bounded controller; record its report and workspace in the checkpoint.</action>
    <rule>A reply ends a turn, not a detached authorized controller. Any authorized caller can resume the same plan and workspace. A legacy PID alone is not an adoptable job; reconcile it separately before enabling any fresh launch.</rule>
    <route when="missing or invalid checkpoint">Recover from verified evidence and job records. Do not start over.</route>
    <route when="in_progress and runnable">Start one turn with the same grant and next_action.</route>
    <route when="job still running">Wait for a signal or bounded check. Do not relaunch or loop on unchanged state.</route>
    <route when="transient access failure">Retry within the grant.</route>
    <route when="user action needed">Set needs_human and name the blocker.</route>
    <route when="needs_human or partial">Wait for the named access or resume condition to change.</route>
    <route when="validated done">Stop.</route>
    <route when="no automatic caller">Leave the checkpoint for manual continuation.</route>
  </resume>

  <delegation>
    <owner>One owner handles both sides through verification. Split only independent cases.</owner>
    <coordinator>Check state on progress or return. Reconcile jobs before replacing an owner.</coordinator>
    <ask>Ask the user only for a missing decision or grant.</ask>
  </delegation>

  <issues>
    <record>Use diagnosis.json for findings affecting execution or results, including setup blockers.</record>
    <render>Generate issues.md. Distinguish workarounds from fixes on the original path.</render>
  </issues>

  <upstream_status when="confirmed library defect or policy difference">
    <check>Use upstream_status.py in a local clone. Check merged main, latest release and the pinned ref.</check>
    <check>Inspect the template at the resolved target commit, not at a PR ref.</check>
    <rule>Open PRs are related work, not merged fixes. Do not change draft scope to avoid them.</rule>
    <route when="fixed on main but present on pin">Prefer upgrade; verify a backport draft for the installed version.</route>
    <route when="present on main">Draft against main.</route>
    <output>First fixed release, merge commit and upgrade blocker.</output>
  </upstream_status>
</workflow_rules>
```

## Request

Method: [request confirmation](request.md).

```xml
<stage id="request">
  <action>Confirm only missing scope or permission. Reuse the current grant.</action>
  <output file="request.json">Scope, resource limits and open items.</output>
  <checkpoint when="new case">Run case_state.py template --state case-state.json.</checkpoint>
  <route to="community_lookup"/>
</stage>
```

## Route community results

```xml
<stage id="community_lookup">
  <action tool="search_community" endpoint="https://numericbridge.com/mcp"/>
  <input>Public hardware, model, library and task per stack. Add pinned version or model_size when relevant.</input>
  <output>Ranked results and index ID.</output>
  <route when="no match or unavailable" to="environment">Record which. Do not use a fallback.</route>
  <route when="pinned version" to="environment">Keep the pin.</route>
  <route when="unpinned version" to="environment">Try a supported recommendation; verify locally.</route>
  <rule>Past PASS does not transfer. Use prior issues to prioritize checks.</rule>
  <optional when="contribution allowed and local contribution MCP available">Call check_contribution_catalog early. It fetches only official public data. Do not block local evaluation on this check.</optional>
</stage>
```

## Prepare and compare

### Prepare the environment

Methods: [framework adaptation](../../framework-adapt/SKILL.md), [pinned public model download and mirror fallback](../../framework-adapt/references/public-model-download.md).

```xml
<stage id="environment">
  <action>Prepare each side independently. Reuse valid readiness evidence.</action>
  <fallback when="Hugging Face connectivity failure and public download authorized">If permitted by host policy, try https://hf-mirror.com for the exact pinned public revision without credentials. Verify required files against the official pinned manifest before loading. Do not prompt again for an in-scope fallback.</fallback>
  <check when="vLLM on 910C or B200">Use the shared native launcher for fixed model files, explicit device namespace, process occupancy, runtime imports and visibility, and clean private IPC. Environment success does not show native readiness.</check>
  <check when="not verified">Check device, native path and capture in the first formal batch. Retain valid samples.</check>
  <exception>Run separate preflight only before a costly or risky launch.</exception>
  <output>Launch command, execution profile, runtime receipt and gaps.</output>
  <route to="comparison">Advance each ready side.</route>
</stage>
```

### Run the comparison

Methods: [capture](../../precision-compare/SKILL.md#collect), [default batch](../../precision-compare/examples/minimal-precision-batch.md), [acceptance](../../precision-compare/references/acceptance.md).

```xml
<stage id="comparison">
  <capture>Align inputs, start state and capture points. Record intended differences.</capture>
  <capture>Reuse valid records. If no dataset is supplied, use the same default batch and order.</capture>
  <rule>Pair online RL at matching histories. Disagreement does not identify the wrong side.</rule>
  <check>Use independent A/B captures, observed dtype, nb-default-v1 and exact checks.</check>
  <check>Bind same-side repeats to their primary reports with --noise-for.</check>
  <transfer when="inference evidence must cross hosts">Use the approved minimal exchange protocol. Only top-100 ID inventories go to the authorized peer; only verified reviewer-addressed summaries go to the authorized comparison destination. Keep raw logs and full rows in place. No transfer without the specific grant.</transfer>
  <check when="completion_goal set">Use request.original_contract. Embed tested_identity from the unchanged baseline onward.</check>
  <output>Workload, stack versions, coverage, metrics and verdict.</output>
  <route when="one side only">Report standalone checks, not A/B acceptance. Stay partial if A/B was required.</route>
  <route when="pass or gap without repair goal" to="delivery">Consider optional optimization for a gap only if enabled.</route>
  <route when="pass or gap with repair goal" to="repair">Apply the completion gate; do not skip a named defect.</route>
  <route when="fail and measurement only" to="delivery">Report the observed failure.</route>
  <route when="fail otherwise" to="diagnosis"/>
  <route when="noise_limited">Stabilize the run.</route>
  <route when="incomplete">Fill missing coverage.</route>
</stage>
```

## Diagnose a difference

Methods: [triage](../../precision-repair/SKILL.md#diagnose), [diagnostic rounds](../../precision-repair/references/diagnostic-round.md).

```xml
<stage id="diagnosis">
  <entry>Numerical failure, reproduced native failure or unverified requested defect.</entry>
  <action>Check setup and the first observed difference. Reconcile jobs and budget before launching a check.</action>
  <check>Only valid, decisive results unlock depends_on. Verify probes against native controls.</check>
  <rule>Use after for probe troubleshooting. Local findings do not change original acceptance.</rule>
  <output file="diagnosis.json">Findings and evidence. Read diagnosis_state.py next.</output>
  <route when="recompare" to="comparison">Correct the setup mismatch first.</route>
  <route when="check or refine">Run the cheapest remaining check that can change the decision.</route>
  <route when="repair" to="repair">Hand off the confirmed cause under existing authorization.</route>
  <route when="localized operator" to="operator_optimization">Check against FP64 on captured operands.</route>
  <route when="stop" to="delivery">Record findings and gaps. Apply the delivery gate.</route>
</stage>
```

## Repair and verify

Method: [repair and verification](../../precision-repair/SKILL.md#patch-and-verify).

```xml
<stage id="repair">
  <entry>Authorized change and comparable reproducer.</entry>
  <action>Verify the smallest focused fix. Reuse single-change results before testing a combination.</action>
  <check when="original metric worsens">Check for regressions and remaining defects. Keep verified local findings.</check>
  <route when="execution restored" to="comparison">Collect the original workload again.</route>
  <output>Separate focused results from original-workload acceptance.</output>
  <completion>
    <changed>Keep the baseline. Verify focused fail-before/pass-after and match request.defect_id if specified.</changed>
    <changed>Require pass or gap on the exact final change. Set delivery.candidate_id and validate against the request.</changed>
    <unchanged when="restore_original_acceptance">Allow unchanged pass or gap. After a failure, retain both reports and use acceptance_retested.</unchanged>
    <route when="required work remains runnable">Keep in_progress and record next_action.</route>
    <route when="required work blocked and jobs settled" to="delivery">Deliver partial.</route>
  </completion>
</stage>
```

## Precision optimization

Methods: [operator optimization](../../operator-optimize/SKILL.md), [handoff](../../operator-optimize/references/handoff.md), [shareable evidence](../../precision-compare/references/community-records.md#operator-variants).

```xml
<stage id="operator_optimization">
  <entry>Localized operator with an FP64 check on captured operands.</entry>
  <route when="outside bound">Open the required round. It does not grant resources.</route>
  <route when="within bound and enabled">Try optional optimization. Record opt-in in request.json.</route>
  <route when="within bound and disabled">Skip. Continue diagnosis if acceptance fails.</route>
  <route when="gap without localized operator">Report the gap. Do not start kernel work.</route>
  <action>Reuse measurements. Try settings, backend or upstream variants and the slow fix before a new kernel.</action>
  <check>Run independent repeats on all required shapes. Score each against FP64.</check>
  <check>Pair timing on one host and session. Record native workload results per kept change.</check>
  <output>Keep measurements, missing checks and next_step under the operator directory.</output>
  <route when="missing measurements">Use handoff_open. Report measured results without a recommendation.</route>
  <route when="measurements complete">Use round_delivered. Report failures as failures.</route>
  <rule>Keep standalone rounds local. Eligible issues, evaluations and patches use typed intake.</rule>
  <rule>Optional work does not delay local delivery or change original acceptance.</rule>
</stage>
```

## Local delivery

Methods: [checkpoint validator](../../precision-compare/scripts/case_state.py), [contribution decisions](../../precision-repair/SKILL.md#declare-contribution-decisions).

```xml
<stage id="delivery">
  <entry>Reconcile jobs and evidence against the request. Keep in_progress while required work can run or jobs remain.</entry>
  <audit when="diagnosis entered">Record decisions and blockers. Run diagnosis_state.py audit.</audit>
  <output when="diagnosis entered" file="result/contribution-decisions.json">Contribution decisions.</output>
  <audit when="no diagnosis">Report N/A. Do not create an empty diagnosis.</audit>
  <outcome when="pass or gap after numerical failure">
    <changed>acceptance_restored</changed>
    <unchanged when="restore_original_acceptance">acceptance_retested</unchanged>
  </outcome>
  <outcome when="other pass or gap">acceptance_passed</outcome>
  <outcome when="observed failure satisfies request">bounded_attribution with evidence</outcome>
  <outcome when="reproduced native failure without numerical acceptance">Use original.native_failure and verdict not_run.</outcome>
  <route when="required work blocked and jobs settled">Deliver partial with open_items and resume_when. Do not submit.</route>
  <output>Write the local report: versions, coverage, criterion, verdict, blockers, focused results and paired metrics.</output>
  <output>Link saved evidence. List optional work and incomplete_drafts separately.</output>
  <check>Apply any repair completion gate. Validate case-state.json against request.json.</check>
  <route when="local result complete and sharing agreed or pending" to="community_contribution">Keep in_progress and save next_action.</route>
  <route when="local result complete and no contribution pending">Set done and next_action=null.</route>
</stage>
```

## Community contribution

Methods: [submission guide](../../precision-compare/references/community-records.md), [privacy scan](../../precision-compare/scripts/anonymize_scan.py).

```xml
<stage id="community_contribution">
  <entry>Local delivery complete. A PASS can be shared without an issue or patch.</entry>
  <boundary>Only submit to NumericBridge. Never open or update upstream PRs/MRs, fork publicly or push.</boundary>
  <boundary>Give runners no upstream write credentials. Maintainers handle upstream filing.</boundary>
  <route when="declined or nothing shareable">Keep local. Do not ask again.</route>
  <check tool="community_contribution_capabilities">Public accepts new anonymous drafts. Named credit requires authenticated intake.</check>
  <check tool="check_contribution_catalog">Use the official catalog resolved by the local contribution tool. Never ask the contributor for archives, paths or checksums. Do not use a search index or stale workspace as a validation baseline.</check>
  <prepare tool="prepare_contribution">Scan private identifiers. Review public and maintainer-only contents.</prepare>
  <resume when="legacy catalog blocker or local MCP not mounted">From a verified current tooling checkout run python -B -m community.contribution_resume --case-dir CASE --drafts PRIVATE_DRAFTS. It validates the old delivery and makes a local preview only, without replacing the case's tested identity. Treat setup_required, catalog_unavailable and preview_ready/awaiting_consent as different states. Never reconstruct a send from an unverified receipt or repeat the evaluation.</resume>
  <route when="catalog_unavailable">Preserve the local result and sanitized draft source, record the error and contribution resume action, and report the blocker. Do not ask the user to choose local-only just because the service is unavailable. Retry preparation on resume without rerunning evaluation.</route>
  <check when="patch">Verify the pinned target template and exact tested diff.</check>
  <record>Save community_draft_sha256 for the current preview. After edits, prepare and review a new draft.</record>
  <route when="intake or validation blocked">Keep the draft local. Report the blocker; do not bypass validation.</route>
  <send tool="submit_community">Use the reviewed draft ID and SHA. Pass consent_record or offer client confirmation once.</send>
  <route when="confirmation declined or cancelled">Keep local. Do not retry.</route>
  <resume when="known receipt">Check status by receipt ID and draft SHA.</resume>
  <resume when="uncertain send">Retry only the same reviewed draft with unchanged consent. Keep the case open.</resume>
  <record when="received" in="delivery_summary.community">Save matching receipt_binding and review_state.</record>
  <record when="local-only" in="delivery_summary.community_local_only">Save draft_sha256 (null if none) and reason.</record>
  <output>Report receipt and preview path, or why it stayed local. An upstream PR is not a receipt.</output>
  <rule>Claim public visibility only after a deployed index match.</rule>
  <route when="received or resolved locally">Set done and next_action=null. Validate; do not wait for review or publication.</route>
</stage>
```
