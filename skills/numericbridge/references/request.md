# Confirm the request

```xml
<entry>
  Confirm new requests or changes to target, scope or resource authorization.
  Otherwise resume at the recorded stage.
</entry>

<action>
  Summarize hardware, model, tested framework, task (inference, SFT or RL) and
  scope in the user's language; for a general check, propose a small end-to-end
  case. Confirm once: the target and scope, the accelerator-hour ceiling with the
  allowed devices, hosts or queues, and access (credentials for both sides).
  Use the default acceptance profile unless the user gives criteria. Check that
  each side's official image or release artifacts for the pinned versions exist
  on its host or can be pulled there; an unavailable artifact is an access item
  settled at intake, not discovered during preparation.
</action>

<output>
  Write request.json with the confirmed fields, pinned versions, acceptance
  profile and pending items. Resolve discoverable versions, device IDs and launch
  commands during environment preparation.
</output>

<next>
  Retrieve community records. A missing grant blocks only its dependent work.
</next>
```

## Ask the user

The user is involved at two points only:

- **Request intake**: target, scope, allowed hosts and devices, budget and
  non-default acceptance criteria, confirmed once. Later sessions resume from
  `request.json` without asking.
- **Access**: a credential, account or permission the agent cannot obtain
  (rejected key or login, missing account, no read/write permission, a required
  shared-system or driver change, moving non-public data to a host outside the
  request). First try connection options, alternative sources and case-private
  workarounds, retry transient failures with backoff, and finish all work that
  does not depend on the access.

## Decide without asking

Decide everything else and record it in `request.json` under `decisions` with a
one-line reason, including: capture mode, instrumentation and diagnostic patches
in case-private copies or environments; case-private environments and
dependencies; download sources and mirrors; transfer of public checkpoints,
inputs and numerical results between the request's hosts; disk use up to half of
the case volume's free space; how the budget is split; diagnosis rounds;
diagnostic variants (eager, FP32/FP64 reference, a reference framework), which
do not change the task scope; retries after setup or resource failures; and
waiting for busy devices instead of preempting jobs. Keep the model, dtype, task
scope and acceptance limits as requested. When the budget is spent, deliver a
partial result. Prepare community records and upstream PRs as local drafts.

Continue with [community retrieval](workflow.md#route-community-results).
