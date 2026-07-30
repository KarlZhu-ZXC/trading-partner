"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class SchemaVersionRow(Base):
    __tablename__ = "schema_versions"

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    applied_at: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class SystemAuditLogRow(Base):
    __tablename__ = "system_audit_log"

    audit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 1B research-state tables ---
