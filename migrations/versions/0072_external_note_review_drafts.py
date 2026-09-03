"""Persist separate escalated model drafts for Observation review."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_external_note_review_drafts"
down_revision: str | None = "0071_external_note_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_note_review_drafts",
        sa.Column("draft_id", sa.Text(), primary_key=True),
        sa.Column("review_id", sa.Text(), nullable=False),
        sa.Column(
            "note_revision_id",
            sa.Text(),
            sa.ForeignKey("external_note_revisions.note_revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reasoning_effort", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("trigger_codes_json", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_external_note_review_draft_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','FAILED')",
            name="ck_external_note_review_draft_status",
        ),
        sa.CheckConstraint(
            "(status='SUCCEEDED' AND error_code IS NULL) OR "
            "(status='FAILED' AND error_code IS NOT NULL)",
            name="ck_external_note_review_draft_failure",
        ),
    )
    op.create_index(
        "ix_external_note_review_draft_latest",
        "external_note_review_drafts",
        ["note_revision_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_note_review_draft_latest",
        table_name="external_note_review_drafts",
    )
    op.drop_table("external_note_review_drafts")
