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
  allowed devices, hosts or queues, and access (credentials, reference side).
  Use the default acceptance profile unless the user gives criteria.
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

## Decide without asking

Within the grant, choose and record in `request.json` under `decisions`, with a
one-line reason: statistics versus tensor capture, instrumentation and diagnostic
patches in case-private copies or environments, disk use up to half of the case
volume's free space, how accelerator hours are split across preflight, comparison
and diagnosis, and the number of diagnosis rounds.

## Ask the user

Ask only for: a larger accelerator-hour ceiling or other devices, hosts or queues;
credentials or access; changes to shared environments, system packages, drivers or
CANN/CUDA; changing the model, dtype, task scope or acceptance limits; moving
private weights or data between hosts; preempting others' jobs; and outward
actions (upstream PRs, community submission).

Continue with [community retrieval](workflow.md#route-community-results).
