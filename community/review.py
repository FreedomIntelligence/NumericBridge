"""Manage the private NumericBridge inbox and its review history."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import urlsplit

from .records.submission import draft_attribution


RECEIPT = re.compile(r'submission-[0-9a-f]{64}')
TRANSITIONS = {
    'pending_review': {'needs_information', 'accepted', 'declined'},
    'needs_information': {'pending_review', 'accepted', 'declined'},
    'accepted': {'published', 'needs_information', 'declined'},
    'declined': {'pending_review'},
    'published': set(),
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id),
    state TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
    at TEXT NOT NULL, case_id TEXT, community_pr TEXT, upstream_pr TEXT, index_sha256 TEXT
);
'''


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


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


def connect(state):
    private_dir(state)
    private_dir(state / 'inbox')
    db = sqlite3.connect(state / 'review.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    return db


def sync(state, db):
    inbox = state / 'inbox'
    known = {row[0] for row in db.execute('SELECT id FROM receipts')}
    for path in sorted(inbox.glob('submission-*.json')):
        if path.stem not in known:
            read_receipt(inbox, path.stem)
            received = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')
            db.execute('INSERT OR IGNORE INTO receipts VALUES (?, ?)', (path.stem, received))


def queue(db):
    return [dict(row) for row in db.execute('''
        SELECT r.id AS receipt_id, r.received_at, COALESCE(e.state, 'pending_review') AS state,
               e.reviewer, e.at AS updated_at, e.case_id, e.community_pr, e.upstream_pr, e.index_sha256
        FROM receipts r LEFT JOIN events e ON e.seq = (
            SELECT MAX(seq) FROM events WHERE receipt_id = r.id
        ) ORDER BY r.received_at, r.id
    ''')]


def pr_url(value):
    if value:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or parts.netloc != 'github.com' or parts.query or parts.fragment
                or not re.fullmatch(r'/[\w.-]+/[\w.-]+/pull/[1-9][0-9]*', parts.path, re.ASCII)):
            raise ValueError('Expected a GitHub pull request URL')
    return value


def publication(case_id, site, index_path):
    from tools.community_search import load_index
    from .records.catalog import Catalog

    Catalog(site).get('case', case_id)
    index, receipt = load_index(index_path)
    if not any(row['id'] == case_id for row in index['cases']):
        raise ValueError('Case is absent from the deployed search index')
    return receipt['index_sha256']


def decide(state, db, args):
    _, raw = read_receipt(state / 'inbox', args.receipt)
    current = next(row for row in queue(db) if row['receipt_id'] == args.receipt)
    linking = args.command == 'link-upstream'
    status = current['state'] if linking else args.status
    if linking and status not in ('accepted', 'published'):
        raise ValueError('An upstream PR can be registered after acceptance')
    if not linking and status not in TRANSITIONS[current['state']]:
        raise ValueError(f"Invalid transition: {current['state']} -> {args.status}")
    if not args.reviewer.strip() or not args.reason.strip():
        raise ValueError('A reviewer and reason are required')
    case_id = args.case or current['case_id']
    community_pr = pr_url(args.community_pr or current['community_pr'])
    upstream_pr = pr_url(args.upstream_pr or current['upstream_pr'])
    index_sha = current['index_sha256'] if linking else None
    if not linking and status == 'published':
        if not (case_id and community_pr and args.site and args.index):
            raise ValueError('Publication needs a case, community PR, published site snapshot and deployed index')
        index_sha = publication(case_id, args.site, args.index)
    elif args.site or args.index:
        raise ValueError('Publication evidence is only used for the published transition')
    db.execute('''INSERT INTO events (receipt_id,state,reviewer,reason,at,case_id,
                  community_pr,upstream_pr,index_sha256) VALUES (?,?,?,?,?,?,?,?,?)''',
               (args.receipt, status, args.reviewer.strip(), args.reason.strip(), now(),
                case_id, community_pr, upstream_pr, index_sha))
    return {'receipt_id': args.receipt, 'state': status,
            'attribution': draft_attribution(json.loads(raw)), 'index_sha256': index_sha}


def verify(state):
    private_dir(state)
    inbox = state / 'inbox'
    private_dir(inbox)
    ledger = state / 'review.sqlite3'
    if not ledger.is_file() or ledger.is_symlink() or ledger.stat().st_mode & 0o077:
        raise ValueError('Expected a private review ledger')
    with closing(sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Review ledger integrity check failed')
        ids = {row[0] for row in db.execute('SELECT id FROM receipts')}
        missing = ids - {path.stem for path in inbox.glob('submission-*.json')}
        if missing:
            raise ValueError(f'Review ledger references missing receipts: {sorted(missing)}')
        receipts = list(inbox.glob('submission-*.json'))
        for path in receipts:
            read_receipt(inbox, path.stem)
        return {'verified_receipts': len(receipts), 'indexed_receipts': len(ids),
                'review_events': db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}


def snapshot(state, db, output):
    output = output.resolve()
    if output == state or state in output.parents or output.exists():
        raise ValueError('Backup needs a new destination outside the live state directory')
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.review-backup-') as tmp:
        root = Path(tmp) / 'snapshot'
        (root / 'inbox').mkdir(parents=True, mode=0o700)
        with closing(sqlite3.connect(root / 'review.sqlite3')) as copy:
            db.backup(copy)
        for path in sorted((state / 'inbox').glob('submission-*.json')):
            _, raw = read_receipt(state / 'inbox', path.stem)
            (root / 'inbox' / path.name).write_bytes(raw)
        result = verify(root)
        root.rename(output)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/numericbridge'))
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--status', choices=TRANSITIONS)
    commands.add_parser('show').add_argument('receipt')
    update = commands.add_parser('decide')
    update.add_argument('receipt')
    update.add_argument('status', choices=TRANSITIONS)
    update.add_argument('--reviewer', required=True)
    update.add_argument('--reason', required=True)
    update.add_argument('--case')
    update.add_argument('--community-pr')
    update.add_argument('--upstream-pr')
    update.add_argument('--site', type=Path)
    update.add_argument('--index', help='published index URL or locally verified snapshot')
    link = commands.add_parser('link-upstream')
    link.add_argument('receipt')
    link.add_argument('--upstream-pr', required=True)
    link.add_argument('--reviewer', required=True)
    link.add_argument('--reason', required=True)
    link.set_defaults(case=None, community_pr=None, site=None, index=None)
    commands.add_parser('backup').add_argument('output', type=Path)
    commands.add_parser('verify')
    args = parser.parse_args()
    state = args.state.resolve()
    os.umask(0o077)
    try:
        if args.command == 'verify':
            result = verify(state)
        else:
            with closing(connect(state)) as db:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    sync(state, db)
                    if args.command == 'list':
                        result = [row for row in queue(db) if not args.status or args.status == row['state']]
                    elif args.command == 'show':
                        _, raw = read_receipt(state / 'inbox', args.receipt)
                        history = db.execute('SELECT * FROM events WHERE receipt_id=? ORDER BY seq', (args.receipt,))
                        result = {'receipt': json.loads(raw), 'history': [dict(row) for row in history]}
                    elif args.command in ('decide', 'link-upstream'):
                        result = decide(state, db, args)
                if args.command == 'backup':
                    result = snapshot(state, db, args.output)
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
