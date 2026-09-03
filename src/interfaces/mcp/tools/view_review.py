"""Intent-first reads for the Moomoo-to-judgment review loop."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any

from pydantic import Field

from application.services._research_support import envelope_success
from bootstrap import ApplicationContainer
from domain.common.ids import EntityIdPrefix
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_view_review_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build bounded structured reads without returning private full note bodies."""

    def success(data: object) -> dict[str, Any]:
        return envelope_success(
            request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
            clock=container.context.clock,
            data=data,
        ).model_dump(mode="json")

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
            return success(container.services.view_reviews.get(note_revision_id))
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

    return SimpleNamespace(
        view_inbox=view_inbox,
        view_review_get=view_review_get,
        current_view_get=current_view_get,
    )
