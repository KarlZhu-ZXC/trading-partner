"""Add optional local semantic vectors for the rebuildable Research Search index.

Revision ID: 0053_research_search_vectors
Revises: 0052_operational_job_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_research_search_vectors"
down_revision: str | None = "0052_operational_job_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_search_vectors",
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector_json", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "entity_type",
            "entity_id",
            "model_id",
            name="pk_research_search_vectors",
        ),
        sa.CheckConstraint("dimensions > 0", name="ck_research_search_vector_dimensions"),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_research_search_vector_sha256",
        ),
    )
    op.create_index(
        "ix_research_search_vectors_model",
        "research_search_vectors",
        ["model_id", "entity_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_search_vectors_model", table_name="research_search_vectors")
    op.drop_table("research_search_vectors")
