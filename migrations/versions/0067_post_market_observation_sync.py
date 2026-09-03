"""Add durable Moomoo Observation results to post-market sync receipts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_post_market_observation_sync"
down_revision: str | None = "0066_decision_external_note_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("post_market_sync_runs") as batch:
        batch.add_column(sa.Column("observation_status", sa.Text(), nullable=True))
        batch.add_column(sa.Column("observation_notes_seen", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("observation_revisions_created", sa.Integer(), nullable=True)
        )
        batch.add_column(sa.Column("observation_full_count", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("observation_summary_only_count", sa.Integer(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_post_market_sync_observation_status",
            "observation_status IS NULL OR observation_status IN ('SUCCEEDED','FAILED')",
        )
        for column in (
            "observation_notes_seen",
            "observation_revisions_created",
            "observation_full_count",
            "observation_summary_only_count",
        ):
            batch.create_check_constraint(
                f"ck_post_market_sync_{column}",
                f"{column} IS NULL OR {column} >= 0",
            )


def downgrade() -> None:
    with op.batch_alter_table("post_market_sync_runs") as batch:
        for constraint in (
            "ck_post_market_sync_observation_summary_only_count",
            "ck_post_market_sync_observation_full_count",
            "ck_post_market_sync_observation_revisions_created",
            "ck_post_market_sync_observation_notes_seen",
            "ck_post_market_sync_observation_status",
        ):
            batch.drop_constraint(constraint, type_="check")
        for column in (
            "observation_summary_only_count",
            "observation_full_count",
            "observation_revisions_created",
            "observation_notes_seen",
            "observation_status",
        ):
            batch.drop_column(column)
