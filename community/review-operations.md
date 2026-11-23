# Private submission review

On the community server, run from `/opt/numericbridge` as the service user or
another authorized operator. Use your own name in `--reviewer`.

```sh
sudo -u numericbridge .venv/bin/python -B -m community.review list --status pending_review
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

## Read-only inbox view

On the maintainer’s computer, run `python -B -m community.inbox_view --ssh HOST_ALIAS`
and open `http://127.0.0.1:18890/`. Use the authorized SSH alias for the
community server. Reload the page to see new deliveries. The view binds only
to loopback and never runs the review CLI or updates the inbox or ledger.
