"""Instrument DTOs (Phase 1A wire fields + Phase 1D optional extensions)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from domain.common.enums import AliasType, AssetType, Market, ResolveMatchType
from domain.instruments.models import Instrument


class InstrumentDTO(BaseModel):
    """Canonical instrument wire DTO.

    Phase 1A fields are required. Phase 1D adds optional/defaulted fields only so
    existing snapshot envelopes remain compatible (additive defaults).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    # --- Phase 1A (frozen wire) ---
    instrument_id: str
    symbol: str
    name: str
    market: Market
    exchange: str
    currency: str
    timezone: str
    asset_type: AssetType
    # --- Phase 1D optional wire (defaults preserve 1A callers) ---
    is_active: bool = True
    listing_status: str = "active"
    underlying_instrument_id: str | None = None

    @classmethod
    def from_domain(cls, instrument: Instrument) -> InstrumentDTO:
        return cls(
            instrument_id=instrument.instrument_id,
            symbol=instrument.symbol,
            name=instrument.name,
            market=instrument.market,
            exchange=instrument.exchange,
            currency=instrument.currency,
            timezone=instrument.timezone,
            asset_type=instrument.asset_type,
            is_active=instrument.is_active,
            listing_status=instrument.listing_status,
            underlying_instrument_id=instrument.underlying_instrument_id,
        )


class InstrumentResolveResultDTO(BaseModel):
    """Success payload for ``instrument_resolve``."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    match_type: ResolveMatchType
    instrument: InstrumentDTO | None
    candidates: tuple[InstrumentDTO, ...]
    queried: str
    normalized_symbol: str | None
    alias_type: AliasType | None
    alias_value: str | None
