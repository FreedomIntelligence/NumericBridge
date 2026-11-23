# NumericBridge MCP integration

The Numeric workflow owns experiments and local delivery. NumericBridge owns
community intake, review, publication, and any related upstream PR. The Numeric
workflow needs only the three MCP tools below. Do not call the review CLI,
open an issue, create a PR, or contact upstream on the contributor's behalf.

## Endpoints and setup

| Caller | Endpoint or command |
|---|---|
| Public search MCP client | `https://community.numericbridge.com/mcp` (Streamable HTTP after HTTPS launch) |
| Local contribution MCP client | `python -B -m community.mcp_submission --intake https://community.numericbridge.com/v1/submissions --token-file /private/upload-token` |
| Contribution MCP transport | `POST /v1/submissions` with Bearer authentication and the exact confirmed draft; the workflow never sends this HTTP request itself |

The client must support MCP form elicitation. Configure the upload credential
outside the prompt, tool arguments, and logs. Append `--search-index
community/site/data/search-index.json` to expose all three tools in one local
MCP entry; use the remote search entry for the server-managed index. During
private staging, an operator can tunnel to port 8788 on the server and replace
the public origin with `http://127.0.0.1:<local-port>` in the client config.
The configured destination is bound into the draft; changing it requires a new
preview and confirmation. Do not assume the public endpoints are live.

## Lookup after request confirmation

Call `search_community` with `hardware`, `model`, `library`, and `task`
(`inference`, `sft`, or `rl`). Optional: `version`, `model_size`, `limit`, `offset`.
Omit `version` unless the user pinned it. Use `matches`, `recommendation`,
`alternatives`, and `retrieval.index_sha256` from the structured response.
`no-records` is a normal result; continue with the evaluation. A null
recommendation means there is no supported stack recommendation. Do not
silently replace a pinned version. If the server-managed MCP is unavailable,
try the public index at `https://numericbridge.com/data/search-index.json` via
the bundled search CLI before falling back to a local snapshot. Record the
source, index SHA and available snapshot date for either route.

## Offer contribution after local delivery

1. Respect a prior refusal or no-share instruction. Prepare only publicly
   shareable results and minimum evidence. Ask for a credit preference if
   needed; that choice does not authorize sending. Default to anonymous and
   never derive a name from local identity.
2. Call `prepare_community_submission` with `title` and the relevant arrays
   `evaluations`, `repairs`, `library_groups`, `entities`, `links`, and the
   `evidence` object, keyed by new `evidence/submissions/*.json` paths. See the
   [record schemas](records/README.md). Optionally pass `attribution_mode`:
   `anonymous` (omit `attribution_name`), `nickname` (supply the chosen name),
   or `github` (supply `@username`; no login). `ready` returns `preview`,
   `draft_id`, and `draft_sha256`; `invalid` returns findings and sends nothing.
3. Show the actual preview, destination, and credit. Named credit covers the
   NumericBridge site, the community PR, and any related upstream PR filed by
   maintainers. Call `submit_community` with only the returned `draft_id` and
   `draft_sha256`. Its form is the only consent to send. Changing content,
   credit, or destination requires a fresh preview and confirmation.
4. On `received`, retain `receipt_id` and report that the contribution is
   pending review. `reused: true` means the receipt already existed.
   `declined` or `cancelled` ends the offer. For `invalid_draft`,
   `approval_unavailable`, `intake_unavailable`, or `submission_failed`, keep
   the draft and report the blocker; do not bypass the tool or auto-send.
   Finish local delivery regardless of the publication outcome.

`review_state: pending_review` is an intake acknowledgment, not a live review
status. NumericBridge has no public review polling tool. Use `search_community`
on a subsequent request to verify an already-published case; do not claim
publication from an intake receipt.
