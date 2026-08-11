"""Add append-only Catalyst Agenda identities and versions.

Revision ID: 0040_catalyst_agenda
Revises: 0039_judgment_scorecard
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_catalyst_agenda"
down_revision: str | None = "0039_judgment_scorecard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalyst_agenda_items",
        sa.Column("agenda_item_id", sa.Text(), nullable=False),
        sa.Column("logical_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("agenda_item_id"),
        sa.UniqueConstraint("logical_key", name="uq_catalyst_agenda_logical_key"),
    )
    op.create_index(
        "ix_catalyst_agenda_logical_key",
        "catalyst_agenda_items",
        ["logical_key"],
    )
    op.create_table(
        "catalyst_agenda_versions",
        sa.Column("agenda_item_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_version", sa.Integer(), nullable=True),
        sa.Column("instrument_id", sa.Text(), nullable=True),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("fiscal_period", sa.Text(), nullable=True),
        sa.Column("upstream_event_key", sa.Text(), nullable=True),
        sa.Column("window_start", sa.Text(), nullable=True),
        sa.Column("window_end", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("date_certainty", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_vendor", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("source_visible_at", sa.Text(), nullable=False),
        sa.Column("last_verified_at", sa.Text(), nullable=False),
        sa.Column("expected_question", sa.Text(), nullable=True),
        sa.Column("linked_event_id", sa.Text(), nullable=True),
        sa.Column("linked_report_id", sa.Text(), nullable=True),
        sa.Column("revision_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("historical_vintage", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("execution_effect", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("version >= 1", name="catalyst_agenda_positive_version"),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_version IS NULL) OR "
            "(version > 1 AND supersedes_version = version - 1)",
            name="catalyst_agenda_supersedes",
        ),
        sa.CheckConstraint(
            "instrument_id IS NOT NULL OR case_id IS NOT NULL "
            "OR kind IN ('MACRO_RELEASE','POLICY')",
            name="catalyst_agenda_scope_required",
        ),
        sa.CheckConstraint(
            "kind IN ('EARNINGS','FILING','DIVIDEND','CORPORATE_ACTION',"
            "'INVESTOR_EVENT','MACRO_RELEASE','POLICY','INDUSTRY','USER_DEFINED')",
            name="catalyst_agenda_kind",
        ),
        sa.CheckConstraint(
            "date_certainty IN ('CONFIRMED','ESTIMATED','RANGE','UNKNOWN')",
            name="catalyst_agenda_certainty",
        ),
        sa.CheckConstraint(
            "status IN ('UPCOMING','OCCURRED','CANCELLED')",
            name="catalyst_agenda_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('USER_CONFIRMED','PROVIDER')",
            name="catalyst_agenda_source_type",
        ),
        sa.CheckConstraint(
            "(source_type = 'USER_CONFIRMED' AND confirmed_by IN ('user','external_agent')) "
            "OR (source_type = 'PROVIDER' AND confirmed_by = 'system' "
            "AND authorization_note LIKE 'provider_sync:%' "
            "AND length(authorization_note) > 14)",
            name="catalyst_agenda_confirmer",
        ),
        sa.CheckConstraint(
            "historical_vintage IN (0, 1)",
            name="catalyst_agenda_historical_vintage",
        ),
        sa.CheckConstraint("schema_version = 1", name="catalyst_agenda_schema"),
        sa.CheckConstraint("execution_effect = 0", name="catalyst_agenda_no_execution"),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.instrument_id"],
            name="fk_catalyst_agenda_instrument",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agenda_item_id"],
            ["catalyst_agenda_items.agenda_item_id"],
            name="fk_catalyst_agenda_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_catalyst_agenda_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_event_id"],
            ["research_events.event_id"],
            name="fk_catalyst_agenda_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_report_id"],
            ["research_reports.report_id"],
            name="fk_catalyst_agenda_report",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("agenda_item_id", "version"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_catalyst_agenda_idempotency"
        ),
    )
    op.create_index(
        "ix_catalyst_agenda_window",
        "catalyst_agenda_versions",
        ["window_start", "window_end"],
    )
    op.create_index(
        "ix_catalyst_agenda_instrument",
        "catalyst_agenda_versions",
        ["instrument_id", "recorded_at"],
    )
    op.create_index(
        "ix_catalyst_agenda_subject",
        "catalyst_agenda_versions",
        ["case_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalyst_agenda_subject", table_name="catalyst_agenda_versions")
    op.drop_index(
        "ix_catalyst_agenda_instrument", table_name="catalyst_agenda_versions"
    )
    op.drop_index(
        "ix_catalyst_agenda_window", table_name="catalyst_agenda_versions"
    )
    op.drop_table("catalyst_agenda_versions")
    op.drop_index("ix_catalyst_agenda_logical_key", table_name="catalyst_agenda_items")
    op.drop_table("catalyst_agenda_items")
