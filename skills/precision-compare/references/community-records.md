# Community submission guide

Archive measurements, not raw outputs. Use [the contribution workflow](../../numericbridge/references/workflow.md#community-contribution) for consent and delivery. Read the MCP schema for field definitions.

## Record the result

| Item | Submit |
|---|---|
| Evaluation (including PASS) | Model revision, workload, both sides' hardware/library versions and settings, summary metrics with units, actual/expected coverage, criterion and verdict. With no defect, submit the evaluation alone. |
| Issue | Measured setup, trigger, expected/observed behavior and classification. No patch required. |
| Patch | At least one measured `{id, before, after, unit}` pair for the issue under the same workload, configuration and metric definition. Use the relevant metric, e.g. numerical error, failed runs or completion rate. |

Every patch needs a focused red/green result, coverage and the exact tested base and diff digest. Label the scope (operator, component or end-to-end). **A focused pass never changes original acceptance.** Include a minimal public reproducer or public evidence link. Keep raw logits, tensors, private inputs, full logs and environment receipts local.

## Prepare and inspect

Use `prepare_contribution` for new evaluations, issues and tested patches. The tool builds the catalog records. For a patch, inspect the merged target with `inspect_upstream_pr_template`; follow its slot order and leave unrun checks pending. Untrusted targets block preparation. Only maintainers receive the diff and PR draft (`preview.review_evidence`); only `preview.additions` is eligible for publication.

## Operator variants

Keep standalone [operator optimization](../../operator-optimize/SKILL.md) rounds
local. The [round validator](../scripts/operator_optimization.py) separates
`handoff_open` (missing evidence and next action) from `round_delivered` (complete
measurements, including measured failures). Typed intake accepts an eligible
evaluation, issue or tested patch, not a standalone round. Share only the
approved evidence for that contribution.

## Local preview

Without MCP, keep the anonymized draft local.

[Submission schema and operator setup](https://github.com/FreedomIntelligence/NumericBridge/blob/main/community/README.md#typed-contribution-input)
