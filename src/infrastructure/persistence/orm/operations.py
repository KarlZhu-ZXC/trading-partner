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


class OperationalJobRunRow(Base):
    __tablename__ = "operational_job_runs"
    __table_args__ = (
        UniqueConstraint("job_name", "idempotency_key", name="uq_operational_job_key"),
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','SKIPPED','FAILED','INTERRUPTED')",
            name="ck_operational_job_status",
        ),
        CheckConstraint("attempt >= 1", name="ck_operational_job_attempt"),
        CheckConstraint("version >= 1", name="ck_operational_job_version"),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_operational_job_terminal_time",
        ),
        Index("ix_operational_job_status_lease", "status", "lease_expires_at"),
        Index("ix_operational_job_updated", "updated_at"),
    )

    job_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner_hash: Mapped[str] = mapped_column(Text, nullable=False)
    lease_expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    heartbeat_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    result_code: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


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
        CheckConstraint(
            "(trade_plan_id IS NULL) = (trade_plan_version IS NULL)",
            name="ck_broker_order_intents_plan_pair",
        ),
        CheckConstraint(
            "(decision_id IS NULL AND trade_plan_id IS NULL) OR subject_id IS NOT NULL",
            name="ck_broker_order_intents_research_subject",
        ),
        ForeignKeyConstraint(
            ["subject_id"], ["investment_cases.case_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["decision_id"], ["decision_records.decision_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["trade_plan_id", "trade_plan_version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="RESTRICT",
        ),
        Index("ix_broker_order_account_created", "account_ref", "created_at"),
        Index("ix_broker_order_intents_subject_created", "subject_id", "created_at"),
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
    subject_id: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[str | None] = mapped_column(Text)
    trade_plan_id: Mapped[str | None] = mapped_column(Text)
    trade_plan_version: Mapped[int | None] = mapped_column(Integer)
    submit_idempotency_key: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(Text)
    submitted_via: Mapped[str | None] = mapped_column(Text)
    authorization_note: Mapped[str | None] = mapped_column(Text)
    broker_order_id: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(Text)
    rejection_code: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class TradeCycleOverrideRevisionRow(Base):
    __tablename__ = "trade_cycle_override_revisions"
    __table_args__ = (
        UniqueConstraint("root_cycle_id", "version", name="uq_trade_cycle_override_root_version"),
        UniqueConstraint("idempotency_key", name="uq_trade_cycle_override_idempotency"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("operation IN ('SPLIT','MERGE','RELINK')", name="operation"),
        CheckConstraint("actor IN ('user','external_agent')", name="actor"),
        CheckConstraint(
            "expected_version IS NULL OR expected_version >= 0", name="expected_version"
        ),
        Index("ix_trade_cycle_override_root_created", "root_cycle_id", "created_at"),
    )

    override_id: Mapped[str] = mapped_column(Text, primary_key=True)
    root_cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    activity_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    split_groups_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_cycle_id: Mapped[str | None] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_note: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expected_version: Mapped[int | None] = mapped_column(Integer)


class BehaviorReviewRunRow(Base):
    __tablename__ = "behavior_review_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_behavior_review_runs_idempotency"),
        CheckConstraint(
            "period_kind IN ('WEEKLY','MONTHLY','QUARTERLY')",
            name="behavior_review_runs_period_kind",
        ),
        CheckConstraint(
            "status IN ('COMPLETE','INCOMPLETE','UNAVAILABLE')",
            name="behavior_review_runs_status",
        ),
        CheckConstraint(
            "source_read_complete IN (0,1)", name="behavior_review_runs_source_complete"
        ),
        CheckConstraint("schema_version = 1", name="behavior_review_runs_schema"),
        CheckConstraint("execution_effect = 0", name="behavior_review_runs_no_execution"),
        Index("ix_behavior_review_runs_period", "period_kind", "period_start", "period_end"),
        Index("ix_behavior_review_runs_cohort_generated", "cohort_key", "generated_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    period_kind: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[str] = mapped_column(Text, nullable=False)
    period_end: Mapped[str] = mapped_column(Text, nullable=False)
    cohort_key: Mapped[str] = mapped_column(Text, nullable=False)
    cohort_json: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_read_complete: Mapped[int] = mapped_column(Integer, nullable=False)
    source_error_code: Mapped[str | None] = mapped_column(Text)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_effect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BehaviorActionObservationRow(Base):
    __tablename__ = "behavior_action_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "stable_key", name="uq_behavior_action_run_key"),
        CheckConstraint(
            "status IN ('NEW','PERSISTENT','RESOLVED','RECURRED')",
            name="behavior_action_status",
        ),
        CheckConstraint("occurrence_count >= 1", name="behavior_action_occurrence"),
        Index("ix_behavior_action_stable_observed", "stable_key", "observed_at"),
        Index("ix_behavior_action_run_status", "run_id", "status"),
    )

    observation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("behavior_review_runs.run_id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    action_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    period_key: Mapped[str] = mapped_column(Text, nullable=False)
    cohort_key: Mapped[str] = mapped_column(Text, nullable=False)
    review_item_source_keys_json: Mapped[str] = mapped_column(Text, nullable=False)
    retro_review_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    decision_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[str] = mapped_column(Text, nullable=False)
    previous_observation_id: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[str | None] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class JournalActivationRow(Base):
    __tablename__ = "journal_activations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_journal_activations_idempotency_key"),
        CheckConstraint(
            "activation_id = 'journal_activation'", name="ck_journal_activations_singleton"
        ),
        Index("ix_journal_activations_at", "journal_activation_at"),
    )

    activation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    journal_activation_at: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)


class DailyEquitySnapshotRow(Base):
    __tablename__ = "daily_equity_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id", "algorithm_version", name="uq_daily_equity_source_algorithm"
        ),
        CheckConstraint(
            "valuation_basis = 'BROKER_NET_ASSETS'", name="ck_daily_equity_valuation_basis"
        ),
        CheckConstraint(
            "coverage_status IN ('COMPLETE','PARTIAL','INCOMPLETE','UNAVAILABLE')",
            name="ck_daily_equity_coverage_status",
        ),
        CheckConstraint(
            "quality_status IN ('COMPLETE','PARTIAL','INCOMPLETE','UNAVAILABLE')",
            name="ck_daily_equity_quality_status",
        ),
        Index(
            "ix_daily_equity_account_currency_valuation",
            "account_ref",
            "currency",
            "valuation_at",
        ),
        Index("ix_daily_equity_source_snapshot", "source_snapshot_id", "algorithm_version"),
    )

    daily_equity_snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    valuation_at: Mapped[str] = mapped_column(Text, nullable=False)
    market_session_date: Mapped[str] = mapped_column(Text, nullable=False)
    equity_value: Mapped[str | None] = mapped_column(Text)
    cash_value: Mapped[str | None] = mapped_column(Text)
    gross_position_value: Mapped[str | None] = mapped_column(Text)
    net_external_cash_flow_since_previous: Mapped[str | None] = mapped_column(Text)
    valuation_basis: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        Text, ForeignKey("account_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    source_fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    journal_activation_at: Mapped[str | None] = mapped_column(Text)
    coverage_status: Mapped[str] = mapped_column(Text, nullable=False)
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)
    materialized_at: Mapped[str] = mapped_column(Text, nullable=False)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
