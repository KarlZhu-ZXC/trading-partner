"""Add exact ReviewItem occurrence lifecycle history.

Revision ID: 0048_review_item_occurrences
Revises: 0047_review_items
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_review_item_occurrences"
down_revision: str | None = "0047_review_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_item_occurrences",
        sa.Column(
            "review_item_id",
            sa.Text(),
            sa.ForeignKey("review_items.review_item_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("occurrence_no", sa.Integer(), primary_key=True),
        sa.Column("opened_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("first_acknowledged_at", sa.Text(), nullable=True),
        sa.Column("first_acknowledged_by", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolution_mode", sa.Text(), nullable=True),
        sa.CheckConstraint("occurrence_no >= 1", name="ck_review_item_occurrences_number"),
        sa.CheckConstraint(
            "resolution_mode IS NULL OR resolution_mode IN ('MANUAL','AUTO')",
            name="ck_review_item_occurrences_resolution_mode",
        ),
        sa.CheckConstraint(
            "last_seen_at >= opened_at",
            name="ck_review_item_occurrences_last_seen",
        ),
        sa.CheckConstraint(
            "(first_acknowledged_at IS NULL AND first_acknowledged_by IS NULL) OR "
            "(first_acknowledged_at >= opened_at AND first_acknowledged_by IS NOT NULL)",
            name="ck_review_item_occurrences_acknowledgment",
        ),
        sa.CheckConstraint(
            "(resolved_at IS NULL AND resolved_by IS NULL AND resolution_mode IS NULL) OR "
            "(resolved_at >= opened_at AND resolved_by IS NOT NULL "
            "AND resolution_mode IS NOT NULL)",
            name="ck_review_item_occurrences_resolution",
        ),
    )
    op.create_index(
        "ix_review_item_occurrences_opened",
        "review_item_occurrences",
        ["opened_at"],
    )
    op.create_index(
        "ix_review_item_occurrences_resolved",
        "review_item_occurrences",
        ["resolved_at"],
    )
    op.execute(
        sa.text(
            "INSERT INTO review_item_occurrences "
            "(review_item_id, occurrence_no, opened_at, last_seen_at, resolved_at, "
            "resolved_by, resolution_mode) "
            "SELECT review_item_id, occurrence_count, first_seen_at, last_seen_at, "
            "resolved_at, resolved_by, "
            "CASE WHEN status = 'RESOLVED' THEN 'MANUAL' "
            "WHEN status = 'AUTO_RESOLVED' THEN 'AUTO' ELSE NULL END "
            "FROM review_items"
        )
    )
    op.add_column(
        "review_item_actions",
        sa.Column("occurrence_no", sa.Integer(), nullable=False, server_default="1"),
    )
    with op.batch_alter_table("review_item_actions") as batch:
        batch.create_check_constraint(
            "ck_review_item_actions_occurrence",
            "occurrence_no >= 1",
        )
        batch.alter_column(
            "occurrence_no",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("review_item_actions") as batch:
        batch.drop_constraint("ck_review_item_actions_occurrence", type_="check")
        batch.drop_column("occurrence_no")
    op.drop_index(
        "ix_review_item_occurrences_resolved",
        table_name="review_item_occurrences",
    )
    op.drop_index(
        "ix_review_item_occurrences_opened",
        table_name="review_item_occurrences",
    )
    op.drop_table("review_item_occurrences")
