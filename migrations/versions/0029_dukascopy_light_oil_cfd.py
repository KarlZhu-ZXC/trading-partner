"""Seed the Dukascopy light-oil rolling CFD identity and aliases.

Revision ID: 0029_dukascopy_light_oil_cfd
Revises: 0028_provider_route_history
Create Date: 2026-08-05

The canonical identity is a broker-feed CFD, not WTI spot or a NYMEX futures
contract.  Upgrade is idempotent; downgrade removes only these seed rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0029_dukascopy_light_oil_cfd"
down_revision: str | Sequence[str] | None = "0028_provider_route_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION = revision
_DESCRIPTION = "Dukascopy Light Oil rolling CFD instrument and aliases"
_SEED_TS = "2026-08-05T00:00:00+00:00"

_SEED_INSTRUMENT_IDS: tuple[str, ...] = ("cfd:OTC:LIGHT_CMD_USD",)
_SEED_ALIAS_IDS: tuple[str, ...] = (
    "alias_00000000-0000-7000-8000-00000000001b",
    "alias_00000000-0000-7000-8000-00000000001c",
    "alias_00000000-0000-7000-8000-00000000001d",
    "alias_00000000-0000-7000-8000-00000000001e",
)

_EMBEDDED_SEED_INSTRUMENTS: tuple[dict[str, Any], ...] = (
    {
        "instrument_id": "cfd:OTC:LIGHT_CMD_USD",
        "symbol": "LIGHT_CMD_USD",
        "name": "Dukascopy Light Oil Rolling CFD (not WTI spot, not a NYMEX future)",
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
        "tick_size": "0.001",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
)

_EMBEDDED_SEED_ALIASES: tuple[dict[str, Any], ...] = (
    {
        "alias_id": _SEED_ALIAS_IDS[0],
        "instrument_id": "cfd:OTC:LIGHT_CMD_USD",
        "alias_type": "symbol",
        "alias_value": "LIGHT_CMD_USD",
        "alias_value_raw": "LIGHT_CMD_USD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": _SEED_ALIAS_IDS[1],
        "instrument_id": "cfd:OTC:LIGHT_CMD_USD",
        "alias_type": "symbol",
        "alias_value": "USOIL",
        "alias_value_raw": "USOIL",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 0,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": _SEED_ALIAS_IDS[2],
        "instrument_id": "cfd:OTC:LIGHT_CMD_USD",
        "alias_type": "symbol",
        "alias_value": "LIGHT.CMD/USD",
        "alias_value_raw": "LIGHT.CMD/USD",
        "market": "OTC",
        "source": "local_seed",
        "is_primary": 0,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": _SEED_ALIAS_IDS[3],
        "instrument_id": "cfd:OTC:LIGHT_CMD_USD",
        "alias_type": "symbol",
        "alias_value": "LIGHT.CMD-USD",
        "alias_value_raw": "LIGHT.CMD-USD",
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


def _seed_light_oil_idempotent() -> None:
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
    _seed_light_oil_idempotent()
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
    bind.execute(aliases.delete().where(aliases.c.alias_id.in_(_SEED_ALIAS_IDS)))
    bind.execute(
        instruments
        .delete()
        .where(instruments.c.instrument_id.in_(_SEED_INSTRUMENT_IDS))
    )
