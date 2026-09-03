"""Use stable source revision keys for replay-safe living observations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_observation_revision_keys"
down_revision: str | None = "0064_external_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_note_revisions",
        sa.Column("source_revision_key", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE external_note_revisions "
        "SET source_revision_key = 'legacy:' || note_revision_id"
    )
    with op.batch_alter_table("external_note_revisions", recreate="always") as batch:
        batch.drop_constraint("uq_external_note_revision_hash", type_="unique")
        batch.alter_column("source_revision_key", existing_type=sa.Text(), nullable=False)
        batch.create_unique_constraint(
            "uq_external_note_revision_source_key",
            ["note_id", "source_revision_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("external_note_revisions", recreate="always") as batch:
        batch.drop_constraint("uq_external_note_revision_source_key", type_="unique")
        batch.drop_column("source_revision_key")
        batch.create_unique_constraint(
            "uq_external_note_revision_hash",
            ["note_id", "content_sha256"],
        )
