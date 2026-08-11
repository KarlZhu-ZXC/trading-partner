"""Add durable Schwab live-order intents and receipts.

Revision ID: 0043_broker_order_execution
Revises: 0042_catalyst_agenda_outcomes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_broker_order_execution"
down_revision: str | None = "0042_catalyst_agenda_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_order_intents",
        sa.Column("order_intent_id", sa.Text(), primary_key=True),
        sa.Column("account_ref", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("session", sa.Text(), nullable=False),
        sa.Column("duration", sa.Text(), nullable=False),
        sa.Column("limit_price", sa.Text()),
        sa.Column("stop_price", sa.Text()),
        sa.Column("trail_offset", sa.Text()),
        sa.Column("trail_type", sa.Text()),
        sa.Column("limit_offset", sa.Text()),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("order_payload_json", sa.Text(), nullable=False),
        sa.Column("preview_idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("account_observed_at", sa.Text(), nullable=False),
        sa.Column("cash_balance", sa.Text()),
        sa.Column("margin_balance", sa.Text()),
        sa.Column("open_buy_order_reserve", sa.Text()),
        sa.Column("position_quantity", sa.Text(), nullable=False),
        sa.Column("quote_at", sa.Text()),
        sa.Column("quote_source", sa.Text()),
        sa.Column("quote_price", sa.Text()),
        sa.Column("estimated_notional", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submit_idempotency_key", sa.Text()),
        sa.Column("confirmed_by", sa.Text()),
        sa.Column("submitted_via", sa.Text()),
        sa.Column("authorization_note", sa.Text()),
        sa.Column("broker_order_id", sa.Text()),
        sa.Column("submitted_at", sa.Text()),
        sa.Column("provider_status", sa.Text()),
        sa.Column("rejection_code", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_broker_order_quantity"),
        sa.CheckConstraint(
            "status IN ('PREVIEWED','SUBMITTING','SUBMITTED','REJECTED','UNKNOWN',"
            "'CANCEL_REQUESTED','CANCELLED')",
            name="ck_broker_order_status",
        ),
        sa.UniqueConstraint("preview_idempotency_key", name="uq_broker_order_preview_idempotency"),
        sa.UniqueConstraint("submit_idempotency_key", name="uq_broker_order_submit_idempotency"),
    )
    op.create_index(
        "ix_broker_order_account_created",
        "broker_order_intents",
        ["account_ref", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_broker_order_account_created", table_name="broker_order_intents")
    op.drop_table("broker_order_intents")
