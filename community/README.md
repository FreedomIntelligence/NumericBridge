# NumericBridge community

The workflow repository holds community service code and schemas, not records.
The [server catalog](../docs/community-storage.md) owns approved records, evidence
and report archives. [NumericBridge Site](https://github.com/FreedomIntelligence/NumericBridge-site)
contains only public frontend code; the private workbench reads catalog metadata.

| Directory | Purpose |
|---|---|
| [site/](site/README.md) | Frontend revision pin and production guide |
| [records/](records/README.md) | Python validation, read-only search and relations |
| [mcp_search.py](mcp_search.py) | Optional MCP community search server |
| [mcp_submission.py](mcp_submission.py) | Local previews and confirmed uploads |
| [intake.py](intake.py) | Private inbox for received submissions |

```sh
SITE=/path/to/reviewed/catalog/site
python -B -m community.records validate --site "$SITE"
python -B -m community.records index --site "$SITE" --output /tmp/numericbridge-records.json
work=$(mktemp -d)
python -B -m scripts.site_bundle --data-site "$SITE" --output "$work/site"
python -m http.server 8000 --bind 127.0.0.1 --directory "$work/site"
```

Run commands from the repository root. The production site at
`https://numericbridge.com/` serves the pinned frontend and canonical public records.
The same HTTPS host exposes community search and authenticated intake. An
operator may also enable token-free intake for new anonymous contributions.

## MCP search

Install the optional server dependencies and start the endpoint from the repository root:

```sh
python -m pip install -r community/requirements-mcp.txt
python -B -m community.mcp_search --index /path/to/search-index.json
```

Connect an MCP client directly to `https://numericbridge.com/mcp` using
Streamable HTTP. No upload token or SSH tunnel is needed for search. If you are
running the server locally, use `http://127.0.0.1:8769/mcp` instead.
For a client that starts its own process, use `python -B -m community.mcp_search --transport stdio --index /path/to/search-index.json`
from the repository root instead.

The server exposes one tool, `search_community`. Its required arguments are
`hardware`, `model`, `library` (the tested framework), and `task` (`inference`,
`sft`, or `rl`). Optional filters are `version` and `model_size`; `limit` and
`offset` page the case list. Example arguments:

```json
{"hardware":"Ascend 910C","model":"Qwen3.5-9B-Base","library":"MindSpeed","task":"rl"}
```

The structured result uses the shared search tool's versioned contract:
`matches` contains case, issue, solution and verification references;
`recommendation` is `null` when the evidence does not support a version
combination for the request. `alternatives` can list evidenced versions of the
same stack when the request pins another version. `no-records` is a successful
empty lookup. `retrieval.index_sha256` identifies the snapshot.
The server reads the public search index at startup;
restart after installing a new snapshot. Operators can set `--index` to a local
snapshot and `--sha256` to its expected digest.

For a public endpoint, run the service behind an HTTPS reverse proxy that
preserves the external Host header. Pass that header with `--allow-host` and
use `--allow-origin` for any required Origin values. For example, for the
placeholder public address `https://mcp.example.org/mcp`:

```sh
python -B -m community.mcp_search --allow-host mcp.example.org
```

The MCP tool reads public records. It does not accept data uploads, change
community records or run model workloads.

## MCP contribution

See the [Numeric workflow integration contract](numeric-workflow-integration.md)
for the agent-facing interface and [private review operations](review-operations.md)
for the maintainer CLI and backup procedure.

The [workflow](../skills/numericbridge/references/workflow.md#community-contribution)
prepares an anonymized preview and asks once before sending it to the community.
The contributor needs no GitHub account or fork.

| Operation | Input | Result |
|---|---|---|
| `prepare_community_submission` | `title` plus optional arrays `evaluations`, `repairs`, `library_groups`, `entities` and `links`, and an `evidence` map of new `evidence/submissions/*.json` paths to JSON objects. | `ready` returns `draft_id`, `draft_sha256` and the exact local `preview`; `invalid` returns findings. This does not upload data. |
| `submit_community` | `draft_id` and `draft_sha256` after showing the preview. The MCP form asks for consent unless a valid `consent_record` has recorded it. | `received` returns `receipt_id`, `review_state: pending_review`, and `reused`. The same upload returns the original receipt. Other outcomes include `declined`, `cancelled`, `approval_unavailable`, `intake_unavailable`, `invalid_draft` and `submission_failed`. |
| `community_submission_status` | `receipt_id`. For public intake, use the same configured inbox and draft directory. | The receipt’s review state, or `status_unavailable` if no local status credential can be read. |

Use the [record format](records/README.md) and
[writing guide](../skills/precision-compare/references/community-records.md).
A draft accepts up to 1 MiB of records and minimum evidence. Evidence hashes
use UTF-8 JSON, two-space indentation, unescaped Unicode and a final newline in
the supplied key order. Preview validates against an explicitly supplied local
catalog generation; the inbox revalidates independently against its current
server-owned catalog before storing bytes.
Changing the content or inbox destination requires a new preview and consent.

### Get a verified catalog snapshot

The workflow checkout and the standalone search index do not contain the
records and evidence needed to validate a draft. Ask the catalog operator for
an export of one published generation’s `site/data/` and `site/evidence/` and its
expected SHA-256, obtained through a trusted channel. Operators can export these
two trees from one pinned [catalog release](../docs/community-storage.md#store-layout-and-access);
do not export the private inbox or archives. Compare the downloaded archive's
SHA-256 (for example with `shasum -a 256 ARCHIVE`) with the operator's expected
value before extracting it into a new local directory. Do not assemble a
snapshot file by file across a live catalog update. Validate the extracted site:

```sh
SITE=/path/to/frozen/site
python -B -m community.records validate --site "$SITE"
python -B -m scripts export-search --check --site "$SITE"
```

Point `--catalog-site` (or `NUMERICBRIDGE_CATALOG_SITE`) at the verified directory
containing `data/` and `evidence/`. The local MCP does not fetch it automatically.

### Configure agent contribution

Configure the local stdio MCP in your agent client. Read the trusted site's
`data/community-service.json` first: when present, `public_submission` advertises
a URL with `method: "POST"` and `auth: "none"`. Use its URL as `--intake` and
omit `--token-file`. For example, **if enabled and advertised**:

```sh
python -B -m community.mcp_submission \
  --intake https://numericbridge.com/v1/public-submissions \
  --catalog-site /path/to/reviewed/catalog/site \
  --search-index /path/to/reviewed/catalog/site/data/search-index.json
```

If `public_submission` is absent, do not assume the public API is available.
The separate `submission` entry advertises the existing authenticated URL
(`auth: "bearer"`); `submission: null` means it is not advertised. To use it,
set `--intake https://numericbridge.com/v1/submissions --token-file /path/to/upload-token`
with an upload credential issued by the operator. Omit `--intake` and
`--token-file` to keep drafts local. To submit in a browser instead, prepare an
anonymous draft with the public MCP configuration above, then select the saved
`draft-<sha256>.json` at [the official submission page](https://numericbridge.com/submit.html).
The browser previews it locally and only uploads it after confirmation. Save the
private receipt file if you want to check its status later.

Public intake accepts only **new anonymous** drafts; it does not accept
`supersedes`. Authenticated intake still supports revisions and anonymous,
nickname, or GitHub `@username` credit without a GitHub login. Pass
`attribution_mode` and, for named credit, `attribution_name` to
`prepare_community_submission`. The same confirmation authorizes the exact
result and named credit on the community site and in related PRs maintained by
NumericBridge, including upstream PRs.

The agent shows the exact preview and waits for the user's MCP confirmation
unless an explicit `share` consent in the case's `request.json` is supplied as
`consent_record` and passes validation. Token-free transport does not authorize
an upload. Do not send after a refusal or bypass the consent gate with HTTP.

If `submit_community` returns `approval_unavailable`, keep the saved draft.
In your own terminal, use the same Python environment and inbox as MCP to
review and confirm that draft. For public intake:

```sh
python -B -m community.submit_reviewed --draft-sha256 DRAFT_SHA256 \
  --intake https://numericbridge.com/v1/public-submissions
```

For authenticated intake, use its URL and add the same `--token-file` as MCP.

Use `--drafts` if the MCP was configured with a different draft directory.
The command displays the draft and only sends it after you type `SEND`. A
receipt means the inbox saved it for review; it does not publish to GitHub.

The public `POST /v1/public-submissions` uses the same confirmed draft envelope
and `X-Draft-SHA256` value as authenticated intake, without an `Authorization`
header. It returns the same receipt fields plus a secret 64-hex `status_token`.
`GET /v1/public-submissions/{receipt}` requires that exact receipt’s token as
`Authorization: Bearer …`; the receipt ID alone grants no access. The MCP and
terminal client store the token in a mode `0600` file under `.status-tokens/`
inside the configured draft directory. Do not display, log, or paste it into a
prompt. If local storage fails or the token is lost, keep the receipt ID;
resubmit the exact reviewed draft through the consent flow to recover access.

With `--search-index`, this client exposes both contribution and search tools. Use an
absolute index path when launching from a different directory. Restart the MCP
after updating its index, or configure a separate connection to the public
search MCP for the server-managed snapshot.

For interactive confirmation, use a client that presents the MCP form to the
user. In clients that launch from another directory, use the absolute Python
executable and set `PYTHONPATH` to the NumericBridge checkout. If MCP is
unavailable, the equivalent local-only preview command is:

```sh
python -B -m community.records.submission --catalog-site "$SITE" --input submission.json --output preview.json
```

The input contains `title` and `additions`. The `additions` groups are
`evaluations` (`evaluations`), `repairs` (`repairs`, `library_groups`), `relations`
(`entities`, `links`) and `evidence` (path to JSON object). Use empty objects for
unused groups. The command writes a new private `local_draft` file; reuse its
additions once an inbox is configured.

### Community operator setup

Production serves the static catalog and endpoint descriptor over HTTPS,
alongside the API, with persistent private storage for intake. For a Linux host:

1. Create a dedicated `numericbridge` service user and group. Check out the
   reviewed source at `/opt/numericbridge` with a current `origin/main` ref.
   Own the checkout as the service user so Git can read it, then install dependencies:

   ```sh
   python3 -m venv /opt/numericbridge/.venv
   /opt/numericbridge/.venv/bin/python -m pip install -r /opt/numericbridge/community/requirements-mcp.txt
   ```

2. Put a generated URL-safe upload token in `/etc/numericbridge/upload-token`
   (root-owned, mode `0600`). Install the
   [systemd unit](deploy/numericbridge-intake.service) and run
   `sudo systemctl enable --now numericbridge-intake`. The service binds to
   loopback; save and back up `/var/lib/numericbridge/inbox` separately from the
   code. Restart the service when rotating the upload token.
3. Set `NUMERICBRIDGE_COMMUNITY_HOST=numericbridge.com` in
   `/etc/numericbridge/search.env`, install the
   [search unit](deploy/numericbridge-search.service), and enable it with
   `sudo systemctl enable --now numericbridge-search`. The
   [Nginx staging config](deploy/numericbridge-nginx.conf) exposes both routes
   on loopback port 8788 for private staging only. For production, point
   `numericbridge.com` to the service host and use the same routes in its HTTPS
   virtual host. With Caddy 2.10 or later, use the
   [reverse proxy config](deploy/Caddyfile) and set the same hostname in its
   service environment. Search exposes `POST /mcp` via stateless Streamable
   HTTP; intake exposes `POST /v1/submissions`, the authenticated receipt
   status `GET /v1/submissions/RECEIPT` and `GET /healthz`. Set request
   rate limits at the public ingress. To additionally expose the optional public
   submission routes, set `NUMERICBRIDGE_PUBLIC_INTAKE_URL` (or pass `--public-url`
   to the intake process) to the exact HTTPS public submissions URL. Apply the
   public intake rate limits at ingress before advertising it.
4. Call `GET /healthz` over HTTPS, check that an unauthorized `POST` returns
   `401`, then test with a synthetic draft. Confirm that the live
   `data/community-service.json` advertises the verified HTTPS intake URL.
   Verify the public MCP with `tools/list` and a known `search_community` query.
   Distribute the upload credential privately; only the endpoint URL belongs
   in the public descriptor. Generate that manifest with
   `python -B -m community.service_manifest --output PATH`; set
   `NUMERICBRIDGE_INTAKE_URL` for authenticated discovery and
   `NUMERICBRIDGE_PUBLIC_INTAKE_URL` only for a verified public submission URL.
   When enabling public intake, verify token-free POST and receipt-scoped GET
   separately. After publishing approved
   records via the server catalog tool, restart `numericbridge-search`
   to load its new index.

For local testing, run `python -B -m community.intake --inbox PATH --token-file PATH`
and use `http://127.0.0.1:8770/v1/submissions` as the authenticated MCP intake URL.
To test public intake locally, add `--public-url http://127.0.0.1:8770/v1/public-submissions`
and `--public-status-key-file /path/to/dedicated-secret` to the server command,
then configure MCP with that URL and no token file. Keep the signing key
separate from the legacy upload token and persistent across restarts.
Keep `origin/main` current on the review host. Each `submission-<sha256>.json`
holds the approved bytes for maintainers to review. Maintainers decide what to
add to the catalog and whether to contact upstream. A case is searchable once
the deployed index returns its record ID.

After accepting a named submission, run `python -B -m community.records.acknowledgments
--receipt RECEIPT --case CASE_ID --zh DESCRIPTION --en DESCRIPTION` against the
authorized review input. Add the returned entry to the candidate's `data/acknowledgments.json`
with the accepted case and use the same name and contribution in the community
and related upstream PR bodies. An anonymous receipt returns `null`. Keep the
receipt private. The maintainer opens PRs and handles any upstream follow-up.

The former `precision_community` package is now `community.records`. Existing
command flags remain the same. The former root `site/` moved to `community/site/`;
published URLs and paths relative to the static site remain unchanged. Historical
provenance strings inside frozen public files retain their original paths.
