"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.common import JsonStringTuple


class IndustryMetricObservationRow(Base):
    __tablename__ = "industry_metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "cycle",
            "dataset_code",
            "metric_code",
            "period_end",
            "published_at",
            name="uq_industry_metric_vintage",
        ),
        Index(
            "ix_industry_metric_series",
            "cycle",
            "metric_code",
            "period_end",
        ),
        Index("ix_industry_metric_publication", "published_at"),
    )

    observation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    cycle: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_code: Mapped[str] = mapped_column(Text, nullable=False)
    metric_code: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    geography: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[str] = mapped_column(Text, nullable=False)
    period_end: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    measurement_basis: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_estimated: Mapped[int] = mapped_column(Integer, nullable=False)
    methodology_version: Mapped[str] = mapped_column(Text, nullable=False)
    methodology_break: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Scheduled operational synchronization receipts ---


class PostMarketSyncRunRow(Base):
    __tablename__ = "post_market_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "market_session_date",
            name="uq_post_market_sync_session_date",
        ),
        CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')",
            name="ck_post_market_sync_status",
        ),
        CheckConstraint(
            "portfolio_status IN ('SUCCEEDED','FAILED')",
            name="ck_post_market_sync_portfolio_status",
        ),
        CheckConstraint(
            "watchlist_status IN ('SUCCEEDED','FAILED')",
            name="ck_post_market_sync_watchlist_status",
        ),
        CheckConstraint("completed_at >= started_at", name="ck_post_market_sync_time_order"),
        CheckConstraint("attempt_count >= 1", name="ck_post_market_sync_attempt_count"),
        CheckConstraint(
            "watchlist_groups_synced IS NULL OR watchlist_groups_synced >= 0",
            name="ck_post_market_sync_group_count",
        ),
        CheckConstraint(
            "watchlist_membership_relations_synced IS NULL"
            " OR watchlist_membership_relations_synced >= 0",
            name="ck_post_market_sync_membership_count",
        ),
        Index("ix_post_market_sync_completed_at", "completed_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    market_session_date: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_status: Mapped[str] = mapped_column(Text, nullable=False)
    watchlist_status: Mapped[str] = mapped_column(Text, nullable=False)
    account_snapshot_ids: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    watchlist_groups_synced: Mapped[int | None] = mapped_column(Integer)
    watchlist_membership_relations_synced: Mapped[int | None] = mapped_column(Integer)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    error_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)


class BrokerOrderIntentRow(Base):
    """Short-lived order preview plus its durable execution receipt."""

    __tablename__ = "broker_order_intents"
    __table_args__ = (
        UniqueConstraint("preview_idempotency_key", name="uq_broker_order_preview_idempotency"),
        UniqueConstraint("submit_idempotency_key", name="uq_broker_order_submit_idempotency"),
        CheckConstraint("quantity > 0", name="ck_broker_order_quantity"),
        CheckConstraint(
            "status IN ('PREVIEWED','SUBMITTING','SUBMITTED','REJECTED','UNKNOWN',"
            "'CANCEL_REQUESTED','CANCELLED')",
            name="ck_broker_order_status",
        ),
        Index("ix_broker_order_account_created", "account_ref", "created_at"),
    )

    order_intent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    session: Mapped[str] = mapped_column(Text, nullable=False)
    duration: Mapped[str] = mapped_column(Text, nullable=False)
    limit_price: Mapped[str | None] = mapped_column(Text)
    stop_price: Mapped[str | None] = mapped_column(Text)
    trail_offset: Mapped[str | None] = mapped_column(Text)
    trail_type: Mapped[str | None] = mapped_column(Text)
    limit_offset: Mapped[str | None] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    order_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    preview_idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    account_observed_at: Mapped[str] = mapped_column(Text, nullable=False)
    cash_balance: Mapped[str | None] = mapped_column(Text)
    margin_balance: Mapped[str | None] = mapped_column(Text)
    open_buy_order_reserve: Mapped[str | None] = mapped_column(Text)
    position_quantity: Mapped[str] = mapped_column(Text, nullable=False)
    quote_at: Mapped[str | None] = mapped_column(Text)
    quote_source: Mapped[str | None] = mapped_column(Text)
    quote_price: Mapped[str | None] = mapped_column(Text)
    estimated_notional: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submit_idempotency_key: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(Text)
    submitted_via: Mapped[str | None] = mapped_column(Text)
    authorization_note: Mapped[str | None] = mapped_column(Text)
    broker_order_id: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(Text)
    rejection_code: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
