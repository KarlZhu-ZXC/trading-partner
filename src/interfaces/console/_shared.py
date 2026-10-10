"""Shared primitives for the Console API and Agent API routers.

Only provably identical pieces live here: the strict request-model base and
the failure-payload shape both routers already produced. Endpoint-specific
semantics (HTTP status, codes, diagnostics) stay in their owning modules.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ConsoleRequestModel(BaseModel):
    """Strict request body: unknown keys are rejected at the boundary."""

    model_config = ConfigDict(extra="forbid")


def failure_payload(code: str, message: str) -> dict[str, Any]:
    """One failed console capability without hiding sibling aggregates."""

    return {
        "ok": False,
        "data": None,
        "warnings": [],
        "errors": [{"code": code, "message": message}],
        "degraded": True,
    }


def canonical_subject_transport(value: Any) -> Any:
    """Translate public compatibility ``case_*`` fields for the internal Console BFF."""

    if isinstance(value, dict):
        aliases = {
            "case_id": "subject_id",
            "case_type": "subject_type",
            "linked_case_ids": "linked_subject_ids",
        }
        return {
            aliases.get(key, key): canonical_subject_transport(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [canonical_subject_transport(item) for item in value]
    return value
