"""Add Research Subject Instrument Selection candidate state.

Revision ID: 0035_instrument_selection_candidates
Revises: 0034_research_subject_lifecycle
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_instrument_selection_candidates"
down_revision: str | None = "0034_research_subject_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("watchlist_items") as batch:
        batch.add_column(sa.Column("instrument_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("selection_reason", sa.Text(), nullable=True))
        batch.drop_constraint("ck_watchlist_status", type_="check")
        batch.create_check_constraint(
            "ck_watchlist_status",
            "status IN ('watching','triggered','shortlisted','selected','rejected',"
            "'promoted_to_case','expired','archived')",
        )
        batch.create_check_constraint(
            "ck_watchlist_selection_reason",
            "(status IN ('selected','rejected')) = (selection_reason IS NOT NULL)",
        )
    op.create_index(
        "uq_watchlist_selected_per_case",
        "watchlist_items",
        ["case_id"],
        unique=True,
        sqlite_where=sa.text("status = 'selected' AND case_id IS NOT NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    unsupported = connection.execute(
        sa.text(
            "SELECT item_id, status FROM watchlist_items "
            "WHERE status IN ('shortlisted','selected','rejected') LIMIT 1"
        )
    ).first()
    if unsupported is not None:
        raise RuntimeError(
            "Cannot remove Instrument Selection state while candidate data exists: "
            f"{unsupported[0]} has status {unsupported[1]}"
        )
    op.drop_index("uq_watchlist_selected_per_case", table_name="watchlist_items")
    with op.batch_alter_table("watchlist_items") as batch:
        batch.drop_constraint("ck_watchlist_selection_reason", type_="check")
        batch.drop_constraint("ck_watchlist_status", type_="check")
        batch.create_check_constraint(
            "ck_watchlist_status",
            "status IN ('watching','triggered','promoted_to_case','expired','archived')",
        )
        batch.drop_column("selection_reason")
        batch.drop_column("instrument_id")
