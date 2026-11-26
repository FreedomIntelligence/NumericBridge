---
name: numericbridge
description: Run the NumericBridge precision workflow for an inference, SFT or RL stack, from confirming the target and optionally searching the community to comparison, acceptance, diagnosis, repair, verification and delivery.
---

# NumericBridge

- Users can describe the goal in plain language or use the [request template](references/prompt-template.md). For a new request, follow [request confirmation](references/request.md).
- For an authorized request, follow the [workflow](references/workflow.md) from
  optional community lookup, or resume at the recorded stage: `workflow.py status` for
  the plan and `diagnosis_state.py next` for an open diagnosis.

Read the method linked for the current stage as needed.

Upstream contribution means preparing a local PR/MR draft and sending it to
[NumericBridge community intake](references/workflow.md#community-contribution).
Do not open or update upstream PRs/MRs, create public forks, or push branches to
publish drafts from this workflow. NumericBridge maintainers handle upstream
filing after community review.
