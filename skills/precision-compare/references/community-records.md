# Community submission guide

Select public findings from diagnosis.json and its linked evidence; follow
[the workflow](../../numericbridge/references/workflow.md#community-contribution)
for preview and consent.

## Record the result

| Work completed | What to record |
|---|---|
| Comparison | Tested configurations, workload, coverage, measured differences and the acceptance verdict with its profile. |
| Diagnosis | The observed issue and investigation linked to the comparison or native failure, with the evidence for its classification. |
| Solution | Each candidate linked to the issue with its scope, configuration, outcome and tradeoffs; mark source review, workaround or original-path verification. |

Group the submission under one `case` relation entity (`details.request` with
hardware, model, framework, task and goal; `details.category`) with an `includes`
link to every comparison, repair, issue and candidate it adds; search indexes
cases, so records outside a case are not retrievable, and the preview then also
checks their search facets. Record hardware, checkpoint and revision, tested
framework/version, task and settings. Use `model.architecture` and `model.size` for search (`tiny` for a custom
miniature fixture, with its dimensions and seed), `frameworks` for tested
frameworks, `libraries` for dependencies and `affected_library` for the repair
target, with versions and commits in their own fields.

Copy each preflight worker's environment receipt into that evaluation side's
`settings.runtime_environment` (or `environment.settings.runtime_environment`
for the measured repair environment). Carry each solution verification's own
versions. Retain the version source and keep unknown distinct from not
applicable. Use the observed configuration for recommendations; keep candidate
versions with the solution proposal until verified. Older records may omit the
receipt.

## Write the public entry

Give the symptom in a short title and summary (`content.title`, optional
`content.summary`, in `zh` and/or `en`; `details.content` for lifecycle entities);
keep settings and metrics in structured fields. `content.workflows` describes
affected tasks. Clarify historical wording by typed ID in `data/content.json`.
Include only the evidence needed to trace the result; keep raw logs, prompts,
private paths and host details local.

## Operator variants

After [operator optimization](../../operator-optimize/SKILL.md), add one
candidate per kept variant (the baseline, the slow fix and each kernel), linked
to the issue, so users can choose between precision and speed. Use
`numerical-policy` or `patch` as the solution type and put the tradeoff in
`applicability` and `limitations`. Attach the `kernel_check.py` report and the
end-to-end results as evidence: per variant the error against FP64 and the
dtype floor, reproducibility, paired timing with its conditions, and the
original workload's metrics, throughput, TTFT/TPOT and peak memory, each with
the baseline measured on the same host. Mark unmeasured values `not_measured`
with the reason. Report measurements only; precision tiers and
recommendations are assigned on the community side.

## Submission identity

`prepare_community_submission` returns `base_commit`: the community catalog
revision the submission service used to validate and merge the record. It is
not the NumericBridge revision the case ran, and a different value is not a
blocker; record it in the case state. Fix the draft only when the result is
`invalid`.

When request.json records `community_contribution.consent: share`, pass its path
as `consent_record` to `submit_community`: the draft is sent without a form when
its attribution matches the consent and the private-identifier scan is clean
(`consent_missing` or `scan_findings` otherwise), and the receipt carries
`confirmation: prior_consent`. The inbox validates every received draft again,
including each `upstream_pr_draft` with `pr_draft.py check`, and rejects one
that fails. Without recorded consent, `submit_community` asks through the client;
if it returns `approval_unavailable`, keep the draft and offer the terminal
fallback (`python -B -m community.submit_reviewed --draft-sha256 SHA`), which
sends only after the user types `SEND`.

## Prepare an upstream PR draft

For a verified patch intended for upstream, read the target repository's base
branch contribution guide and PR template; otherwise use **Problem / Change /
Validation / Patch** and note which guidance could not be checked. Include a
title, affected setup, minimal reproducer, matched before/after results,
commands run and the patch. Base the draft on current main and cite only merged
upstream commits; do not reference open issues or unmerged PRs (see the
workflow's upstream status). A PR draft travels with the community submission;
maintainers review it and file it upstream, so the user never opens the PR.
Leave pending checks unchecked and record a PR URL only once it exists.

Prepare a draft so maintainers only review and file it: a patch that applies to
the target branch, a test in the target repository's style that fails before
and passes after, native verification on the target branch or released build,
performance numbers and a body following the repository template. Mark each as
a `diagnosis_state.py decide --check-item` (`applies_cleanly`,
`repo_style_test`, `native_on_target`, `performance`, `template_body`); a draft
missing one is submitted as incomplete with the missing check named. A backport
targets the distribution the user installs (for example a plugin's release
branch) and uses that distribution's own patch or build mechanism. Check
`applies_cleanly` per target ref with
[upstream_status.py](../scripts/upstream_status.py) `--patch`.

The record follows `result/contribution-decisions.json`: one issue per decided
hypothesis, a draft for each `pr_draft_main` or `backport_draft`, and the
ownership and reason in the issue's classification. Before preparing, run
[anonymize_scan.py](../scripts/anonymize_scan.py) over the submission input,
evidence and draft bodies with `--term` for host aliases and account names, and
fix every finding.

Record where the defect is and where it was fixed in the candidate's optional
`details.upstream`: `affected` (versions or refs still containing it), `fixed_in`
(first fixed release, or null), `fixed_commit` (the merged fix), `upgrade_blocker`
and `refs` (`ref`, `commit`, `state` rows from `upstream_status.py`). A fixed
release needs the merge commit or a fixed ref; search returns the block with the
solution.

When decisions change after a submission (a new or revised draft), prepare a new
draft with `supersedes` listing the earlier receipt IDs and submit it the same
way; reviewers then accept only the latest receipt. `community_submission_status`
(or `python -B -m community.submission_status RECEIPT --intake URL --token-file
FILE`) returns a receipt's review state, linked PRs and any later receipt that
supersedes it.

Attach the draft as `upstream_pr_draft` in a new `evidence/submissions/*.json`
document (`repository`, `base_branch`, `title`, `body_markdown`, `guidance_urls`,
`pending_checks`) referenced from the `prepare_community_submission` evidence map;
the regular preview and consent cover it. [pr_draft.py](../scripts/pr_draft.py)
`scaffold` builds that document from the decision, the filled sections and the
target repository's template headings, fills `pending_checks` from the decision's
checks and, for a backport, states the fixed release and upgrade blocker; `check`
rejects placeholders, empty sections and references to pull requests.

## Local preview

Without the contribution MCP, keep the anonymized input and validate it from the
repository checkout:

```sh
python -B -m community.records.submission --input submission.json --output preview.json
```
