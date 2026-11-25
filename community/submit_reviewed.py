"""Submit a saved NumericBridge draft after confirmation in your local terminal."""

import argparse
import json
from pathlib import Path
import sys

from .consent import scan_draft
from .intake import IntakeClient
from .records.submission import Submissions
from .submission_client import public_draft_error, send_reviewed


ROOT = Path(__file__).resolve().parents[1]


def confirm_and_send(submissions, intake, digest, terminal_in, terminal_out):
    draft = submissions.reviewed(f'draft-{digest}', digest)
    if reason := public_draft_error(intake, draft):
        raise ValueError(reason)
    if intake.token_file is None and scan_draft(draft):
        raise ValueError('Remove private identifiers, prepare a new draft and submit it again')
    review = {key: draft[key] for key in ('title', 'destination', 'attribution', 'supersedes', 'additions')
              if key in draft}
    print(json.dumps(review, ensure_ascii=False, indent=2), file=terminal_out)
    print(f'\nDraft SHA-256: {digest}\n'
          'Type SEND to share this exact draft with the community inbox for review. '
          'Press Enter to cancel: ', end='', file=terminal_out, flush=True)
    if terminal_in.readline().rstrip('\r\n') != 'SEND':
        return {'status': 'cancelled'}
    return send_reviewed(submissions, intake, draft, digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft-sha256', required=True, help='SHA-256 returned by prepare_community_submission')
    parser.add_argument('--intake', required=True, help='same inbox URL used to prepare the draft')
    parser.add_argument('--token-file', type=Path, help='required for authenticated intake only')
    parser.add_argument('--drafts', type=Path, default=Path.home() / '.numericbridge' / 'drafts')
    args = parser.parse_args()
    if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        parser.exit(1, 'Run this command yourself in a local terminal; piped approval is not supported.\n')
    try:
        intake = IntakeClient(args.intake, args.token_file)
        drafts = Submissions(ROOT, args.drafts, destination=intake.url)
        with open('/dev/tty', 'r', encoding='utf-8') as tty_in, open('/dev/tty', 'w', encoding='utf-8') as tty_out:
            result = confirm_and_send(drafts, intake, args.draft_sha256, tty_in, tty_out)
    except KeyboardInterrupt:
        parser.exit(130, '\nInterrupted; if you already typed SEND, retry the same draft to recover its receipt.\n')
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
