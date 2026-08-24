"""Add structured Phase 4A fields to Decision Records.

Revision ID: 0055_structured_decision_records
Revises: 0054_agent_turn_failure_metadata
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_structured_decision_records"
down_revision: str | None = "0054_agent_turn_failure_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable fields so all Phase 1C Decision rows remain readable."""

    with op.batch_alter_table("decision_records") as batch:
        batch.add_column(sa.Column("strategy_code", sa.Text(), nullable=True))
        batch.add_column(sa.Column("strategy_version", sa.Text(), nullable=True))
        batch.add_column(sa.Column("scenario", sa.Text(), nullable=True))
        batch.add_column(sa.Column("trade_plan_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("trade_plan_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("review_due_at", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "scenario",
            "scenario IS NULL OR scenario IN ('UPSIDE','SIDEWAYS','PULLBACK','INVALIDATION')",
        )
        batch.create_check_constraint(
            "trade_plan_pair",
            "(trade_plan_id IS NULL) = (trade_plan_version IS NULL)",
        )
        batch.create_check_constraint(
            "trade_plan_version",
            "trade_plan_version IS NULL OR trade_plan_version >= 1",
        )
def downgrade() -> None:
    with op.batch_alter_table("decision_records") as batch:
        batch.drop_constraint("trade_plan_version", type_="check")
        batch.drop_constraint("trade_plan_pair", type_="check")
        batch.drop_constraint("scenario", type_="check")
        batch.drop_column("review_due_at")
        batch.drop_column("trade_plan_version")
        batch.drop_column("trade_plan_id")
        batch.drop_column("scenario")
        batch.drop_column("strategy_version")
        batch.drop_column("strategy_code")
