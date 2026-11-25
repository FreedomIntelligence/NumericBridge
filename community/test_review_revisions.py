from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from community import review


class ReviewRevisionTests(unittest.TestCase):
    def test_confirm_versions_without_modifying_receipts_or_decisions(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            inbox = state / 'inbox'
            inbox.mkdir(mode=0o700)
            def submit(title, timestamp, supersedes=None):
                body = {'title': title}
                if supersedes:
                    body['supersedes'] = [supersedes]
                raw = json.dumps(body).encode()
                receipt = 'submission-' + sha256(raw).hexdigest()
                path = inbox / (receipt + '.json')
                path.write_bytes(raw)
                path.chmod(0o600)
                os.utime(path, (timestamp, timestamp))
                return receipt, raw
            old, original = submit('old', 1)
            new, _ = submit('new', 2)
            with closing(review.connect(state)) as db, db:
                review.sync(state, db)
                self.assertEqual(review.queue(db, inbox)[0]['superseded_by'], [])
                args = SimpleNamespace(new_receipt=new, old_receipt=old, reviewer='Maintainer', reason='Verified same run')
                self.assertEqual(review.link_revision(state, db, args)['status'], 'linked')
                self.assertEqual(review.queue(db, inbox)[0]['superseded_by'], [new])
                self.assertEqual(review.queue(db, inbox)[0]['state'], 'pending_review')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
                with self.assertRaisesRegex(ValueError, 'already recorded'):
                    review.link_revision(state, db, args)
                with self.assertRaisesRegex(ValueError, 'newer'):
                    review.link_revision(state, db, SimpleNamespace(**(vars(args) | {'new_receipt': old, 'old_receipt': new})))
                self.assertEqual(db.execute('SELECT reviewer, reason FROM revision_links').fetchone()[:],
                                 ('Maintainer', 'Verified same run'))
            self.assertEqual(review.receipt_status(state, old)['superseded_by'], [new])
            self.assertEqual(review.read_receipt(inbox, old)[1], original)

    def test_old_ledger_has_no_revision_table(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'inbox').mkdir(mode=0o700)
            with closing(review.connect(state)) as db, db:
                db.execute('DROP TABLE revision_links')
                self.assertEqual(review.supersession(state / 'inbox', db), ({}, {}))
