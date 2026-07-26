"""Phase 3A-3 OTC spot and rolling CFD instrument seeds.

Revision ID: 0018_phase3a_otc_spot_seeds
Revises: 0017_phase3a_futures_definitions
Create Date: 2026-07-25

Seeds Dukascopy-backed OTC identities:

- commodity_spot:OTC:XAUUSD  (SWFX broker gold, not LBMA)
- commodity_spot:OTC:XAGUSD  (SWFX broker silver, not LBMA)
- cfd:OTC:COPPER_CMD_USD     (rolling copper CFD, not spot/LME/COMEX)

Upgrade is idempotent; downgrade removes only these rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0018_phase3a_otc_spot_seeds"
down_revision: str | Sequence[str] | None = "0017_phase3a_futures_definitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION = revision
_DESCRIPTION = "Phase 3A-3 OTC XAU/XAG spot and copper CFD seeds"
_SEED_TS = "2026-07-25T00:00:00+00:00"

_SEED_INSTRUMENT_IDS: tuple[str, ...] = (
    "commodity_spot:OTC:XAUUSD",
    "commodity_spot:OTC:XAGUSD",
    "cfd:OTC:COPPER_CMD_USD",
)

_SEED_ALIAS_IDS: tuple[str, ...] = (
    "alias_00000000-0000-7000-8000-000000000015",
    "alias_00000000-0000-7000-8000-000000000016",
    "alias_00000000-0000-7000-8000-000000000017",
    "alias_00000000-0000-7000-8000-000000000018",
    "alias_00000000-0000-7000-8000-000000000019",
    "alias_00000000-0000-7000-8000-00000000001a",
)

_EMBEDDED_SEED_INSTRUMENTS: tuple[dict[str, Any], ...] = (
    {
        "instrument_id": "commodity_spot:OTC:XAUUSD",
        "symbol": "XAUUSD",
        "name": "OTC Gold vs USD (Dukascopy SWFX)",
        "market": "OTC",
        "exchange": "DUKASCOPY_SWFX",
        "currency": "USD",
        "timezone": "UTC",
        "asset_type": "commodity_spot",
        "is_active": 1,
        "listing_status": "active",
        "country": None,
        "mic": None,
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "commodity_spot:OTC:XAGUSD",
        "symbol": "XAGUSD",
        "name": "OTC Silver vs USD (Dukascopy SWFX)",
        "market": "OTC",
        "exchange": "DUKASCOPY_SWFX",
        "currency": "USD",
        "timezone": "UTC",
        "asset_type": "commodity_spot",
        "is_active": 1,
        "listing_status": "active",
        "country": None,
        "mic": None,
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": "0.001",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "cfd:OTC:COPPER_CMD_USD",
        "symbol": "COPPER_CMD_USD",
        "name": "Dukascopy Copper Rolling CFD (not spot)",
        "market": "OTC",
        "exchange": "DUKASCOPY_SWFX",
        "currency": "USD",
        "timezone": "UTC",
        "asset_type": "cfd",
        "is_active": 1,
        "listing_status": "active",
        "country": None,
        "mic": None,
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": "0.0001",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
)

_EMBEDDED_SEED_ALIASES: tuple[dict[str, Any], ...] = (
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000015",
        "instrument_id": "commodity_spot:OTC:XAUUSD",
        "alias_type": "symbol",
        "alias_value": "XAUUSD",
        "alias_value_raw": "XAUUSD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000016",
        "instrument_id": "commodity_spot:OTC:XAUUSD",
        "alias_type": "symbol",
        "alias_value": "XAU/USD",
        "alias_value_raw": "XAU/USD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 0,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000017",
        "instrument_id": "commodity_spot:OTC:XAGUSD",
        "alias_type": "symbol",
        "alias_value": "XAGUSD",
        "alias_value_raw": "XAGUSD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000018",
        "instrument_id": "commodity_spot:OTC:XAGUSD",
        "alias_type": "symbol",
        "alias_value": "XAG/USD",
        "alias_value_raw": "XAG/USD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 0,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000019",
        "instrument_id": "cfd:OTC:COPPER_CMD_USD",
        "alias_type": "symbol",
        "alias_value": "COPPER_CMD_USD",
        "alias_value_raw": "COPPER_CMD_USD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-00000000001a",
        "instrument_id": "cfd:OTC:COPPER_CMD_USD",
        "alias_type": "symbol",
        "alias_value": "COPPER.CMD/USD",
        "alias_value_raw": "COPPER.CMD/USD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 0,
        "created_at": _SEED_TS,
    },
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


def _seed_otc_instruments_idempotent() -> None:
    bind = op.get_bind()
    instruments = _instrument_table()
    aliases = _alias_table()

    existing_ids = {
        row[0]
        for row in bind.execute(
            sa.select(instruments.c.instrument_id).where(
                instruments.c.instrument_id.in_(_SEED_INSTRUMENT_IDS)
            )
        )
    }
    to_insert_instruments = [
        row for row in _EMBEDDED_SEED_INSTRUMENTS if row["instrument_id"] not in existing_ids
    ]
    if to_insert_instruments:
        op.bulk_insert(instruments, to_insert_instruments)

    existing_alias_ids = {
        row[0]
        for row in bind.execute(
            sa.select(aliases.c.alias_id).where(aliases.c.alias_id.in_(_SEED_ALIAS_IDS))
        )
    }
    to_insert_aliases = [
        row for row in _EMBEDDED_SEED_ALIASES if row["alias_id"] not in existing_alias_ids
    ]
    if to_insert_aliases:
        op.bulk_insert(aliases, to_insert_aliases)


def upgrade() -> None:
    _seed_otc_instruments_idempotent()
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=_VERSION
        )
    )
    bind = op.get_bind()
    aliases = _alias_table()
    instruments = _instrument_table()
    bind.execute(
        aliases.delete().where(aliases.c.alias_id.in_(_SEED_ALIAS_IDS))
    )
    bind.execute(
        instruments.delete().where(
            instruments.c.instrument_id.in_(_SEED_INSTRUMENT_IDS)
        )
    )
