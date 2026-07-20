"""Phase 1F F2a: US quote/bars cache codec roundtrip."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USQuote
from infrastructure.providers.us.codecs import us_bars_codec, us_quote_codec

NY = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 17, 20, 0, tzinfo=NY)
FETCHED = datetime(2026, 7, 17, 20, 0, 1, tzinfo=NY)
EXPIRES = datetime(2026, 7, 17, 20, 5, tzinfo=NY)
INSTRUMENT = "equity:US:NVDA"


def _meta(
    *,
    category: DataCategory = DataCategory.MARKET_QUOTE,
    adjustment: AdjustmentMethod | None = None,
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.YFINANCE,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=FETCHED,
        freshness=Freshness.DELAYED,
        session=TradingSession.POST_MARKET,
        latency_ms=12,
        cache_disposition=CacheDisposition.MISS,
        adjustment=adjustment,
        data_delay_seconds=30,
        warnings=(),
    )


def _entry(payload: str, category: DataCategory) -> CacheEntry:
    return CacheEntry(
        key=f"v1|US|{category.value}|{INSTRUMENT}|{AS_OF.isoformat()}|op|abcdef0123456789",
        market=Market.US,
        category=category,
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        fetched_at=FETCHED,
        expires_at=EXPIRES,
        freshness=Freshness.DELAYED,
        vendor=VendorId.YFINANCE,
        payload_json=payload,
    )


def test_us_quote_codec_roundtrip() -> None:
    quote = USQuote(
        instrument_id=INSTRUMENT,
        quote_at=datetime(2026, 7, 17, 16, 0, tzinfo=NY),
        session=TradingSession.POST_MARKET,
        last=Decimal("122.50"),
        open=Decimal("120.00"),
        high=Decimal("123.00"),
        low=Decimal("119.50"),
        previous_close=Decimal("119.00"),
        volume=Decimal("1400000"),
        average_volume=None,
        market_cap=None,
        beta=Decimal("1.25"),
        week_52_low=Decimal("90.00"),
        week_52_high=Decimal("140.00"),
    )
    success = ProviderSuccess(value=quote, meta=_meta())
    codec = us_quote_codec()
    payload = codec.encode(success)
    decoded = codec.decode(_entry(payload, DataCategory.MARKET_QUOTE))
    assert decoded.value == quote
    assert decoded.meta.vendor is VendorId.YFINANCE
    assert decoded.meta.cache_disposition is CacheDisposition.HIT
    assert decoded.meta.category is DataCategory.MARKET_QUOTE
    assert type(decoded.value.last) is Decimal
    assert "122.50" in payload


def test_us_bars_codec_roundtrip() -> None:
    bar = MarketBar(
        timestamp=datetime(2026, 7, 17, 16, 0, tzinfo=NY),
        open=Decimal("120.00"),
        high=Decimal("123.00"),
        low=Decimal("119.50"),
        close=Decimal("122.50"),
        volume=Decimal("1400000"),
    )
    series = USBarSeries(
        instrument_id=INSTRUMENT,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=date(2026, 7, 17),
        end=date(2026, 7, 17),
        bars=(bar,),
    )
    success = ProviderSuccess(
        value=series,
        meta=_meta(
            category=DataCategory.MARKET_OHLCV,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        ),
    )
    codec = us_bars_codec()
    payload = codec.encode(success)
    decoded = codec.decode(_entry(payload, DataCategory.MARKET_OHLCV))
    assert decoded.value == series
    assert decoded.meta.adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
    assert decoded.meta.cache_disposition is CacheDisposition.HIT
    assert decoded.value.bars[0].close == Decimal("122.50")
