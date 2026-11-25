"""Keep public receipt credentials in local client state, never in tool results."""

from hashlib import sha256
import os
import re
import stat
from tempfile import NamedTemporaryFile

from .records.submission import draft_attribution


RECEIVED_FIELDS = ('status', 'receipt_id', 'review_state', 'reused')
_RECEIPT = re.compile(r'submission-[0-9a-f]{64}')
_STATUS_TOKEN = re.compile(r'[0-9a-f]{64}')
PUBLIC_DRAFT_MESSAGE = ('Public intake accepts only new anonymous submissions; '
                        'use authenticated intake for named credit or revisions')


def _token_path(submissions, intake, receipt_id):
    if not _RECEIPT.fullmatch(receipt_id):
        raise ValueError('Use a submission-<sha256> receipt ID')
    directory = submissions.drafts / '.status-tokens'
    # A receipt token must never be sent to a different configured URL.
    endpoint_hash = sha256(intake.url.encode('utf-8')).hexdigest()
    return directory / f'{receipt_id}-{endpoint_hash}'


def _check_directory(path):
    mode = path.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise ValueError('The receipt token directory must be private (mode 0700)')


def _save_token(path, token):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    _check_directory(path.parent)
    with NamedTemporaryFile(mode='w', encoding='ascii', dir=path.parent, delete=False) as stream:
        temporary = stream.name
        try:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(token + '\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _load_token(path):
    _check_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
            raise ValueError('The receipt token file must be private (mode 0600)')
        raw = stream.read(66)
    token = raw.decode('ascii').removesuffix('\n')
    if not _STATUS_TOKEN.fullmatch(token):
        raise ValueError('Invalid local receipt token')
    return token


def public_draft_error(intake, draft):
    if intake.token_file is None and (draft.get('supersedes') or draft_attribution(draft)['mode'] != 'anonymous'):
        return PUBLIC_DRAFT_MESSAGE
    return None


def send_reviewed(submissions, intake, draft, digest):
    """Return only the public receipt fields, storing its secret locally if possible."""
    response = intake.send(draft, digest)
    result = {key: response[key] for key in RECEIVED_FIELDS if key in response}
    if intake.token_file is None and result.get('status') == 'received':
        try:
            token = response.get('status_token')
            if (not isinstance(token, str) or not _STATUS_TOKEN.fullmatch(token)
                    or result.get('receipt_id') != f'submission-{digest}'):
                raise ValueError('Invalid receipt credential')
            _save_token(_token_path(submissions, intake, result['receipt_id']), token)
        except (OSError, ValueError):
            # The draft was accepted even if private local storage failed.
            result['status_lookup_warning'] = ('Receipt received, but its status credential could not be saved. '
                                               'Retry the same reviewed draft to recover status access.')
    return result


def receipt_status(submissions, intake, receipt_id):
    if intake.token_file is not None:
        return intake.status(receipt_id)
    try:
        token = _load_token(_token_path(submissions, intake, receipt_id))
    except (OSError, ValueError):
        return dict(status='status_unavailable', receipt_id=receipt_id,
                    message='No usable local status credential for this receipt and inbox')
    return intake.status(receipt_id, status_token=token)
