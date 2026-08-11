"""Catalyst Agenda identity and append-only version rows."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class CatalystAgendaIdentityRow(Base):
    __tablename__ = "catalyst_agenda_items"
    __table_args__ = (
        UniqueConstraint("logical_key", name="uq_catalyst_agenda_logical_key"),
        Index("ix_catalyst_agenda_logical_key", "logical_key"),
    )

    agenda_item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    logical_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class CatalystAgendaVersionRow(Base):
    __tablename__ = "catalyst_agenda_versions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_catalyst_agenda_idempotency"),
        CheckConstraint("version >= 1", name="catalyst_agenda_positive_version"),
        CheckConstraint(
            "(version = 1 AND supersedes_version IS NULL) OR "
            "(version > 1 AND supersedes_version = version - 1)",
            name="catalyst_agenda_supersedes",
        ),
        CheckConstraint(
            "instrument_id IS NOT NULL OR case_id IS NOT NULL "
            "OR kind IN ('MACRO_RELEASE','POLICY')",
            name="catalyst_agenda_scope_required",
        ),
        CheckConstraint(
            "kind IN ('EARNINGS','FILING','DIVIDEND','CORPORATE_ACTION',"
            "'INVESTOR_EVENT','MACRO_RELEASE','POLICY','INDUSTRY','USER_DEFINED')",
            name="catalyst_agenda_kind",
        ),
        CheckConstraint(
            "date_certainty IN ('CONFIRMED','ESTIMATED','RANGE','UNKNOWN')",
            name="catalyst_agenda_certainty",
        ),
        CheckConstraint(
            "status IN ('UPCOMING','OCCURRED','CANCELLED')",
            name="catalyst_agenda_status",
        ),
        CheckConstraint(
            "source_type IN ('USER_CONFIRMED','PROVIDER')",
            name="catalyst_agenda_source_type",
        ),
        CheckConstraint(
            "(source_type = 'USER_CONFIRMED' AND confirmed_by IN ('user','external_agent')) "
            "OR (source_type = 'PROVIDER' AND confirmed_by = 'system' "
            "AND authorization_note LIKE 'provider_sync:%' "
            "AND length(authorization_note) > 14)",
            name="catalyst_agenda_confirmer",
        ),
        CheckConstraint(
            "schema_version IN (1, 2)",
            name="catalyst_agenda_schema",
        ),
        CheckConstraint(
            "schema_version = 1 OR status != 'OCCURRED' OR "
            "((linked_event_id IS NOT NULL OR linked_report_id IS NOT NULL "
            "OR linked_evidence_id IS NOT NULL) "
            "AND outcome_occurred_at IS NOT NULL "
            "AND length(trim(outcome_note)) > 0)",
            name="catalyst_agenda_outcome_contract",
        ),
        CheckConstraint("execution_effect = 0", name="catalyst_agenda_no_execution"),
        CheckConstraint(
            "historical_vintage IN (0, 1)",
            name="catalyst_agenda_historical_vintage",
        ),
        Index("ix_catalyst_agenda_window", "window_start", "window_end"),
        Index("ix_catalyst_agenda_instrument", "instrument_id", "recorded_at"),
        Index("ix_catalyst_agenda_subject", "case_id", "recorded_at"),
    )

    agenda_item_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("catalyst_agenda_items.agenda_item_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    supersedes_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instrument_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject_id: Mapped[str | None] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    fiscal_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_event_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    window_start: Mapped[str | None] = mapped_column(Text, nullable=True)
    window_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    date_certainty: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_vendor: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_visible_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified_at: Mapped[str] = mapped_column(Text, nullable=False)
    expected_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_event_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("research_events.event_id", ondelete="RESTRICT"),
        nullable=True,
    )
    linked_report_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("research_reports.report_id", ondelete="RESTRICT"),
        nullable=True,
    )
    linked_evidence_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("research_evidence.evidence_id", ondelete="RESTRICT"),
        nullable=True,
    )
    outcome_occurred_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_note: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    historical_vintage: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_effect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CatalystAgendaSyncReceiptRow(Base):
    __tablename__ = "catalyst_agenda_sync_receipts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_catalyst_agenda_sync_idempotency"),
        CheckConstraint(
            "status IN ('COMPLETE','PARTIAL','FAILED')",
            name="catalyst_agenda_sync_status",
        ),
        CheckConstraint("schema_version = 1", name="catalyst_agenda_sync_schema"),
        CheckConstraint("execution_effect = 0", name="catalyst_agenda_sync_no_execution"),
        Index("ix_catalyst_agenda_sync_completed", "completed_at"),
    )

    receipt_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[str] = mapped_column(Text, nullable=False)
    window_end: Mapped[str] = mapped_column(Text, nullable=False)
    scope_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_instrument_count: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded_scope_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_scope_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    appended_count: Mapped[int] = mapped_column(Integer, nullable=False)
    revised_count: Mapped[int] = mapped_column(Integer, nullable=False)
    date_drift_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_results_json: Mapped[str] = mapped_column(Text, nullable=False)
    limitation_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_effect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
