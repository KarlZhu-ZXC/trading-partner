"""Phase 1A foundation tables: schema_versions and system_audit_log.

Revision ID: 0001_phase1a_foundation
Revises:
Create Date: 2026-07-16

schema_versions is written only inside this migration transaction and is not a
runtime substitute for alembic_version.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0001_phase1a_foundation"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PHASE1A_VERSION = "phase1a_foundation"
_PHASE1A_DESCRIPTION = "Phase 1A foundation: schema_versions and system_audit_log"


def upgrade() -> None:
    op.create_table(
        "schema_versions",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("version", name="pk_schema_versions"),
    )
    op.create_table(
        "system_audit_log",
        sa.Column("audit_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id", name="pk_system_audit_log"),
    )

    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
        sa.column("applied_at", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.execute(
        schema_versions.insert().values(
            version=_PHASE1A_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_PHASE1A_DESCRIPTION,
        )
    )


def downgrade() -> None:
    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
    )
    op.execute(
        schema_versions.delete().where(schema_versions.c.version == _PHASE1A_VERSION)
    )
    op.drop_table("system_audit_log")
    op.drop_table("schema_versions")
