# NumericBridge MCP integration

The Numeric workflow owns experiments and local delivery. NumericBridge owns
community intake, review, publication, and any related upstream PR. The Numeric
workflow uses the MCP interfaces below. Do not call the review CLI,
open an issue, create a PR, or contact upstream on the contributor's behalf.

## Endpoints and setup

| Caller | Endpoint or command |
|---|---|
| Public search MCP client | `https://numericbridge.com/mcp` (Streamable HTTP, read-only, no token) |
| Local contribution MCP (public intake, if enabled) | `python -B -m community.mcp_submission --intake https://numericbridge.com/v1/public-submissions`; no token file |
| Local contribution MCP (authenticated) | `python -B -m community.mcp_submission --intake https://numericbridge.com/v1/submissions --token-file /private/upload-token` |

For public intake, set `--intake` to the URL in the trusted site's
`data/community-service.json` (`public_submission`, `auth: "none"`) and omit
`--token-file`. If that entry is absent and there is no separately verified
public URL, keep drafts local or use authorized authenticated intake. Configure
any authenticated upload credential outside prompts, tool arguments, and logs.
The [local contribution MCP resolves the official catalog automatically](README.md#get-a-verified-catalog-snapshot). Call `check_contribution_catalog` early when contribution is allowed; a failed check does not block evaluation. At preparation, use the tool again (or let `prepare_contribution` retry). Preserve local results and the resume point on `catalog_unavailable`; do not ask contributors to bring a snapshot or checksum.
Use a client with form elicitation unless consent was already recorded or the
user confirms from their own terminal.
The workflow never sends the HTTP request itself. The public HTTPS endpoints
require no SSH tunnel. For private tunneling on port 8788, use authenticated
intake or configure the public server and client with the same loopback URL.
The configured destination is bound into the draft; changing it requires a
new preview and confirmation.

## Lookup after request confirmation

Call the official [`search_community` MCP](README.md#mcp-search) and read its
structured response. Keep `retrieval.index_sha256` with the result. See the
[workflow](../skills/numericbridge/references/workflow.md#route-community-results)
for how to use matches and when to skip lookup.

## Offer contribution after local delivery

Workflow agents submit local PR/MR drafts only to NumericBridge intake. Never
open or update PRs/MRs (including drafts) in the original upstream repository,
create public forks, or push branches to publish those drafts. Read upstream
repositories for verification and templates only. NumericBridge maintainers
review the community submission and handle any upstream filing. An existing
upstream PR does not count as a NumericBridge contribution or receipt.

1. Call `community_contribution_capabilities` before preparing a contribution.
   Respect a prior refusal or no-share instruction. Prepare only publicly
   shareable results and minimum evidence. Public intake accepts only new
   anonymous drafts, not named credit or revisions. Use authenticated intake
   when the user wants named credit or a revision; do not silently convert
   named consent to anonymous consent. A credit choice does not authorize
   sending. Never derive a name from local identity.
2. Call `prepare_contribution` with a [typed contribution](README.md#typed-contribution-input).
   Use `inspect_upstream_pr_template` before preparing a patch candidate. Show
   both `preview.additions` (eligible to publish) and `preview.review_evidence`
   (maintainers only). Optionally pass `attribution_mode`: `anonymous` (omit
   `attribution_name`), `nickname` (chosen name), or `github` (chosen
   `@username`; no login), and `supersedes` (prior receipt IDs). `ready` returns
   `preview`, `draft_id`, and `draft_sha256`; preparation never sends. Do not use
   the legacy catalog tool to route around an unsupported or untrusted template.
3. Named credit covers the NumericBridge site, the community PR, and any related
   upstream PR filed by maintainers. When `request.json` records
   `community_contribution.consent: "share"`, pass `consent_record` (the
   `request.json` path) to `submit_community`: it verifies matching attribution
   and a clean private-identifier scan, then submits without an interactive
   form. Without prior intake consent, show the preview, destination, and credit
   and call `submit_community` with only `draft_id` and `draft_sha256` so its
   form asks once. A token-free destination does not waive consent. Changing
   credit or destination requires a fresh preview and approval.
4. On `received`, retain `receipt_id` and report that the contribution is
   pending review. `reused: true` means the receipt already existed. If the
   result includes `status_lookup_warning`, the private status credential could
   not be saved locally. Retain the receipt ID; to recover status access, retry
   only that same reviewed draft through the consent flow.
   `declined` or `cancelled` ends the offer. For `invalid_draft`,
   `approval_unavailable`, `intake_unavailable`, or `submission_failed`, keep
   the draft and report the blocker; do not bypass the tool or auto-send.
   Finish local delivery regardless of the publication outcome.

`review_state: pending_review` is an intake acknowledgment, not publication.
Call `community_submission_status` with `receipt_id` to inspect the inbox
review state. For public intake, use the same configured endpoint and draft
directory; the local client attaches the private receipt credential. Never ask
for the token in a prompt, tool call, or log. Status values include
`pending_review`, `needs_information`, `accepted`, `declined`, and `published`;
a result can also include `superseded_by` or linked PRs. Use `search_community`
to verify a published case is live in the deployed search index.

## Recover a legacy checkpoint

If local delivery is complete but an older checkpoint asks for a catalog
archive, leave the original checkout, measurements and request unchanged.
Install the [local contribution dependencies](requirements-mcp.txt) in an
isolated interpreter and run from the **current verified tooling checkout**:

```sh
python -B -m community.contribution_resume --case-dir CASE --drafts PRIVATE_DRAFTS
```

The command finds the typed input from the old checkpoint, validates local
delivery, fetches the official catalog and writes a digest-bound local preview.
It does not edit the case, rerun it, ask for consent, or call intake. Do not
transfer the full case or raw evidence to this tool's catalog endpoint.

`setup_required` means the local tool or its inputs are not ready;
`catalog_unavailable` is a service/cache validation blocker, not a request for
an archive. `preview_ready` with `awaiting_consent` is a verified preview, not
approval. Show the exact draft and use the normal consent-aware submission tool
only when authorized (set its `--drafts` to the same private directory).
`already_received` reuses a bound receipt; `receipt_unverified` requires
reconciliation and must not trigger a second send. A local-only decision stays
local.
