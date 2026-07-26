"""Shared MCP validation/error-envelope boundary."""

from typing import Any

from application.dto.error_mapper import to_error_info_from_exception
from application.dto.tool_envelope import ToolEnvelope
from bootstrap import ApplicationContainer
from domain.common.enums import Freshness
from domain.common.ids import EntityIdPrefix


def unexpected_failure(
    container: ApplicationContainer,
    exc: BaseException,
) -> dict[str, Any]:
    """Map unexpected handler exceptions to a redacted Tool Envelope."""
    request_id = container.id_generator.new(EntityIdPrefix.REQ)
    now = container.clock.now()
    error = to_error_info_from_exception(exc, container.secret_redactor)
    envelope: ToolEnvelope[None] = ToolEnvelope.failure(
        request_id=request_id,
        market=None,
        as_of=now,
        fetched_at=now,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(error,),
        degraded=True,
    )
    return envelope.model_dump(mode="json")
