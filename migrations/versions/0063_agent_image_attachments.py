"""Persist bounded Agent image attachment metadata."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_agent_image_attachments"
down_revision: str | None = "0062_activity_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_messages") as batch:
        batch.add_column(sa.Column("attachments_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_messages") as batch:
        batch.drop_column("attachments_json")
