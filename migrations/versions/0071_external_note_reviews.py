"""Add append-only review outcomes for exact external note revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_external_note_reviews"
down_revision: str | None = "0070_retire_unlinked_review_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE',"
            "'OBSERVATION_REVIEW_DUE','UNLINKED_ACTIVITY')",
        )
    op.create_table(
        "external_note_review_revisions",
        sa.Column("review_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column(
            "note_revision_id",
            sa.Text(),
            sa.ForeignKey("external_note_revisions.note_revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "note_id",
            sa.Text(),
            sa.ForeignKey("external_note_identities.note_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "subject_id",
            sa.Text(),
            sa.ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "decision_id",
            sa.Text(),
            sa.ForeignKey("decision_records.decision_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("due_at", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "note_revision_id",
            "version",
            name="uq_external_note_review_revision_version",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_external_note_review_idempotency",
        ),
        sa.CheckConstraint("version >= 1", name="ck_external_note_review_version"),
        sa.CheckConstraint(
            "status IN ('PENDING','DEFERRED','ADOPTED','NO_ACTION')",
            name="ck_external_note_review_status",
        ),
        sa.CheckConstraint(
            "((status IN ('ADOPTED','NO_ACTION')) AND subject_id IS NOT NULL "
            "AND decision_id IS NOT NULL) OR "
            "((status IN ('PENDING','DEFERRED')) AND decision_id IS NULL)",
            name="ck_external_note_review_terminal_links",
        ),
    )
    op.create_index(
        "ix_external_note_review_latest",
        "external_note_review_revisions",
        ["note_revision_id", "version"],
    )
    op.create_index(
        "ix_external_note_review_subject_status",
        "external_note_review_revisions",
        ["subject_id", "status", "created_at"],
    )
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO external_note_review_revisions("
            "review_id,version,note_revision_id,note_id,status,subject_id,decision_id,"
            "due_at,actor,authorization_note,idempotency_key,created_at) "
            "SELECT 'external_note_review_backfill_' || decision_id,1,"
            "external_note_revision_id,note_id,"
            "CASE WHEN decision_type='no_action' THEN 'NO_ACTION' ELSE 'ADOPTED' END,"
            "case_id,decision_id,NULL,decided_by,"
            "'Backfilled from an existing explicitly confirmed Decision.',"
            "'migration:0071:' || decision_id,recorded_at FROM ("
            "SELECT d.*,r.note_id,ROW_NUMBER() OVER ("
            "PARTITION BY d.external_note_revision_id "
            "ORDER BY d.recorded_at DESC,d.decision_id DESC) AS review_rank "
            "FROM decision_records d JOIN external_note_revisions r "
            "ON r.note_revision_id=d.external_note_revision_id "
            "WHERE d.external_note_revision_id IS NOT NULL"
            ") WHERE review_rank=1"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_note_review_subject_status",
        table_name="external_note_review_revisions",
    )
    op.drop_index(
        "ix_external_note_review_latest",
        table_name="external_note_review_revisions",
    )
    op.drop_table("external_note_review_revisions")
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE',"
            "'UNLINKED_ACTIVITY')",
        )
