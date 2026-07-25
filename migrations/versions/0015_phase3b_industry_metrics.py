"""Phase 3B durable publication-aware industry metric history.

Revision ID: 0015_phase3b_industry_metrics
Revises: 0014_phase3_commodity_futures
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0015_phase3b_industry_metrics"
down_revision: str | Sequence[str] | None = "0014_phase3_commodity_futures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION = revision
_DESCRIPTION = "Phase 3B durable industry-cycle metric vintages"


def upgrade() -> None:
    op.create_table(
        "industry_metric_observations",
        sa.Column("observation_key", sa.Text(), primary_key=True),
        sa.Column("cycle", sa.Text(), nullable=False),
        sa.Column("dataset_code", sa.Text(), nullable=False),
        sa.Column("metric_code", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("geography", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Text(), nullable=False),
        sa.Column("period_end", sa.Text(), nullable=False),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("measurement_basis", sa.Text(), nullable=False),
        sa.Column("published_at", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("is_estimated", sa.Integer(), nullable=False),
        sa.Column("methodology_version", sa.Text(), nullable=False),
        sa.Column("methodology_break", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cycle",
            "dataset_code",
            "metric_code",
            "period_end",
            "published_at",
            name="uq_industry_metric_vintage",
        ),
    )
    op.create_index(
        "ix_industry_metric_series",
        "industry_metric_observations",
        ["cycle", "metric_code", "period_end"],
    )
    op.create_index(
        "ix_industry_metric_publication",
        "industry_metric_observations",
        ["published_at"],
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=_VERSION
        )
    )
    op.drop_index(
        "ix_industry_metric_publication",
        table_name="industry_metric_observations",
    )
    op.drop_index(
        "ix_industry_metric_series",
        table_name="industry_metric_observations",
    )
    op.drop_table("industry_metric_observations")
