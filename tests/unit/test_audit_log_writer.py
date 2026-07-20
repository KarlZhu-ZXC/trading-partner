"""Audit log writer tests."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from conftest import FixedClock, SequentialIdGenerator
from infrastructure.persistence.audit_log_writer import SqlAlchemyAuditLogWriter
from infrastructure.persistence.metadata import Base
from infrastructure.system.redactor import DefaultSecretRedactor


def test_append_redacts_and_persists(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "audit.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    writer = SqlAlchemyAuditLogWriter(
        engine=engine,
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
    )
    audit_id = writer.append(
        event_type="test.event",
        payload={"api_key": "secret-value", "symbol": "NVDA"},
        request_id="req_1",
    )
    assert audit_id.startswith("audit_")
    with Session(engine) as session:
        row = session.execute(text("SELECT payload_json, recorded_at FROM system_audit_log")).one()
        payload = json.loads(row[0])
        assert payload["api_key"] == "***REDACTED***"
        assert payload["symbol"] == "NVDA"
        assert "secret-value" not in row[0]
        recorded_at = datetime.fromisoformat(row[1])
        assert recorded_at.tzinfo is not None
    engine.dispose()
