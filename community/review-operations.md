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
each receipt's `supersedes` and `superseded_by`. A replaced receipt cannot be
accepted or published. Leave its original review outcome alone and review the
current version; do not record a new rejection just to remove an older version
from the active queue. The workbench keeps old receipts under version history.
Only decline a receipt for an actual review decision. Submitters read a
receipt's current state through the inbox's authenticated
`GET /v1/submissions/RECEIPT` (`community_submission_status` in MCP). New receipts
are discovered from `inbox/` on the next CLI operation and start as
`pending_review`. The original receipt is never modified. `show` includes
private submission data and the event history; do not share its output publicly.

After validating and promoting a [server catalog generation](../docs/community-storage.md),
verify that the published case is visible. Record the generation below; the CLI
checks the active catalog, the public index and the expected index digest. A GitHub
PR in the workflow repository is not needed.

```sh
sudo -u numericbridge .venv/bin/python -B -m community.review decide "$RECEIPT" published \
  --reviewer "$REVIEWER" --reason 'Case and search index deployed' \
  --catalog-generation "$GENERATION"
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

The optional read-only dashboard is maintained in the restricted
[NumericBridge Admin repository](https://github.com/FreedomIntelligence/NumericBridge-admin)
and deployed separately from the public website and Skills. Authorized
maintainers can use the [authenticated administration site](https://admin.numericbridge.com/)
or follow the private repository's SSH and backup instructions.

The dashboard is not required to run the public workflows. Record review
and publication decisions with the CLI above. Keep receipts, reviewer events,
backups, and deployment credentials out of all Git repositories.
