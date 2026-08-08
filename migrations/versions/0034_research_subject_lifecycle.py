"""Restrict Research Subject status to lifecycle-only values.

Revision ID: 0034_research_subject_lifecycle
Revises: 0033_monitor_web_search_receipts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_research_subject_lifecycle"
down_revision: str | None = "0033_monitor_web_search_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    unsupported = connection.execute(
        sa.text(
            "SELECT case_id, status FROM investment_cases "
            "WHERE status NOT IN ('draft','active','archived') LIMIT 1"
        )
    ).first()
    if unsupported is not None:
        raise RuntimeError(
            "Cannot narrow Research Subject lifecycle status while unsupported "
            f"data exists: {unsupported[0]} has status {unsupported[1]}"
        )

    with op.batch_alter_table("investment_cases") as batch:
        batch.drop_constraint("ck_investment_cases_status", type_="check")
        batch.create_check_constraint(
            "ck_investment_cases_status",
            "status IN ('draft','active','archived')",
        )


def downgrade() -> None:
    with op.batch_alter_table("investment_cases") as batch:
        batch.drop_constraint("ck_investment_cases_status", type_="check")
        batch.create_check_constraint(
            "ck_investment_cases_status",
            "status IN ('draft','active','strengthened','weakened','invalidated','archived')",
        )
