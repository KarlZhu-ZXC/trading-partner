"""Commodity spot / OTC observation domain models (Phase 3A)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from domain.common.enums import AdjustmentMethod, AssetType, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.cross_asset.enums import OfferSide, SpotVenueBasis, SpotVolumeBasis
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval

_SPOT_ASSET_TYPES = frozenset(
    {AssetType.COMMODITY_SPOT, AssetType.CFD, AssetType.BENCHMARK}
)
_SPOT_MARKETS = frozenset({Market.OTC, Market.LME, Market.CME})


def _require_str(value: object, *, field: str, max_len: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    text = value.strip()
    if len(text) > max_len:
        raise DataContractError(
            f"{field} exceeds max length",
            details={"field": field, "max": max_len},
        )
    if text != value:
        raise DataContractError(
            f"{field} must not have leading/trailing whitespace",
            details={"field": field},
        )
    return text


def _require_optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise DataContractError(
            f"{field} must not be float; use Decimal",
            details={"field": field, "rule": "no_float"},
        )
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field},
        )
    if value < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field},
        )
    return value


def _require_decimal(value: object, *, field: str) -> Decimal:
    number = _require_optional_decimal(value, field=field)
    if number is None:
        raise DataContractError(
            f"{field} is required",
            details={"field": field},
        )
    return number


def _require_spot_instrument_id(instrument_id: str) -> tuple[AssetType, Market, str]:
    text = _require_str(instrument_id, field="instrument_id", max_len=128)
    try:
        asset_type, market, symbol = parse_instrument_id(text)
    except DataContractError as exc:
        raise DataContractError(
            "instrument_id must be a well-formed instrument_id",
            details={"field": "instrument_id", "rule": "instrument_id_syntax"},
        ) from exc
    if asset_type not in _SPOT_ASSET_TYPES:
        raise DataContractError(
            "instrument_id asset type must be commodity_spot|cfd|benchmark",
            details={"asset_type": asset_type.value},
        )
    if market not in _SPOT_MARKETS:
        raise DataContractError(
            "instrument_id market must be OTC|LME|CME",
            details={"market": market.value},
        )
    if asset_type is AssetType.CFD and market is not Market.OTC:
        raise DataContractError(
            "cfd instruments must use Market.OTC",
            details={"market": market.value},
        )
    return asset_type, market, symbol


def _validate_market_bar(bar: object, *, index: int) -> MarketBar:
    if not isinstance(bar, MarketBar):
        raise DataContractError(
            "bars entries must be MarketBar",
            details={"field": f"bars[{index}]", "type": type(bar).__name__},
        )
    for field_name, value in (
        ("open", bar.open),
        ("high", bar.high),
        ("low", bar.low),
        ("close", bar.close),
        ("volume", bar.volume),
    ):
        _require_decimal(value, field=f"bars[{index}].{field_name}")
    if bar.high < bar.low:
        raise DataContractError(
            "bar high must be >= low",
            details={"field": f"bars[{index}].high", "rule": "ohlc"},
        )
    if bar.high < bar.open or bar.high < bar.close:
        raise DataContractError(
            "bar high must be >= open and close",
            details={"field": f"bars[{index}].high", "rule": "ohlc"},
        )
    if bar.low > bar.open or bar.low > bar.close:
        raise DataContractError(
            "bar low must be <= open and close",
            details={"field": f"bars[{index}].low", "rule": "ohlc"},
        )
    if bar.volume < 0:
        raise DataContractError(
            "bar volume must be nonnegative",
            details={"field": f"bars[{index}].volume", "rule": "nonnegative"},
        )
    return bar


@dataclass(frozen=True, slots=True)
class SpotObservation:
    """One commodity spot / OTC / CFD / benchmark observation.

    Dukascopy SWFX XAU/XAG are aggregated broker feeds, not LBMA benchmarks.
    Rolling commodity CFDs must keep AssetType.CFD and never claim spot cash.
    """

    instrument_id: str
    currency: str
    unit: str
    quote_at: datetime | None
    venue_basis: SpotVenueBasis
    source: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    mid: Decimal | None = None
    last: Decimal | None = None
    delivery_location: str | None = None

    def __post_init__(self) -> None:
        asset_type, _market, _symbol = _require_spot_instrument_id(self.instrument_id)
        _require_str(self.currency, field="currency", max_len=16)
        _require_str(self.unit, field="unit", max_len=64)
        if self.quote_at is not None:
            require_aware_datetime(self.quote_at, field_name="quote_at")
        if not isinstance(self.venue_basis, SpotVenueBasis):
            raise DataContractError("venue_basis must be SpotVenueBasis")
        _require_str(self.source, field="source", max_len=64)
        bid = _require_optional_decimal(self.bid, field="bid")
        ask = _require_optional_decimal(self.ask, field="ask")
        mid = _require_optional_decimal(self.mid, field="mid")
        last = _require_optional_decimal(self.last, field="last")
        if bid is None and ask is None and mid is None and last is None:
            raise DataContractError(
                "spot observation requires at least one of bid/ask/mid/last",
                details={"field": "price"},
            )
        if bid is not None and ask is not None and bid > ask:
            raise DataContractError(
                "bid must be <= ask",
                details={"bid": str(bid), "ask": str(ask)},
            )
        if mid is None and bid is not None and ask is not None:
            # mid may be omitted; callers may derive later. No auto-write.
            pass
        if self.delivery_location is not None:
            _require_str(self.delivery_location, field="delivery_location", max_len=128)
        if (
            asset_type is AssetType.BENCHMARK
            and self.venue_basis is SpotVenueBasis.DUKASCOPY_SWFX
        ):
            raise DataContractError(
                "benchmark instruments cannot use dukascopy_swfx venue_basis",
                details={"venue_basis": self.venue_basis.value},
            )


@dataclass(frozen=True, slots=True)
class CommoditySpotBarSeries:
    """Coherent OTC/spot/CFD OHLCV series with offer side and volume basis.

    Bars use UTC day boundaries for daily intervals. Volume is not exchange
    traded volume unless ``volume_basis`` says otherwise.
    """

    instrument_id: str
    interval: USBarInterval
    offer_side: OfferSide
    start: date
    end: date
    adjustment: AdjustmentMethod
    bars: tuple[MarketBar, ...]
    volume_basis: SpotVolumeBasis

    def __post_init__(self) -> None:
        _require_spot_instrument_id(self.instrument_id)
        if not isinstance(self.interval, USBarInterval):
            raise DataContractError(
                "interval must be USBarInterval",
                details={"field": "interval"},
            )
        if not isinstance(self.offer_side, OfferSide):
            raise DataContractError(
                "offer_side must be OfferSide",
                details={"field": "offer_side"},
            )
        if not isinstance(self.start, date) or isinstance(self.start, datetime):
            raise DataContractError(
                "start must be date",
                details={"field": "start"},
            )
        if not isinstance(self.end, date) or isinstance(self.end, datetime):
            raise DataContractError(
                "end must be date",
                details={"field": "end"},
            )
        if self.end < self.start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(self.adjustment, AdjustmentMethod):
            raise DataContractError(
                "adjustment must be AdjustmentMethod",
                details={"field": "adjustment"},
            )
        if self.adjustment is not AdjustmentMethod.NONE:
            raise DataContractError(
                "commodity spot/CFD bars require adjustment=none",
                details={
                    "field": "adjustment",
                    "rule": "none_only",
                    "adjustment": self.adjustment.value,
                },
            )
        if not isinstance(self.volume_basis, SpotVolumeBasis):
            raise DataContractError(
                "volume_basis must be SpotVolumeBasis",
                details={"field": "volume_basis"},
            )
        if not isinstance(self.bars, tuple):
            raise DataContractError(
                "bars must be a tuple",
                details={"field": "bars", "type": type(self.bars).__name__},
            )
        prev_ts: datetime | None = None
        seen: set[datetime] = set()
        for idx, raw_bar in enumerate(self.bars):
            bar = _validate_market_bar(raw_bar, index=idx)
            ts = bar.timestamp
            local_day = ts.astimezone(UTC).date()
            if local_day < self.start or local_day > self.end:
                raise DataContractError(
                    "bar timestamp must fall inside inclusive UTC start/end",
                    details={
                        "field": f"bars[{idx}].timestamp",
                        "rule": "inclusive_range",
                    },
                )
            if ts in seen:
                raise DataContractError(
                    "bars timestamps must be unique",
                    details={"field": f"bars[{idx}].timestamp", "rule": "unique"},
                )
            if prev_ts is not None and ts <= prev_ts:
                raise DataContractError(
                    "bars timestamps must be strictly ascending",
                    details={
                        "field": f"bars[{idx}].timestamp",
                        "rule": "strict_order",
                    },
                )
            seen.add(ts)
            prev_ts = ts
