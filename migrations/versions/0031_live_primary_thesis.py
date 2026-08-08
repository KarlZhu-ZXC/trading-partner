"""Enforce one live PRIMARY Thesis per Research Subject.

Revision ID: 0031_live_primary_thesis
Revises: 0030_generic_notification_outbox
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_live_primary_thesis"
down_revision: str | None = "0030_generic_notification_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    conflicts = connection.execute(
        sa.text(
            "SELECT case_id FROM theses "
            "WHERE role = 'primary' "
            "AND status IN ('active','strengthened','weakened') "
            "GROUP BY case_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if conflicts is not None:
        raise RuntimeError(
            "Cannot enforce live PRIMARY Thesis uniqueness: resolve duplicate "
            f"Research Subject {conflicts[0]} first"
        )
    op.create_index(
        "uq_theses_live_primary_per_subject",
        "theses",
        ["case_id"],
        unique=True,
        sqlite_where=sa.text(
            "role = 'primary' AND status IN ('active','strengthened','weakened')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_theses_live_primary_per_subject", table_name="theses")
