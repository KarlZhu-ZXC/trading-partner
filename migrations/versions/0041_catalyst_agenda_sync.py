"""Add Catalyst Agenda Provider sync receipts and tighten source contracts.

Revision ID: 0041_catalyst_agenda_sync
Revises: 0040_catalyst_agenda
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_catalyst_agenda_sync"
down_revision: str | None = "0040_catalyst_agenda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_contracts(*, upgraded: bool) -> None:
    with op.batch_alter_table("catalyst_agenda_versions", recreate="always") as batch:
        batch.drop_constraint("catalyst_agenda_scope_required", type_="check")
        batch.drop_constraint("catalyst_agenda_confirmer", type_="check")
        if upgraded:
            batch.create_check_constraint(
                "catalyst_agenda_scope_required",
                "instrument_id IS NOT NULL OR case_id IS NOT NULL "
                "OR kind IN ('MACRO_RELEASE','POLICY')",
            )
            batch.create_check_constraint(
                "catalyst_agenda_confirmer",
                "(source_type = 'USER_CONFIRMED' "
                "AND confirmed_by IN ('user','external_agent')) "
                "OR (source_type = 'PROVIDER' AND confirmed_by = 'system' "
                "AND authorization_note LIKE 'provider_sync:%' "
                "AND length(authorization_note) > 14)",
            )
        else:
            batch.create_check_constraint(
                "catalyst_agenda_scope_required",
                "instrument_id IS NOT NULL OR case_id IS NOT NULL",
            )
            batch.create_check_constraint(
                "catalyst_agenda_confirmer",
                "confirmed_by IN ('user','external_agent','system')",
            )


def upgrade() -> None:
    _replace_contracts(upgraded=True)
    op.create_table(
        "catalyst_agenda_sync_receipts",
        sa.Column("receipt_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Text(), nullable=False),
        sa.Column("window_end", sa.Text(), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("eligible_instrument_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_scope_count", sa.Integer(), nullable=False),
        sa.Column("failed_scope_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("appended_count", sa.Integer(), nullable=False),
        sa.Column("revised_count", sa.Integer(), nullable=False),
        sa.Column("date_drift_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("provider_results_json", sa.Text(), nullable=False),
        sa.Column("limitation_codes_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("execution_effect", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("receipt_id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_catalyst_agenda_sync_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETE','PARTIAL','FAILED')",
            name="catalyst_agenda_sync_status",
        ),
        sa.CheckConstraint("schema_version = 1", name="catalyst_agenda_sync_schema"),
        sa.CheckConstraint(
            "execution_effect = 0", name="catalyst_agenda_sync_no_execution"
        ),
    )
    op.create_index(
        "ix_catalyst_agenda_sync_completed",
        "catalyst_agenda_sync_receipts",
        ["completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalyst_agenda_sync_completed",
        table_name="catalyst_agenda_sync_receipts",
    )
    op.drop_table("catalyst_agenda_sync_receipts")
    _replace_contracts(upgraded=False)
