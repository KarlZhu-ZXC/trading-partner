"""Intent-first reads for the Moomoo-to-judgment review loop."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any

from pydantic import Field

from application.services._research_support import envelope_success
from bootstrap import ApplicationContainer
from domain.common.ids import EntityIdPrefix
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_view_review_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build note-led reads with explicit private-text authorization."""

    def success(data: object) -> dict[str, Any]:
        return envelope_success(
            request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
            clock=container.context.clock,
            data=data,
        ).model_dump(mode="json")

    def notes_inbox(
        limit: Annotated[int, Field(ge=1, le=100)] = 30,
        observed_after: datetime | None = None,
    ) -> dict[str, Any]:
        """Read Moomoo note metadata without model or Subject prerequisites."""
        try:
            return success(
                container.services.note_desk.inbox(limit=limit, observed_after=observed_after)
            )
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def note_read(
        note_revision_id: Annotated[str, Field(min_length=1, max_length=128)],
        include_content: bool = False,
        private_content_authorized: bool = False,
        block_index: Annotated[int, Field(ge=0)] = 0,
        text_offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int, Field(ge=1, le=2000)] = 2000,
        journal_id: Annotated[str | None, Field(min_length=1, max_length=128)] = None,
        context_cursor: Annotated[str | None, Field(min_length=1, max_length=256)] = None,
    ) -> dict[str, Any]:
        """Read attributed text after the owner's explicit private-content request."""
        try:
            data = container.services.note_desk.note(
                note_revision_id,
                include_content=include_content,
                private_content_authorized=private_content_authorized,
                block_index=block_index,
                text_offset=text_offset,
                max_chars=max_chars,
                journal_id=journal_id,
                context_cursor=context_cursor,
            )
            if not include_content:
                # Metadata includes bounded account context. Full saved discussion
                # text is separately paged through journal_id.
                for discussion in data.get("discussion_history", []):
                    if "body_preview" in discussion:
                        discussion["body_preview"] = discussion["body_preview"][:160]
                        discussion["body_truncated"] = True
            if include_content:
                # Keep a full text page below the MCP transport byte budget. Context
                # and history are obtained by the separate metadata read.
                data = {
                    key: data[key]
                    for key in (
                        "note_id",
                        "note_revision_id",
                        "version",
                        "title",
                        "coverage",
                        "observed_at",
                        "content",
                        "discussion_content",
                        "formal_judgment_updated",
                    )
                }
            return success(data)
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def notes_sync() -> dict[str, Any]:
        """Sync Moomoo notes only, without accounts or model calls."""
        try:
            receipt = await container.services.external_notes.sync(
                analyze=False, source_code="MOOMOO_NOTE"
            )
            return success(asdict(receipt))
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def view_inbox(
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        """List note changes waiting for review; excludes private full note bodies."""
        try:
            return success(container.services.view_reviews.inbox(limit=limit))
        except Exception as exc:  # noqa: BLE001 - return typed/redacted Tool Envelope
            return _unexpected_failure(container, exc)

    def view_review_get(
        note_revision_id: Annotated[str, Field(min_length=1, max_length=128)],
    ) -> dict[str, Any]:
        """Compare one exact Observation draft with confirmed local judgment context."""
        try:
            return success(
                container.services.view_reviews.get(
                    note_revision_id,
                    explicit_review=True,
                )
            )
        except Exception as exc:  # noqa: BLE001 - return typed/redacted Tool Envelope
            return _unexpected_failure(container, exc)

    def current_view_get(
        subject_id: Annotated[str, Field(min_length=1, max_length=128)],
    ) -> dict[str, Any]:
        """Read the latest confirmed view derived from exact Decision provenance."""
        try:
            return success(container.services.view_reviews.current(subject_id))
        except Exception as exc:  # noqa: BLE001 - return typed/redacted Tool Envelope
            return _unexpected_failure(container, exc)

    async def view_review_run(
        note_revision_id: Annotated[str, Field(min_length=1, max_length=128)],
        force: bool = False,
    ) -> dict[str, Any]:
        """Run the configured escalated review model; never confirm a judgment."""
        try:
            async with container.services.external_notes.exclusive_session():
                value = await container.services.external_note_review_drafts.review(
                    note_revision_id,
                    explicit_review=True,
                    force=force,
                )
            return success(
                value
                if value is not None
                else {
                    "note_revision_id": note_revision_id,
                    "status": "NOT_REQUIRED_OR_NOT_CONFIGURED",
                }
            )
        except Exception as exc:  # noqa: BLE001 - return typed/redacted Tool Envelope
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        view_inbox=view_inbox,
        notes_inbox=notes_inbox,
        note_read=note_read,
        notes_sync=notes_sync,
        view_review_get=view_review_get,
        view_review_run=view_review_run,
        current_view_get=current_view_get,
    )
