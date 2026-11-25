"""Public intake contract tests; never write to a live queue."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from starlette.testclient import TestClient

from community.intake import Inbox, MAX_BODY, PATH, PUBLIC_PATH, create_app
from community.records.submission import encode


TOKEN = 'test-only-upload-token-12345678901234567890'
URL = 'https://numericbridge.com' + PUBLIC_PATH


class PublicIntakeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.inbox = object.__new__(Inbox)
        self.inbox.directory = Path(temporary.name) / 'inbox'
        self.inbox.directory.mkdir(mode=0o700)
        self.inbox.records = Mock()
        self.client = TestClient(create_app(self.inbox, TOKEN, public_url=URL, public_status_key='isolated-test-status-signing-key'))
        self.addCleanup(self.client.close)

    def draft(self, **overrides):
        return dict(schema_version=3, title='Synthetic local test', destination=URL,
                    attribution={'mode': 'anonymous'}, base_commit='0' * 40,
                    additions={'evaluations': {'evaluations': [{'id': 'test'}]}, 'repairs': {},
                               'relations': {}, 'evidence': {}}, files={}, **overrides)

    def submit(self, draft, path=PUBLIC_PATH):
        body = encode(draft, compact=True)
        return self.client.post(path, content=body, headers={'Content-Type': 'application/json',
                                                           'X-Draft-SHA256': sha256(body).hexdigest()})

    def test_public_post_dedup_and_scoped_status(self):
        first = self.submit(self.draft())
        self.assertEqual(first.status_code, 201, first.text)
        first = first.json()
        again = self.submit(self.draft())
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()['reused'])
        self.assertEqual(again.json()['status_token'], first['status_token'])
        second = self.submit(self.draft() | {'title': 'Another local test'}).json()
        path = PUBLIC_PATH + '/' + first['receipt_id']
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers={'Authorization': 'Bearer ' + second['status_token']}).status_code, 404)
        response = self.client.get(path, headers={'Authorization': 'Bearer ' + first['status_token']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(set(response.json()), {'receipt_id', 'review_state', 'updated_at'})
        self.assertEqual(self.client.get(PATH + '/' + first['receipt_id']).status_code, 401)
        self.assertEqual(self.client.get(PATH + '/' + first['receipt_id'], headers={'Authorization': 'Bearer ' + first['status_token']}).status_code, 401)
        self.assertEqual(len(list(self.inbox.directory.glob('submission-*.json'))), 2)
        self.assertEqual(self.inbox.records.preview.call_count, 2)

    def test_replacement_named_attribution_and_wrong_destination_refused(self):
        for updates in ({'schema_version': 4, 'supersedes': ['submission-' + '1' * 64]},
                        {'attribution': {'mode': 'nickname', 'name': 'Someone else'}},
                        {'destination': 'https://example.invalid' + PUBLIC_PATH}):
            self.assertEqual(self.submit(self.draft() | updates).status_code, 422)
        self.inbox.records.preview.assert_not_called()

    def test_capacity_size_and_legacy_guard(self):
        with patch('community.intake.PUBLIC_PENDING_LIMIT', 1):
            self.assertEqual(self.submit(self.draft()).status_code, 201)
            self.assertEqual(self.submit(self.draft()).status_code, 200)
            busy = self.submit(self.draft() | {'title': 'New local test'})
            self.assertEqual(busy.status_code, 429)
            self.assertEqual(busy.headers['retry-after'], '60')
        big = self.client.post(PUBLIC_PATH, content=b'a' * (MAX_BODY + 1), headers={
            'Content-Type': 'application/json', 'X-Draft-SHA256': '0' * 64})
        self.assertEqual(big.status_code, 413)
        self.assertEqual(self.submit(self.draft(), PATH).status_code, 401)
        self.assertEqual(self.client.get(PUBLIC_PATH).status_code, 405)
        self.assertEqual(self.client.post(PUBLIC_PATH, headers={'Authorization': 'Bearer ' + TOKEN}).status_code, 400)
        with TestClient(create_app(self.inbox, TOKEN)) as disabled:
            self.assertEqual(disabled.post(PUBLIC_PATH).status_code, 404)
