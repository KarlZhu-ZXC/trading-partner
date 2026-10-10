"""Secret-safe capability invocation usage boundary.

The usage record deliberately contains only closed, low-cardinality values.  It
is intended for aggregate diagnostics and must not become a second request or
business-event log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

CAPABILITY_USAGE_SOURCES = frozenset({"MCP", "CONSOLE", "AGENT", "INTERNAL"})
CAPABILITY_USAGE_OUTCOMES = frozenset(
    {"OK", "TOOL_FAILURE", "INVALID_REQUEST", "DENIED", "ERROR", "CANCELLED"}
)
CAPABILITY_USAGE_NAME_SENTINELS = frozenset(
    {"__unknown__", "__direct__", "__invalid__"}
)
CAPABILITY_USAGE_RETENTION_DAYS = 30
CAPABILITY_USAGE_MAX_ROWS = 20_000
CAPABILITY_USAGE_MAX_ELAPSED_MS = 86_400_000

_CAPABILITY_USAGE_NAME = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")


def _validate_name(field: str, value: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if (
        value not in CAPABILITY_USAGE_NAME_SENTINELS
        and _CAPABILITY_USAGE_NAME.fullmatch(value) is None
    ):
        raise ValueError(f"{field} has an invalid diagnostic name")


@dataclass(frozen=True, slots=True)
class CapabilityUsageRecord:
    """One bounded capability invocation observation.

    Names are intentionally restricted to lowercase identifiers so callers
    cannot accidentally persist payloads, URLs, or user-provided prose.
    """

    source: str
    capability: str
    operation: str
    outcome: str
    elapsed_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or self.source not in CAPABILITY_USAGE_SOURCES:
            raise ValueError("source is not a supported usage source")
        _validate_name("capability", self.capability)
        _validate_name("operation", self.operation)
        if not isinstance(self.outcome, str) or self.outcome not in CAPABILITY_USAGE_OUTCOMES:
            raise ValueError("outcome is not a supported usage outcome")
        if (
            isinstance(self.elapsed_ms, bool)
            or not isinstance(self.elapsed_ms, int)
            or not 0 <= self.elapsed_ms <= CAPABILITY_USAGE_MAX_ELAPSED_MS
        ):
            raise ValueError("elapsed_ms is outside the supported range")


class CapabilityUsageStore(Protocol):
    """Durable aggregate sink for secret-safe capability usage records."""

    def record(self, record: CapabilityUsageRecord) -> None:
        """Record one observation; diagnostic failures may be raised."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Return a bounded, explicitly scoped aggregate snapshot."""
        ...


__all__ = [
    "CAPABILITY_USAGE_MAX_ELAPSED_MS",
    "CAPABILITY_USAGE_MAX_ROWS",
    "CAPABILITY_USAGE_NAME_SENTINELS",
    "CAPABILITY_USAGE_OUTCOMES",
    "CAPABILITY_USAGE_RETENTION_DAYS",
    "CAPABILITY_USAGE_SOURCES",
    "CapabilityUsageRecord",
    "CapabilityUsageStore",
]
