"""Allow durable post-market run summaries in the Monitor notification outbox.

Revision ID: 0025_monitor_run_notification_outbox
Revises: 0024_monitor_notification_outbox
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_monitor_run_notification_outbox"
down_revision: str | Sequence[str] | None = "0024_monitor_notification_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_v2_table(name: str) -> None:
    op.create_table(
        name,
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
            name=f"ck_{name}_channel",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','DELIVERED','DEAD_LETTER','EXPIRED')",
            name=f"ck_{name}_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=f"ck_{name}_attempt_count",
        ),
        sa.CheckConstraint(
            "(source_event_id IS NOT NULL AND source_run_id IS NULL) OR "
            "(source_event_id IS NULL AND source_run_id IS NOT NULL)",
            name=f"ck_{name}_source",
        ),
        sa.UniqueConstraint(
            "source_event_id",
            "channel",
            name=f"uq_{name}_event_channel",
        ),
        sa.UniqueConstraint(
            "source_run_id",
            "channel",
            name=f"uq_{name}_run_channel",
        ),
    )


def upgrade() -> None:
    temporary = "monitor_notification_outbox_v2"
    _create_v2_table(temporary)
    op.execute(
        f"""
        INSERT INTO {temporary} (
            notification_id, source_event_id, source_run_id, channel, title, body,
            status, attempt_count, next_attempt_at, created_at, last_attempt_at,
            delivered_at, provider_message_id, last_error_code
        )
        SELECT
            source_event_id, source_event_id, NULL, channel, title, body,
            status, attempt_count, next_attempt_at, created_at, last_attempt_at,
            delivered_at, provider_message_id, last_error_code
        FROM monitor_notification_outbox
        """
    )
    op.drop_index(
        "ix_monitor_notification_outbox_due",
        table_name="monitor_notification_outbox",
    )
    op.drop_table("monitor_notification_outbox")
    op.rename_table(temporary, "monitor_notification_outbox")
    op.create_index(
        "ix_monitor_notification_outbox_due",
        "monitor_notification_outbox",
        ["channel", "status", "next_attempt_at"],
    )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0025_monitor_run_notification_outbox',
            '2026-07-30T00:00:00+00:00',
            'Durable market-close run summaries in the Monitor notification outbox'
        )
        """
    )


def downgrade() -> None:
    temporary = "monitor_notification_outbox_v1"
    op.create_table(
        temporary,
        sa.Column(
            "source_event_id",
            sa.Text(),
            sa.ForeignKey("monitor_events.event_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channel", sa.Text(), primary_key=True),
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
    )
    op.execute(
        f"""
        INSERT INTO {temporary} (
            source_event_id, channel, title, body, status, attempt_count,
            next_attempt_at, created_at, last_attempt_at, delivered_at,
            provider_message_id, last_error_code
        )
        SELECT
            source_event_id, channel, title, body, status, attempt_count,
            next_attempt_at, created_at, last_attempt_at, delivered_at,
            provider_message_id, last_error_code
        FROM monitor_notification_outbox
        WHERE source_event_id IS NOT NULL
        """
    )
    op.drop_index(
        "ix_monitor_notification_outbox_due",
        table_name="monitor_notification_outbox",
    )
    op.drop_table("monitor_notification_outbox")
    op.rename_table(temporary, "monitor_notification_outbox")
    op.create_index(
        "ix_monitor_notification_outbox_due",
        "monitor_notification_outbox",
        ["channel", "status", "next_attempt_at"],
    )
    op.execute(
        "DELETE FROM schema_versions WHERE version = "
        "'0025_monitor_run_notification_outbox'"
    )
