"""Durable Reddit RSS samples and provider-wide cooldown.

Revision ID: 0011_reddit_rss_resilience
Revises: 0008_phase1l_workflows
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_reddit_rss_resilience"
down_revision: str | Sequence[str] | None = "0008_phase1l_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reddit_sample_cache",
        sa.Column("instrument_id", sa.Text(), primary_key=True),
        sa.Column("config_key", sa.Text(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_reddit_sample_cache_expires",
        "reddit_sample_cache",
        ["expires_at"],
    )
    op.create_table(
        "reddit_provider_cooldown",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("cooldown_until", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "scope = 'anonymous_rss'",
            name="ck_reddit_provider_cooldown_scope",
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES ('0011_reddit_rss_resilience', '2026-07-19T00:00:00+00:00', "
            "'Durable Reddit RSS cache and shared cooldown')"
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0011_reddit_rss_resilience'")
    op.drop_table("reddit_provider_cooldown")
    op.drop_index("ix_reddit_sample_cache_expires", table_name="reddit_sample_cache")
    op.drop_table("reddit_sample_cache")
