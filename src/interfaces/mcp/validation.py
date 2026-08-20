"""Shared MCP validation/error-envelope boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from application.dto.error_mapper import to_error_info_from_exception
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from bootstrap import ApplicationContainer
from domain.common.enums import Freshness
from domain.common.ids import EntityIdPrefix

_REASON_CODES = {
    "missing": "MISSING",
    "extra_forbidden": "UNEXPECTED",
    "unexpected_keyword_argument": "UNEXPECTED",
    "enum": "ENUM",
    "literal_error": "ENUM",
    "int_type": "TYPE",
    "string_type": "TYPE",
    "bool_type": "TYPE",
    "float_type": "TYPE",
    "list_type": "TYPE",
    "dict_type": "TYPE",
    "greater_than_equal": "CONSTRAINT",
    "less_than_equal": "CONSTRAINT",
    "string_too_short": "CONSTRAINT",
    "string_too_long": "CONSTRAINT",
    "value_error": "VALUE",
}


def _field_name(location: tuple[object, ...]) -> str:
    parts = [str(part) for part in location if part not in {"__root__", "body"}]
    name = ".".join(parts)
    return name[:128] if name else "request"


def closed_variant_invalid_details(error: ValidationError) -> dict[str, object]:
    missing: list[str] = []
    unexpected: list[str] = []
    invalid: list[dict[str, str]] = []
    for item in error.errors():
        name = _field_name(tuple(item.get("loc") or ()))
        typ = str(item.get("type") or "")
        if typ == "missing":
            missing.append(name)
        elif typ in {"extra_forbidden", "unexpected_keyword_argument"}:
            unexpected.append(name)
        else:
            invalid.append({"name": name, "reason_code": _REASON_CODES.get(typ, "INVALID")})
    return {
        "missing_fields": sorted(set(missing)),
        "unexpected_fields": sorted(set(unexpected)),
        "invalid_fields": sorted(invalid, key=lambda item: item["name"]),
    }


def tool_input_invalid_envelope(
    *,
    tool: str,
    operation: str | None,
    error: ValidationError,
) -> dict[str, Any]:
    """Return a secret-safe closed-variant validation envelope."""

    now = datetime.now(UTC)
    details = closed_variant_invalid_details(error)
    details["tool"] = tool
    if operation:
        details["operation"] = operation
    envelope: ToolEnvelope[None] = ToolEnvelope.failure(
        request_id=f"req_invalid_{tool}",
        market=None,
        as_of=now,
        fetched_at=now,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(
            ErrorInfo(
                code="TOOL_INPUT_INVALID",
                message="The request failed closed-variant validation.",
                retryable=False,
                details=details,
            ),
        ),
        degraded=True,
    )
    return envelope.model_dump(mode="json")


def unexpected_failure(
    container: ApplicationContainer,
    exc: BaseException,
) -> dict[str, Any]:
    """Map unexpected handler exceptions to a redacted Tool Envelope."""
    request_id = container.context.id_generator.new(EntityIdPrefix.REQ)
    now = container.context.clock.now()
    error = to_error_info_from_exception(exc, container.context.secret_redactor)
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
