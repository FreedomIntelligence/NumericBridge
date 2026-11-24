# Server-side community catalog storage

The community catalog is operator-managed release data, not a Git checkout or
an upload target. Run [the release CLI](../community/catalog_store.py) on the
catalog server from an approved workflow-code checkout. It does not publish to
Git, start or reconfigure a service, or access the network. Private intake,
review queues and working candidates remain outside the store. Never grant a
public service or submission worker write access to the store or mount an
upload path beneath it.

## Store layout and access

```text
/var/lib/numericbridge-catalog/
  current -> releases/<generation>
  releases/<generation>/
    site/data/
    site/evidence/
    archive/reports/
    SHA256SUMS
    SOURCE_REVISION
  .promotions/
```

Use an operator-owned local POSIX filesystem with working directory locks,
atomic rename, and directory `fsync`. Pre-create the root and ensure neither
it nor its release paths are group/world writable. A reader should have only
read/traverse access; expose only the two public trees under `current/site`,
not the storage root, archive, staging directories or release metadata. The
release command does not configure public routes. If the service caches its
catalog, follow its documented reload/health-check procedure after promotion or
rollback. Readers handling multiple files should resolve `current` once per
snapshot rather than open individual files across a pointer change.

## Prepare a reviewed candidate

Supply a complete, explicit `SITE` directory outside the store containing
exactly `data/` and `evidence/`. An integrated website checkout may contain
HTML or other assets beside these directories; build a clean export of just
these two trees in a trusted operator workspace. Use a known release export as
the baseline rather than attempting to assemble current history from workflow
Git. From the approved code checkout, run
`python3 -B -m scripts export-search --site "$SITE" --refresh` to refresh the
candidate’s search index. Run it only against the reviewed staging copy. Keep
the input trees stable and ensure untrusted users cannot alter them while the
command runs. For the seed, independently verify
that the candidate and report archive represent the existing published state;
there is no older server release for the command to compare against.

Select only final, publication-reviewed files. The CLI uses an exact data
filename registry and a small extension allowlist for evidence and reports.
Already-published binary evidence and two historical report examples that
resemble intake drafts are accepted only at their existing paths with their
pinned hashes. Path checks reject dotfiles, traversal, unsafe or ambiguous
names, common private paths and receipt IDs, symlinks, hard links and special
files. JSON checks reject duplicate keys and recognizable intake envelopes.
These checks are not a detector for personal data, credentials, sensitive
measurements or misnamed private content: manually review the actual bytes and
provenance, including textual attachments, SVG and code. Do not execute
attachments. Review any new canonical data filename or binary format before
adding it to the policy.

Pass `--reports REPORTS` with a separate, complete, reviewed report tree to
retain report material under `archive/reports/`. Omission on promotion carries
the prior archive forward; omission on seed creates an empty archive. Existing
reports must remain byte-identical at the same paths even if a new complete
report tree is supplied. Do not pass dossiers or private working material as
reports.

## Seed, promote, and roll back

Set `SITE` and `REPORTS` to the reviewed export directories and `REVISION`
to its independently approved full lowercase Git object ID. The revision is
recorded for traceability; the CLI neither resolves it in Git nor attests that
the input came from it. Use a fresh, unique generation for each attempt.

```sh
python3 -B -m community.catalog_store seed --generation seed-1 \
  --site "$SITE" --reports "$REPORTS" --source-revision "$REVISION" --dry-run

python3 -B -m community.catalog_store promote --generation release-2 \
  --expect-current seed-1 --site "$SITE" --source-revision "$REVISION" --dry-run

python3 -B -m community.catalog_store rollback --generation release-2 --dry-run
```

The examples are sequential operation templates, **not** a dry-run sequence:
a promotion requires a seeded `current` and rollback requires the named
promotion to be current. Inspect the dry-run result, and only then repeat that
operation without `--dry-run` to apply it. Use `--root /absolute/test/store` for
isolated tests. Dry-run locks the same store directory as a live operation,
copies and validates in a private directory under `/tmp`, and makes no changes
to the store or published pointer. Every real operation takes a nonblocking
exclusive lock; if another operator holds it, diagnose or wait before retrying.

The command copies regular files into a private stage, validates the catalog,
its repair, evaluation, survey, upstream and acknowledgment data, checks the
exact compiled search snapshot, and compares against the current server
release manifest before publication. It writes a deterministic `SHA256SUMS`
containing every file’s digest (including `SOURCE_REVISION` and archived reports,
excluding the manifest itself), then rereads the stage to verify it. Only then
does it install a new generation and atomically replace the relative
`current -> releases/<generation>` symlink. Before switching it records the old
generation and old manifest digest under operator-only `.promotions/`. Rollback
switches back to exactly those verified bytes; it does not recompile with the
new checkout, overwrite either generation, or authorize an arbitrary target.
The seed has no predecessor. After rollback, any subsequent promotion compares
against the restored current release, not against the abandoned generation.

A verification failure never changes `current`. An interrupted live operation
may leave a private stage or an inactive generation: inspect the pointer and
receipts, keep prior releases intact, and do not reuse a generation name. If a
failure occurred at the pointer switch, verify the actual current target before
retrying. If an interrupted first seed left releases but no pointer, stop for
operator recovery instead of bypassing the nonempty-store check. Back up release
data and the operator metadata; checksums are not signatures and do not defend
against a trusted operator rewriting the store.

### Append-only policy and limitations

All historical evidence, reports, and data outside the supported appendable
collections keep their exact bytes at the same paths. Evaluations, repairs and
library groups, relations, survey rows/sources/branches, upstream events and
contributions, public credit, descriptions and supported source/annotation
maps can grow without rewriting existing objects. Existing list rows must
remain in order at the same indices; existing map entries remain unchanged.
Only supported release date metadata can advance and checked derived survey
counts and the search snapshot can be regenerated. Unrecognized metadata,
live RSI snapshots and standalone historical data exports are frozen in a
normal promotion; a change requires an explicitly reviewed policy/migration
rather than a generic bypass. The Git history validator’s standalone hardware
naming correction is **not** applied during server promotion: seed the already
corrected published state or handle a discrepancy through a separately reviewed
migration. The CLI has no throwaway-history, force or delete mode.
