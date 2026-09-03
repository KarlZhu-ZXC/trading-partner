"""Persist external living-note revisions and model interpretations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_external_notes"
down_revision: str | None = "0063_agent_image_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_note_identities",
        sa.Column("note_id", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("primary_instrument_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_external_note_source_id"),
    )
    op.create_index(
        "ix_external_note_instrument_seen",
        "external_note_identities",
        ["primary_instrument_id", "last_seen_at"],
    )
    op.create_table(
        "external_note_revisions",
        sa.Column("note_revision_id", sa.Text(), primary_key=True),
        sa.Column(
            "note_id",
            sa.Text(),
            sa.ForeignKey("external_note_identities.note_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("full_body", sa.Text(), nullable=True),
        sa.Column("coverage", sa.Text(), nullable=False),
        sa.Column("source_timestamp", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("related_stock_ids_json", sa.Text(), nullable=False),
        sa.Column("related_codes_json", sa.Text(), nullable=False),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("note_id", "version", name="uq_external_note_revision_version"),
        sa.UniqueConstraint("note_id", "content_sha256", name="uq_external_note_revision_hash"),
        sa.CheckConstraint("version >= 1", name="ck_external_note_revision_version"),
        sa.CheckConstraint(
            "coverage IN ('FULL','SUMMARY_ONLY')",
            name="ck_external_note_revision_coverage",
        ),
    )
    op.create_index(
        "ix_external_note_revision_observed",
        "external_note_revisions",
        ["observed_at"],
    )
    op.create_table(
        "external_note_interpretations",
        sa.Column("interpretation_id", sa.Text(), primary_key=True),
        sa.Column(
            "note_revision_id",
            sa.Text(),
            sa.ForeignKey("external_note_revisions.note_revision_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reasoning_effort", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','FAILED')",
            name="ck_external_note_interpretation_status",
        ),
    )
    op.create_table(
        "external_note_sync_receipts",
        sa.Column("receipt_id", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("cache_files_scanned", sa.Integer(), nullable=False),
        sa.Column("notes_seen", sa.Integer(), nullable=False),
        sa.Column("identities_created", sa.Integer(), nullable=False),
        sa.Column("revisions_created", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("full_count", sa.Integer(), nullable=False),
        sa.Column("summary_only_count", sa.Integer(), nullable=False),
        sa.Column("interpretations_created", sa.Integer(), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.Column("error_codes_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')",
            name="ck_external_note_sync_status",
        ),
    )
    op.create_index(
        "ix_external_note_sync_completed",
        "external_note_sync_receipts",
        ["completed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_note_sync_completed", table_name="external_note_sync_receipts")
    op.drop_table("external_note_sync_receipts")
    op.drop_table("external_note_interpretations")
    op.drop_index("ix_external_note_revision_observed", table_name="external_note_revisions")
    op.drop_table("external_note_revisions")
    op.drop_index("ix_external_note_instrument_seen", table_name="external_note_identities")
    op.drop_table("external_note_identities")
