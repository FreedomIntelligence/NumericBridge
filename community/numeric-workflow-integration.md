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
For preview validation, [get a verified public snapshot](README.md#get-a-verified-catalog-snapshot)
and set `--catalog-site` to its local site directory.
Append `--search-index /path/to/reviewed/catalog/site/data/search-index.json`
if you want local search in the same MCP entry; use the remote search MCP for
the server-managed index. Use a client with form elicitation unless consent was
explicitly recorded at intake or the user confirms from their own terminal.
The workflow never sends the HTTP request itself. The public HTTPS endpoints
require no SSH tunnel. For private tunneling on port 8788, use authenticated
intake or configure the public server and client with the same loopback URL.
The configured destination is bound into the draft; changing it requires a
new preview and confirmation.

## Lookup after request confirmation

Call `search_community` with `hardware`, `model`, `library`, and `task`
(`inference`, `sft`, or `rl`). Optional: `version`, `model_size`, `limit`, `offset`.
Omit `version` unless the user pinned it. Use `matches`, `recommendation`,
`alternatives`, and `retrieval.index_sha256` from the structured response.
`no-records` is a normal result; continue with the evaluation. A null
recommendation means there is no supported stack recommendation. Do not
silently replace a pinned version. If the server-managed MCP is unavailable,
try the public index at `https://freedomintelligence.github.io/NumericBridge/data/search-index.json` via
the bundled search CLI before falling back to a local snapshot. Record the
source, index SHA and available snapshot date for either route.

## Offer contribution after local delivery

1. Respect a prior refusal or no-share instruction. Prepare only publicly
   shareable results and minimum evidence. Public intake accepts only new
   anonymous drafts, not named credit or revisions. Use authenticated intake
   when the user wants named credit or a revision; do not silently convert
   named consent to anonymous consent. A credit choice does not authorize
   sending. Never derive a name from local identity.
2. Call `prepare_community_submission` with `title` and the relevant arrays
   `evaluations`, `repairs`, `library_groups`, `entities`, `links`, and the
   `evidence` object, keyed by new `evidence/submissions/*.json` paths. See the
   [record schemas](records/README.md). Optionally pass `attribution_mode`:
   `anonymous` (omit `attribution_name`), `nickname` (supply the chosen name),
   or `github` (supply `@username`; no login), and `supersedes` (prior receipt
   IDs when updating an earlier submission). `ready` returns `preview`,
   `draft_id`, and `draft_sha256`; `invalid` returns findings and sends nothing.
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
