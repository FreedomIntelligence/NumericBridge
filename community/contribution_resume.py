"""Recover a completed case's NumericBridge contribution as an official local preview only.

The case is read-only. No workflow command, intake, receipt lookup, or consent
prompt runs. Only the public catalog is fetched; drafts are saved separately.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from stat import S_ISREG
from tempfile import NamedTemporaryFile, TemporaryDirectory

from tools.case_state import validate
from tools.contribution_contract import DIGEST, PUBLIC_PATH, candidate_versions, intake_mode
from tools.strict_pr import load_registry

from .catalog_snapshot import CatalogUnavailable, OfficialCatalog
from .consent import recorded_consent, scan_draft
from .records.submission import Submissions, attribution, encode


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_INTAKE = 'https://numericbridge.com' + PUBLIC_PATH
INPUT_LIMIT = 2 * 1024 * 1024
_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError)


def _object(path):
    if not path.is_file():
        raise ValueError('Expected a regular JSON file.')
    with path.open('rb') as stream:
        raw = stream.read(INPUT_LIMIT + 1)
    if len(raw) > INPUT_LIMIT:
        raise ValueError('Local JSON exceeds the input size limit.')

    def unique(rows):
        result = dict(rows)
        if len(result) != len(rows):
            raise ValueError('Duplicate JSON fields.')
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object.')
    return value


def _inside(case, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError('A checkpoint path must be relative to the case.')
    path = (case / name).resolve()
    if not path.is_relative_to(case):
        raise ValueError('A checkpoint path points outside the case.')
    return path


def _is_digest(value):
    return isinstance(value, str) and bool(DIGEST.fullmatch(value))


def _save_local_draft(directory, draft_id, raw):
    """Install verified bytes atomically; do not change an existing draft."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (draft_id + '.json')
    with NamedTemporaryFile(dir=directory) as temporary:
        temporary.write(raw)
        temporary.flush()
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            if not S_ISREG(path.lstat().st_mode) or path.read_bytes() != raw:
                raise ValueError('The existing draft differs or is not a regular file.')
    return path


def resume(case_dir, drafts, *, input_path=None, intake=PUBLIC_INTAKE, upstream_registry=None,
           repo=ROOT, catalog_factory=OfficialCatalog):
    """Return local recovery status and, on success, the exact version-5 preview.

    ``case_dir`` and ``drafts`` are explicit; writes are confined to the separate
    draft directory and scratch space. ``repo`` and ``catalog_factory`` let
    tests exercise real preview validation without using production services.
    """
    def result(status, **details):
        return dict(status=status, sent=False, **details)

    try:
        case = Path(case_dir).resolve(strict=True)
        if not case.is_dir():
            raise ValueError('Expected a case directory.')
        state = _object(_inside(case, 'case-state.json'))
        request_path = _inside(case, 'request.json')
        request = _object(request_path)
    except _ERRORS:
        return result('setup_required', reason='Provide a case directory with readable case-state.json and request.json.')
    if state.get('stage') != 'community_contribution' or state.get('status') not in ('needs_human', 'in_progress', 'done'):
        return result('not_applicable', reason='The checkpoint is not at the contribution stage after local delivery.')
    try:
        checked = validate(state, request=request, case_dir=case)
    except _ERRORS:
        return result('invalid_case', reason='Could not validate the checkpoint and saved local delivery.')
    if checked['status'] != 'ok':
        return result('invalid_case', problems=checked['problems'])
    summary = state['delivery_summary']
    original = summary['original']
    pinned_acceptance = state.get('acceptance_digest')
    if 'acceptance_digest' in state:
        try:
            matches_pin = _is_digest(pinned_acceptance) and any(
                sha256(_inside(case, ref).read_bytes()).hexdigest() == pinned_acceptance
                for ref in original['evidence'])
        except _ERRORS:
            matches_pin = False
        if not matches_pin:
            return result('invalid_case', reason='The saved acceptance report does not match the checkpoint digest.')

    old = state.get('community_candidate', {})
    if not isinstance(old, dict):
        return result('invalid_case', reason='The legacy community_candidate must be an object.')
    pinned_draft = state.get('community_draft_sha256')
    if old.get('draft_id') is not None:
        draft_id = old['draft_id']
        if not isinstance(draft_id, str) or not draft_id.startswith('draft-') or not _is_digest(draft_id[6:]):
            return result('invalid_case', reason='The legacy draft ID is invalid.')
        if pinned_draft and pinned_draft != draft_id[6:]:
            return result('invalid_case', reason='The pinned draft SHA-256 and legacy draft ID disagree.')
        pinned_draft = draft_id[6:]

    # A received draft is terminal for this recovery, even if the catalog
    # already contains it or the original draft/typed input is no longer here.
    for row in summary.get('community', []):
        binding = row['receipt_binding']
        if (binding['draft_sha256'] == pinned_draft and binding['destination'].startswith('https://numericbridge.com/')
                and old.get('public_intake', binding['destination']) == binding['destination']):
            return result('already_received', receipt_id=binding['receipt_id'],
                          draft_sha256=pinned_draft, review_state=row['review_state'])
    if old.get('receipt') is not None or old.get('intake_attempted') or summary.get('community'):
        return result('receipt_unverified', reason='Existing intake history is not bound to the current draft; check it separately.')
    sharing = request.get('community_contribution', {})
    if not isinstance(sharing, dict) or sharing.get('consent') not in (None, 'decline', 'share'):
        return result('setup_required', reason='The recorded contribution preference is invalid.')
    if sharing.get('consent') == 'decline' or state['status'] == 'done' or summary.get('community_local_only'):
        return result('local_only', consent='declined' if sharing.get('consent') == 'decline' else 'not_requested',
                      reason='The case already records a local-only outcome or a decision not to share.')

    try:
        from pydantic import ValidationError
        from .contribution_input import Contribution, Evaluation, compile_contribution
    except ImportError:
        return result('setup_required', reason='Install the current local contribution dependencies (community/requirements-mcp.txt).')
    try:
        out = Path(drafts).resolve()
        if out.is_relative_to(case) or case.is_relative_to(out):
            raise ValueError('Use a dedicated draft directory separate from the case.')
        if any((parent / 'case-state.json').exists() for parent in (out, *out.parents)):
            raise ValueError('The draft directory must not be inside another case.')
        if intake_mode(intake) == 'local_only' or not intake.startswith('https://numericbridge.com/'):
            raise ValueError('Use an official NumericBridge contribution destination.')
        if old.get('public_intake') is not None and intake != old['public_intake']:
            raise ValueError('The configured destination differs from the saved contribution destination.')
        credit = attribution(sharing.get('attribution_mode', 'anonymous'), sharing.get('attribution_name'))
        if intake_mode(intake) == 'public' and credit['mode'] != 'anonymous':
            raise ValueError('Named attribution requires the official authenticated destination.')
        evidence_path = (Path(input_path).resolve() if input_path is not None else
                         _inside(case, old.get('typed_input', 'community-draft/contribution.json')))
        registry = load_registry(Path(upstream_registry).resolve()) if upstream_registry is not None else {}
        raw_input = _object(evidence_path)
        # Check that this checkout can be the code baseline before using the network.
        base = Submissions(repo, out, base_ref='HEAD').baseline()
    except _ERRORS as error:
        return result('setup_required', reason=str(error))

    try:
        typed = Contribution.model_validate(raw_input)
        for item in typed.items:
            if not hasattr(item, 'task'):
                continue
            if request.get('task') in ('inference', 'sft', 'rl') and item.task != request['task']:
                return result('preview_invalid', reason='Typed input task differs from the confirmed request.')
            if isinstance(item, Evaluation) and (item.run_status != 'completed' or
                                                 item.acceptance.verdict != original['verdict']):
                return result('preview_invalid', reason='The typed evaluation differs from the completed original verdict.')
        additions, review = compile_contribution(typed, credit, trusted_repos=registry)
        versions = candidate_versions(review)
        pinned_versions = {row['id']: (row.get('target_commit'), row.get('patch_sha256'))
                           for row in summary['candidates']}
        if any(pinned_versions.get(row['candidate_id']) != (row['target_commit'], row['patch_sha256'])
               for row in versions):
            return result('preview_invalid', reason='A typed patch is not bound to the case’s saved target and diff digests.')
    except ValidationError as error:
        return result('preview_invalid', findings=error.errors(include_input=False, include_url=False, include_context=False))
    except _ERRORS:
        return result('preview_invalid', reason='Could not compile the typed contribution or verify its patch evidence.')

    # Scan what will be reviewed, including the title and attribution. Return
    # only kinds and line numbers, not the potentially private matched values.
    wire = dict(schema_version=5, title=typed.title, destination=intake, attribution=credit,
                additions=additions, review_evidence=review)
    try:
        findings = scan_draft(wire)
        if findings:
            return result('scan_findings', findings=[{k: row[k] for k in ('line', 'kind')} for row in findings[:20]])
        digest = sha256(encode(wire, compact=True)).hexdigest()
    except _ERRORS:
        return result('preview_invalid', reason='Could not encode and scan the local draft.')
    if pinned_draft and pinned_draft != digest:
        return result('preview_invalid', reason='Current typed input does not reproduce the pinned draft SHA-256.')

    with TemporaryDirectory(prefix='numericbridge-resume-') as temporary:
        scratch = Path(temporary)
        try:
            provider = catalog_factory(scratch, cache=scratch / 'cache')
            site = provider()
            snapshot = provider.snapshot
            catalog = dict(generation=snapshot.generation, sha256=snapshot.sha256)
            # Pass the verified site explicitly; a stale catalog environment
            # override or the search index alone must never be the baseline.
            drafts_service = Submissions(repo, scratch / 'drafts', destination=intake, base_ref=base,
                                         catalog_site=site, trusted_repos=registry)
            with drafts_service.checkout(base):
                pass
        except CatalogUnavailable as error:
            return result('catalog_unavailable', reason=str(error))
        except _ERRORS:
            return result('catalog_unavailable', reason='Could not validate the official catalog baseline.')
        try:
            prepared = drafts_service.prepare(typed.title, additions, review_evidence=review,
                                              attribution_mode=credit['mode'], attribution_name=credit.get('name'))
            draft_id = prepared['draft_id']
            reviewed = drafts_service.reviewed(draft_id, digest)
            if prepared['draft_sha256'] != digest or reviewed != wire:
                raise ValueError('Draft changed after validation.')
        except _ERRORS:
            return result('preview_invalid', reason='The current contribution tooling rejected the draft against the official catalog.')
        try:
            path = _save_local_draft(out, draft_id, encode(reviewed, compact=True))
        except _ERRORS:
            return result('setup_required', reason='Cannot save the exact draft in the separate draft directory.')
        consent, consent_reason = recorded_consent(request_path, reviewed)
        return dict(prepared, status='preview_ready', sent=False, draft_path=str(path),
                    candidate_versions=versions, catalog=catalog,
                    consent='recorded' if consent else 'awaiting_consent', consent_reason=consent_reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', required=True, type=Path)
    parser.add_argument('--drafts', required=True, type=Path, help='dedicated local output directory outside the case')
    parser.add_argument('--input', type=Path, help='explicit typed input instead of the saved case reference')
    parser.add_argument('--intake', default=PUBLIC_INTAKE, help='official destination to bind into the preview; never contacted')
    parser.add_argument('--upstream-registry', type=Path, help='trusted local upstream Git clones for typed patches')
    args = parser.parse_args(argv)
    recovered = resume(args.case_dir, args.drafts, input_path=args.input, intake=args.intake,
                       upstream_registry=args.upstream_registry)
    print(json.dumps(recovered, ensure_ascii=False))
    return int(recovered['status'] not in ('preview_ready', 'already_received', 'local_only'))


if __name__ == '__main__':
    raise SystemExit(main())
