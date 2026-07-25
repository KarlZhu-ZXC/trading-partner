"""Add an optional validity cutoff to versioned Monitor definitions.

Revision ID: 0016_monitor_valid_until
Revises: 0015_phase3b_industry_metrics
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_monitor_valid_until"
down_revision: str | Sequence[str] | None = "0015_phase3b_industry_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "monitor_versions",
        sa.Column("valid_until", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_monitor_versions_valid_until",
        "monitor_versions",
        ["valid_until"],
    )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0016_monitor_valid_until',
            '2026-07-25T00:00:00+00:00',
            'Optional Monitor validity cutoff without deleting historical events'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0016_monitor_valid_until'")
    op.drop_index("ix_monitor_versions_valid_until", table_name="monitor_versions")
    op.drop_column("monitor_versions", "valid_until")
