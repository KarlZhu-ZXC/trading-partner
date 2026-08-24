"""Add secret-safe durable Agent turn failure metadata.

Revision ID: 0054_agent_turn_failure_metadata
Revises: 0053_research_search_vectors
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_agent_turn_failure_metadata"
down_revision: str | None = "0053_research_search_vectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        batch.add_column(sa.Column("model", sa.Text(), nullable=True))
        batch.add_column(sa.Column("error_http_status", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("error_retryable", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("error_attempts", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_agent_turns_error_http_status",
            "error_http_status IS NULL OR error_http_status BETWEEN 100 AND 599",
        )
        batch.create_check_constraint(
            "ck_agent_turns_error_retryable",
            "error_retryable IS NULL OR error_retryable IN (0, 1)",
        )
        batch.create_check_constraint(
            "ck_agent_turns_error_attempts",
            "error_attempts IS NULL OR error_attempts BETWEEN 1 AND 100",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("ck_agent_turns_error_attempts", type_="check")
        batch.drop_constraint("ck_agent_turns_error_retryable", type_="check")
        batch.drop_constraint("ck_agent_turns_error_http_status", type_="check")
        batch.drop_column("error_attempts")
        batch.drop_column("error_retryable")
        batch.drop_column("error_http_status")
        batch.drop_column("model")
