"""Instrument aggregate identity (Phase 1A fields frozen; Phase 1D extensions)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.enums import AliasType, AssetType, Market, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id, parse_instrument_id

# Wire format frozen in Phase 1D design §5.3 as ``alias_<uuid7>``.
# ``alias`` is intentionally NOT in EntityIdPrefix (Phase 1A freeze). Validation
# uses a string pattern only; callers mint IDs outside IdGenerator.
_ALIAS_ID_RE = re.compile(
    r"^alias_"
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_LISTING_STATUSES = frozenset({"active", "delisted", "suspended", "unknown"})

# Frozen source grammar (Phase 1D §5.3): local_seed | user | provider:<VendorId>
_ALIAS_SOURCE_LITERALS = frozenset({"local_seed", "user"})
_PROVIDER_SOURCE_PREFIX = "provider:"


def _validate_alias_source(source: str) -> None:
    """Reject padded/unknown sources; only exact frozen grammar is accepted."""
    if source in _ALIAS_SOURCE_LITERALS:
        return
    if source.startswith(_PROVIDER_SOURCE_PREFIX):
        vendor_raw = source.removeprefix(_PROVIDER_SOURCE_PREFIX)
        try:
            VendorId(vendor_raw)
        except ValueError as exc:
            raise DataContractError(
                "source provider vendor must be a known VendorId value",
                details={
                    "source": source,
                    "vendor": vendor_raw,
                    "expected": "provider:<VendorId value>",
                },
            ) from exc
        return
    raise DataContractError(
        "source must be local_seed, user, or provider:<VendorId value>",
        details={
            "source": source,
            "expected": "local_seed|user|provider:<VendorId>",
        },
    )


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    symbol: str  # == canonical_symbol
    name: str
    market: Market
    exchange: str  # SSE/SZSE/BSE/NASDAQ/NYSE/ARCA/CBOE/INDEX/...
    currency: str  # ISO 4217: CNY/USD/...
    timezone: str  # IANA: Asia/Shanghai / America/New_York
    asset_type: AssetType
    # --- Phase 1D extensions; Phase 1A mock/seed use design defaults ---
    is_active: bool = True
    listing_status: str = "active"  # active|delisted|suspended|unknown
    country: str | None = None  # CN|US|...
    mic: str | None = None  # exchange MIC, optional
    underlying_instrument_id: str | None = None  # options / some ETFs
    multiplier: Decimal | None = None
    tick_size: Decimal | None = None
    lot_size: Decimal | None = None
    metadata_version: int = 1

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip():
            raise DataContractError(
                "symbol must be non-empty canonical form without leading/trailing whitespace",
                details={"symbol": self.symbol},
            )
        if ":" in self.symbol:
            raise DataContractError(
                "symbol (canonical_symbol) must not contain ':'",
                details={"symbol": self.symbol},
            )
        for field_name, value in (
            ("exchange", self.exchange),
            ("currency", self.currency),
            ("timezone", self.timezone),
        ):
            if not isinstance(value, str) or not value.strip():
                raise DataContractError(
                    f"{field_name} must be a non-empty string",
                    details={field_name: value},
                )
        if self.listing_status not in _LISTING_STATUSES:
            raise DataContractError(
                "listing_status must be active|delisted|suspended|unknown",
                details={"listing_status": self.listing_status},
            )
        if not isinstance(self.metadata_version, int) or self.metadata_version < 1:
            raise DataContractError(
                "metadata_version must be a positive integer",
                details={"metadata_version": self.metadata_version},
            )
        expected = build_instrument_id(self.asset_type, self.market, self.symbol)
        if self.instrument_id != expected:
            raise DataContractError(
                "instrument_id must equal build_instrument_id(asset_type, market, symbol)",
                details={
                    "instrument_id": self.instrument_id,
                    "expected": expected,
                },
            )


@dataclass(frozen=True, slots=True)
class InstrumentAlias:
    """Alias row for instrument lookup.

    ``alias_id`` wire format is ``alias_<uuid7>`` (Phase 1D §5.3). This is a
    domain string pattern, not an ``EntityIdPrefix`` member — Phase 1A froze
    that enum without ``alias``, and prefix-freeze tests take precedence.
    """

    alias_id: str
    instrument_id: str
    alias_type: AliasType
    alias_value: str  # normalized lookup key
    alias_value_raw: str  # original input retained
    market: Market
    source: str  # local_seed|user|provider:<VendorId>
    is_primary: bool
    created_at: datetime  # timezone-aware

    def __post_init__(self) -> None:
        if not _ALIAS_ID_RE.fullmatch(self.alias_id):
            raise DataContractError(
                "alias_id must match alias_<uuid7> format",
                details={
                    "alias_id": self.alias_id,
                    "expected_pattern": _ALIAS_ID_RE.pattern,
                    "note": (
                        "alias is not EntityIdPrefix; mint as literal "
                        "'alias_' + uuid7 outside IdGenerator"
                    ),
                },
            )
        if not self.instrument_id or self.instrument_id != self.instrument_id.strip():
            raise DataContractError(
                "instrument_id must be a non-empty public identity "
                "without leading/trailing whitespace",
                details={"instrument_id": self.instrument_id},
            )
        _, parsed_market, _ = parse_instrument_id(self.instrument_id)
        if parsed_market != self.market:
            raise DataContractError(
                "instrument_id market must equal alias.market",
                details={
                    "instrument_id": self.instrument_id,
                    "parsed_market": parsed_market.value,
                    "market": self.market.value,
                },
            )
        if not self.alias_value or self.alias_value != self.alias_value.strip():
            raise DataContractError(
                "alias_value must be a non-empty normalized lookup key "
                "without leading/trailing whitespace",
                details={"alias_value": self.alias_value},
            )
        if not self.alias_value_raw or not self.alias_value_raw.strip():
            raise DataContractError(
                "alias_value_raw must contain at least one non-whitespace character",
                details={"alias_value_raw": self.alias_value_raw},
            )
        _validate_alias_source(self.source)
        require_aware_datetime(self.created_at, field_name="created_at")
