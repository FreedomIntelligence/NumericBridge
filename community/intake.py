"""Run the community's private submission inbox behind an HTTPS proxy."""

import argparse
from contextlib import closing
import fcntl
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .records.submission import (COLLECTIONS, DIGEST, Submissions, add_catalog_site_argument,
                                 draft_attribution, draft_supersedes, encode)


ROOT = Path(__file__).resolve().parent.parent
PATH = '/v1/submissions'
PUBLIC_PATH = '/v1/public-submissions'
PUBLIC_PENDING_LIMIT = 40
MAX_BODY = 1280 * 1024
MAX_RECEIPT = 4096
RECEIPT = re.compile(r'submission-[a-f0-9]{64}')
REVIEW_STATES = ('pending_review', 'needs_information', 'accepted', 'declined', 'published')
STATUS_FIELDS = ('receipt_id', 'review_state', 'updated_at', 'community_pr', 'upstream_pr', 'superseded_by')


def read_token(path):
    with Path(path).open('r', encoding='utf-8') as stream:
        token = stream.read(257).strip()
    if not re.fullmatch(r'[A-Za-z0-9._~-]{32,256}', token):
        raise ValueError('Use a community upload token of 32 to 256 URL-safe characters')
    return token


def endpoint(url):
    parts = urlsplit(url)
    local = parts.hostname in ('127.0.0.1', '::1')
    secure = parts.scheme == 'https' or (local and parts.scheme == 'http')
    if (not secure or not parts.hostname or parts.path not in (PATH, PUBLIC_PATH) or parts.query or parts.fragment
            or parts.username is not None or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError('Use an HTTPS community submission URL (HTTP only on loopback)')
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IntakeClient:
    def __init__(self, url, token_file=None):
        self.url = endpoint(url)
        self.public = urlsplit(self.url).path == PUBLIC_PATH
        self.token_file = token_file
        if self.public and token_file is not None:
            raise ValueError('Public submission must not send the private upload token')
        if not self.public:
            if token_file is None:
                raise ValueError('Authenticated submission requires --token-file')
            read_token(token_file)

    def send(self, draft, digest):
        raw = encode(draft, compact=True)
        if draft['destination'] != self.url or sha256(raw).hexdigest() != digest:
            raise ValueError('Submission differs from the confirmed draft or inbox')
        headers = {'Content-Type': 'application/json', 'X-Draft-SHA256': digest}
        if not self.public:
            headers['Authorization'] = 'Bearer ' + read_token(self.token_file)
        request = Request(self.url, data=raw, method='POST', headers=headers)
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
                result = json.loads(response.read(MAX_RECEIPT + 1))
        except HTTPError as error:
            error.close()
            raise ValueError(f'Community inbox did not accept the draft (HTTP {error.code})') from error
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            raise ValueError('No valid community receipt; keep the draft and retry the same submission') from error
        if (not isinstance(result, dict) or result.get('status') != 'received'
                or result.get('receipt_id') != 'submission-' + digest
                or result.get('review_state') != 'pending_review' or type(result.get('reused')) is not bool):
            raise ValueError('Community inbox returned an invalid receipt')
        receipt = {k: result[k] for k in ('status', 'receipt_id', 'review_state', 'reused')}
        if self.public:
            if not isinstance(result.get('status_token'), str) or not DIGEST.fullmatch(result['status_token']):
                raise ValueError('Community inbox returned an invalid status token')
            receipt['status_token'] = result['status_token']
        return receipt

    def status(self, receipt_id, *, status_token=None):
        if not RECEIPT.fullmatch(receipt_id or ''):
            raise ValueError('Use a submission-<sha256> receipt ID')
        if self.public and (not isinstance(status_token, str) or not DIGEST.fullmatch(status_token)):
            raise ValueError('Use the private status token for this public receipt')
        credential = status_token if self.public else read_token(self.token_file)
        request = Request(f'{self.url}/{receipt_id}', method='GET',
                          headers={'Authorization': 'Bearer ' + credential})
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
                result = json.loads(response.read(MAX_RECEIPT + 1))
        except HTTPError as error:
            error.close()
            if error.code == 404:
                return dict(status='unknown_receipt', receipt_id=receipt_id)
            raise ValueError(f'Community inbox did not return a status (HTTP {error.code})') from error
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            raise ValueError('No valid status from the community inbox; retry later') from error
        if (not isinstance(result, dict) or result.get('receipt_id') != receipt_id
                or result.get('review_state') not in REVIEW_STATES):
            raise ValueError('Community inbox returned an invalid status')
        return dict(status='found', **{k: result.get(k) for k in STATUS_FIELDS})


class Inbox:
    def __init__(self, repo, directory, *, catalog_site=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.stat().st_mode & 0o077:
            raise ValueError('The community inbox directory must be private (mode 0700)')
        if not os.access(self.directory, os.W_OK | os.X_OK):
            raise ValueError('The service account must be able to write to the community inbox')
        self.records = Submissions(repo, self.directory, catalog_site=catalog_site)
        self.records.baseline()

    def receive(self, raw, digest, *, public_url=None):
        if not DIGEST.fullmatch(digest) or len(raw) > MAX_BODY or sha256(raw).hexdigest() != digest:
            raise ValueError('Invalid draft size or digest')
        receipt_id = 'submission-' + digest
        target = self.directory / (receipt_id + '.json')
        if target.exists() and public_url is None:
            if target.read_bytes() != raw:
                raise ValueError('Stored submission differs from the reviewed draft')
            return self.receipt(receipt_id, True)

        draft = json.loads(raw)
        keys = {'schema_version', 'title', 'destination', 'base_commit', 'additions', 'files'}
        extra = {2: set(), 3: {'attribution'}, 4: {'attribution', 'supersedes'}}
        if (not isinstance(draft, dict) or type(draft.get('schema_version')) is not int
                or draft['schema_version'] not in extra or set(draft) != keys | extra[draft['schema_version']]
                or raw != encode(draft, compact=True)):
            raise ValueError('Invalid submission format')
        credit = draft_attribution(draft)
        replaced = draft_supersedes(draft)
        endpoint(draft['destination'])
        if public_url is not None:
            if draft['destination'] != public_url or replaced or credit['mode'] != 'anonymous':
                raise ValueError('Public intake accepts only new anonymous submissions to this endpoint')
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError('Stored submission differs from the reviewed draft')
                return self.receipt(receipt_id, True)
            self.check_public_capacity(public_url)
        elif urlsplit(draft['destination']).path == PUBLIC_PATH:
            raise ValueError('Use the configured public intake endpoint for a public draft')
        additions = draft['additions']
        if (not isinstance(additions, dict) or set(additions) != {*COLLECTIONS, 'evidence'}
                or not all(isinstance(additions[k], dict) and additions[k].keys() <= set(keys)
                           and all(isinstance(v, list) for v in additions[k].values())
                           for k, keys in COLLECTIONS.items())
                or not isinstance(additions['evidence'], dict)
                or not all(isinstance(v, dict) for v in additions['evidence'].values())):
            raise ValueError('Invalid community additions')
        # Validate against the community baseline, without changing the approved bytes.
        # The submitter's code baseline and generated path digests are preview metadata.
        self.records.preview(draft['title'], additions, attribution_mode=credit['mode'],
                             attribution_name=credit.get('name'), replaces=replaced)
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix='.incoming-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != raw:
                    raise ValueError('Stored submission differs from the reviewed draft')
                return self.receipt(receipt_id, True)
        finally:
            os.unlink(temporary)
        return self.receipt(receipt_id, False)

    def check_public_capacity(self, public_url):
        ledger = self.directory.parent / 'review.sqlite3'
        closed = set()
        if ledger.is_file():
            with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone():
                    closed = {row[0] for row in db.execute(
                        'SELECT e.receipt_id FROM events e JOIN (SELECT MAX(seq) AS seq FROM events GROUP BY receipt_id) latest '
                        "ON e.seq=latest.seq WHERE e.state IN ('declined','published')")}
        pending = 0
        for path in self.directory.glob('submission-*.json'):
            if path.stem not in closed and json.loads(path.read_bytes()).get('destination') == public_url:
                pending += 1
                if pending >= PUBLIC_PENDING_LIMIT:
                    raise PublicBusy('Public inbox capacity reached')

    def receive_public(self, raw, digest, public_url):
        # One public validation at a time across workers: a preview copies and reindexes the full catalog.
        with (self.directory / '.public-intake.lock').open('ab') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PublicBusy('Public intake is busy') from error
            return self.receive(raw, digest, public_url=public_url)

    def status(self, receipt_id):
        from .review import receipt_status
        if not RECEIPT.fullmatch(receipt_id):
            raise KeyError(receipt_id)
        if not (self.directory / (receipt_id + '.json')).is_file():
            raise KeyError(receipt_id)
        result = receipt_status(self.directory.parent, receipt_id)
        return {k: result[k] for k in STATUS_FIELDS}

    @staticmethod
    def receipt(receipt_id, reused):
        return dict(status='received', receipt_id=receipt_id, review_state='pending_review', reused=reused)


class PublicBusy(Exception):
    pass


def create_app(inbox, token, *, public_url=None, public_status_key=None):
    from starlette.applications import Starlette
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import ClientDisconnect
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    expected = ('Bearer ' + token).encode('ascii')

    if public_url and not public_status_key:
        raise ValueError('Public intake requires a separate persistent status signing key')

    def status_token(receipt):
        return hmac.new(public_status_key.encode('ascii'), ('numericbridge-public-receipt-v1:' + receipt).encode('ascii'), sha256).hexdigest()

    async def receive(request, *, public=False):
        authorization = request.headers.get('authorization', '').encode('latin-1')
        if not public and not hmac.compare_digest(authorization, expected):
            return JSONResponse({'status': 'unauthorized'}, status_code=401)
        if public and authorization:
            return JSONResponse({'status': 'invalid'}, status_code=400)
        digest = request.headers.get('x-draft-sha256', '')
        if not DIGEST.fullmatch(digest):
            return JSONResponse({'status': 'invalid'}, status_code=422)
        content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            return JSONResponse({'status': 'invalid'}, status_code=415)
        data = bytearray()
        try:
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_BODY:
                    return JSONResponse({'status': 'too_large'}, status_code=413)
                data.extend(chunk)
            if public:
                result = await run_in_threadpool(inbox.receive_public, bytes(data), digest, public_url)
                result['status_token'] = status_token(result['receipt_id'])
            else:
                result = await run_in_threadpool(inbox.receive, bytes(data), digest)
        except PublicBusy:
            return JSONResponse({'status': 'busy'}, status_code=429, headers={'Retry-After': '60'})
        except (ClientDisconnect, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
            return JSONResponse({'status': 'invalid'}, status_code=422)
        except (OSError, sqlite3.Error):
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return JSONResponse(result, status_code=200 if result['reused'] else 201,
                            headers={'Cache-Control': 'no-store'})

    async def status(request):
        authorization = request.headers.get('authorization', '').encode('latin-1')
        if not hmac.compare_digest(authorization, expected):
            return JSONResponse({'status': 'unauthorized'}, status_code=401)
        try:
            result = await run_in_threadpool(inbox.status, request.path_params['receipt'])
        except KeyError:
            return JSONResponse({'status': 'not_found'}, status_code=404)
        except (ValueError, OSError, sqlite3.Error):
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    async def health(_request):
        return JSONResponse({'status': 'ok'}, headers={'Cache-Control': 'no-store'})

    async def public_receive(request):
        return await receive(request, public=True)

    async def public_status(request):
        headers = {'Cache-Control': 'no-store'}
        receipt = request.path_params['receipt']
        credential = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not DIGEST.fullmatch(credential) or not request.headers.get('authorization', '').startswith('Bearer '):
            return JSONResponse({'status': 'unauthorized'}, status_code=401, headers=headers)
        if not RECEIPT.fullmatch(receipt) or not hmac.compare_digest(credential, status_token(receipt)):
            return JSONResponse({'status': 'not_found'}, status_code=404, headers=headers)
        try:
            stored = await run_in_threadpool((inbox.directory / (receipt + '.json')).read_bytes)
            if json.loads(stored).get('destination') != public_url:
                raise KeyError(receipt)
            result = await run_in_threadpool(inbox.status, receipt)
        except (FileNotFoundError, KeyError):
            return JSONResponse({'status': 'not_found'}, status_code=404, headers=headers)
        except (ValueError, OSError, sqlite3.Error):
            return JSONResponse({'status': 'unavailable'}, status_code=503, headers=headers)
        return JSONResponse({k: result[k] for k in ('receipt_id', 'review_state', 'updated_at')}, headers=headers)

    routes = [Route('/healthz', health, methods=['GET']), Route(PATH, receive, methods=['POST']),
              Route(PATH + '/{receipt}', status, methods=['GET'])]
    if public_url:
        if urlsplit(endpoint(public_url)).path != PUBLIC_PATH:
            raise ValueError('Public intake URL must end in /v1/public-submissions')
        routes += [Route(PUBLIC_PATH, public_receive, methods=['POST']),
                   Route(PUBLIC_PATH + '/{receipt}', public_status, methods=['GET'])]
    return Starlette(routes=routes)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inbox', required=True, type=Path, help='private persistent receipt directory')
    parser.add_argument('--token-file', required=True, type=Path, help='community upload credential file')
    parser.add_argument('--public-url', default=os.environ.get('NUMERICBRIDGE_PUBLIC_INTAKE_URL'),
                        help='enable public intake for this exact HTTPS destination')
    parser.add_argument('--public-status-key-file', type=Path, default=os.environ.get('NUMERICBRIDGE_PUBLIC_STATUS_KEY_FILE'),
                        help='private signing key file for persistent public receipt tokens')
    parser.add_argument('--port', type=int, default=8770)
    add_catalog_site_argument(parser)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        app = create_app(Inbox(ROOT, args.inbox, catalog_site=args.catalog_site), read_token(args.token_file),
                         public_url=args.public_url, public_status_key=(
                             read_token(args.public_status_key_file) if args.public_status_key_file else None))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, proxy_headers=False,
                limit_concurrency=16)


if __name__ == '__main__':
    main()
