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
