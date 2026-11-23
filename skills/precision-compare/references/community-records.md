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

Record hardware, checkpoint and revision, tested framework/version, task and
settings. Use `model.architecture` and `model.size` for search (`tiny` for a custom
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

## Prepare an upstream PR draft

For a verified patch intended for upstream, read the target repository's base
branch contribution guide and PR template; otherwise use **Problem / Change /
Validation / Patch** and note which guidance could not be checked. Include a
title, existing issue link, affected setup, minimal reproducer, matched
before/after results, commands run and the patch. Leave pending checks unchecked
and record a PR URL only once it exists.

Attach the draft as `upstream_pr_draft` in a new `evidence/submissions/*.json`
document (`repository`, `base_branch`, `title`, `body_markdown`, `guidance_urls`,
`pending_checks`) referenced from the `prepare_community_submission` evidence map;
the regular preview and consent cover it.

## Local preview

Without the contribution MCP, keep the anonymized input and validate it from the
repository checkout:

```sh
python -B -m community.records.submission --input submission.json --output preview.json
```
