"""Persist secret-safe Provider diagnostics on Monitor observations.

Revision ID: 0036_monitor_provider_diagnostics
Revises: 0035_instrument_selection_candidates
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_monitor_provider_diagnostics"
down_revision: str | None = "0035_instrument_selection_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitor_run_observations") as batch:
        batch.add_column(
            sa.Column(
                "diagnostics_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("monitor_run_observations") as batch:
        batch.drop_column("diagnostics_json")
