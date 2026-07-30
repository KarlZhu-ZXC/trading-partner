"""Durable Monitor notification outbox.

Revision ID: 0024_monitor_notification_outbox
Revises: 0023_monitoring_hub_v3
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_monitor_notification_outbox"
down_revision: str | Sequence[str] | None = "0023_monitoring_hub_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_notification_outbox",
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
    )
    op.create_index(
        "ix_monitor_notification_outbox_due",
        "monitor_notification_outbox",
        ["channel", "status", "next_attempt_at"],
    )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0024_monitor_notification_outbox',
            '2026-07-29T00:00:00+00:00',
            'Durable Monitor notification outbox and delivery state'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM schema_versions WHERE version = "
        "'0024_monitor_notification_outbox'"
    )
    op.drop_index(
        "ix_monitor_notification_outbox_due",
        table_name="monitor_notification_outbox",
    )
    op.drop_table("monitor_notification_outbox")
