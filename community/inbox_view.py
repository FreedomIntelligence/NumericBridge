"""Serve a private, read-only view of the NumericBridge inbox on loopback."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from socketserver import ThreadingMixIn, UnixStreamServer
import sqlite3
import stat
import subprocess
from sys import maxsize
from urllib.parse import parse_qs, urlsplit


RECEIPT = re.compile(r'submission-[0-9a-f]{64}')
STATES = {'pending_review': '待审核', 'needs_information': '需要补充',
          'accepted': '已接受', 'published': '已公开', 'declined': '未采纳'}
QUEUE_FILTERS = {'active_pending': '待审 · 无更新稿', 'superseded': '有更新稿'}
ORDERS = {'newest', 'oldest'}
PAGE_SIZE = 12
REMOTE = ('cd /opt/numericbridge && sudo -n -u numericbridge '
          '/opt/numericbridge/.venv/bin/python -B - --snapshot')


def _read_draft(inbox_fd, receipt):
    if not RECEIPT.fullmatch(receipt):
        raise ValueError('Invalid receipt ID')
    # Check permissions, contents and timestamp on the open file, not a second
    # path lookup. NOFOLLOW rejects symlinks; NONBLOCK avoids waiting on a FIFO.
    fd = os.open(receipt + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=inbox_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Expected a private receipt')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read()
    finally:
        os.close(fd)
    if sha256(raw).hexdigest() != receipt.removeprefix('submission-'):
        raise ValueError('Receipt checksum mismatch')
    draft = json.loads(raw)
    if not isinstance(draft, dict) or not isinstance(draft.get('title'), str):
        raise ValueError('Invalid receipt draft')
    return info.st_mtime_ns, draft


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _evidence_counts(draft):
    # Counts describe what's declared, not its validity or reproducibility.
    additions = _mapping(draft.get('additions'))
    evaluations = _mapping(additions.get('evaluations'))
    repairs = _mapping(additions.get('repairs'))
    relations = _mapping(additions.get('relations'))
    return {'evidence_files': len(_mapping(additions.get('evidence'))),
            'evaluations': len(_list(evaluations.get('evaluations'))),
            'repairs': len(_list(repairs.get('repairs'))),
            'library_groups': len(_list(repairs.get('library_groups'))),
            'entities': len(_list(relations.get('entities'))),
            'links': len(_list(relations.get('links')))}


def _receipt_row(inbox_fd, receipt):
    timestamp_ns, draft = _read_draft(inbox_fd, receipt)
    timestamp = timestamp_ns // 1_000_000_000
    earlier = dict.fromkeys(value for value in _list(draft.get('supersedes'))
                            if isinstance(value, str) and value != receipt and RECEIPT.fullmatch(value))
    return {'id': receipt, 'title': draft['title'],
            'received': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec='seconds'),
            'received_ns': timestamp_ns, 'supersedes': list(earlier), 'evidence': _evidence_counts(draft)}


def _queue_rows(inbox_fd):
    rows, replaced_by = [], {}
    for filename in os.listdir(inbox_fd):
        if not filename.startswith('submission-') or not filename.endswith('.json'):
            continue
        receipt = filename.removesuffix('.json')
        row = _receipt_row(inbox_fd, receipt)
        rows.append(row)
        for old in row['supersedes']:
            replaced_by.setdefault(old, []).append(receipt)
    for row in rows:
        row['superseded_by'] = sorted(replaced_by.get(row['id'], ()))
    return rows


def _queue_order(row):
    return row.get('received', ''), row.get('received_ns', 0), row.get('id', '')


def visible_rows(rows, query='', status='', order='newest'):
    """Apply the same queue view to rendering and default detail selection."""
    term = query.strip().casefold()
    if status == 'active_pending':
        subset = (row for row in rows if row.get('active_pending'))
    elif status == 'superseded':
        subset = (row for row in rows if row.get('superseded_by'))
    else:
        subset = (row for row in rows if not status or row.get('state') == status)
    return sorted((row for row in subset if not term or any(
        term in str(row.get(field) or '').casefold() for field in ('id', 'title')
    )), key=_queue_order, reverse=order != 'oldest')


def _parse_page(value):
    if isinstance(value, str):
        if not re.fullmatch(r'[+-]?[0-9]+', value):
            raise ValueError('Expected an integer page')
        if value.startswith('-'):
            return 1
        digits = value.removeprefix('+').lstrip('0')
        # An in-memory queue cannot have more than maxsize items. Bound the
        # conversion to handle arbitrarily large numeric requests safely.
        value = maxsize if len(digits) > len(str(maxsize)) else int(digits or '0')
    if type(value) is not int:
        raise ValueError('Expected an integer page')
    return min(max(1, value), maxsize)


def _review_selection(rows, db, selected, query, status, order, page=1):
    states = {} if db is None else dict(db.execute(
        'SELECT e.receipt_id, e.state FROM events e JOIN '
        '(SELECT MAX(seq) AS seq FROM events GROUP BY receipt_id) latest ON e.seq=latest.seq'))
    received_at = {} if db is None else dict(db.execute('SELECT id, received_at FROM receipts'))
    for row in rows:
        row['state'] = states.get(row['id'], 'pending_review')
        row['active_pending'] = row['state'] == 'pending_review' and not row['superseded_by']
        indexed_at = received_at.get(row['id'])
        if indexed_at:
            dt = datetime.fromisoformat(indexed_at.replace('Z', '+00:00'))
            canonical = dt.astimezone(timezone.utc).isoformat(timespec='seconds')
            if canonical != row['received']:
                # Backups may have copy times rather than original file mtimes.
                row['received'] = canonical
                row['received_ns'] = 0
    rows.sort(key=_queue_order, reverse=True)
    visible = visible_rows(rows, query, status, order)
    pages = max(1, (len(visible) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    if selected:
        # A receipt in this view determines its page. Keep explicit detail
        # even when the receipt is filtered out or absent from the inbox.
        for index, row in enumerate(visible):
            if row['id'] == selected:
                page = index // PAGE_SIZE + 1
                break
    else:
        selected = visible[(page - 1) * PAGE_SIZE]['id'] if visible else None
    found = any(row['id'] == selected for row in rows)
    history = [] if not found or db is None else [dict(row) for row in db.execute(
        'SELECT receipt_id, state, reviewer, reason, at AS created_at '
        'FROM events WHERE receipt_id=? ORDER BY seq', (selected,))]
    return selected, found, history, page, pages


def _review_snapshot(ledger, rows, selected, query, status, order, page=1):
    if ledger.is_symlink():
        raise ValueError('Unexpected symlink for review ledger')
    if ledger.is_file():
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            # Keep queue states and selected history on the same ledger snapshot.
            db.execute('BEGIN')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='events' AND type='table'").fetchone():
                return _review_selection(rows, db, selected, query, status, order, page)
    return _review_selection(rows, None, selected, query, status, order, page)


def snapshot(state, selected, query='', status='', order='newest', page=1):
    """Collect queue metadata and the detail of at most one verified receipt.

    Supersession records direct declarations without altering queue state or
    selection. Active pending excludes any pending receipt with a replacement.
    Evidence counts describe declarations; only the chosen draft is normalized.
    The full queue is kept for counts; page and pages describe the filtered view.
    """
    from community.normalize import normalize_draft
    from community.review import private_dir

    page = _parse_page(page)
    state = state.resolve()
    private_dir(state)
    inbox_fd = os.open(state / 'inbox', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(inbox_fd).st_mode & 0o077:
            raise ValueError('Expected a private inbox directory')
        rows = _queue_rows(inbox_fd)
        selected, found, history, page, pages = _review_snapshot(
            state / 'review.sqlite3', rows, selected, query, status, order, page)
        # Recheck the chosen file after selection; fail closed if it no longer
        # opens as a private receipt or its bytes no longer match the receipt ID.
        draft = _read_draft(inbox_fd, selected)[1] if found else None
    finally:
        os.close(inbox_fd)
    normalized, normalization_unavailable = None, False
    if draft:
        try:
            normalized = normalize_draft(draft, receipt_id=selected)
        except (AttributeError, KeyError, TypeError, ValueError):
            # Legacy free-form evidence can be readable even when it cannot be
            # normalized. Preserve review access to the raw receipt and queue.
            normalization_unavailable = True
    return {'rows': rows, 'selected': selected, 'read_only': True,
            'draft': {key: draft[key] for key in ('attribution', 'additions') if key in draft} if draft else None,
            'normalized': normalized, 'normalization_unavailable': normalization_unavailable,
            'history': history, 'page': page, 'pages': pages}


def fetch(host, selected, query='', status='', order='newest', page=1):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Expected an SSH host alias')
    args = (['--receipt=' + selected] if selected else []) + [
        '--query=' + query, '--status=' + status, '--order=' + order, f'--page={_parse_page(page)}']
    # Keep operator search terms and receipt IDs out of local/remote process
    # arguments and sudo command logs. Only data literals go into the wrapper.
    request = json.dumps(args)
    source = Path(__file__).read_bytes()
    wrapper = (f'import json, sys\nsys.argv.extend(json.loads({request!r}))\n'
               f'exec(compile({source!r}, "<numericbridge-review>", "exec"))\n').encode()
    call = subprocess.run(['ssh', '-T', '-o', 'LogLevel=ERROR', '-o', 'BatchMode=yes',
                           '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=6', host, REMOTE],
                          input=wrapper, capture_output=True, timeout=12, check=True)
    return json.loads(call.stdout)


def render(data, **filters):
    from .inbox_dashboard import render as dashboard
    return dashboard(data, **filters)


def server(host, port, *, state=None, unix_socket=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host') != self.server.expected_host:
                self.send_error(403)
                return
            url = urlsplit(self.path)
            if url.path != '/':
                self.send_error(404)
                return
            try:
                values = parse_qs(url.query, keep_blank_values=True, max_num_fields=8)
                (raw_page,) = values.pop('page', ['1'])
                page = _parse_page(raw_page)
            except ValueError:
                self.send_error(400)
                return
            selected = next(filter(None, values.get('receipt', ())), None)
            query = next(filter(None, values.get('q', ())), '').strip()
            status = next(filter(None, values.get('status', ())), '')
            order = next(filter(None, values.get('order', ())), 'newest')
            if ((selected and not RECEIPT.fullmatch(selected)) or len(query) > 120 or
                    (status and status not in STATES and status not in QUEUE_FILTERS) or order not in ORDERS):
                self.send_error(400)
                return
            try:
                data = (snapshot(state, selected, query, status, order, page=page) if state else
                        fetch(host, selected, query, status, order, page=page))
                body = render(data, query=query, state=status, order=order, page=data['page']).encode()
                code = 200 if not selected or data['draft'] is not None else 404
            except (OSError, ValueError, subprocess.SubprocessError, TypeError, KeyError, sqlite3.Error):
                from .inbox_dashboard import render_unavailable
                body = render_unavailable(remote=state is None).encode()
                code = 503
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    if unix_socket is None:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        httpd.expected_host = f'127.0.0.1:{httpd.server_port}'
        return httpd

    class PrivateUnixServer(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True
        expected_host = 'localhost'

    httpd = PrivateUnixServer(str(unix_socket), Handler)
    try:
        os.chmod(unix_socket, 0o660)
    except OSError:
        httpd.server_close()
        raise
    return httpd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--ssh', help='SSH alias for the community host')
    mode.add_argument('--state', type=Path, help='Private state directory on this machine')
    mode.add_argument('--snapshot', nargs='?', const='', help=argparse.SUPPRESS)
    parser.add_argument('--receipt', help=argparse.SUPPRESS)
    parser.add_argument('--query', default='', help=argparse.SUPPRESS)
    parser.add_argument('--status', default='', help=argparse.SUPPRESS)
    parser.add_argument('--order', choices=sorted(ORDERS), default='newest', help=argparse.SUPPRESS)
    parser.add_argument('--page', type=_parse_page, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=18890)
    parser.add_argument('--unix-socket', type=Path, help='Private socket for an authenticated local proxy')
    args = parser.parse_args()
    if args.unix_socket and not args.state:
        parser.error('--unix-socket requires --state')
    if args.snapshot is not None:
        selected = args.receipt or args.snapshot
        if selected and not RECEIPT.fullmatch(selected):
            parser.error('Invalid receipt ID')
        print(json.dumps(snapshot(Path('/var/lib/numericbridge'), selected, args.query, args.status,
                                  args.order, page=args.page)))
    else:
        with server(args.ssh, args.port, state=args.state, unix_socket=args.unix_socket) as httpd:
            httpd.serve_forever()


if __name__ == '__main__':
    main()
