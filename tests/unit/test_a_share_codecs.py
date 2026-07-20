"""Phase 1E E2: A-share cache codec roundtrip and adversarial drift."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from domain.a_share.enums import BarInterval, TickDirection
from domain.a_share.models import (
    AShareBar,
    AShareQuote,
    IndustryPerformanceRow,
    MarketBoardSnapshot,
    OrderBookLevel,
    TradeTick,
)
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
from domain.common.errors import DataContractError
from infrastructure.providers.a_share.codecs import (
    E2_CODEC_IDS,
    bars_codec,
    industry_performance_codec,
    market_board_codec,
    order_book_codec,
    quote_codec,
    ticks_codec,
)

AS_OF = datetime(2024, 1, 16, 6, 30, tzinfo=UTC)
FETCHED = datetime(2024, 1, 16, 6, 30, 1, tzinfo=UTC)
EXPIRES = datetime(2024, 1, 16, 6, 35, tzinfo=UTC)
INSTRUMENT_ID = "equity:A_SHARE:600519.SH"


def _meta(category: DataCategory = DataCategory.MARKET_QUOTE) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=FETCHED,
        freshness=Freshness.FRESH,
        session=TradingSession.REGULAR,
        latency_ms=1,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=0,
        warnings=(),
    )


def _entry(payload: str, category: DataCategory) -> CacheEntry:
    return CacheEntry(
        key=f"v1|A_SHARE|{category.value}|{INSTRUMENT_ID}|{AS_OF.isoformat()}|op|a1b2c3d4e5f67890",
        market=Market.A_SHARE,
        category=category,
        instrument_id=INSTRUMENT_ID,
        as_of=AS_OF,
        fetched_at=FETCHED,
        expires_at=EXPIRES,
        freshness=Freshness.FRESH,
        vendor=VendorId.EASTMONEY,
        payload_json=payload,
    )


def _quote() -> AShareQuote:
    return AShareQuote(
        instrument_id=INSTRUMENT_ID,
        quote_at=datetime(2024, 1, 16, 6, 30, 5, tzinfo=UTC),
        session=TradingSession.REGULAR,
        last=Decimal("1680.50"),
        open=Decimal("1675.00"),
        high=Decimal("1690.00"),
        low=Decimal("1660.00"),
        previous_close=Decimal("1670.00"),
        change=Decimal("10.50"),
        change_percent=Decimal("0.63"),
        volume_shares=12_345_600,
        turnover_amount_cny=Decimal("2074567800.00"),
        turnover_rate=Decimal("0.45"),
        pe_ttm=Decimal("28.50"),
        pb=Decimal("10.20"),
        total_market_cap_cny=Decimal("2100000000000.00"),
        float_market_cap_cny=Decimal("2100000000000.00"),
        limit_up_price=Decimal("1848.55"),
        limit_down_price=Decimal("1512.45"),
    )


def test_e2_codec_inventory_exact() -> None:
    assert (
        frozenset(
            {
                "a_share_quote.v1",
                "a_share_bars.v1",
                "a_share_order_book.v1",
                "a_share_ticks.v1",
                "a_share_industry_performance.v1",
                "a_share_market_board.v1",
            }
        )
        == E2_CODEC_IDS
    )


def test_quote_codec_roundtrip_canonical() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = codec.encode(success)
    assert " " not in payload
    assert '"last":"1680.50"' in payload
    # Real invariant: Decimal fields are JSON strings, never JSON numbers.
    loaded = json.loads(payload)
    assert isinstance(loaded["value"]["last"], str)
    assert type(_quote().last) is Decimal
    assert not isinstance(loaded["value"]["last"], float)
    entry = _entry(payload, DataCategory.MARKET_QUOTE)
    decoded = codec.decode(entry)
    assert decoded.value.last == Decimal("1680.50")
    assert decoded.meta.cache_disposition is CacheDisposition.HIT
    assert codec.encode(ProviderSuccess(value=decoded.value, meta=_meta())) == payload


def test_bars_codec_roundtrip() -> None:
    codec = bars_codec()
    bar = AShareBar(
        start_at=datetime(2024, 1, 16, 1, 30, tzinfo=UTC),
        end_at=datetime(2024, 1, 16, 7, 0, tzinfo=UTC),
        interval=BarInterval.ONE_DAY,
        open=Decimal("1675.00"),
        high=Decimal("1690.00"),
        low=Decimal("1660.00"),
        close=Decimal("1680.50"),
        volume_shares=12_345_600,
        turnover_amount_cny=Decimal("2074567800.00"),
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
    )
    success = ProviderSuccess(value=(bar,), meta=_meta(DataCategory.MARKET_OHLCV))
    payload = codec.encode(success)
    decoded = codec.decode(_entry(payload, DataCategory.MARKET_OHLCV))
    assert decoded.value[0].close == Decimal("1680.50")
    assert decoded.value[0].adjustment is AdjustmentMethod.FORWARD_ADJUSTED


def test_order_book_and_ticks_and_structure_codecs() -> None:
    book = (
        OrderBookLevel(
            level=1,
            bid_price=Decimal("1680.00"),
            bid_volume_shares=100,
            ask_price=Decimal("1681.00"),
            ask_volume_shares=100,
        ),
    )
    book_codec = order_book_codec()
    book_success = ProviderSuccess(value=book, meta=_meta(DataCategory.MARKET_STRUCTURE))
    book_payload = book_codec.encode(book_success)
    assert book_codec.decode(_entry(book_payload, DataCategory.MARKET_STRUCTURE)).value[
        0
    ].bid_price == Decimal("1680.00")

    tick = TradeTick(
        occurred_at=datetime(2024, 1, 16, 6, 30, tzinfo=UTC),
        price=Decimal("1680.50"),
        volume_shares=300,
        direction=TickDirection.BUY,
    )
    ticks = ticks_codec()
    t_payload = ticks.encode(
        ProviderSuccess(value=(tick,), meta=_meta(DataCategory.MARKET_STRUCTURE))
    )
    assert (
        ticks.decode(_entry(t_payload, DataCategory.MARKET_STRUCTURE)).value[0].direction
        is TickDirection.BUY
    )

    row = IndustryPerformanceRow(
        industry_code="BK0477",
        industry_name="白酒",
        trade_date=date(2024, 1, 16),
        change_percent=Decimal("1.25"),
        advancing_count=20,
        declining_count=5,
        unchanged_count=2,
        leading_instrument_id=None,
        leading_change_percent=None,
        turnover_amount_cny=Decimal("5000000000.00"),
    )
    ind = industry_performance_codec()
    i_payload = ind.encode(ProviderSuccess(value=(row,), meta=_meta(DataCategory.MARKET_STRUCTURE)))
    assert (
        ind.decode(_entry(i_payload, DataCategory.MARKET_STRUCTURE)).value[0].industry_code
        == "BK0477"
    )

    board = MarketBoardSnapshot(
        trade_date=date(2024, 1, 16),
        advancing_count=2500,
        declining_count=1800,
        unchanged_count=200,
        limit_up_count=45,
        limit_down_count=12,
        broken_limit_count=8,
        total_turnover_cny=Decimal("850000000000.00"),
        median_change_percent=Decimal("0.15"),
        industries=(row,),
    )
    board_codec = market_board_codec()
    b_payload = board_codec.encode(
        ProviderSuccess(value=board, meta=_meta(DataCategory.MARKET_STRUCTURE))
    )
    decoded_board = board_codec.decode(_entry(b_payload, DataCategory.MARKET_STRUCTURE)).value
    assert decoded_board.limit_up_count == 45


def test_codec_rejects_extra_and_missing_keys() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = json.loads(codec.encode(success))
    payload["extra"] = "nope"
    with pytest.raises(DataContractError):
        codec.decode(_entry(json.dumps(payload), DataCategory.MARKET_QUOTE))

    payload2 = json.loads(codec.encode(success))
    del payload2["value"]
    with pytest.raises(DataContractError):
        codec.decode(_entry(json.dumps(payload2), DataCategory.MARKET_QUOTE))


def test_codec_rejects_json_number_float_for_decimal_fields() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = json.loads(codec.encode(success))
    payload["value"]["last"] = 1680.5
    with pytest.raises(DataContractError):
        codec.decode(_entry(json.dumps(payload), DataCategory.MARKET_QUOTE))


def test_codec_rejects_wrong_codec_id() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = json.loads(codec.encode(success))
    payload["codec"] = "a_share_bars.v1"
    with pytest.raises(DataContractError):
        codec.decode(_entry(json.dumps(payload), DataCategory.MARKET_QUOTE))


def test_codec_rejects_top_level_duplicate_keys() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = codec.encode(success)
    # Inject duplicate top-level key via raw string surgery after encode.
    bad = payload[:-1] + ',"codec":"a_share_quote.v1"}'
    with pytest.raises(DataContractError) as exc:
        codec.decode(_entry(bad, DataCategory.MARKET_QUOTE))
    assert exc.value.details.get("rule") == "duplicate_key"


def test_codec_rejects_nested_duplicate_keys() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = json.loads(codec.encode(success))
    # Build nested object with duplicate key in meta via raw JSON.
    raw = (
        '{"codec":"a_share_quote.v1","schema_version":1,'
        '"meta":{"vendor":"eastmoney","vendor":"tencent","category":"market_quote",'
        '"role":"primary","as_of":"2024-01-16T06:30:00+00:00",'
        '"fetched_at":"2024-01-16T06:30:01+00:00","freshness":"fresh",'
        '"session":"regular","latency_ms":1,"cache_disposition":"miss",'
        '"adjustment":null,"data_delay_seconds":0,"warnings":[]},'
        f'"value":{json.dumps(payload["value"])}}}'
    )
    with pytest.raises(DataContractError) as exc:
        codec.decode(_entry(raw, DataCategory.MARKET_QUOTE))
    assert exc.value.details.get("rule") == "duplicate_key"


def test_codec_rejects_nan_infinity_constants() -> None:
    codec = quote_codec()
    success = ProviderSuccess(value=_quote(), meta=_meta())
    payload = json.loads(codec.encode(success))
    # Python json.dumps allows NaN by default; inject into payload string.
    payload["value"]["last"] = "1.0"
    base = json.dumps(payload, allow_nan=False)
    # Force Infinity constant into JSON text.
    bad = base.replace('"1.0"', "Infinity")
    with pytest.raises(DataContractError) as exc:
        codec.decode(_entry(bad, DataCategory.MARKET_QUOTE))
    assert exc.value.details.get("rule") in {"no_nan_infinity", "json", "decimal_string"}
