# Private submission review

On the community server, run from `/opt/numericbridge` as the service user or
another authorized operator. Use your own name in `--reviewer`.

```sh
sudo -u numericbridge .venv/bin/python -B -m community.review list --status pending_review
sudo -u numericbridge .venv/bin/python -B -m community.review normalize --latest-pending
sudo -u numericbridge .venv/bin/python -B -m community.review show "$RECEIPT"
sudo -u numericbridge .venv/bin/python -B -m community.review decide "$RECEIPT" accepted \
  --reviewer "$REVIEWER" --reason 'Verified reproduction and evidence' --case "$CASE_ID"
```

`needs_information` and `declined` record other review outcomes. `list` shows
each receipt's `supersedes` and `superseded_by`; a superseded receipt cannot be
accepted or published, so decline it and review the latest one. Submitters read a
receipt's current state through the inbox's authenticated
`GET /v1/submissions/RECEIPT` (`community_submission_status` in MCP). New receipts
are discovered from `inbox/` on the next CLI operation and start as
`pending_review`. The original receipt is never modified. `show` includes
private submission data and the event history; do not share its output publicly.

After merging the community PR and confirming the case is visible, record the
publication with a checkout of the deployed site and the deployed index URL
(or a separately verified local copy). The command checks both contain the case
and saves the index digest. The maintainer confirms the community PR has merged.

```sh
sudo -u numericbridge .venv/bin/python -B -m community.review decide "$RECEIPT" published \
  --reviewer "$REVIEWER" --reason 'Case and search index deployed' \
  --site "$DEPLOYED_SITE" --index "$DEPLOYED_INDEX" --community-pr "$COMMUNITY_PR"
```

Use `link-upstream RECEIPT --upstream-pr URL --reviewer NAME --reason TEXT` as
needed; it works after acceptance or publication. Carry the original authorized
credit into the PR body. For the public acknowledgment use the
[contribution procedure](README.md#mcp-contribution).

The ledger is `/var/lib/numericbridge/review.sqlite3`, separate from the
immutable receipts under `inbox/`. Run `backup OUTPUT` under a private directory
outside `/var/lib/numericbridge`. It snapshots SQLite plus the receipts and checks
the database and receipt hashes before exposing the result. Store verified
snapshots in encrypted off-host storage with restricted access. To drill a
restore, unpack a snapshot into a new private directory and run `--state DIR
verify` and `--state DIR list`; leave live state untouched during the drill.
The upload token is managed separately and is never included in these backups.

## Private review workbench

On a trusted maintainer workstation, run `python -B -m community.inbox_view --ssh HOST_ALIAS`
using an authorized maintainer SSH alias for the community server. Open
`http://127.0.0.1:18890/`. This runs an on-demand read-only SSH command and
does not use the former contribution tunnel. To inspect a verified private
backup on the same workstation without SSH, run
`python -B -m community.inbox_view --state PATH_TO_PRIVATE_BACKUP`.

Loopback prevents network access but does not authenticate local users or
processes. Do not run the HTTP workbench on a shared host or expose it through
a public proxy; serve only on a workstation you trust with the review data.

Search by title or receipt ID. The status overview shows inbox-wide counts;
the queue shows the number of matches. The active-pending filter shows only
pending-review receipts without a newer replacement; the superseded filter
shows older versions. The ledger status of an older receipt is not changed.
Sorting and pagination apply to the current matches. The bundled evidence
count includes only files supplied in the receipt, not external or prior
references cited by its records.

Open a receipt to inspect its normalized evaluation, submitted metrics and
coverage, declared runtime configuration, explicit relationship claims,
original evidence, and review history. The page follows the operating system's
light or dark appearance and works without JavaScript.
Relationship evidence links only jump to matching evidence included in the
selected receipt. The workbench does not infer missing relationships. A
reported zero is not the same as a missing value. A coverage warning only
compares the submitted observed and expected values; it does not determine
acceptance or verify the submission independently.
Reload the page to see new deliveries. The workbench binds only to
loopback, sends no private data to third parties, and does not update the
inbox or ledger. Do not expose it through the public reverse proxy. Continue
to record decisions with the review CLI above; the workbench does not approve
or publish submissions.
