"""Route community writes and bind receipts to an exact draft and patch versions."""

from hashlib import sha256
import re
from urllib.parse import urlsplit


TARGET = 'numericbridge'
PUBLIC_PATH = '/v1/public-submissions'
AUTHENTICATED_PATH = '/v1/submissions'
DIGEST = re.compile(r'[a-f0-9]{64}')
COMMIT = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
CANDIDATE_FIELDS = {'candidate_id', 'target_commit', 'patch_sha256', 'tested_commit', 'tested_patch_sha256'}


def require_target(delivery_target):
    if delivery_target != TARGET:
        raise ValueError('Contribution can only go to NumericBridge intake; upstream is read-only')


def intake_mode(destination):
    """Allow only the official HTTPS inbox or an explicit loopback test inbox."""
    if destination is None:
        return 'local_only'
    if not isinstance(destination, str) or any(c.isspace() or ord(c) < 32 for c in destination):
        raise ValueError('Use the official NumericBridge community inbox or a loopback test inbox')
    url = urlsplit(destination)
    official = url.scheme == 'https' and url.netloc == 'numericbridge.com'
    loopback_host = url.hostname in ('127.0.0.1', '::1') and url.username is None
    loopback = loopback_host and url.scheme in ('http', 'https')
    modes = {PUBLIC_PATH: 'public', AUTHENTICATED_PATH: 'authenticated'}
    if not (official or loopback) or url.path not in modes or url.query or url.fragment:
        raise ValueError('Use the official NumericBridge community inbox or a loopback test inbox')
    return modes[url.path]


def capabilities(destination):
    mode = intake_mode(destination)
    return dict(schema_version=1, delivery_target=TARGET, destination=destination,
                intake_mode=mode, upstream_access='read_only', can_submit=mode != 'local_only',
                supports_supersedes=mode == 'authenticated',
                attribution_modes=['anonymous'] if mode == 'public' else ['anonymous', 'nickname', 'github'])


def _matches(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def candidate_versions(evidence):
    """Validate versioned evidence. Old accepted drafts without this field stay readable."""
    seen = set()
    versions = []
    for path, document in sorted(evidence.items()):
        if not isinstance(document, dict) or 'candidate_version' not in document:
            continue
        version = document['candidate_version']
        if not isinstance(version, dict) or set(version) != CANDIDATE_FIELDS:
            raise ValueError(f'{path}: candidate_version requires ' + ', '.join(sorted(CANDIDATE_FIELDS)))
        candidate = version['candidate_id']
        if not _matches(SLUG, candidate) or len(candidate) > 120 or candidate in seen:
            raise ValueError(f'{path}: candidate_id must be a unique slug in the draft')
        seen.add(candidate)
        target, patch = version['target_commit'], version['patch_sha256']
        tested, tested_patch = version['tested_commit'], version['tested_patch_sha256']
        if not _matches(COMMIT, target) or not _matches(DIGEST, patch):
            raise ValueError(f'{path}: use a full target commit and patch SHA-256')
        if (tested is None) != (tested_patch is None) or (tested is not None and (
                not _matches(COMMIT, tested) or not _matches(DIGEST, tested_patch))):
            raise ValueError(f'{path}: supply both tested commit and tested patch SHA-256, or neither')
        pr = document.get('upstream_pr_draft')
        if not isinstance(pr, dict):
            raise ValueError(f'{path}: candidate version needs an upstream_pr_draft')
        requirements = pr.get('contribution_requirements')
        if not isinstance(requirements, dict) or requirements.get('commit') != target:
            raise ValueError(f'{path}: candidate target must match the commit inspected for the PR draft')
        patch_text = document.get('patch_text')
        if not isinstance(patch_text, str) or not patch_text or sha256(patch_text.encode('utf-8')).hexdigest() != patch:
            raise ValueError(f'{path}: patch_text UTF-8 bytes do not match patch_sha256')
        if 'patch_sha256' in document and document['patch_sha256'] != patch:
            raise ValueError(f'{path}: conflicting patch_sha256 fields')
        pending = pr.get('pending_checks')
        if (tested, tested_patch) != (target, patch) and (
                not isinstance(pending, list) or 'native_on_target' not in pending):
            raise ValueError(f'{path}: mark native_on_target pending when the exact target and patch were not tested')
        versions.append(dict(**version, repository=pr.get('repository'), base_branch=pr.get('base_branch')))
    return sorted(versions, key=lambda value: value['candidate_id'])


def receipt_binding(draft, digest, receipt_id):
    """Return a binding only for a verified receipt ID and its exact local draft."""
    if not _matches(DIGEST, digest) or receipt_id != f'submission-{digest}':
        raise ValueError('Receipt does not belong to the requested draft SHA-256')
    evidence = draft['review_evidence'] if draft['schema_version'] == 5 else draft['additions']['evidence']
    return dict(schema_version=1, receipt_id=receipt_id, draft_sha256=digest,
                destination=draft['destination'], candidates=candidate_versions(evidence))
