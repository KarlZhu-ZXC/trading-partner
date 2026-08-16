"""Discard legacy Moomoo initial-margin values stored as financing usage.

Revision ID: 0051_moomoo_margin_semantics
Revises: 0050_agent_preferences
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_moomoo_margin_semantics"
down_revision: str | None = "0050_agent_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Before this revision the Moomoo adapter stored OpenD ``initial_margin``
    # (a collateral requirement) in the canonical ``margin_used`` field. The
    # original debtCash value cannot be reconstructed from durable snapshots.
    account_snapshots = sa.table(
        "account_snapshots",
        sa.column("provider", sa.Text()),
        sa.column("margin_used", sa.Text()),
    )
    op.execute(
        account_snapshots.update()
        .where(account_snapshots.c.provider == "moomoo")
        .values(margin_used=None)
    )


def downgrade() -> None:
    # The discarded values were semantically invalid and cannot be restored as
    # financing usage. Schema shape is unchanged.
    pass
