"""Phase 1F US market proxy instrument seeds (QQQ, IWM).

Revision ID: 0005_phase1f_us_proxy_seeds
Revises: 0004_phase1c_research_memory
Create Date: 2026-07-18

Inserts canonical active US ETF proxies used by market context (SPY already
exists from 0003). Upgrade is idempotent; downgrade removes only these rows.
Does not read instruments_seed.json at runtime.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase1f_us_proxy_seeds"
down_revision: str | Sequence[str] | None = "0004_phase1c_research_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PHASE1F_VERSION = "phase1f_us_proxy_seeds"
_PHASE1F_DESCRIPTION = "Phase 1F US market proxy seeds: etf:US:QQQ, etf:US:IWM"

# Fixed seed timestamps (deterministic; independent of migration wall clock).
_SEED_TS = "2026-07-18T00:00:00+00:00"

_SEED_INSTRUMENT_IDS: tuple[str, ...] = (
    "etf:US:QQQ",
    "etf:US:IWM",
)

_SEED_ALIAS_IDS: tuple[str, ...] = (
    "alias_00000000-0000-7000-8000-000000000007",
    "alias_00000000-0000-7000-8000-000000000008",
)

_EMBEDDED_SEED_INSTRUMENTS: tuple[dict[str, Any], ...] = (
    {
        "instrument_id": "etf:US:QQQ",
        "symbol": "QQQ",
        "name": "Invesco QQQ Trust",
        "market": "US",
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "etf",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": "XNAS",
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "etf:US:IWM",
        "symbol": "IWM",
        "name": "iShares Russell 2000 ETF",
        "market": "US",
        "exchange": "ARCA",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "etf",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": "ARCX",
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
)

_EMBEDDED_SEED_ALIASES: tuple[dict[str, Any], ...] = (
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000007",
        "instrument_id": "etf:US:QQQ",
        "alias_type": "symbol",
        "alias_value": "QQQ",
        "alias_value_raw": "QQQ",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000008",
        "instrument_id": "etf:US:IWM",
        "alias_type": "symbol",
        "alias_value": "IWM",
        "alias_value_raw": "IWM",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
)


def _seed_us_proxy_instruments_idempotent() -> None:
    """Insert QQQ/IWM instruments and symbol aliases if not already present."""
    bind = op.get_bind()
    instruments = sa.table(
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
    aliases = sa.table(
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

    existing_ids = {
        row[0]
        for row in bind.execute(
            sa.select(instruments.c.instrument_id).where(
                instruments.c.instrument_id.in_(_SEED_INSTRUMENT_IDS)
            )
        )
    }
    to_insert_instruments = [
        row
        for row in _EMBEDDED_SEED_INSTRUMENTS
        if row["instrument_id"] not in existing_ids
    ]
    if to_insert_instruments:
        op.bulk_insert(instruments, to_insert_instruments)

    existing_alias_ids = {
        row[0]
        for row in bind.execute(
            sa.select(aliases.c.alias_id).where(
                aliases.c.alias_id.in_(_SEED_ALIAS_IDS)
            )
        )
    }
    to_insert_aliases = [
        row
        for row in _EMBEDDED_SEED_ALIASES
        if row["alias_id"] not in existing_alias_ids
    ]
    if to_insert_aliases:
        op.bulk_insert(aliases, to_insert_aliases)


def upgrade() -> None:
    _seed_us_proxy_instruments_idempotent()

    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
        sa.column("applied_at", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.execute(
        schema_versions.insert().values(
            version=_PHASE1F_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_PHASE1F_DESCRIPTION,
        )
    )


def downgrade() -> None:
    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
    )
    op.execute(
        schema_versions.delete().where(
            schema_versions.c.version == _PHASE1F_VERSION
        )
    )

    aliases = sa.table(
        "instrument_aliases",
        sa.column("alias_id", sa.Text()),
    )
    instruments = sa.table(
        "instruments",
        sa.column("instrument_id", sa.Text()),
    )
    # Aliases first (FK to instruments).
    op.execute(
        aliases.delete().where(aliases.c.alias_id.in_(_SEED_ALIAS_IDS))
    )
    op.execute(
        instruments.delete().where(
            instruments.c.instrument_id.in_(_SEED_INSTRUMENT_IDS)
        )
    )
