"""Phase 1L workflow receipts and historical account transactions.

Revision ID: 0008_phase1l_workflows
Revises: 0007_phase1k_challenge_reviews
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_phase1l_workflows"
down_revision: str | Sequence[str] | None = "0007_phase1k_challenge_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("workflow_type", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text()),
        sa.Column("instrument_id", sa.Text()),
        sa.Column("requested_as_of", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("report_id", sa.Text()),
        sa.ForeignKeyConstraint(["case_id"], ["investment_cases.case_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "workflow_type IN ('deep_dive','catalyst_review','a_share_market_review',"
            "'us_market_review','portfolio_review')",
            name="ck_research_runs_workflow_type",
        ),
        sa.CheckConstraint(
            "status IN ('complete','partial','failed')", name="ck_research_runs_status"
        ),
    )
    op.create_index("ix_research_runs_case_completed", "research_runs", ["case_id", "completed_at"])
    op.create_table(
        "research_run_steps",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("step_name", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("required", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Integer(), nullable=False),
        sa.Column("degraded", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("source_names", sa.Text(), nullable=False),
        sa.Column("warning_codes", sa.Text(), nullable=False),
        sa.Column("error_codes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("required IN (0,1)", name="ck_research_run_steps_required"),
        sa.CheckConstraint("ok IN (0,1)", name="ck_research_run_steps_ok"),
        sa.CheckConstraint("degraded IN (0,1)", name="ck_research_run_steps_degraded"),
    )
    op.create_table(
        "account_transactions",
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("account_ref", sa.Text(), primary_key=True),
        sa.Column("provider_transaction_id", sa.Text(), primary_key=True),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("side", sa.Text()),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("price", sa.Text()),
        sa.Column("fees", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_account_transactions_occurred", "account_transactions", ["occurred_at"]
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES ('0008_phase1l_workflows', '2026-07-18T00:00:00+00:00', "
            "'Phase 1L workflow receipts and account transactions')"
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0008_phase1l_workflows'")
    op.drop_index("ix_account_transactions_occurred", table_name="account_transactions")
    op.drop_table("account_transactions")
    op.drop_table("research_run_steps")
    op.drop_index("ix_research_runs_case_completed", table_name="research_runs")
    op.drop_table("research_runs")
