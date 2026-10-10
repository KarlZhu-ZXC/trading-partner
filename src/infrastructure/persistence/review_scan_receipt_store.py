"""Durable Review Queue scan receipt storage on the existing audit table."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.ports.audit_log_writer import AuditLogWriter
from application.ports.review_scan_receipt_store import ReviewScanReceiptStore
from infrastructure.persistence.orm import SystemAuditLogRow

REVIEW_SCAN_RECEIPT_EVENT = "REVIEW_ITEMS_REFRESHED"


class SqlAlchemyReviewScanReceiptStore(ReviewScanReceiptStore):
    """Save via the existing independent audit transaction and read latest."""

    def __init__(self, engine: Engine, audit_log_writer: AuditLogWriter) -> None:
        self._engine = engine
        self._audit_log_writer = audit_log_writer

    def save(self, receipt: Mapping[str, object]) -> str:
        return self._audit_log_writer.append(REVIEW_SCAN_RECEIPT_EVENT, receipt)

    def latest(self) -> dict[str, object] | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(SystemAuditLogRow)
                .where(SystemAuditLogRow.event_type == REVIEW_SCAN_RECEIPT_EVENT)
                .order_by(
                    SystemAuditLogRow.recorded_at.desc(),
                    SystemAuditLogRow.audit_id.desc(),
                )
                .limit(1)
            )
            if row is None:
                return None
            try:
                payload = json.loads(row.payload_json)
            except (TypeError, ValueError):
                return None
            if not isinstance(payload, dict):
                return None
            value = cast(dict[str, object], payload)
            # The append-only audit writer allocates the identity.  Expose it
            # on reads alongside the stored safe metadata without mutating the
            # existing audit row.
            value.setdefault("receipt_id", row.audit_id)
            value.setdefault(
                "persistence",
                {"status": "SAVED", "receipt_id": row.audit_id},
            )
            return value
