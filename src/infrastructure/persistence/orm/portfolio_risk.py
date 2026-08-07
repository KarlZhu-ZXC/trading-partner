"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class AccountSnapshotRow(Base):
    __tablename__ = "account_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)
    account_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    cash: Mapped[str | None] = mapped_column(Text)
    buying_power: Mapped[str | None] = mapped_column(Text)
    net_assets: Mapped[str | None] = mapped_column(Text)
    margin_used: Mapped[str | None] = mapped_column(Text)
    open_orders_json: Mapped[str] = mapped_column(Text, nullable=False)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)


class AccountPositionRow(Base):
    __tablename__ = "account_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id"], ["account_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[str] = mapped_column(Text, nullable=False)
    sellable_quantity: Mapped[str | None] = mapped_column(Text)
    average_cost: Mapped[str | None] = mapped_column(Text)
    diluted_cost: Mapped[str | None] = mapped_column(Text)
    market_price: Mapped[str | None] = mapped_column(Text)
    market_price_at: Mapped[str | None] = mapped_column(Text)
    market_value: Mapped[str | None] = mapped_column(Text)
    unrealized_pnl: Mapped[str | None] = mapped_column(Text)
    realized_pnl: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioSnapshotRow(Base):
    __tablename__ = "portfolio_snapshots"

    portfolio_snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    account_snapshot_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)
    total_value: Mapped[str | None] = mapped_column(Text)
    exposures_json: Mapped[str] = mapped_column(Text, nullable=False)
    missing_instrument_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)


class SubjectPositionLinkRow(Base):
    __tablename__ = "case_position_links"

    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    account_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 2B risk-engine policy snapshots ---


class RiskPolicyRow(Base):
    __tablename__ = "risk_policies"
    __table_args__ = (
        UniqueConstraint("version", name="uq_risk_policies_version"),
        UniqueConstraint("idempotency_key", name="uq_risk_policies_idempotency_key"),
        CheckConstraint("version >= 1", name="ck_risk_policies_version"),
        CheckConstraint(
            "CAST(single_position_max_percent AS REAL) >= 0"
            " AND CAST(single_position_max_percent AS REAL) <= 100",
            name="ck_risk_policies_single_position_max_percent",
        ),
        CheckConstraint(
            "CAST(gross_exposure_max_percent AS REAL) > 0"
            " AND CAST(gross_exposure_max_percent AS REAL) <= 1000",
            name="ck_risk_policies_gross_exposure_max_percent",
        ),
        CheckConstraint(
            "CAST(minimum_cash_percent AS REAL) >= 0 AND CAST(minimum_cash_percent AS REAL) <= 100",
            name="ck_risk_policies_minimum_cash_percent",
        ),
        CheckConstraint(
            "CAST(margin_usage_max_percent AS REAL) >= 0"
            " AND CAST(margin_usage_max_percent AS REAL) <= 1000",
            name="ck_risk_policies_margin_usage_max_percent",
        ),
        CheckConstraint("max_account_age_seconds >= 1", name="ck_risk_policies_account_age"),
        CheckConstraint("max_price_age_seconds >= 1", name="ck_risk_policies_price_age"),
        CheckConstraint("is_system_default IN (0, 1)", name="ck_risk_policies_system_default"),
        CheckConstraint(
            "confirmed_by IN ('system_default', 'user', 'external_agent')",
            name="ck_risk_policies_confirmed_by",
        ),
        CheckConstraint("schema_version = 1", name="ck_risk_policies_schema_version"),
        Index("ix_risk_policies_version", "version"),
    )

    policy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    single_position_max_percent: Mapped[str] = mapped_column(Text, nullable=False)
    gross_exposure_max_percent: Mapped[str] = mapped_column(Text, nullable=False)
    minimum_cash_percent: Mapped[str] = mapped_column(Text, nullable=False)
    margin_usage_max_percent: Mapped[str] = mapped_column(Text, nullable=False)
    max_account_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_price_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    is_system_default: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    risk_budget_max_percent: Mapped[str] = mapped_column(Text, nullable=False, default="2")
    theme_exposure_max_percent: Mapped[str] = mapped_column(Text, nullable=False, default="40")
    drawdown_max_percent: Mapped[str] = mapped_column(Text, nullable=False, default="20")
    liquidity_participation_max_percent: Mapped[str] = mapped_column(
        Text, nullable=False, default="10"
    )
    correlation_max_absolute: Mapped[str] = mapped_column(Text, nullable=False, default="0.85")
    event_blackout_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# --- Phase 3D versioned Trade Plans ---


class TradePlanIdentityRow(Base):
    __tablename__ = "trade_plan_identities"
    __table_args__ = (UniqueConstraint("case_id", name="uq_trade_plan_identities_case_id"),)

    plan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class TradePlanVersionRow(Base):
    __tablename__ = "trade_plan_versions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_trade_plan_versions_idempotency_key"),
        CheckConstraint("version >= 1", name="ck_trade_plan_versions_version"),
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')",
            name="ck_trade_plan_versions_status",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_trade_plan_versions_confirmed_by",
        ),
        CheckConstraint("schema_version = 1", name="ck_trade_plan_versions_schema"),
        Index("ix_trade_plan_versions_plan_version", "plan_id", "version"),
    )

    plan_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("trade_plan_identities.plan_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    reference_price: Mapped[str] = mapped_column(Text, nullable=False)
    reference_price_at: Mapped[str] = mapped_column(Text, nullable=False)
    target_position_percent: Mapped[str] = mapped_column(Text, nullable=False)
    max_position_percent: Mapped[str] = mapped_column(Text, nullable=False)
    risk_budget_percent: Mapped[str] = mapped_column(Text, nullable=False)
    stop_price: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TradePlanConditionRow(Base):
    __tablename__ = "trade_plan_conditions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 0", name="ck_trade_plan_conditions_position"),
        CheckConstraint(
            "phase IN ('ENTRY','SCALE','EXIT','INVALIDATION','REVIEW')",
            name="ck_trade_plan_conditions_phase",
        ),
        CheckConstraint(
            "mode IN ('MANUAL','MONITORABLE')",
            name="ck_trade_plan_conditions_mode",
        ),
    )

    plan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    condition_code: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str | None] = mapped_column(Text)
    metric_key: Mapped[str | None] = mapped_column(Text)
    comparator: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    instrument_id: Mapped[str | None] = mapped_column(Text)
    max_fact_age_seconds: Mapped[int | None] = mapped_column(Integer)
    event_after: Mapped[str | None] = mapped_column(Text)


# --- Phase 2C Monitoring ---
