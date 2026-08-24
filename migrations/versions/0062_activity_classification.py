"""Add explicit user classification to activity annotation revisions.

Revision ID: 0062_activity_classification
Revises: 0061_broker_order_research_links
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_activity_classification"
down_revision: str | None = "0061_broker_order_research_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DROP VIEW IF EXISTS activity_annotations"))
    with op.batch_alter_table("transaction_decision_links") as batch:
        batch.add_column(sa.Column("classification", sa.Text(), nullable=True))
        batch.add_column(sa.Column("order_intent_id", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_transaction_decision_links_order_intent",
            "broker_order_intents",
            ["order_intent_id"],
            ["order_intent_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_transaction_decision_links_classification",
            "classification IS NULL OR classification IN "
            "('ACTIVE_TRADE','LONG_TERM_INVESTMENT','HEDGE','CASH_MANAGEMENT',"
            "'TRANSFER_OR_ADMIN','UNCLASSIFIED')",
        )
    op.execute(
        sa.text(
            "CREATE VIEW activity_annotations AS SELECT * FROM transaction_decision_links"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW IF EXISTS activity_annotations"))
    with op.batch_alter_table("transaction_decision_links") as batch:
        batch.drop_constraint(
            "ck_transaction_decision_links_classification", type_="check"
        )
        batch.drop_constraint(
            "fk_transaction_decision_links_order_intent", type_="foreignkey"
        )
        batch.drop_column("order_intent_id")
        batch.drop_column("classification")
    op.execute(
        sa.text(
            "CREATE VIEW activity_annotations AS SELECT * FROM transaction_decision_links"
        )
    )
