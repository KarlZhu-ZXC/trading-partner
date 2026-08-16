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
