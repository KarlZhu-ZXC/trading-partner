"""Persist bounded web-search provenance for Monitor judgments.

Revision ID: 0033_monitor_web_search_receipts
Revises: 0032_monitor_llm_judgments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_monitor_web_search_receipts"
down_revision: str | None = "0032_monitor_llm_judgments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitor_judgments") as batch:
        batch.add_column(
            sa.Column(
                "web_search_used",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "web_source_urls",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("monitor_judgments") as batch:
        batch.drop_column("web_source_urls")
        batch.drop_column("web_search_used")
