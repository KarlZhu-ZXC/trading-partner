"""Allow Decision review-due rows in the durable ReviewItem projection.

Revision ID: 0056_decision_review_due_items
Revises: 0055_structured_decision_records
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056_decision_review_due_items"
down_revision: str | None = "0055_structured_decision_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE')",
        )


def downgrade() -> None:
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT')",
        )
