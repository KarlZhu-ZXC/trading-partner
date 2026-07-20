"""Phase 1D instrument master and provider state tables.

Revision ID: 0003_phase1d_instrument_provider
Revises: 0002_phase1b_research_state
Create Date: 2026-07-16

Creates instruments, instrument_aliases, provider_cache, provider_health,
provider_rate_limits. Inserts the deterministic minimum instrument seed.
schema_versions is written only inside this migration transaction.

The seed payload is embedded below and must stay in parity with
src/infrastructure/persistence/seeds/instruments_seed.json (parity test enforces).
This migration MUST NOT read the runtime JSON file.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase1d_instrument_provider"
down_revision: str | Sequence[str] | None = "0002_phase1b_research_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PHASE1D_VERSION = "phase1d_instrument_provider"
_PHASE1D_DESCRIPTION = (
    "Phase 1D Instrument Master, aliases, provider cache/health/rate limits"
)

# Fixed seed timestamps (deterministic; independent of migration wall clock).
_SEED_TS = "2026-07-16T00:00:00+00:00"

# Embedded minimum seed — keep in lockstep with instruments_seed.json (parity test).
# Order: parents before children for underlying_instrument_id FK.
_EMBEDDED_SEED_INSTRUMENTS: tuple[dict[str, Any], ...] = (
    {
        "instrument_id": "equity:A_SHARE:600519.SH",
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "market": "A_SHARE",
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "asset_type": "equity",
        "is_active": 1,
        "listing_status": "active",
        "country": "CN",
        "mic": "XSHG",
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": "0.01",
        "lot_size": "100",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "equity:US:NVDA",
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "market": "US",
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "equity",
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
        "instrument_id": "index:A_SHARE:000300.SH",
        "symbol": "000300.SH",
        "name": "沪深300",
        "market": "A_SHARE",
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "asset_type": "index",
        "is_active": 1,
        "listing_status": "active",
        "country": "CN",
        "mic": "XSHG",
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": None,
        "lot_size": None,
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "index:US:SPX",
        "symbol": "SPX",
        "name": "S&P 500 Index",
        "market": "US",
        "exchange": "INDEX",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "index",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": None,
        "underlying_instrument_id": None,
        "multiplier": None,
        "tick_size": None,
        "lot_size": None,
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "etf:A_SHARE:510300.SH",
        "symbol": "510300.SH",
        "name": "沪深300ETF",
        "market": "A_SHARE",
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "asset_type": "etf",
        "is_active": 1,
        "listing_status": "active",
        "country": "CN",
        "mic": "XSHG",
        "underlying_instrument_id": "index:A_SHARE:000300.SH",
        "multiplier": None,
        "tick_size": "0.001",
        "lot_size": "100",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "etf:US:SPY",
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "market": "US",
        "exchange": "ARCA",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "etf",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": "ARCX",
        "underlying_instrument_id": "index:US:SPX",
        "multiplier": None,
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "option:A_SHARE:10007601.SH",
        "symbol": "10007601.SH",
        "name": "A-share sample option",
        "market": "A_SHARE",
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "asset_type": "option",
        "is_active": 1,
        "listing_status": "active",
        "country": "CN",
        "mic": "XSHG",
        "underlying_instrument_id": None,
        "multiplier": "10000",
        "tick_size": "0.0001",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
    {
        "instrument_id": "option:US:NVDA260717C00150000",
        "symbol": "NVDA260717C00150000",
        "name": "NVDA sample call",
        "market": "US",
        "exchange": "CBOE",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "option",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": "XCBO",
        "underlying_instrument_id": "equity:US:NVDA",
        "multiplier": "100",
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    },
)

_EMBEDDED_SEED_ALIASES: tuple[dict[str, Any], ...] = (
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000001",
        "instrument_id": "equity:A_SHARE:600519.SH",
        "alias_type": "local_code",
        "alias_value": "600519",
        "alias_value_raw": "600519",
        "market": "A_SHARE",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000002",
        "instrument_id": "equity:A_SHARE:600519.SH",
        "alias_type": "name",
        "alias_value": "茅台",
        "alias_value_raw": "茅台",
        "market": "A_SHARE",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000003",
        "instrument_id": "equity:US:NVDA",
        "alias_type": "name_en",
        "alias_value": "nvidia",
        "alias_value_raw": "nvidia",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000004",
        "instrument_id": "index:US:SPX",
        "alias_type": "symbol",
        "alias_value": "^GSPC",
        "alias_value_raw": "^GSPC",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000005",
        "instrument_id": "option:A_SHARE:10007601.SH",
        "alias_type": "local_code",
        "alias_value": "10007601",
        "alias_value_raw": "10007601",
        "market": "A_SHARE",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
    {
        "alias_id": "alias_00000000-0000-7000-8000-000000000006",
        "instrument_id": "option:US:NVDA260717C00150000",
        "alias_type": "option_occ",
        "alias_value": "NVDA260717C00150000",
        "alias_value_raw": "NVDA260717C00150000",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": _SEED_TS,
    },
)


def _seed_instruments_and_aliases() -> None:
    """Insert the deterministic minimum seed (self-contained; no file I/O)."""
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
    op.bulk_insert(instruments, list(_EMBEDDED_SEED_INSTRUMENTS))
    op.bulk_insert(aliases, list(_EMBEDDED_SEED_ALIASES))


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False),
        sa.Column("listing_status", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("mic", sa.Text(), nullable=True),
        sa.Column("underlying_instrument_id", sa.Text(), nullable=True),
        sa.Column("multiplier", sa.Text(), nullable=True),
        sa.Column("tick_size", sa.Text(), nullable=True),
        sa.Column("lot_size", sa.Text(), nullable=True),
        sa.Column("metadata_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("instrument_id", name="pk_instruments"),
        sa.ForeignKeyConstraint(
            ["underlying_instrument_id"],
            ["instruments.instrument_id"],
            name="fk_instruments_underlying_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "asset_type",
            "market",
            "symbol",
            name="uq_instruments_asset_type_market_symbol",
        ),
        sa.CheckConstraint(
            "market IN ('A_SHARE','US')",
            name="ck_instruments_market",
        ),
        sa.CheckConstraint(
            "asset_type IN ('equity','etf','index','option')",
            name="ck_instruments_asset_type",
        ),
        sa.CheckConstraint(
            "is_active IN (0, 1)",
            name="ck_instruments_is_active",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_instruments_updated_at",
        ),
    )
    op.create_index(
        "ix_instruments_market_name",
        "instruments",
        ["market", "name"],
    )

    op.create_table(
        "instrument_aliases",
        sa.Column("alias_id", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("alias_type", sa.Text(), nullable=False),
        sa.Column("alias_value", sa.Text(), nullable=False),
        sa.Column("alias_value_raw", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("alias_id", name="pk_instrument_aliases"),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.instrument_id"],
            name="fk_instrument_aliases_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "instrument_id",
            "alias_type",
            "alias_value",
            name="uq_instrument_aliases_instrument_type_value",
        ),
        sa.CheckConstraint(
            "is_primary IN (0, 1)",
            name="ck_instrument_aliases_is_primary",
        ),
        sa.CheckConstraint(
            "market IN ('A_SHARE','US')",
            name="ck_instrument_aliases_market",
        ),
    )
    op.create_index(
        "ix_instrument_aliases_value",
        "instrument_aliases",
        ["market", "alias_value"],
    )
    op.create_index(
        "ix_instrument_aliases_instrument",
        "instrument_aliases",
        ["instrument_id"],
    )
    # Partial unique: at most one primary alias per (instrument_id, alias_type).
    op.create_index(
        "uq_instrument_aliases_one_primary",
        "instrument_aliases",
        ["instrument_id", "alias_type"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
    )

    op.create_table(
        "provider_cache",
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=True),
        sa.Column("vendor", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("freshness", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("cache_key", name="pk_provider_cache"),
    )
    op.create_index(
        "ix_provider_cache_expires",
        "provider_cache",
        ["expires_at"],
    )
    op.create_index(
        "ix_provider_cache_lookup",
        "provider_cache",
        ["market", "category", "instrument_id"],
    )

    op.create_table(
        "provider_health",
        sa.Column("vendor", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_success_at", sa.Text(), nullable=True),
        sa.Column("last_failure_at", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("circuit_state", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("vendor", "category", name="pk_provider_health"),
        sa.CheckConstraint(
            "state IN ('ok','degraded','error')",
            name="ck_provider_health_state",
        ),
        sa.CheckConstraint(
            "circuit_state IN ('closed','open','half_open')",
            name="ck_provider_health_circuit_state",
        ),
    )

    op.create_table(
        "provider_rate_limits",
        sa.Column("vendor", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Text(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("limit_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "vendor",
            "category",
            "window_start",
            name="pk_provider_rate_limits",
        ),
    )

    _seed_instruments_and_aliases()

    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
        sa.column("applied_at", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.execute(
        schema_versions.insert().values(
            version=_PHASE1D_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_PHASE1D_DESCRIPTION,
        )
    )


def downgrade() -> None:
    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
    )
    op.execute(
        schema_versions.delete().where(schema_versions.c.version == _PHASE1D_VERSION)
    )

    op.drop_table("provider_rate_limits")
    op.drop_table("provider_health")

    op.drop_index("ix_provider_cache_lookup", table_name="provider_cache")
    op.drop_index("ix_provider_cache_expires", table_name="provider_cache")
    op.drop_table("provider_cache")

    op.drop_index(
        "uq_instrument_aliases_one_primary",
        table_name="instrument_aliases",
    )
    op.drop_index(
        "ix_instrument_aliases_instrument",
        table_name="instrument_aliases",
    )
    op.drop_index("ix_instrument_aliases_value", table_name="instrument_aliases")
    op.drop_table("instrument_aliases")

    op.drop_index("ix_instruments_market_name", table_name="instruments")
    op.drop_table("instruments")
