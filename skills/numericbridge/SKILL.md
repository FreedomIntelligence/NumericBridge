---
name: numericbridge
description: Run the NumericBridge precision workflow for an inference, SFT or RL stack, from confirming the target and optionally searching the community to comparison, acceptance, diagnosis, repair, verification and delivery.
---

# NumericBridge

- Users can describe the goal in plain language or use the [request template](references/prompt-template.md). For a new request, follow [request confirmation](references/request.md).
- For an existing case, read and validate its checkpoint first, then use the
  [resume protocol](references/workflow.md#stages-and-handoffs) to choose the next
  action. Consult the plan or diagnosis only when the recorded stage needs it.
- For a new case, follow the [workflow](references/workflow.md) at request confirmation.

Read only the current method. A reply ends a turn, not a case.

Upstream contribution means preparing a local PR/MR draft and sending it to
[NumericBridge community intake](references/workflow.md#community-contribution).
Do not open or update upstream PRs/MRs, create public forks, or push branches to
publish drafts from this workflow. NumericBridge maintainers handle upstream
filing after community review.
