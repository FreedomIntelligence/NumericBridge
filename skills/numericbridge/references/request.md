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
    <fallback>Try in-scope alternatives or wait for busy devices. Report partial results if budget ends.</fallback>
    <acceptance>Use nb-default-v1. Do not ask for thresholds or change them after observing results.</acceptance>
    <decisions>Choose routine setup, capture, controls and retries within the grant. Log material choices.</decisions>
  </boundaries>

  <record>
    <required>Scope, pins, resource limits, acceptance profile and open items.</required>
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
      <default>The request template offers anonymous review by NumericBridge.</default>
      <fields when="user chose">consent (share or decline), attribution_mode, attribution_name, confirmed_at</fields>
      <route when="no choice">Leave consent unset. Do not ask during request confirmation.</route>
    </community_contribution>
    <operator_optimize>
      <defaults enabled="false" max_attempts="8"/>
      <rule>A confirmed out-of-bound operator still requires an optimization round.</rule>
    </operator_optimize>
  </record>
  <route to="community_lookup"/>
</request>
```
