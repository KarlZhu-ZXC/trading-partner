"""SQLAlchemy rows for durable non-factual Agent preferences."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.common import JsonStringTuple


class AgentPreferencesRow(Base):
    __tablename__ = "agent_preferences"
    __table_args__ = (
        CheckConstraint("language IN ('zh-CN','en')", name="ck_agent_preferences_language"),
        CheckConstraint(
            "response_density IN ('compact','standard','detailed')",
            name="ck_agent_preferences_density",
        ),
        CheckConstraint(
            "risk_style IN ('balanced','cautious','direct')",
            name="ck_agent_preferences_risk_style",
        ),
        CheckConstraint("version >= 1", name="ck_agent_preferences_version"),
        CheckConstraint("default_chart IN (0,1)", name="ck_agent_preferences_default_chart"),
        CheckConstraint("web_background IN (0,1)", name="ck_agent_preferences_web_background"),
        UniqueConstraint("owner_principal", name="uq_agent_preferences_owner"),
        Index("ix_agent_preferences_updated_at", "updated_at"),
    )

    preferences_id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_principal: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    response_density: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_source_codes: Mapped[tuple[str, ...]] = mapped_column(
        "preferred_source_codes_json", JsonStringTuple(), nullable=False
    )
    risk_style: Mapped[str] = mapped_column(Text, nullable=False)
    default_chart: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    web_background: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AgentPreferencesRevisionRow(Base):
    __tablename__ = "agent_preferences_revisions"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('CREATE','UPDATE','RESET')",
            name="ck_agent_preferences_revision_operation",
        ),
        CheckConstraint("version >= 1", name="ck_agent_preferences_revision_version"),
        UniqueConstraint(
            "owner_principal",
            "idempotency_key",
            name="uq_agent_preferences_revision_idempotency",
        ),
        Index("ix_agent_preferences_revisions_owner_created", "owner_principal", "created_at"),
    )

    revision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    preferences_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_principal: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_note: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    response_density: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_source_codes: Mapped[tuple[str, ...]] = mapped_column(
        "preferred_source_codes_json", JsonStringTuple(), nullable=False
    )
    risk_style: Mapped[str] = mapped_column(Text, nullable=False)
    default_chart: Mapped[bool] = mapped_column(Integer, nullable=False)
    web_background: Mapped[bool] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = ["AgentPreferencesRevisionRow", "AgentPreferencesRow"]
