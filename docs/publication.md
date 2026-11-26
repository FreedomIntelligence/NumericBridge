# Public repository scope

The workflow repository contains reusable code, schemas and current guides.
Developer regression suites remain local in the Git-ignored `tests/` directory.
The separate [site repository](https://github.com/FreedomIntelligence/NumericBridge-site)
contains frontend code only. Approved records, evidence, reports and figures
live in the [server-owned catalog](community-storage.md), not in the workflow,
site, or private workbench repositories. `.github/` contains GitHub configuration.
A result identifies hardware, model, software revisions, settings, measured scope,
metrics, limitations and reproducibility or immutable evidence references.
Completion and numerical acceptance have separate fields.

For public record fields and wording, use the
[record format](../community/records/README.md) and
[writing guide](../skills/precision-compare/references/community-records.md).

Community contributors send anonymized results to the
[private intake](../community/README.md#mcp-contribution). Maintainers review
the receipt, select records for publication and handle any upstream submission.
Typed submissions keep full patches and generated upstream PR drafts in the
private inbox. The public catalog only materializes the reviewed public
additions; do not copy maintainer-only attachments or reference them from
public records.

## Publish on the appropriate surface

- Framework and skill code, minimum reproducers, patches and workflow validators.
- Current architecture, usage and maintenance guides.
- Publish reviewed reports, scoped metrics, SVG figures, records and referenced
  evidence from an approved server catalog generation. Include relevant failures
  and unresolved gaps. A later result does not rewrite a historical observation.

## Keep local

- Developer regression and browser test suites in `tests/`; retain permanent cases locally.
- Optional RSI runner source in `research/`, its experiment state, candidates and full traces.
  Public dashboard snapshots may remain when they contain reviewed summaries only.

- Unreviewed inbox receipts, execution logs, handoffs, completed one-off plans and exploratory candidates.
- Raw tensors, checkpoints, full responses, traces and historical delivery ZIPs.
- Duplicate metric snapshots and generated PNG/PDF copies when the public SVG
  and its underlying final measurements already provide the same result.

Use one canonical metrics file per published result. Reports link to it instead
of copying the same JSON into each report directory. Keep aggregate CSV exports
when they are a documented user interface. Public snapshots are retained when
pages or immutable source references consume them; file size alone does not
make a source redundant.

Before removing a file, inspect code, page and record references; retain a local
byte-verified backup and verify the replacement source. Do not drop numerical
coverage, failure states or caveats while shortening the presentation. Validate
links, public history and the affected tools after cleanup. Private cases retain
full experiment evidence; preserve local regression coverage when changing a dependency.

Cleanup changes the current branch only. Previously committed material remains
in Git history; this policy does not authorize rewriting published history.
