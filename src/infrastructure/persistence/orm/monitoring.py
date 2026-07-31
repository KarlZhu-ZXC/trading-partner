"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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


class MonitorIdentityRow(Base):
    __tablename__ = "monitor_identities"

    monitor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorVersionRow(Base):
    __tablename__ = "monitor_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trade_plan_id", "trade_plan_version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key", name="uq_monitor_versions_idempotency_key"),
        CheckConstraint("version >= 1", name="ck_monitor_versions_version"),
        CheckConstraint(
            "cadence IN ("
            "'ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET','US_POST_MARKET','KR_POST_MARKET')",
            name="ck_monitor_versions_cadence",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','PAUSED','ARCHIVED')",
            name="ck_monitor_versions_status",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_monitor_versions_confirmed_by",
        ),
        CheckConstraint("schema_version IN (1,2)", name="ck_monitor_versions_schema"),
        CheckConstraint(
            "(cadence = 'INTERVAL' AND interval_minutes >= 60 "
            "AND interval_minutes % 60 = 0) OR "
            "(cadence != 'INTERVAL' AND interval_minutes IS NULL)",
            name="ck_monitor_versions_interval",
        ),
        Index("ix_monitor_versions_monitor_version", "monitor_id", "version"),
    )

    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("investment_cases.case_id", ondelete="RESTRICT")
    )
    primary_instrument_id: Mapped[str | None] = mapped_column(Text)
    trade_plan_id: Mapped[str | None] = mapped_column(Text)
    trade_plan_version: Mapped[int | None] = mapped_column(Integer)
    cadence: Mapped[str] = mapped_column(Text, nullable=False)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MonitorRuleStateRow(Base):
    __tablename__ = "monitor_rule_states"
    __table_args__ = (
        CheckConstraint("monitor_version >= 1", name="ck_monitor_rule_states_version"),
        CheckConstraint(
            "state IN ('QUIET','TRIGGERED','NOT_EVALUATED')",
            name="ck_monitor_rule_states_state",
        ),
    )

    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_code: Mapped[str] = mapped_column(Text, primary_key=True)
    monitor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[str | None] = mapped_column(Text)
    fact_as_of: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorEventRow(Base):
    __tablename__ = "monitor_events"
    __table_args__ = (
        CheckConstraint("monitor_version >= 1", name="ck_monitor_events_version"),
        CheckConstraint(
            "event_type IN ('TRIGGERED','RECOVERED','NOT_EVALUATED')",
            name="ck_monitor_events_type",
        ),
        CheckConstraint("severity IN ('INFO','MEDIUM','HIGH')", name="ck_monitor_events_severity"),
        Index("ix_monitor_events_monitor_created", "monitor_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    monitor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[str | None] = mapped_column(Text)
    threshold_value: Mapped[str | None] = mapped_column(Text)
    fact_as_of: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorEventResolutionRow(Base):
    __tablename__ = "monitor_event_resolutions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_monitor_event_resolutions_idempotency_key"),
        CheckConstraint(
            "action IN ('ACKNOWLEDGE','RESOLVE')",
            name="ck_monitor_event_resolutions_action",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_monitor_event_resolutions_confirmed_by",
        ),
        Index("ix_monitor_event_resolutions_event", "event_id", "created_at"),
    )

    resolution_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        Text, ForeignKey("monitor_events.event_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorRunRow(Base):
    __tablename__ = "monitor_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')",
            name="ck_monitor_runs_status",
        ),
        CheckConstraint(
            "cadence IS NULL OR cadence IN ("
            "'ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET','US_POST_MARKET','KR_POST_MARKET')",
            name="ck_monitor_runs_cadence",
        ),
        CheckConstraint("completed_at >= started_at", name="ck_monitor_runs_time_order"),
        Index("ix_monitor_runs_completed", "completed_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    requested_monitor_ids: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False
    )
    selected_monitor_ids: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    cadence: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    monitors_evaluated: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_evaluated: Mapped[int] = mapped_column(Integer, nullable=False)
    events_created: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    error_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    observation_history_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class MonitorRunObservationRow(Base):
    __tablename__ = "monitor_run_observations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('QUIET','TRIGGERED','NOT_EVALUATED')",
            name="ck_monitor_run_observations_state",
        ),
        CheckConstraint(
            "severity IN ('INFO','MEDIUM','HIGH')",
            name="ck_monitor_run_observations_severity",
        ),
        Index(
            "ix_monitor_run_observations_monitor_run",
            "monitor_id",
            "run_id",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    rule_code: Mapped[str] = mapped_column(Text, primary_key=True)
    monitor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[str | None] = mapped_column(Text)
    threshold_value: Mapped[str | None] = mapped_column(Text)
    distance_value: Mapped[str | None] = mapped_column(Text)
    distance_percent: Mapped[str | None] = mapped_column(Text)
    fact_as_of: Mapped[str | None] = mapped_column(Text)
    fact_age_seconds: Mapped[int | None] = mapped_column(Integer)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    error_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorNotificationOutboxRow(Base):
    __tablename__ = "monitor_notification_outbox"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('TELEGRAM')",
            name="ck_monitor_notification_outbox_channel",
        ),
        CheckConstraint(
            "status IN ('PENDING','DELIVERED','DEAD_LETTER','EXPIRED')",
            name="ck_monitor_notification_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_monitor_notification_outbox_attempt_count",
        ),
        CheckConstraint(
            "(source_event_id IS NOT NULL AND source_run_id IS NULL) OR "
            "(source_event_id IS NULL AND source_run_id IS NOT NULL)",
            name="ck_monitor_notification_outbox_source",
        ),
        UniqueConstraint(
            "source_event_id",
            "channel",
            name="uq_monitor_notification_outbox_event_channel",
        ),
        UniqueConstraint(
            "source_run_id",
            "channel",
            name="uq_monitor_notification_outbox_run_channel",
        ),
        Index(
            "ix_monitor_notification_outbox_due",
            "channel",
            "status",
            "next_attempt_at",
        ),
    )

    notification_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_event_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("monitor_events.event_id", ondelete="CASCADE"),
    )
    source_run_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("monitor_runs.run_id", ondelete="CASCADE"),
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_attempt_at: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(Text)


# --- Phase 1K persistent Challenge Reviews ---
