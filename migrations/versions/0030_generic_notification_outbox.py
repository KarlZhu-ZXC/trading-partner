"""Generalize the Monitor notification outbox to all deterministic producers.

Revision ID: 0030_generic_notification_outbox
Revises: 0029_dukascopy_light_oil_cfd
Create Date: 2026-08-06

The canonical table is ``notification_outbox``. Existing 0025 Monitor rows are
copied without changing their IDs, timestamps, delivery state, or payload. The
old physical table is dropped after the copy and reconstructed on downgrade;
runtime writes target only the generic table.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0030_generic_notification_outbox"
down_revision: str | Sequence[str] | None = "0029_dukascopy_light_oil_cfd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("notification_id", sa.Text(), primary_key=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_attempt_at", sa.Text()),
        sa.Column("delivered_at", sa.Text()),
        sa.Column("provider_message_id", sa.Text()),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("confirmed_by", sa.Text()),
        sa.Column("authorization_note", sa.Text()),
        sa.Column("expires_at", sa.Text()),
        sa.CheckConstraint(
            "source_type IN ('MONITOR_EVENT','MONITOR_RUN','MANUAL','SYSTEM')",
            name="ck_notification_outbox_source_type",
        ),
        sa.CheckConstraint(
            "source_type <> 'MANUAL' OR ("
            "idempotency_key IS NOT NULL AND length(trim(idempotency_key)) > 0 AND "
            "source_id = idempotency_key AND "
            "confirmed_by IN ('user','external_agent') AND "
            "authorization_note IS NOT NULL AND length(trim(authorization_note)) > 0 AND "
            "expires_at IS NOT NULL AND length(trim(expires_at)) > 0"
            ")",
            name="ck_notification_outbox_manual_metadata",
        ),
        sa.CheckConstraint(
            "source_type = 'MANUAL' OR (confirmed_by IS NULL AND authorization_note IS NULL)",
            name="ck_notification_outbox_non_manual_authorization",
        ),
        sa.CheckConstraint(
            "channel IN ('TELEGRAM')",
            name="ck_notification_outbox_channel",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','DELIVERED','DEAD_LETTER','EXPIRED')",
            name="ck_notification_outbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_notification_outbox_attempt_count",
        ),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "channel",
            name="uq_notification_outbox_source_channel",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_notification_outbox_idempotency_key",
        ),
    )
    op.create_index(
        "ix_notification_outbox_due",
        "notification_outbox",
        ["channel", "status", "next_attempt_at"],
    )
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "monitor_notification_outbox" in tables:
        op.execute(
            sa.text(
                """
                INSERT INTO notification_outbox (
                    notification_id, source_type, source_id, channel, title, body,
                    status, attempt_count, next_attempt_at, created_at,
                    last_attempt_at, delivered_at, provider_message_id,
                    last_error_code
                )
                SELECT
                    notification_id,
                    CASE
                        WHEN source_event_id IS NOT NULL THEN 'MONITOR_EVENT'
                        ELSE 'MONITOR_RUN'
                    END,
                    CASE
                        WHEN source_event_id IS NOT NULL THEN source_event_id
                        ELSE source_run_id
                    END,
                    channel, title, body, status, attempt_count, next_attempt_at,
                    created_at, last_attempt_at, delivered_at,
                    provider_message_id, last_error_code
                FROM monitor_notification_outbox
                """
            )
        )
        op.drop_index(
            "ix_monitor_notification_outbox_due",
            table_name="monitor_notification_outbox",
        )
        op.drop_table("monitor_notification_outbox")
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=revision,
            applied_at=datetime.now(UTC).isoformat(),
            description="Generic deterministic notification outbox",
        )
    )


def downgrade() -> None:
    op.create_table(
        "monitor_notification_outbox",
        sa.Column("notification_id", sa.Text(), primary_key=True),
        sa.Column(
            "source_event_id",
            sa.Text(),
            sa.ForeignKey("monitor_events.event_id", ondelete="CASCADE"),
        ),
        sa.Column(
            "source_run_id",
            sa.Text(),
            sa.ForeignKey("monitor_runs.run_id", ondelete="CASCADE"),
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_attempt_at", sa.Text()),
        sa.Column("delivered_at", sa.Text()),
        sa.Column("provider_message_id", sa.Text()),
        sa.Column("last_error_code", sa.Text()),
        sa.CheckConstraint(
            "channel IN ('TELEGRAM')",
            name="ck_monitor_notification_outbox_channel",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','DELIVERED','DEAD_LETTER','EXPIRED')",
            name="ck_monitor_notification_outbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_monitor_notification_outbox_attempt_count",
        ),
        sa.CheckConstraint(
            "(source_event_id IS NOT NULL AND source_run_id IS NULL) OR "
            "(source_event_id IS NULL AND source_run_id IS NOT NULL)",
            name="ck_monitor_notification_outbox_source",
        ),
        sa.UniqueConstraint(
            "source_event_id",
            "channel",
            name="uq_monitor_notification_outbox_event_channel",
        ),
        sa.UniqueConstraint(
            "source_run_id",
            "channel",
            name="uq_monitor_notification_outbox_run_channel",
        ),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO monitor_notification_outbox (
                notification_id, source_event_id, source_run_id, channel, title, body,
                status, attempt_count, next_attempt_at, created_at, last_attempt_at,
                delivered_at, provider_message_id, last_error_code
            )
            SELECT
                notification_id,
                CASE WHEN source_type = 'MONITOR_EVENT' THEN source_id ELSE NULL END,
                CASE WHEN source_type = 'MONITOR_RUN' THEN source_id ELSE NULL END,
                channel, title, body, status, attempt_count, next_attempt_at,
                created_at, last_attempt_at, delivered_at, provider_message_id,
                last_error_code
            FROM notification_outbox
            WHERE source_type IN ('MONITOR_EVENT','MONITOR_RUN')
            """
        )
    )
    op.create_index(
        "ix_monitor_notification_outbox_due",
        "monitor_notification_outbox",
        ["channel", "status", "next_attempt_at"],
    )
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=revision
        )
    )
    op.drop_index("ix_notification_outbox_due", table_name="notification_outbox")
    op.drop_table("notification_outbox")
