"""Audit log writer port — sole application-facing writer for system_audit_log."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class AuditLogWriter(Protocol):
    """Append-only audit sink.

    Engine-bound (Phase 1A) and Session-bound (Phase 1B UoW) implementations
    share this contract. Session-bound writers must only add/flush and never
    commit; the surrounding Unit of Work owns the transaction.
    """

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object],
        request_id: str | None = None,
    ) -> str:
        """Persist one audit row; return audit_id."""
        ...
