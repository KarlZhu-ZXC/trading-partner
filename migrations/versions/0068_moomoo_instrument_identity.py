"""Canonicalize the Moomoo SOXL account identity to the Instrument Master ETF."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_moomoo_instrument_identity"
down_revision: str | None = "0067_post_market_observation_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSUMED_ID = "equity:US:SOXL"
_CANONICAL_ID = "etf:US:SOXL"


def _canonical_instrument_exists() -> bool:
    connection = op.get_bind()
    return bool(connection.scalar(sa.text(
        "SELECT 1 FROM instruments WHERE instrument_id=:instrument_id AND symbol='SOXL' "
        "AND market='US' AND asset_type='etf' LIMIT 1"
    ), {"instrument_id": _CANONICAL_ID}))


def upgrade() -> None:
    if not _canonical_instrument_exists():
        return
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE account_transactions SET instrument_id=:canonical_id, "
        "mapping_version='moomoo_deals_v2' WHERE provider='moomoo' "
        "AND instrument_id=:assumed_id"
    ), {"canonical_id": _CANONICAL_ID, "assumed_id": _ASSUMED_ID})
    connection.execute(sa.text(
        "UPDATE account_positions AS position SET instrument_id=:canonical_id "
        "WHERE instrument_id=:assumed_id AND EXISTS (SELECT 1 FROM account_snapshots "
        "AS snapshot WHERE snapshot.snapshot_id=position.snapshot_id "
        "AND snapshot.provider='moomoo') AND NOT EXISTS (SELECT 1 FROM account_positions "
        "AS canonical WHERE canonical.snapshot_id=position.snapshot_id "
        "AND canonical.instrument_id=:canonical_id)"
    ), {"canonical_id": _CANONICAL_ID, "assumed_id": _ASSUMED_ID})
    connection.execute(sa.text(
        "UPDATE account_snapshots SET open_orders_json=replace(open_orders_json,"
        ":assumed_json,:canonical_json) WHERE provider='moomoo' "
        "AND open_orders_json LIKE :pattern"
    ), {
        "assumed_json": f'"instrument_id":"{_ASSUMED_ID}"',
        "canonical_json": f'"instrument_id":"{_CANONICAL_ID}"',
        "pattern": f'%"instrument_id":"{_ASSUMED_ID}"%',
    })


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE account_transactions SET instrument_id=:assumed_id, "
        "mapping_version='moomoo_deals_v1' WHERE provider='moomoo' "
        "AND instrument_id=:canonical_id AND mapping_version='moomoo_deals_v2'"
    ), {"canonical_id": _CANONICAL_ID, "assumed_id": _ASSUMED_ID})
    connection.execute(sa.text(
        "UPDATE account_positions AS position SET instrument_id=:assumed_id "
        "WHERE instrument_id=:canonical_id AND EXISTS (SELECT 1 FROM account_snapshots "
        "AS snapshot WHERE snapshot.snapshot_id=position.snapshot_id "
        "AND snapshot.provider='moomoo') AND NOT EXISTS (SELECT 1 FROM account_positions "
        "AS assumed WHERE assumed.snapshot_id=position.snapshot_id "
        "AND assumed.instrument_id=:assumed_id)"
    ), {"canonical_id": _CANONICAL_ID, "assumed_id": _ASSUMED_ID})
    connection.execute(sa.text(
        "UPDATE account_snapshots SET open_orders_json=replace(open_orders_json,"
        ":canonical_json,:assumed_json) WHERE provider='moomoo' "
        "AND open_orders_json LIKE :pattern"
    ), {
        "canonical_json": f'"instrument_id":"{_CANONICAL_ID}"',
        "assumed_json": f'"instrument_id":"{_ASSUMED_ID}"',
        "pattern": f'%"instrument_id":"{_CANONICAL_ID}"%',
    })
