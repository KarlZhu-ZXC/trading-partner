"""Add exact optional Research links to durable Broker order intents.

Revision ID: 0061_broker_order_research_links
Revises: 0060_daily_equity_projection
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_broker_order_research_links"
down_revision: str | None = "0060_daily_equity_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("broker_order_intents") as batch:
        batch.add_column(sa.Column("subject_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("decision_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("trade_plan_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("trade_plan_version", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_broker_order_intents_subject",
            "investment_cases",
            ["subject_id"],
            ["case_id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_broker_order_intents_decision",
            "decision_records",
            ["decision_id"],
            ["decision_id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_broker_order_intents_trade_plan",
            "trade_plan_versions",
            ["trade_plan_id", "trade_plan_version"],
            ["plan_id", "version"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_broker_order_intents_plan_pair",
            "(trade_plan_id IS NULL) = (trade_plan_version IS NULL)",
        )
        batch.create_check_constraint(
            "ck_broker_order_intents_research_subject",
            "(decision_id IS NULL AND trade_plan_id IS NULL) OR subject_id IS NOT NULL",
        )
        batch.create_index(
            "ix_broker_order_intents_subject_created",
            ["subject_id", "created_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("broker_order_intents") as batch:
        batch.drop_index("ix_broker_order_intents_subject_created")
        batch.drop_constraint("ck_broker_order_intents_research_subject", type_="check")
        batch.drop_constraint("ck_broker_order_intents_plan_pair", type_="check")
        batch.drop_constraint("fk_broker_order_intents_trade_plan", type_="foreignkey")
        batch.drop_constraint("fk_broker_order_intents_decision", type_="foreignkey")
        batch.drop_constraint("fk_broker_order_intents_subject", type_="foreignkey")
        batch.drop_column("trade_plan_version")
        batch.drop_column("trade_plan_id")
        batch.drop_column("decision_id")
        batch.drop_column("subject_id")
