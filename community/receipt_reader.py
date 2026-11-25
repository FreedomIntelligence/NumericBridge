"""Read and verify receipts for intake status; this module never writes to the review ledger."""

from contextlib import closing
from hashlib import sha256
import json
import re
import sqlite3


RECEIPT = re.compile(r'submission-[0-9a-f]{64}')


def private_dir(path):
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError(f'Expected a private directory: {path}')


def read_receipt(inbox, receipt_id):
    if not RECEIPT.fullmatch(receipt_id):
        raise ValueError('Invalid receipt ID')
    path = inbox / (receipt_id + '.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError(f'Missing or non-private receipt: {receipt_id}')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != receipt_id.removeprefix('submission-'):
        raise ValueError(f'Receipt checksum mismatch: {receipt_id}')
    return path, raw


def supersession(inbox, db=None):
    """Map each receipt to the receipts it replaces and the later receipts that replace it."""
    replaces, replaced_by = {}, {}
    for path in sorted(inbox.glob('submission-*.json')):
        try:
            draft = json.loads(read_receipt(inbox, path.stem)[1])
        except ValueError:
            continue
        earlier = draft.get('supersedes', []) if isinstance(draft, dict) else []
        replaces[path.stem] = list(earlier)
    if db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='revision_links'").fetchone():
        for new, old in db.execute('SELECT new_receipt, old_receipt FROM revision_links ORDER BY at, new_receipt, old_receipt'):
            if new in replaces and old in replaces and old not in replaces[new]:
                replaces[new].append(old)
    for new, parents in replaces.items():
        for old in parents:
            replaced_by.setdefault(old, []).append(new)
    return replaces, replaced_by


def receipt_status(state, receipt_id):
    """Current review state of one receipt for its submitter, read-only (no ledger yet: pending_review)."""
    inbox = state / 'inbox'
    read_receipt(inbox, receipt_id)
    row = None
    _, replaced_by = supersession(inbox)
    ledger = state / 'review.sqlite3'
    if ledger.is_file() and not ledger.is_symlink():
        with closing(sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("SELECT name FROM sqlite_master WHERE name='events'").fetchone():
                row = db.execute('''SELECT state, at, community_pr, upstream_pr FROM events
                                    WHERE receipt_id=? ORDER BY seq DESC LIMIT 1''', (receipt_id,)).fetchone()
            _, replaced_by = supersession(inbox, db)
    return dict(receipt_id=receipt_id, review_state=row['state'] if row else 'pending_review',
                updated_at=row['at'] if row else None, community_pr=row['community_pr'] if row else None,
                upstream_pr=row['upstream_pr'] if row else None, superseded_by=replaced_by.get(receipt_id, []))
