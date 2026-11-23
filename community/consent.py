"""Check a case's recorded contribution consent and scan a draft before sending it without a form.

The user agrees once, at request intake, to share anonymized community records and
PR drafts for maintainer review; request.json records it under
`community_contribution` as {"consent": "share"|"decline", "attribution_mode":
"anonymous"|"nickname"|"github", "attribution_name": null|TEXT, "confirmed_at": ISO-8601}.
A draft is sent on that consent only when its attribution matches and the built-in
private-identifier scan finds nothing; maintainers review every receipt before
publication, and the inbox re-validates the records and PR drafts it receives.
"""

import json
from pathlib import Path
import tempfile

from tools.anonymize_scan import scan
from .records.submission import attribution, draft_attribution, encode


def recorded_consent(path, draft):
    """Return (consent, None) when request.json allows sending this draft, else (None, reason)."""
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return None, f'cannot read the consent record: {error}'
    consent = record.get('community_contribution') if isinstance(record, dict) else None
    if not isinstance(consent, dict) or consent.get('consent') != 'share':
        return None, 'request.json records no consent to share (community_contribution.consent != "share")'
    if not isinstance(consent.get('confirmed_at'), str) or not consent['confirmed_at'].strip():
        return None, 'community_contribution needs confirmed_at'
    try:
        expected = attribution(consent.get('attribution_mode', 'anonymous'), consent.get('attribution_name'))
    except ValueError as error:
        return None, f'invalid consented attribution: {error}'
    if draft_attribution(draft) != expected:
        return None, 'draft attribution differs from the consented attribution'
    return consent, None


def scan_draft(draft, terms=()):
    """Built-in private-identifier findings in the exact draft bytes (empty when clean)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'draft.json'
        path.write_bytes(encode(draft))
        result = scan([str(path)], terms)
    return [{k: f[k] for k in ('line', 'kind', 'match')} for f in result['findings']]
