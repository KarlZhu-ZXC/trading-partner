"""Add an optional exact external observation revision reference to Decisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_decision_external_note_revision"
down_revision: str | None = "0065_observation_revision_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep historical Decisions readable while adding one exact note revision link."""

    with op.batch_alter_table("decision_records") as batch:
        batch.add_column(
            sa.Column(
                "external_note_revision_id",
                sa.Text(),
                sa.ForeignKey(
                    "external_note_revisions.note_revision_id",
                    ondelete="RESTRICT",
                ),
                nullable=True,
            )
        )
        batch.create_index(
            "ix_decisions_external_note_revision",
            ["external_note_revision_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("decision_records") as batch:
        batch.drop_index("ix_decisions_external_note_revision")
        batch.drop_column("external_note_revision_id")
