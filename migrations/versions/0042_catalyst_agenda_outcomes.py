"""Add durable Catalyst Agenda outcome facts and revisions.

Revision ID: 0042_catalyst_agenda_outcomes
Revises: 0041_catalyst_agenda_sync
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_catalyst_agenda_outcomes"
down_revision: str | None = "0041_catalyst_agenda_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add v2 outcome fields while retaining readable schema-v1 rows."""

    with op.batch_alter_table("catalyst_agenda_versions", recreate="always") as batch:
        batch.drop_constraint("catalyst_agenda_schema", type_="check")
        batch.add_column(sa.Column("linked_evidence_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("outcome_occurred_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("outcome_note", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_catalyst_agenda_evidence",
            "research_evidence",
            ["linked_evidence_id"],
            ["evidence_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "catalyst_agenda_schema",
            "schema_version IN (1, 2)",
        )
        batch.create_check_constraint(
            "catalyst_agenda_outcome_contract",
            "schema_version = 1 OR status != 'OCCURRED' OR "
            "((linked_event_id IS NOT NULL OR linked_report_id IS NOT NULL "
            "OR linked_evidence_id IS NOT NULL) "
            "AND outcome_occurred_at IS NOT NULL "
            "AND length(trim(outcome_note)) > 0)",
        )


def downgrade() -> None:
    """Remove v2 fields after rejecting rows that cannot be represented by v1."""

    with op.batch_alter_table("catalyst_agenda_versions", recreate="always") as batch:
        batch.drop_constraint("catalyst_agenda_outcome_contract", type_="check")
        batch.drop_constraint("catalyst_agenda_schema", type_="check")
        batch.drop_constraint("fk_catalyst_agenda_evidence", type_="foreignkey")
        batch.drop_column("outcome_note")
        batch.drop_column("outcome_occurred_at")
        batch.drop_column("linked_evidence_id")
        batch.create_check_constraint(
            "catalyst_agenda_schema",
            "schema_version = 1",
        )
