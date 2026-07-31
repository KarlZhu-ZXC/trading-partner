"""Add Korea Exchange identities and post-market Monitor cadence.

Revision ID: 0026_korean_market_support
Revises: 0025_monitor_run_notification_outbox
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0026_korean_market_support"
down_revision: str | Sequence[str] | None = "0025_monitor_run_notification_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MARKETS = "market IN ('A_SHARE','US','KR','CME','DCE','OTC','LME')"
_PREVIOUS_MARKETS = "market IN ('A_SHARE','US','CME','DCE','OTC','LME')"
_CADENCES = (
    "cadence IN ('ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET',"
    "'US_POST_MARKET','KR_POST_MARKET')"
)
_RUN_CADENCES = (
    "cadence IS NULL OR cadence IN ('ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET',"
    "'US_POST_MARKET','KR_POST_MARKET')"
)
_PREVIOUS_CADENCES = (
    "cadence IN ('ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET','US_POST_MARKET')"
)
_PREVIOUS_RUN_CADENCES = (
    "cadence IS NULL OR cadence IN ("
    "'ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET','US_POST_MARKET')"
)


def upgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.drop_constraint("ck_instruments_market", type_="check")
        batch.create_check_constraint("ck_instruments_market", _MARKETS)
    with op.batch_alter_table("instrument_aliases") as batch:
        batch.drop_constraint("ck_instrument_aliases_market", type_="check")
        batch.create_check_constraint("ck_instrument_aliases_market", _MARKETS)
    with op.batch_alter_table("monitor_versions") as batch:
        batch.drop_constraint("ck_monitor_versions_cadence", type_="check")
        batch.create_check_constraint("ck_monitor_versions_cadence", _CADENCES)
    with op.batch_alter_table("monitor_runs") as batch:
        batch.drop_constraint("ck_monitor_runs_cadence", type_="check")
        batch.create_check_constraint("ck_monitor_runs_cadence", _RUN_CADENCES)

    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=revision,
            applied_at=datetime.now(UTC).isoformat(),
            description="Korea Exchange market identity and post-market monitoring",
        )
    )


def downgrade() -> None:
    op.execute(
        "UPDATE monitor_versions SET cadence = 'ON_DEMAND', interval_minutes = NULL "
        "WHERE cadence = 'KR_POST_MARKET'"
    )
    op.execute("UPDATE monitor_runs SET cadence = 'ON_DEMAND' WHERE cadence = 'KR_POST_MARKET'")
    op.execute("DELETE FROM instrument_aliases WHERE market = 'KR'")
    op.execute("DELETE FROM instruments WHERE market = 'KR'")

    with op.batch_alter_table("monitor_runs") as batch:
        batch.drop_constraint("ck_monitor_runs_cadence", type_="check")
        batch.create_check_constraint("ck_monitor_runs_cadence", _PREVIOUS_RUN_CADENCES)
    with op.batch_alter_table("monitor_versions") as batch:
        batch.drop_constraint("ck_monitor_versions_cadence", type_="check")
        batch.create_check_constraint("ck_monitor_versions_cadence", _PREVIOUS_CADENCES)
    with op.batch_alter_table("instrument_aliases") as batch:
        batch.drop_constraint("ck_instrument_aliases_market", type_="check")
        batch.create_check_constraint("ck_instrument_aliases_market", _PREVIOUS_MARKETS)
    with op.batch_alter_table("instruments") as batch:
        batch.drop_constraint("ck_instruments_market", type_="check")
        batch.create_check_constraint("ck_instruments_market", _PREVIOUS_MARKETS)
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=revision
        )
    )
