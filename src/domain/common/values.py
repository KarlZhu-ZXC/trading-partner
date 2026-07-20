"""Shared domain value helpers.

Instrument public identity is a stable business key, not a UUID:

    instrument_id = <asset_type>:<market>:<canonical_symbol>
    equity:US:NVDA
    equity:A_SHARE:600519.SH
"""

from __future__ import annotations

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError


def build_instrument_id(
    asset_type: AssetType,
    market: Market,
    canonical_symbol: str,
) -> str:
    """Build the stable public instrument identity."""
    symbol = canonical_symbol.strip()
    if not symbol:
        raise DataContractError(
            "canonical_symbol must be non-empty",
            details={"asset_type": asset_type.value, "market": market.value},
        )
    return f"{asset_type.value}:{market.value}:{symbol}"


def parse_instrument_id(instrument_id: str) -> tuple[AssetType, Market, str]:
    """Parse a public instrument identity into asset type, market, and symbol."""
    parts = instrument_id.split(":", 2)
    if len(parts) != 3:
        raise DataContractError(
            "instrument_id must be asset_type:market:canonical_symbol",
            details={"instrument_id": instrument_id},
        )
    asset_raw, market_raw, symbol = parts
    try:
        asset_type = AssetType(asset_raw)
        market = Market(market_raw)
    except ValueError as exc:
        raise DataContractError(
            "instrument_id contains unknown asset_type or market",
            details={"instrument_id": instrument_id},
        ) from exc
    if not symbol:
        raise DataContractError(
            "instrument_id symbol segment must be non-empty",
            details={"instrument_id": instrument_id},
        )
    return asset_type, market, symbol
