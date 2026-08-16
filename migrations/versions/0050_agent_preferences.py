"""Add explicit owner-scoped Agent presentation preferences.

Revision ID: 0050_agent_preferences
Revises: 0049_agent_turns
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_agent_preferences"
down_revision: str | None = "0049_agent_turns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_preferences",
        sa.Column("preferences_id", sa.Text(), primary_key=True),
        sa.Column("owner_principal", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("response_density", sa.Text(), nullable=False),
        sa.Column("preferred_source_codes_json", sa.Text(), nullable=False),
        sa.Column("risk_style", sa.Text(), nullable=False),
        sa.Column("default_chart", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("web_background", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("owner_principal", name="uq_agent_preferences_owner"),
        sa.CheckConstraint(
            "language IN ('zh-CN','en')",
            name="ck_agent_preferences_language",
        ),
        sa.CheckConstraint(
            "response_density IN ('compact','standard','detailed')",
            name="ck_agent_preferences_density",
        ),
        sa.CheckConstraint(
            "risk_style IN ('balanced','cautious','direct')",
            name="ck_agent_preferences_risk_style",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_preferences_version"),
    )
    op.create_index(
        "ix_agent_preferences_updated_at",
        "agent_preferences",
        ["updated_at"],
    )
    op.create_table(
        "agent_preferences_revisions",
        sa.Column("revision_id", sa.Text(), primary_key=True),
        sa.Column("preferences_id", sa.Text(), nullable=False),
        sa.Column("owner_principal", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("response_density", sa.Text(), nullable=False),
        sa.Column("preferred_source_codes_json", sa.Text(), nullable=False),
        sa.Column("risk_style", sa.Text(), nullable=False),
        sa.Column("default_chart", sa.Boolean(), nullable=False),
        sa.Column("web_background", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "owner_principal",
            "idempotency_key",
            name="uq_agent_preferences_revision_idempotency",
        ),
        sa.CheckConstraint(
            "operation IN ('CREATE','UPDATE','RESET')",
            name="ck_agent_preferences_revision_operation",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_preferences_revision_version"),
    )
    op.create_index(
        "ix_agent_preferences_revisions_owner_created",
        "agent_preferences_revisions",
        ["owner_principal", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_preferences_revisions_owner_created",
        table_name="agent_preferences_revisions",
    )
    op.drop_table("agent_preferences_revisions")
    op.drop_index("ix_agent_preferences_updated_at", table_name="agent_preferences")
    op.drop_table("agent_preferences")
