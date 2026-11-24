# Community website

The main repository contains the website documentation and frontend pin, not the
canonical records or evidence. The frontend lives in
[NumericBridge Site](https://github.com/FreedomIntelligence/NumericBridge-site)
and is pinned by [frontend-revision.txt](frontend-revision.txt). The public site is
[`numericbridge.com`](https://numericbridge.com/).

## Local preview

Select a local, read-only catalog site with `data/` and `evidence/` directories.
Run this from the source checkout, setting `CATALOG_SITE` explicitly:

```sh
CATALOG_SITE=/path/to/catalog/site
work=$(mktemp -d)
python -B -m scripts.site_bundle --data-site "$CATALOG_SITE" --output "$work/site"
python -m http.server 8000 --bind 127.0.0.1 --directory "$work/site"
```

Only the frontend is read from Git. The build does not locate, fetch or copy a
catalog unless `--data-site` is supplied. Use `--frontend-only` instead for a
build with no records; data-dependent views will not load. Add
`--frontend-checkout` to either mode to reuse a locally fetched frontend
commit. When changing the frontend, review the new commit and update the pin
before building the new site.

## Production operators

- Keep the application checkout (for example, `/opt/numericbridge`) separate
  from the catalog store. The web root remains the separate frontend release
  at `/opt/numericbridge-site/current`; its only catalog links are `data/` and
  `evidence/` under `/var/lib/numericbridge-catalog/current/site`, served at their
  existing same-origin URLs. Do not serve the store, unpublished
  generations, the checkout, or private intake state.
- Validate and stage each new generation before switching `current`. Allow
  the web server and the `numericbridge` service account to read the site;
  publication owns writes. Restart the [search unit](../deploy/numericbridge-search.service)
  after switching so its in-memory index matches the public site. Set
  `NUMERICBRIDGE_COMMUNITY_HOST=numericbridge.com` in `/etc/numericbridge/search.env`.
- Keep the production service descriptor and live PR status in their existing
  exact Nginx routes; neither belongs in a versioned catalog. The [intake unit](../deploy/numericbridge-intake.service)
  reads the catalog with `NUMERICBRIDGE_CATALOG_SITE=/var/lib/numericbridge-catalog/current/site`.
  Its upload credential and `/var/lib/numericbridge/inbox` stay private and independent.
- After a switch, verify the homepage, a known evidence link, the JSON search
  index and the live MCP search against the same generation. Check the service
  descriptor and intake health endpoint when intake is enabled.

## Legacy GitHub Pages compatibility

Keep the existing GitHub Pages custom-domain setting and DNS unchanged. The
legacy project URL [`freedomintelligence.github.io/NumericBridge/`](https://freedomintelligence.github.io/NumericBridge/)
currently issues HTTP 301 redirects to the production domain, preserving the
path below `/NumericBridge/` and the query. `numericbridge.com` already
resolves to the independent server; `www.numericbridge.com` points at the apex.
The Pages workflow publishes only browser redirect pages and a `404.html`
browser fallback, with no catalog, service descriptor, credentials or access to
production. The fallback preserves the path, query, and fragment with JavaScript.

Verify the production routes before merging workflow changes that publish a new
Pages artifact. After any Pages release, verify the legacy homepage, a deep
HTML link with a query and fragment, and legacy `data/search-index.json` and
`evidence/` URLs.
Use `curl -I` to check that Pages sends the 301 to the matching production path
and `curl -L` to verify the final response and content type. If GitHub drops the
custom-domain routing, the browser fallback cannot restore HTTP redirects for
JSON clients; those clients must use the production origin. Do not point the
production domain back at Pages: the legacy artifact is not a working catalog.

## Pages

| Route | Content |
|---|---|
| `index.html` | Comparison board, platform views and repair results |
| `index.html#/cases` | Target selection, library combinations, issues and solutions |
| `index.html#/report/<case-id>` | Measured report for one comparison or verification linked from the case library |
| `index.html#/compare` | Hardware platform comparison across measured targets |
| `index.html#/upstream` | Upstream PR/MR contributions with status timelines and Side Peek verification details |
| `index.html#/list` | Historical measurement list and existing record links |
| `archive.html` | Evaluation, repair and measurement archives |
| `rsi.html` | RSI iteration summaries |

Opening the case library without filters shows a measured example. Reset clears
the filters; links with a target or a case open that selection directly.

## Shared data

`data/evaluations.json` and `data/repairs.json` own experiment configurations and
measurements. `data/relations.json` connects cases, issues, solutions and
verifications. `data/upstream.json` tracks PRs/MRs submitted upstream with
append-only status events. Public titles and summaries live in record `content` or
`data/content.json`. The board, case library, archive and Agent retrieval consume
these descriptions. See the [record model](../records/README.md).

`board-data.js` derives the board's metrics and groups at page load.
`community.records.search` compiles `data/search-index.json` for both `search.js`
and the standalone Skill search tool. The generated index pins all contributing
catalogs, including content and historical framework annotations. Refresh it
after changes to those sources, in the staged catalog, not the active generation:

```sh
python -B -m scripts export-search --site "$STAGED_SITE" --refresh
python -B -m community.records validate --site "$STAGED_SITE"
```

Titles describe a task or problem. Summaries explain its effect or solution in
plain language. Versions, runtime settings, verification outcomes and evidence
are separate fields. Implementation detail and historical notes are available in
the detail views. Missing optional summaries render as empty rather than a stock
warning. Editorial updates use typed record IDs; frontend JavaScript does not
contain per-case title overrides.

## Configuration and issue discovery

The left panel selects hardware, model, framework and an optional framework
version. Framework choices are grouped by recorded workload: training (SFT),
inference and RL. A framework can appear in several groups; selecting it also
selects that workload and scopes the available versions. Each group shows up to
three rows. Its overflow button opens the shared framework drawer at that group.
The drawer offers search and an unscoped list for records without a workload
label. Framework options come from the measured sides and repair targets.
Dependencies and their versions stay in the configuration details.
Inline options come from actual catalog values, ordered by matching record count.
The “…” drawer exposes the full inventory and custom targets. The example button
selects an eligible recorded configuration from the current index.

The right panel displays a recommended configuration and its issues. Opening an
issue shows its solutions, verification outcomes and tested versions. Evaluation
cases show a short result summary. Use the [community records interface](../records/README.md)
for machine retrieval of exact settings, metrics and source evidence.

A query without matching records returns an empty result. The Agent can continue
its normal comparison/repair workflow. Records with insufficient recommendation
support remain available in the report and archive. Other supported library
versions use a separate version-selection action.

Recommendation support is compiled from pinned dependencies, measured coverage,
recorded acceptance and links between the solution, verification and original
workload. Resolution also requires a link from the solution to the selected issue.
Partial, rejected and source-inspected candidates retain their own states.

Hardware aliases group H20Z under H200, B20Z under B200 and 910_9382 under Ascend 910C. Device
counts, topology and raw settings remain attached to the actual configuration.
Unknown hardware and model names retain their identities.

## Maintenance

`cases.js` and `cases.css` implement the case route. `search.js` queries the shared
index and resolves localized content. `evaluation-presentation.js` and
`repair-presentation.js` support archive clients; current records supply content
metadata, while older standalone callers retain their formatting fallback.
`differences.js` displays measurement views and their original source references.

Source catalogs are checked against the search index when loading the case route.
Use localhost or HTTPS for browser hash APIs. Query parameters and case/issue deep
links survive reloads.
The site is static; submissions are reviewed before a new catalog is published.

The [publication guide](../../docs/publication.md) describes public artifacts.
The [association ledger](https://numericbridge.com/evidence/community-record-associations-20261117.json),
[H200 correction](https://numericbridge.com/evidence/hardware-name-correction-H200-20261117.json) and
[token-mean clarification](https://numericbridge.com/evidence/token-mean-scope-clarification-20261117.md)
retain the historical record mapping.
