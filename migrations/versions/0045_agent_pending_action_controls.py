"""Add Agent-D action routing, token digests, and result receipts.

Revision ID: 0045_agent_pending_action_controls
Revises: 0044_shared_agent_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_agent_pending_action_controls"
down_revision: str | None = "0044_shared_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable columns keep historical 0044 read-only records intact.  New
    # Agent-D proposals populate all four fields and never persist token text.
    op.add_column(
        "agent_pending_actions",
        sa.Column("capability", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_pending_actions",
        sa.Column("operation", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_pending_actions",
        sa.Column("token_sha256", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_pending_actions",
        sa.Column("result_receipt_json", sa.Text(), nullable=True),
    )
    bind = op.get_bind()
    token_check = (
        "token_sha256 IS NULL OR "
        "(length(token_sha256) = 64 AND token_sha256 = lower(token_sha256) "
        "AND token_sha256 NOT GLOB '*[^0-9a-f]*')"
        if bind.dialect.name == "sqlite"
        else "token_sha256 IS NULL OR token_sha256 ~ '^[0-9a-f]{64}$'"
    )
    with op.batch_alter_table("agent_pending_actions") as batch:
        batch.create_check_constraint(
            "ck_agent_pending_actions_token_sha256",
            token_check,
        )
    op.create_index(
        "uq_agent_pending_actions_token_sha256",
        "agent_pending_actions",
        ["token_sha256"],
        unique=True,
        sqlite_where=sa.text("token_sha256 IS NOT NULL"),
        postgresql_where=sa.text("token_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_pending_actions_token_sha256", table_name="agent_pending_actions")
    with op.batch_alter_table("agent_pending_actions") as batch:
        batch.drop_constraint("ck_agent_pending_actions_token_sha256", type_="check")
    op.drop_column("agent_pending_actions", "result_receipt_json")
    op.drop_column("agent_pending_actions", "token_sha256")
    op.drop_column("agent_pending_actions", "operation")
    op.drop_column("agent_pending_actions", "capability")
