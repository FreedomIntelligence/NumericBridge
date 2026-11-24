"""Run the community's private submission inbox behind an HTTPS proxy."""

import argparse
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
    if (not secure or not parts.hostname or parts.path != PATH or parts.query or parts.fragment
            or parts.username is not None or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError('Use an HTTPS community /v1/submissions URL (HTTP only on loopback)')
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IntakeClient:
    def __init__(self, url, token_file):
        self.url = endpoint(url)
        self.token_file = token_file
        read_token(token_file)

    def send(self, draft, digest):
        raw = encode(draft, compact=True)
        if draft['destination'] != self.url or sha256(raw).hexdigest() != digest:
            raise ValueError('Submission differs from the confirmed draft or inbox')
        request = Request(self.url, data=raw, method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + read_token(self.token_file),
            'X-Draft-SHA256': digest,
        })
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
        return {k: result[k] for k in ('status', 'receipt_id', 'review_state', 'reused')}

    def status(self, receipt_id):
        if not RECEIPT.fullmatch(receipt_id or ''):
            raise ValueError('Use a submission-<sha256> receipt ID')
        request = Request(f'{self.url}/{receipt_id}', method='GET',
                          headers={'Authorization': 'Bearer ' + read_token(self.token_file)})
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

    def receive(self, raw, digest):
        if not DIGEST.fullmatch(digest) or len(raw) > MAX_BODY or sha256(raw).hexdigest() != digest:
            raise ValueError('Invalid draft size or digest')
        receipt_id = 'submission-' + digest
        target = self.directory / (receipt_id + '.json')
        if target.exists():
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


def create_app(inbox, token):
    from starlette.applications import Starlette
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import ClientDisconnect
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    expected = ('Bearer ' + token).encode('ascii')

    async def receive(request):
        authorization = request.headers.get('authorization', '').encode('latin-1')
        if not hmac.compare_digest(authorization, expected):
            return JSONResponse({'status': 'unauthorized'}, status_code=401)
        content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            return JSONResponse({'status': 'invalid'}, status_code=415)
        data = bytearray()
        try:
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_BODY:
                    return JSONResponse({'status': 'too_large'}, status_code=413)
                data.extend(chunk)
            result = await run_in_threadpool(inbox.receive, bytes(data), request.headers.get('x-draft-sha256', ''))
        except (ClientDisconnect, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
            return JSONResponse({'status': 'invalid'}, status_code=422)
        except OSError:
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return JSONResponse(result, status_code=200 if result['reused'] else 201)

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

    return Starlette(routes=[Route('/healthz', health, methods=['GET']),
                             Route(PATH, receive, methods=['POST']),
                             Route(PATH + '/{receipt}', status, methods=['GET'])])


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inbox', required=True, type=Path, help='private persistent receipt directory')
    parser.add_argument('--token-file', required=True, type=Path, help='community upload credential file')
    parser.add_argument('--port', type=int, default=8770)
    add_catalog_site_argument(parser)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        app = create_app(Inbox(ROOT, args.inbox, catalog_site=args.catalog_site), read_token(args.token_file))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, proxy_headers=False,
                limit_concurrency=16)


if __name__ == '__main__':
    main()
