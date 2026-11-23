"""Run the NumericBridge contribution MCP over local stdio."""

import argparse
import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp import types
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError
from pydantic import Field, StringConstraints

from .intake import IntakeClient
from .mcp_search import register_search
from .records.submission import Submissions, draft_attribution


ROOT = Path(__file__).resolve().parents[1]
Title = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1,
                                        max_length=120, pattern=r'^[^\x00-\x1f\x7f]+$')]
DraftID = Annotated[str, StringConstraints(strict=True, pattern=r'^draft-[a-f0-9]{64}$')]
DraftHash = Annotated[str, StringConstraints(strict=True, pattern=r'^[a-f0-9]{64}$')]
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


def create_server(submissions: Submissions, intake: IntakeClient | None = None) -> MCPServer:
    server = MCPServer('numericbridge-contribution', version='1.0.0', subscriptions=False,
                       instructions='Show the local preview, then use submit_community for the only sharing confirmation. Never answer it on the user\'s behalf.')

    @server.tool(
        title='Prepare a NumericBridge contribution',
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
    ) -> dict[str, Any]:
        """Validate additions and save an exact local preview without sending it.

        Only include public, reviewed summaries and minimum evidence. Evidence
        maps a new site-relative evidence/submissions/*.json path to its JSON
        object. Reuse the community record schemas for all supplied records.
        Use a name only when the contributor chooses a nickname or GitHub @username.
        Show the actual additions, destination and attribution, then offer submission through
        the client confirmation.
        """
        additions = dict(evaluations=dict(evaluations=evaluations or []),
                         repairs=dict(repairs=repairs or [], library_groups=library_groups or []),
                         relations=dict(entities=entities or [], links=links or []), evidence=evidence or {})
        try:
            return await asyncio.to_thread(submissions.prepare, title, additions,
                attribution_mode=attribution_mode, attribution_name=attribution_name)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
            return dict(status='invalid', findings=[str(error)])

    @server.tool(
        title='Submit a reviewed NumericBridge contribution',
        annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=True),
        structured_output=True,
    )
    async def submit_community(draft_id: DraftID, draft_sha256: DraftHash, ctx: Context) -> dict[str, Any]:
        """Request confirmation and send this exact draft to the community inbox.

        After showing the preview, use this tool as the sharing offer. The client
        presents the only user confirmation. Rejecting or cancelling sends nothing.
        """
        try:
            draft = await asyncio.to_thread(submissions.reviewed, draft_id, draft_sha256)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return dict(status='invalid_draft', message=str(error))
        if intake is None:
            return dict(status='intake_unavailable', message='No community inbox is configured; keep this local draft')
        session = ctx.request_context.session
        if not session.check_client_capability(types.ClientCapabilities(elicitation=types.ElicitationCapability())):
            return dict(status='approval_unavailable', reason='form_unsupported',
                        message='Use a client that presents MCP form elicitation to the user')
        credit = draft_attribution(draft)
        credit_message = ('Anonymous; no public credit name.' if credit['mode'] == 'anonymous' else
            f"Credit: {credit['name']} ({credit['mode']}) on the NumericBridge site and in the "
            'community PR and any related upstream PR submitted by the maintainers.')
        try:
            approval = await session.elicit_form(
                message=f"Share this anonymized result with NumericBridge so others can reuse it?\n"
                f"Title: {draft['title']}\nCommunity inbox: {draft['destination']}\n"
                f"{credit_message}\n"
                f"Draft SHA-256: {draft_sha256}",
                requested_schema=APPROVAL_SCHEMA, related_request_id=ctx.request_id)
        except MCPError as error:
            return dict(status='approval_unavailable', reason='form_request_failed', rpc_code=error.code,
                        message='The client could not open the confirmation form')
        except (TypeError, ValueError):
            return dict(status='approval_unavailable', reason='form_response_invalid',
                        message='The client did not return a valid confirmation response')
        if approval.action != 'accept':
            return dict(status='declined' if approval.action == 'decline' else 'cancelled')
        approved = (approval.content or {}).get('approve')
        if approved is False:
            return dict(status='declined')
        if approved is not True:
            return dict(status='approval_unavailable', reason='form_response_invalid',
                        message='The client did not return a valid confirmation response')
        try:
            return await asyncio.to_thread(intake.send, draft, draft_sha256)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return dict(status='submission_failed', message=str(error))

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intake', help='community submission URL; omit for local drafts only')
    parser.add_argument('--token-file', type=Path, help='file containing the community upload credential')
    parser.add_argument('--drafts', type=Path, default=Path.home() / '.numericbridge' / 'drafts',
                        help='private local draft directory')
    parser.add_argument('--search-index', type=Path, help='also expose search using this public index snapshot')
    parser.add_argument('--search-sha256', help='expected SHA-256 of the search index snapshot')
    args = parser.parse_args()
    if bool(args.intake) != bool(args.token_file):
        parser.error('--intake and --token-file must be configured together')
    if args.search_sha256 and args.search_index is None:
        parser.error('--search-sha256 requires --search-index')
    try:
        intake = IntakeClient(args.intake, args.token_file) if args.intake else None
        submissions = Submissions(ROOT, args.drafts, destination=intake.url if intake else None)
        server = create_server(submissions, intake)
        if args.search_index is not None:
            register_search(server, args.search_index, index_sha256=args.search_sha256)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    server.run('stdio')


if __name__ == '__main__':
    main()
