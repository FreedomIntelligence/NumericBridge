# Confirm the request

Methods: [acceptance](../../precision-compare/references/acceptance.md), [community lookup](workflow.md#route-community-results).

```xml
<request file="request.json">
  <confirm>
    <input>Model, both stacks, task (inference, SFT or RL), and requested outcome.</input>
    <default>Start general precision checks with a small end-to-end case.</default>
    <ask>Ask only if the answer changes the test or permission.</ask>
    <resume>Reuse request.json. Recheck only what changed.</resume>
  </confirm>

  <boundaries>
    <scope>Keep pinned versions, model, dtype and workload. Resolve launch details during setup.</scope>
    <resources>Reuse grants. Check access and disk quota. Ask before exceeding the grant.</resources>
    <approval>Ask before changing shared systems or moving private data outside authorized hosts.</approval>
    <exchange when="cross-host inference">If authorized, record allowed fields, source, peer and local comparison destination once. Default: no private transfer. A peer inventory grant does not permit sending scores to the peer or posting to the community; bind the exact per-run artifacts before export.</exchange>
    <fallback>Try in-scope alternatives or wait for busy devices. Report partial results if budget ends.</fallback>
    <acceptance>Use nb-default-v1. Do not ask for thresholds or change them after observing results.</acceptance>
    <decisions>Choose routine setup, capture, controls and retries within the grant. Log material choices.</decisions>
  </boundaries>

  <record>
    <required>Scope, pins, resource limits, acceptance profile and open items.</required>
    <failure_policy record_in="request.json, case-state.json">
      <default value="diagnose">For new cases, diagnose a failed numerical acceptance in the existing grant when no explicit choice is supplied. Record the default; do not ask to confirm it.</default>
      <route when="user explicitly requests measurement only" value="measure_only">Deliver a verified failure without diagnosis. A comparison or delivery request alone does not mean measurement only.</route>
      <rule>Record before formal capture and reuse on resume. Do not infer or retrofit this field on legacy pinned cases. A repair completion_goal is incompatible with measure_only.</rule>
    </failure_policy>
    <completion_goal record_in="request.json, case-state.json">
      <route when="measure or diagnose only" action="omit"/>
      <route when="require acceptance" value="restore_original_acceptance"/>
      <route when="require a named defect fixed and acceptance" value="repair_defect_and_restore_acceptance"/>
      <require when="named defect">Record defect_id in request.json.</require>
    </completion_goal>
    <original_contract when="completion_goal set" freeze="before formal capture">
      <fields>workload_sha256, base_sha256, criterion_sha256</fields>
    </original_contract>
    <community_contribution>
      <default>Local delivery. Do not treat the task, handoff or optimization choice as a request to share.</default>
      <fields when="user requests community review">review_requested=true. A request for a preview does not authorize a send.</fields>
      <fields when="user approves or declines the exact draft">consent (share or decline), attribution_mode, attribution_name, confirmed_at, reviewed_draft_sha256 and destination when sharing.</fields>
      <route when="no choice">Leave consent unset and deliver locally. Do not ask during request confirmation.</route>
    </community_contribution>
    <operator_optimize>
      <defaults enabled="false" max_attempts="8"/>
      <rule>A confirmed out-of-bound operator still requires an optimization round.</rule>
    </operator_optimize>
  </record>
  <route to="community_lookup"/>
</request>
```
