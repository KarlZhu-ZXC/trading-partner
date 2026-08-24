"""Add append-only transaction links and the Unlinked Activity review source.

Revision ID: 0057_unlinked_activity_annotations
Revises: 0056_decision_review_due_items
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_unlinked_activity_annotations"
down_revision: str | None = "0056_decision_review_due_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction_decision_links",
        sa.Column("annotation_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("account_ref", sa.Text(), nullable=False),
        sa.Column("provider_transaction_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("decision_id", sa.Text(), nullable=True),
        sa.Column("trade_plan_id", sa.Text(), nullable=True),
        sa.Column("trade_plan_version", sa.Integer(), nullable=True),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("annotation_id", name="pk_transaction_decision_links"),
        sa.UniqueConstraint(
            "provider",
            "account_ref",
            "provider_transaction_id",
            "version",
            name="uq_transaction_decision_links_key_version",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_transaction_decision_links_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["provider", "account_ref", "provider_transaction_id"],
            [
                "account_transactions.provider",
                "account_transactions.account_ref",
                "account_transactions.provider_transaction_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decision_records.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["trade_plan_id", "trade_plan_version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["investment_cases.case_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('LINKED_DECISION_PLAN','UNPLANNED','CASH_MANAGEMENT',"
            "'TRANSFER_OR_CORPORATE_ACTION','PROVIDER_CORRECTION')",
            name="ck_transaction_decision_links_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_transaction_decision_links_version"),
        sa.CheckConstraint(
            "(trade_plan_id IS NULL) = (trade_plan_version IS NULL)",
            name="ck_transaction_decision_links_plan_pair",
        ),
    )
    op.create_index(
        "ix_transaction_decision_links_activity",
        "transaction_decision_links",
        ["provider", "account_ref", "provider_transaction_id", "version"],
    )
    op.create_index(
        "ix_transaction_decision_links_status",
        "transaction_decision_links",
        ["status", "created_at"],
    )

    # This view keeps the descriptive ActivityAnnotation name available to
    # local diagnostics without maintaining a second mutable source of truth.
    op.execute(
        sa.text(
            "CREATE VIEW activity_annotations AS "
            "SELECT * FROM transaction_decision_links"
        )
    )

    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE',"
            "'UNLINKED_ACTIVITY')",
        )


def downgrade() -> None:
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE')",
        )
    op.execute(sa.text("DROP VIEW IF EXISTS activity_annotations"))
    op.drop_index(
        "ix_transaction_decision_links_status", table_name="transaction_decision_links"
    )
    op.drop_index(
        "ix_transaction_decision_links_activity", table_name="transaction_decision_links"
    )
    op.drop_table("transaction_decision_links")
