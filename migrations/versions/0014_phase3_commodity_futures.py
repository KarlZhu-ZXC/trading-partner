"""Phase 3 commodity-futures instrument support and deterministic seeds.

Revision ID: 0014_phase3_commodity_futures
Revises: 0013_phase2c_monitoring
Create Date: 2026-07-21

Adds the append-only ``future`` AssetType to the instrument registry and seeds
Yahoo continuous COMEX/NYMEX proxy instruments. These instruments are futures,
never OTC spot aliases.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0014_phase3_commodity_futures"
down_revision: str | Sequence[str] | None = "0013_phase2c_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION = "0014_phase3_commodity_futures"
_DESCRIPTION = "Phase 3 Yahoo continuous commodity-futures market data"
_SEED_TS = "2026-07-21T00:00:00+00:00"

_INSTRUMENTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("GC=F", "COMEX Gold Futures Continuous", "COMEX", "100", "0.1"),
    ("MGC=F", "COMEX Micro Gold Futures Continuous", "COMEX", "10", "0.1"),
    ("SI=F", "COMEX Silver Futures Continuous", "COMEX", "5000", "0.005"),
    ("HG=F", "COMEX Copper Futures Continuous", "COMEX", "25000", "0.0005"),
    ("PL=F", "NYMEX Platinum Futures Continuous", "NYMEX", "50", "0.1"),
    ("PA=F", "NYMEX Palladium Futures Continuous", "NYMEX", "100", "0.1"),
)

_SEED_ROWS: tuple[dict[str, Any], ...] = tuple(
    {
        "instrument_id": f"future:US:{symbol}",
        "symbol": symbol,
        "name": name,
        "market": "US",
        "exchange": exchange,
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "future",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": None,
        "underlying_instrument_id": None,
        "multiplier": multiplier,
        "tick_size": tick_size,
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    }
    for symbol, name, exchange, multiplier, tick_size in _INSTRUMENTS
)

_ALIAS_VALUES: tuple[tuple[str, str], ...] = (
    ("future:US:GC=F", "黄金期货"),
    ("future:US:MGC=F", "微型黄金期货"),
    ("future:US:SI=F", "白银期货"),
    ("future:US:HG=F", "COMEX铜期货"),
    ("future:US:PL=F", "铂金期货"),
    ("future:US:PA=F", "钯金期货"),
)

_ALIAS_ROWS: tuple[dict[str, Any], ...] = tuple(
    {
        "alias_id": f"alias_00000000-0000-7000-8000-{index:012d}",
        "instrument_id": instrument_id,
        "alias_type": "name",
        "alias_value": alias,
        "alias_value_raw": alias,
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    }
    for index, (instrument_id, alias) in enumerate(_ALIAS_VALUES, start=9)
)


def _instrument_table() -> sa.TableClause:
    return sa.table(
        "instruments",
        sa.column("instrument_id", sa.Text()),
        sa.column("symbol", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("market", sa.Text()),
        sa.column("exchange", sa.Text()),
        sa.column("currency", sa.Text()),
        sa.column("timezone", sa.Text()),
        sa.column("asset_type", sa.Text()),
        sa.column("is_active", sa.Integer()),
        sa.column("listing_status", sa.Text()),
        sa.column("country", sa.Text()),
        sa.column("mic", sa.Text()),
        sa.column("underlying_instrument_id", sa.Text()),
        sa.column("multiplier", sa.Text()),
        sa.column("tick_size", sa.Text()),
        sa.column("lot_size", sa.Text()),
        sa.column("metadata_version", sa.Integer()),
        sa.column("created_at", sa.Text()),
        sa.column("updated_at", sa.Text()),
    )


def _alias_table() -> sa.TableClause:
    return sa.table(
        "instrument_aliases",
        sa.column("alias_id", sa.Text()),
        sa.column("instrument_id", sa.Text()),
        sa.column("alias_type", sa.Text()),
        sa.column("alias_value", sa.Text()),
        sa.column("alias_value_raw", sa.Text()),
        sa.column("market", sa.Text()),
        sa.column("source", sa.Text()),
        sa.column("is_primary", sa.Integer()),
        sa.column("created_at", sa.Text()),
    )


def upgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.drop_constraint("ck_instruments_asset_type", type_="check")
        batch.create_check_constraint(
            "ck_instruments_asset_type",
            "asset_type IN ('equity','etf','index','option','future')",
        )

    instruments = _instrument_table()
    aliases = _alias_table()
    bind = op.get_bind()
    existing_instruments = {
        row[0]
        for row in bind.execute(
            sa.select(instruments.c.instrument_id).where(
                instruments.c.instrument_id.in_(tuple(row["instrument_id"] for row in _SEED_ROWS))
            )
        )
    }
    rows = [row for row in _SEED_ROWS if row["instrument_id"] not in existing_instruments]
    if rows:
        op.bulk_insert(instruments, rows)

    existing_aliases = {
        row[0]
        for row in bind.execute(
            sa.select(aliases.c.alias_id).where(
                aliases.c.alias_id.in_(tuple(row["alias_id"] for row in _ALIAS_ROWS))
            )
        )
    }
    alias_rows = [row for row in _ALIAS_ROWS if row["alias_id"] not in existing_aliases]
    if alias_rows:
        op.bulk_insert(aliases, alias_rows)

    versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
        sa.column("applied_at", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.execute(
        versions.insert().values(
            version=_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    aliases = _alias_table()
    instruments = _instrument_table()
    op.execute(
        aliases.delete().where(
            aliases.c.instrument_id.in_(tuple(row["instrument_id"] for row in _SEED_ROWS))
        )
    )
    # The old schema cannot represent futures, so remove all future registry rows.
    op.execute(instruments.delete().where(instruments.c.asset_type == "future"))

    versions = sa.table("schema_versions", sa.column("version", sa.Text()))
    op.execute(versions.delete().where(versions.c.version == _VERSION))

    with op.batch_alter_table("instruments") as batch:
        batch.drop_constraint("ck_instruments_asset_type", type_="check")
        batch.create_check_constraint(
            "ck_instruments_asset_type",
            "asset_type IN ('equity','etf','index','option')",
        )
