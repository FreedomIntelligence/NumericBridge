"""Serve a private, read-only view of the NumericBridge inbox on loopback."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import parse_qs, urlsplit


RECEIPT = re.compile(r'submission-[0-9a-f]{64}')
STATES = {'pending_review': '待审核', 'accepted': '已接受', 'declined': '未采纳',
          'needs_information': '需要补充', 'published': '已公开'}
REMOTE = ('cd /opt/numericbridge && sudo -n -u numericbridge '
          '/opt/numericbridge/.venv/bin/python -B - --snapshot')
STYLE = '''
*{box-sizing:border-box}body{margin:0;background:#f5f6fa;color:#172036;font:15px/1.55 system-ui,sans-serif}
main{max-width:1300px;margin:auto;padding:32px 24px}header{margin-bottom:24px}
h1,h2,h3,p{margin:0}small{color:#526077}.layout{display:grid;grid-template-columns:minmax(225px,1fr) minmax(0,2.8fr);gap:18px}
section,nav{background:white;border:1px solid #dce1eb;border-radius:14px;padding:18px;overflow-wrap:anywhere}
nav a{display:block;color:inherit;text-decoration:none;padding:12px;border-radius:8px;margin-top:6px}
nav a:hover,nav a[aria-current]{background:#eef2ff}.badge{display:inline-block;background:#edf0f8;color:#334a73;border-radius:12px;padding:1px 9px;margin:5px 0}
article{border-top:1px solid #e2e6ee;margin-top:15px;padding-top:12px}details{margin-top:8px}
summary{cursor:pointer}pre{padding:10px;background:#f5f6fa;white-space:pre-wrap;overflow-wrap:anywhere;border-radius:7px}
@media(max-width:700px){main{padding:16px}.layout{grid-template-columns:1fr}}
'''


def snapshot(state, selected):
    # Loaded only on the machine holding the private receipts.
    from community.review import read_receipt, private_dir

    inbox = state / 'inbox'
    private_dir(state)
    private_dir(inbox)
    statuses = {}
    ledger = state / 'review.sqlite3'
    if ledger.is_symlink():
        raise ValueError('Unexpected symlink for review ledger')
    if ledger.is_file():
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
            statuses = dict(db.execute('SELECT receipt_id, state FROM events ORDER BY seq'))
    rows, documents = [], {}
    for path in inbox.glob('submission-*.json'):
        _, raw = read_receipt(inbox, path.stem)
        draft = json.loads(raw)
        received = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')
        rows.append({'id': path.stem, 'title': draft['title'], 'received': received,
                     'state': statuses.get(path.stem, 'pending_review')})
        documents[path.stem] = {key: draft[key] for key in ('attribution', 'additions') if key in draft}
    rows.sort(key=lambda row: (row['received'], row['id']), reverse=True)
    selected = selected or (rows[0]['id'] if rows else None)
    return {'rows': rows, 'selected': selected, 'draft': documents.get(selected)}


def fetch(host, selected):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Expected an SSH host alias')
    call = subprocess.run(['ssh', '-T', '-o', 'LogLevel=ERROR', '-o', 'BatchMode=yes',
                           '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=6', host,
                           REMOTE + (f' {selected}' if selected else '')],
                          input=Path(__file__).read_bytes(), capture_output=True, timeout=12, check=True)
    return json.loads(call.stdout)


def render(data):
    rows, selected, draft = (data[key] for key in ('rows', 'selected', 'draft'))
    links = []
    detail = '<p>暂无投稿。</p>' if not rows else '<p>未找到这份投稿。</p>'
    for row in rows:
        receipt = row['id']
        title = escape(row['title'])
        time = escape(row['received'].replace('T', ' ').replace('+00:00', ' UTC'))
        status = escape(STATES.get(row['state'], row['state']))
        current = ' aria-current="page"' if receipt == selected else ''
        links.append(f'<a href="/?receipt={escape(receipt)}"{current}><strong>{title}</strong><br>'
                     f'<small>{time}</small><br><span class="badge">{status}</span></a>')
        if receipt != selected or draft is None:
            continue
        name = escape((draft.get('attribution') or {}).get('name') or '匿名')
        detail = (f'<h2>{title}</h2><span class="badge">{status}</span> '
                  f'<small>收件于 {time} · {name}</small>')
        groups = draft.get('additions', {})
        for key, label in [('evaluations', '评测'), ('repairs', '问题与修复'), ('relations', '关联记录'), ('evidence', '证据')]:
            group = groups.get(key) or {}
            records = ([(k, v) for k, v in group.items()] if key == 'evidence' else
                       [(item.get('title') or item.get('name') or item.get('id') or kind, item)
                        for kind, items in group.items() for item in items])
            if records:
                detail += f'<article><h3>{label} · {len(records)}</h3>'
                for heading, record in records:
                    text = escape(json.dumps(record, ensure_ascii=False, indent=2))
                    detail += f'<details><summary>{escape(str(heading))}</summary><pre>{text}</pre></details>'
                detail += '</article>'
        detail += f'<article><small>回执 ID：{escape(receipt)}</small></article>'
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>NumericBridge 投稿收件箱</title>'
            f'<style>{STYLE}</style><main><header><h1>NumericBridge 投稿收件箱</h1>'
            f'<small>仅限本机查看 · {len(rows)} 份投稿</small></header><div class="layout">'
            f'<nav aria-label="投稿列表"><h2>投稿列表</h2>{"".join(links)}</nav>'
            f'<section>{detail}</section></div></main></html>')


def server(host, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.send_error(403)
                return
            url = urlsplit(self.path)
            if url.path != '/':
                self.send_error(404)
                return
            values = parse_qs(url.query).get('receipt', [])
            selected = values[0] if values else None
            if selected and not RECEIPT.fullmatch(selected):
                self.send_error(400)
                return
            try:
                data = fetch(host, selected)
                body = render(data).encode()
            except (OSError, ValueError, subprocess.SubprocessError, TypeError, KeyError):
                self.send_error(503, 'Inbox unavailable; check the SSH connection')
                return
            self.send_response(200 if not selected or data['draft'] is not None else 404)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--ssh', help='SSH alias for the community host')
    mode.add_argument('--snapshot', nargs='?', const='', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=18890)
    args = parser.parse_args()
    if args.snapshot is not None:
        if args.snapshot and not RECEIPT.fullmatch(args.snapshot):
            parser.error('Invalid receipt ID')
        print(json.dumps(snapshot(Path('/var/lib/numericbridge'), args.snapshot)))
    else:
        with server(args.ssh, args.port) as httpd:
            httpd.serve_forever()


if __name__ == '__main__':
    main()
