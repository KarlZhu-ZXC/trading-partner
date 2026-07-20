"""Instrument identity construction and validation (pure domain).

Phase 1D design §5.5.
"""

from __future__ import annotations

from decimal import Decimal

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument


def build_canonical_instrument(
    *,
    asset_type: AssetType,
    market: Market,
    canonical_symbol: str,
    name: str,
    exchange: str,
    currency: str,
    timezone: str,
    is_active: bool = True,
    listing_status: str = "active",
    country: str | None = None,
    mic: str | None = None,
    underlying_instrument_id: str | None = None,
    multiplier: Decimal | None = None,
    tick_size: Decimal | None = None,
    lot_size: Decimal | None = None,
    metadata_version: int = 1,
) -> Instrument:
    """Validate inputs, build ``instrument_id``, return a frozen ``Instrument``."""
    if not isinstance(canonical_symbol, str):
        raise DataContractError(
            "canonical_symbol must be a string",
            details={"type": type(canonical_symbol).__name__},
        )
    symbol = canonical_symbol.strip()
    if not symbol or symbol != canonical_symbol:
        raise DataContractError(
            "canonical_symbol must be non-empty without leading/trailing whitespace",
            details={"canonical_symbol": canonical_symbol},
        )
    if ":" in symbol:
        raise DataContractError(
            "canonical_symbol must not contain ':'",
            details={"canonical_symbol": canonical_symbol},
        )

    instrument_id = build_instrument_id(asset_type, market, symbol)
    return Instrument(
        instrument_id=instrument_id,
        symbol=symbol,
        name=name,
        market=market,
        exchange=exchange,
        currency=currency,
        timezone=timezone,
        asset_type=asset_type,
        is_active=is_active,
        listing_status=listing_status,
        country=country,
        mic=mic,
        underlying_instrument_id=underlying_instrument_id,
        multiplier=multiplier,
        tick_size=tick_size,
        lot_size=lot_size,
        metadata_version=metadata_version,
    )


def assert_instrument_id_matches(instrument: Instrument) -> None:
    """Raise ``DataContractError`` if ``instrument_id`` disagrees with fields."""
    expected = build_instrument_id(
        instrument.asset_type,
        instrument.market,
        instrument.symbol,
    )
    if instrument.instrument_id != expected:
        raise DataContractError(
            "instrument_id does not match asset_type:market:symbol",
            details={
                "instrument_id": instrument.instrument_id,
                "expected": expected,
            },
        )
