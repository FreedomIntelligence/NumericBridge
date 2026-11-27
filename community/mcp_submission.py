"""Run the NumericBridge contribution MCP over local stdio."""

import argparse
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal

from mcp import types
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError
from pydantic import Field, StringConstraints, ValidationError

from tools.contribution_contract import TARGET, candidate_versions, capabilities, receipt_binding, require_target
from tools.strict_pr import inspect_template, load_registry

from .catalog_snapshot import CatalogUnavailable, OfficialCatalog
from .consent import recorded_consent, scan_draft
from .contribution_input import Contribution, PatchTarget, compile_contribution
from .intake import IntakeClient
from .mcp_search import register_search
from .records.submission import Submissions, add_catalog_site_argument, attribution, draft_attribution
from .submission_client import PUBLIC_DRAFT_MESSAGE, public_draft_error, receipt_status, send_reviewed


ROOT = Path(__file__).resolve().parents[1]
Title = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1,
                                        max_length=120, pattern=r'^[^\x00-\x1f\x7f]+$')]
DraftID = Annotated[str, StringConstraints(strict=True, pattern=r'^draft-[a-f0-9]{64}$')]
DraftHash = Annotated[str, StringConstraints(strict=True, pattern=r'^[a-f0-9]{64}$')]
Receipt = Annotated[str, StringConstraints(strict=True, pattern=r'^submission-[a-f0-9]{64}$')]
Record = dict[str, Any]
Records = Annotated[list[Record], Field(max_length=50)]
Evidence = Annotated[dict[str, Record], Field(max_length=20)]


# Keep the form root to the MCP fields Codex accepts. The SDK's Pydantic
# helper emits a top-level title, which Codex's typed form parser rejects.
APPROVAL_SCHEMA: types.ElicitRequestedSchema = {
    'type': 'object',
    'properties': {'approve': {'type': 'boolean', 'title': 'Share this draft',
                               'description': 'Send this exact draft to NumericBridge for review'}},
    'required': ['approve'],
}


def terminal_fallback(submissions: Submissions, intake: IntakeClient, digest: str) -> str:
    """Command the user runs in their own terminal to review and send the same draft."""
    def quote(value):
        return "'" + str(value).replace("'", "'\\''") + "'"
    credential = f' --token-file {quote(intake.token_file)}' if intake.token_file is not None else ''
    return (f'cd {quote(ROOT)} && {quote(sys.executable)} -B -m community.submit_reviewed '
            f'--draft-sha256 {digest} --intake {quote(intake.url)}{credential} '
            f'--drafts {quote(submissions.drafts)}')


def create_server(submissions: Submissions, intake: IntakeClient | None = None) -> MCPServer:
    trusted_repos = submissions.trusted_repos
    configured = capabilities(intake.url if intake is not None else None)
    if submissions.destination != configured['destination']:
        raise ValueError('The draft destination must match the configured NumericBridge inbox')
    server = MCPServer('numericbridge-contribution', version='1.2.0', subscriptions=False,
                       instructions='Upstream is read-only. Use NumericBridge intake for contribution. '
                                    'Show the local preview, then use submit_community for the only sharing '
                                    'confirmation. Never answer it on the user\'s behalf.')

    @server.tool(
        title='Read NumericBridge contribution capabilities',
        annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False),
        structured_output=True,
    )
    async def community_contribution_capabilities() -> dict[str, Any]:
        """Report the only contribution route, supported attribution and revision support."""
        return dict(configured, contribution_input_versions=[1], typed_draft_schema_version=5,
                    review_evidence_separate=True, trusted_upstream_templates_configured=bool(trusted_repos))

    @server.tool(title='Check the NumericBridge contribution catalog',
                 annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                   idempotentHint=True, openWorldHint=True),
                 structured_output=True)
    async def check_contribution_catalog() -> dict[str, Any]:
        """Fetch and validate the official baseline without reading or sending any case data."""
        def check():
            with submissions.checkout(submissions.baseline()):
                pass
            snapshot = getattr(submissions.catalog_provider, 'snapshot', None)
            return (dict(status='ready', generation=snapshot.generation, sha256=snapshot.sha256)
                    if snapshot else dict(status='ready', source='local'))

        try:
            return await asyncio.to_thread(check)
        except CatalogUnavailable as error:
            return dict(status='catalog_unavailable', message=str(error))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
            return dict(status='catalog_unavailable', message='Could not validate the configured catalog baseline.')

    @server.tool(title='Inspect the pinned upstream PR template',
                 annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                   idempotentHint=True, openWorldHint=False),
                 structured_output=True)
    async def inspect_upstream_pr_template(target: PatchTarget) -> dict[str, Any]:
        """Read the official template from a configured local clone, never from caller-supplied paths."""
        try:
            result = await asyncio.to_thread(inspect_template, repository=target.repository,
                base_branch=target.base_branch, target_commit=target.commit,
                template_path=target.template_path, trusted_repos=trusted_repos)
            return dict(status='ok', **result)
        except (OSError, ValueError) as error:
            return dict(status='invalid', findings=[str(error)])

    @server.tool(title='Prepare typed NumericBridge contribution',
                 annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                   idempotentHint=True, openWorldHint=False),
                 structured_output=True)
    async def prepare_contribution(
        contribution: Contribution,
        attribution_mode: Literal['anonymous', 'nickname', 'github'] = 'anonymous',
        attribution_name: str | None = None,
        supersedes: Annotated[list[Receipt], Field(max_length=10)] | None = None,
        delivery_target: str = TARGET,
    ) -> dict[str, Any]:
        """Compile evaluated results, issues and patches into public records and review-only material."""
        try:
            require_target(delivery_target)
            if configured['intake_mode'] == 'public' and (attribution_mode != 'anonymous' or supersedes):
                raise ValueError(PUBLIC_DRAFT_MESSAGE)
            credit = attribution(attribution_mode, attribution_name)
            additions, review = await asyncio.to_thread(compile_contribution, contribution, credit,
                trusted_repos=trusted_repos)
            versions = candidate_versions(review)
            findings = await asyncio.to_thread(scan_draft, dict(additions=additions, review_evidence=review))
            if findings:
                return dict(status='scan_findings', findings=[{k: v for k, v in finding.items() if k != 'match'}
                                                               for finding in findings[:20]])
            prepared = await asyncio.to_thread(submissions.prepare, contribution.title, additions,
                review_evidence=review, attribution_mode=attribution_mode, attribution_name=attribution_name,
                replaces=supersedes or [])
            return dict(prepared, candidate_versions=versions)
        except CatalogUnavailable as error:
            return dict(status='catalog_unavailable', message=str(error))
        except ValidationError as error:
            return dict(status='invalid', findings=error.errors(include_input=False, include_url=False, include_context=False))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
            return dict(status='invalid', findings=[str(error)])

    @server.tool(
        title='Prepare a NumericBridge contribution (legacy catalog objects)',
        annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False),
        structured_output=True,
    )
    async def prepare_community_submission(
        title: Title,
        evaluations: Records | None = None,
        repairs: Records | None = None,
        library_groups: Records | None = None,
        entities: Records | None = None,
        links: Records | None = None,
        evidence: Evidence | None = None,
        attribution_mode: Literal['anonymous', 'nickname', 'github'] = 'anonymous',
        attribution_name: str | None = None,
        supersedes: Annotated[list[Receipt], Field(max_length=10)] | None = None,
        delivery_target: str = TARGET,
    ) -> dict[str, Any]:
        """Validate additions and save an exact local preview without sending it.

        Only include public, reviewed summaries and minimum evidence. Evidence
        maps a new site-relative evidence/submissions/*.json path to its JSON
        object. Reuse the community record schemas for all supplied records.
        Use `prepare_contribution` for new PR or patch candidates. Legacy drafts
        already received remain readable; new legacy public patch evidence is
        not accepted. This tool still supports specialized evidence such as
        `operator_optimization`: output of operator_optimization.py scaffold/check
          (`kind: "operator-optimization"`, `schema_version: 1`, `round_status`:
          `"handoff_open"` | `"round_delivered"`; never include server-assigned
          `tier` or `recommendation` fields).
        Public intake only accepts new anonymous submissions. With authenticated
        intake, use a name only when the contributor chooses a nickname or GitHub
        @username. For an authenticated revision, list any earlier submission
        receipt IDs in supersedes.
        Show the actual additions, destination and attribution, then offer submission through
        the client confirmation.
        """
        try:
            require_target(delivery_target)
        except ValueError as error:
            return dict(status='invalid', findings=[str(error)])
        if configured['intake_mode'] == 'public' and (attribution_mode != 'anonymous' or supersedes):
            return dict(status='invalid', findings=[PUBLIC_DRAFT_MESSAGE])
        additions = dict(evaluations=dict(evaluations=evaluations or []),
                         repairs=dict(repairs=repairs or [], library_groups=library_groups or []),
                         relations=dict(entities=entities or [], links=links or []), evidence=evidence or {})
        try:
            versions = candidate_versions(additions['evidence'])
            prepared = await asyncio.to_thread(submissions.prepare, title, additions,
                attribution_mode=attribution_mode, attribution_name=attribution_name,
                replaces=supersedes or [])
            return dict(prepared, candidate_versions=versions)
        except CatalogUnavailable as error:
            return dict(status='catalog_unavailable', message=str(error))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
            return dict(status='invalid', findings=[str(error)])

    @server.tool(
        title='Submit a reviewed NumericBridge contribution',
        annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=True),
        structured_output=True,
    )
    async def submit_community(draft_id: DraftID, draft_sha256: DraftHash, ctx: Context,
                               consent_record: str | None = None,
                               delivery_target: str = TARGET) -> dict[str, Any]:
        """Send this exact draft to the community inbox for maintainer review.

        With consent_record (the case's request.json path), send without a form when
        the user agreed at intake (community_contribution.consent = "share"), the
        draft's attribution matches that consent and the private-identifier scan is
        clean; then tell the user the receipt. Without it, the client presents the
        confirmation; rejecting or cancelling sends nothing.
        """
        try:
            require_target(delivery_target)
            draft = await asyncio.to_thread(submissions.reviewed, draft_id, draft_sha256)
            evidence = draft['review_evidence'] if draft['schema_version'] == 5 else draft['additions']['evidence']
            candidate_versions(evidence)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            return dict(status='invalid_draft', message=str(error))
        if intake is None:
            return dict(status='intake_unavailable', message='No community inbox is configured; keep this local draft')
        if reason := public_draft_error(intake, draft):
            return dict(status='invalid_draft', message=reason)
        findings = await asyncio.to_thread(scan_draft, draft)
        if findings:
            return dict(status='scan_findings', findings=findings[:20],
                        message='Remove private identifiers, prepare a new draft and submit it again')
        if consent_record is not None:
            consent, reason = await asyncio.to_thread(recorded_consent, consent_record, draft)
            if consent is None:
                return dict(status='consent_missing', message=reason)
            try:
                result = await asyncio.to_thread(send_reviewed, submissions, intake, draft, draft_sha256)
            except (OSError, ValueError, KeyError, TypeError) as error:
                return dict(status='submission_failed', message=str(error))
            return dict(result, confirmation='prior_consent', consented_at=consent['confirmed_at'])
        session = ctx.request_context.session
        def unavailable(reason, message, **extra):
            return dict(status='approval_unavailable', reason=reason, message=message,
                        fallback_command=terminal_fallback(submissions, intake, draft_sha256),
                        fallback_note='Ask the user to run this in their own terminal; it shows the draft and '
                                      'sends only after they type SEND.', **extra)
        if not session.check_client_capability(types.ClientCapabilities(elicitation=types.ElicitationCapability())):
            return unavailable('form_unsupported', 'Use a client that presents MCP form elicitation to the user')
        credit = draft_attribution(draft)
        credit_message = ('Anonymous; no public credit name.' if credit['mode'] == 'anonymous' else
            f"Credit: {credit['name']} ({credit['mode']}) on the NumericBridge site and in the "
            'community PR and any related upstream PR submitted by the maintainers.')
        try:
            approval = await session.elicit_form(
                message=f"Share this anonymized result with NumericBridge so others can reuse it?\n"
                f"Title: {draft['title']}\nCommunity inbox: {draft['destination']}\n"
                f"{credit_message}\n"
                + (f"Maintainer-only attachments: {len(draft['review_evidence'])}. Review both parts of the preview.\n"
                   if draft.get('review_evidence') else '')
                + (f"Replaces earlier submissions: {', '.join(draft['supersedes'])}\n"
                   if draft.get('supersedes') else '')
                + f"Draft SHA-256: {draft_sha256}",
                requested_schema=APPROVAL_SCHEMA, related_request_id=ctx.request_id)
        except MCPError as error:
            return unavailable('form_request_failed', 'The client could not open the confirmation form',
                               rpc_code=error.code, error=str(error)[:300])
        except (TypeError, ValueError) as error:
            return unavailable('form_response_invalid', 'The client did not return a valid confirmation response',
                               error=str(error)[:300])
        if approval.action != 'accept':
            return dict(status='declined' if approval.action == 'decline' else 'cancelled')
        approved = (approval.content or {}).get('approve')
        if approved is False:
            return dict(status='declined')
        if approved is not True:
            return unavailable('form_response_invalid', 'The client did not return a valid confirmation response')
        try:
            return await asyncio.to_thread(send_reviewed, submissions, intake, draft, draft_sha256)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return dict(status='submission_failed', message=str(error))

    @server.tool(
        title='Check a NumericBridge submission',
        annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=True),
        structured_output=True,
    )
    async def community_submission_status(receipt_id: Receipt,
                                          draft_sha256: DraftHash | None = None) -> dict[str, Any]:
        """Read the review state of a receipt returned by submit_community.

        States: pending_review, needs_information, accepted, declined, published.
        The public inbox uses a credential saved by this client; never ask the user
        to put a status token into chat. superseded_by lists later submissions that
        replace this one. A receipt is searchable only after it is published and
        the deployed index returns it.
        """
        binding = None
        if draft_sha256 is not None:
            if receipt_id != f'submission-{draft_sha256}':
                return dict(status='invalid_binding', message='Receipt does not belong to the requested draft SHA-256')
            try:
                draft = await asyncio.to_thread(submissions.reviewed, f'draft-{draft_sha256}', draft_sha256)
                binding = receipt_binding(draft, draft_sha256, receipt_id)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                return dict(status='invalid_binding', message=str(error))
        if intake is None:
            return dict(status='intake_unavailable', message='No community inbox is configured')
        try:
            status = await asyncio.to_thread(receipt_status, submissions, intake, receipt_id)
        except (OSError, ValueError) as error:
            return dict(status='status_unavailable', message=str(error))
        if binding is not None and status.get('status') == 'found':
            return dict(status, receipt_binding=binding)
        return status

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intake', help='public or authenticated community submission URL; omit for local drafts only')
    parser.add_argument('--token-file', type=Path, help='file containing the upload credential (authenticated intake only)')
    parser.add_argument('--drafts', type=Path, default=Path.home() / '.numericbridge' / 'drafts',
                        help='private local draft directory')
    add_catalog_site_argument(parser)
    parser.add_argument('--upstream-registry', type=Path, help='operator-managed JSON repo slug to local Git clone mapping')
    parser.add_argument('--search-index', type=Path, help='also expose search using this public index snapshot')
    parser.add_argument('--search-sha256', help='expected SHA-256 of the search index snapshot')
    args = parser.parse_args()
    if args.token_file is not None and not args.intake:
        parser.error('--token-file requires --intake')
    if args.search_sha256 and args.search_index is None:
        parser.error('--search-sha256 requires --search-index')
    try:
        intake = IntakeClient(args.intake, args.token_file) if args.intake else None
        registry = args.upstream_registry.resolve() if args.upstream_registry is not None else None
        with TemporaryDirectory(prefix='numericbridge-catalog-') as lifetime:
            submissions = Submissions(ROOT, args.drafts, destination=intake.url if intake else None,
                                      base_ref='HEAD', catalog_site=args.catalog_site,
                                      catalog_provider=OfficialCatalog(lifetime), trusted_repos=load_registry(registry))
            server = create_server(submissions, intake)
            if args.search_index is not None:
                register_search(server, args.search_index, index_sha256=args.search_sha256)
            server.run('stdio')
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
