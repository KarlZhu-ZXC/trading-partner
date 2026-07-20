"""Phase 1E E4b limit-up / sentiment / interactive-QA adapter contracts (offline)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from a_share_fixture_transport import FixtureHttpTransport, PathMappedFixtureTransport
from conftest import FixedClock
from domain.a_share.enums import LimitPoolType, SentimentSourceType
from domain.common.enums import AssetType, DataCategory, Market, ReliabilityLevel, VendorId
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.ths import ThsAShareAdapter
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

# 2024-01-16 07:00 UTC = 15:00 Asia/Shanghai closed session of 2024-01-16.
AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
TRADE_DATE = date(2024, 1, 16)
_CALENDAR_PATH = Path(__file__).resolve().parents[3] / "config" / "a_share_trading_calendar.v1.json"
_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
)
_JSON = {"content-type": "application/json; charset=utf-8"}


def _equity(symbol: str = "000001.SZ", exchange: str = "SZSE") -> Instrument:
    return Instrument(
        instrument_id=f"equity:A_SHARE:{symbol}",
        symbol=symbol,
        name="test",
        market=Market.A_SHARE,
        exchange=exchange,
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )


def _gate():
    async def no_sleep(_dt: float) -> None:
        return None

    return create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.001,
        jitter_seconds=0.0,
        clock=lambda: 0.0,
        sleep=no_sleep,
        random_func=lambda: 0.0,
    )


def _calendar() -> JsonAShareTradingCalendar:
    return JsonAShareTradingCalendar.load(_CALENDAR_PATH)


def _em_limit_success() -> EastmoneyAShareAdapter:
    root = _FIXTURE_ROOT / "eastmoney" / "limit_pools"
    transport = PathMappedFixtureTransport(
        path_to_fixture={
            "/getTopicZTPool": root / "success_zt.json",
            "/getTopicZBPool": root / "success_zb.json",
            "/getTopicDTPool": root / "success_dt.json",
            "/getYesterdayZTPool": root / "success_yz.json",
        }
    )
    return EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


def _em_sentiment(case: str) -> EastmoneyAShareAdapter:
    transport = FixtureHttpTransport(vendor="eastmoney", operation="sentiment", case=case)
    return EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


def _ths(operation: str, case: str) -> ThsAShareAdapter:
    return ThsAShareAdapter(
        FixtureHttpTransport(vendor="ths", operation=operation, case=case),
        clock=FixedClock(AS_OF),
    )


def _cninfo_qa(case: str) -> CninfoAShareAdapter:
    return CninfoAShareAdapter(
        FixtureHttpTransport(vendor="cninfo", operation="interactive_qa", case=case),
        clock=FixedClock(AS_OF),
        org_id_map={"000001": "gssz0000001"},
    )


# --- Eastmoney limit pools ----------------------------------------------------


@pytest.mark.asyncio
async def test_em_limit_pools_all_four_parse_summary_ladder() -> None:
    adapter = _em_limit_success()
    result = await adapter.get_limit_pools(
        trade_date=TRADE_DATE,
        pools=tuple(LimitPoolType),
        as_of=AS_OF,
    )
    assert result.meta.vendor is VendorId.EASTMONEY
    assert result.meta.category is DataCategory.LIMIT_UP
    ctx = result.value
    assert ctx.trade_date == TRADE_DATE
    assert ctx.limit_up_count == 2
    assert ctx.limit_down_count == 1
    assert ctx.broken_limit_count == 1
    assert ctx.broken_rate == Decimal("0.3333")
    assert ctx.max_consecutive_count == 2
    assert ctx.promotion_rate is None
    assert len(ctx.ladder) == 2
    assert ctx.ladder[0].consecutive_limit_count == 1
    assert ctx.ladder[1].consecutive_limit_count == 2
    # No reason tags from Eastmoney; not exchange editorial authority.
    assert all(e.reason_tags == () for e in ctx.entries)
    assert all(e.source_vendor is VendorId.EASTMONEY for e in ctx.entries)
    # Price scaled /100
    up = next(e for e in ctx.entries if e.instrument_id.endswith("000001.SZ"))
    assert up.last == Decimal("11.00")
    assert up.pool_type is LimitPoolType.LIMIT_UP
    assert type(up.change_percent) is Decimal


@pytest.mark.asyncio
async def test_em_limit_pools_subset_and_empty() -> None:
    root = _FIXTURE_ROOT / "eastmoney" / "limit_pools"
    transport = PathMappedFixtureTransport(
        path_to_fixture={
            "/getTopicZTPool": root / "no_data.json",
        }
    )
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    result = await adapter.get_limit_pools(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.value.entries == ()
    assert result.value.limit_up_count == 0
    assert result.value.ladder == ()


@pytest.mark.asyncio
async def test_em_limit_pools_contract_drift_and_rate_limit() -> None:
    root = _FIXTURE_ROOT / "eastmoney" / "limit_pools"
    drift = EastmoneyAShareAdapter(
        PathMappedFixtureTransport(
            path_to_fixture={"/getTopicZTPool": root / "contract_drift.json"}
        ),
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    with pytest.raises(DataContractError):
        await drift.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )

    rl = EastmoneyAShareAdapter(
        FixtureHttpTransport(vendor="eastmoney", operation="limit_pools", case="rate_limit"),
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    with pytest.raises(ProviderRateLimitError):
        await rl.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_em_limit_pools_rejects_non_trading_or_bad_types() -> None:
    adapter = _em_limit_success()
    with pytest.raises(DataContractError):
        await adapter.get_limit_pools(
            trade_date=date(2024, 1, 14),  # Sunday
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    with pytest.raises(DataContractError):
        await adapter.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP, LimitPoolType.LIMIT_UP),
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_em_limit_pool_qdate_and_tc_mismatch_rejected() -> None:
    root = _FIXTURE_ROOT / "eastmoney" / "limit_pools"

    class _CountingTransport:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.requests: list[object] = []

        async def send(self, request: object) -> object:
            self.requests.append(request)
            from application.ports.http_transport import HttpResponse

            return HttpResponse(
                status_code=200,
                headers=_JSON,
                body=self.path.read_bytes(),
            )

    q_transport = _CountingTransport(root / "qdate_mismatch.json")
    q_adapter = EastmoneyAShareAdapter(
        q_transport,  # type: ignore[arg-type]
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    with pytest.raises(DataContractError) as ei:
        await q_adapter.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    assert (ei.value.details or {}).get("rule") == "qdate_trade_date"
    assert len(q_transport.requests) == 1  # network happened; reject before emit

    tc_transport = _CountingTransport(root / "tc_mismatch.json")
    tc_adapter = EastmoneyAShareAdapter(
        tc_transport,  # type: ignore[arg-type]
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    with pytest.raises(DataContractError) as ei2:
        await tc_adapter.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    assert (ei2.value.details or {}).get("rule") == "pool_completeness"


@pytest.mark.asyncio
async def test_em_limit_historical_and_stale_as_of_zero_network() -> None:
    class _CountingTransport:
        def __init__(self) -> None:
            self.requests: list[object] = []

        async def send(self, request: object) -> object:
            self.requests.append(request)
            raise AssertionError("network must not be called")

    transport = _CountingTransport()
    adapter = EastmoneyAShareAdapter(
        transport,  # type: ignore[arg-type]
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=300,
    )
    # Arbitrary historical trade_date (not supportable closed session).
    with pytest.raises(DataContractError) as ei:
        await adapter.get_limit_pools(
            trade_date=date(2024, 1, 2),
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    assert (ei.value.details or {}).get("rule") == "current_only_trade_date"
    assert transport.requests == []

    # Stale as_of outside max_delayed_seconds.
    stale = AS_OF - timedelta(seconds=301)
    with pytest.raises(StaleMarketData):
        await adapter.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=stale,
        )
    assert transport.requests == []


# --- Eastmoney sentiment ------------------------------------------------------


@pytest.mark.asyncio
async def test_em_eastmoney_hot_success_and_filter() -> None:
    adapter = _em_sentiment("success")
    result = await adapter.get_sentiment_signals(
        None,
        trade_date=TRADE_DATE,
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        as_of=AS_OF,
    )
    assert len(result.value) == 2
    assert result.value[0].rank == 1
    assert result.value[0].instrument_id == "equity:A_SHARE:600519.SH"
    assert result.value[0].is_authoritative is False
    assert result.value[0].reliability is ReliabilityLevel.LOW
    assert "LOW_RELIABILITY_MARKET_SIGNAL" in result.meta.warnings
    filtered = await adapter.get_sentiment_signals(
        _equity("600519.SH", "SSE"),
        trade_date=TRADE_DATE,
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        as_of=AS_OF,
    )
    assert len(filtered.value) == 1


@pytest.mark.asyncio
async def test_em_concept_heat_requires_instrument_before_network() -> None:
    adapter = _em_sentiment("success")
    with pytest.raises(DataContractError) as exc:
        await adapter.get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=AS_OF,
        )
    assert (exc.value.details or {}).get("rule") == "asset_support"
    # Zero network on concept_heat fail-closed.
    assert adapter  # transport not exercised for pre-network path
    # Use a transport that would fail if called:
    failing = FixtureHttpTransport(vendor="eastmoney", operation="sentiment", case="success")
    adapter2 = EastmoneyAShareAdapter(
        failing,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    with pytest.raises(DataContractError):
        await adapter2.get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=AS_OF,
        )
    assert failing.requests == []


@pytest.mark.asyncio
async def test_em_sentiment_drift_rate_limit_no_data() -> None:
    empty = await _em_sentiment("no_data").get_sentiment_signals(
        None,
        trade_date=TRADE_DATE,
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        as_of=AS_OF,
    )
    assert empty.value == ()
    with pytest.raises(DataContractError):
        await _em_sentiment("contract_drift").get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.EASTMONEY_HOT,),
            as_of=AS_OF,
        )
    with pytest.raises(ProviderRateLimitError):
        await _em_sentiment("rate_limit").get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.EASTMONEY_HOT,),
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_em_hot_list_current_only_zero_network() -> None:
    class _CountingTransport:
        def __init__(self) -> None:
            self.requests: list[object] = []

        async def send(self, request: object) -> object:
            self.requests.append(request)
            raise AssertionError("network must not be called")

    transport = _CountingTransport()
    adapter = EastmoneyAShareAdapter(
        transport,  # type: ignore[arg-type]
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=300,
    )
    # Malicious historical trade_date labeling of current ranks.
    with pytest.raises(DataContractError) as ei:
        await adapter.get_sentiment_signals(
            None,
            trade_date=date(2024, 1, 10),
            sources=(SentimentSourceType.EASTMONEY_HOT,),
            as_of=AS_OF,
        )
    assert (ei.value.details or {}).get("rule") == "current_only_local_date"
    assert transport.requests == []

    # Stale as_of.
    with pytest.raises(StaleMarketData):
        await adapter.get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.EASTMONEY_HOT,),
            as_of=AS_OF - timedelta(seconds=301),
        )
    assert transport.requests == []


# --- THS limit reason tags + hot list -----------------------------------------


@pytest.mark.asyncio
async def test_ths_limit_reason_tags_only() -> None:
    adapter = _ths("limit_pools", "success")
    result = await adapter.get_limit_pools(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.meta.vendor is VendorId.THS
    assert result.value.limit_up_count == 2
    tags = {e.instrument_id: e.reason_tags for e in result.value.entries}
    assert tags["equity:A_SHARE:600519.SH"] == ("ths:白酒", "ths:消费复苏")
    assert all(e.source_vendor is VendorId.THS for e in result.value.entries)
    assert all(e.reliability is ReliabilityLevel.LOW for e in result.value.entries)
    with pytest.raises(DataContractError):
        await adapter.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_DOWN,),
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_ths_hot_list_and_concept_heat_blocked() -> None:
    adapter = _ths("sentiment", "success")
    result = await adapter.get_sentiment_signals(
        None,
        trade_date=TRADE_DATE,
        sources=(SentimentSourceType.THS_HOT,),
        as_of=AS_OF,
    )
    assert "LOW_RELIABILITY_MARKET_SIGNAL" in result.meta.warnings
    assert len(result.value) == 2
    assert result.value[0].source_type is SentimentSourceType.THS_HOT
    assert result.value[0].concept_tags == ("白酒", "消费")
    assert result.value[0].is_authoritative is False
    transport = FixtureHttpTransport(vendor="ths", operation="sentiment", case="success")
    blocked = ThsAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(ProviderUnavailableError):
        await blocked.get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=AS_OF,
        )
    assert transport.requests == []


@pytest.mark.asyncio
async def test_ths_limit_and_hot_drift_rate_limit() -> None:
    with pytest.raises(DataContractError):
        await _ths("limit_pools", "contract_drift").get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    with pytest.raises(ProviderRateLimitError):
        await _ths("limit_pools", "rate_limit").get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    empty = await _ths("sentiment", "no_data").get_sentiment_signals(
        None,
        trade_date=TRADE_DATE,
        sources=(SentimentSourceType.THS_HOT,),
        as_of=AS_OF,
    )
    assert "LOW_RELIABILITY_MARKET_SIGNAL" in empty.meta.warnings
    assert empty.value == ()


@pytest.mark.asyncio
async def test_ths_limit_and_hot_current_only_zero_network() -> None:
    class _CountingTransport:
        def __init__(self) -> None:
            self.requests: list[object] = []

        async def send(self, request: object) -> object:
            self.requests.append(request)
            raise AssertionError("network must not be called")

    transport = _CountingTransport()
    adapter = ThsAShareAdapter(
        transport,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        current_window_seconds=300,
    )
    with pytest.raises(DataContractError) as ei:
        await adapter.get_limit_pools(
            trade_date=date(2024, 1, 10),
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF,
        )
    assert (ei.value.details or {}).get("rule") == "current_only_local_date"
    assert transport.requests == []

    with pytest.raises(StaleMarketData):
        await adapter.get_limit_pools(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
            as_of=AS_OF - timedelta(seconds=301),
        )
    assert transport.requests == []

    with pytest.raises(DataContractError) as ei2:
        await adapter.get_sentiment_signals(
            None,
            trade_date=date(2024, 1, 10),
            sources=(SentimentSourceType.THS_HOT,),
            as_of=AS_OF,
        )
    assert (ei2.value.details or {}).get("rule") == "current_only_local_date"
    assert transport.requests == []

    with pytest.raises(StaleMarketData):
        await adapter.get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.THS_HOT,),
            as_of=AS_OF - timedelta(seconds=301),
        )
    assert transport.requests == []


# --- CNINFO interactive QA ----------------------------------------------------


@pytest.mark.asyncio
async def test_cninfo_interactive_qa_cutoff_unique_order() -> None:
    adapter = _cninfo_qa("success")
    result = await adapter.get_interactive_qa(_equity(), limit=10, as_of=AS_OF)
    assert result.meta.category is DataCategory.INTERACTIVE_QA
    # Only answered items (attachedContent) kept.
    assert len(result.value) == 1
    item = result.value[0]
    assert item.qa_key == "11_1001"
    assert item.answered_at <= AS_OF
    assert item.asked_at is not None
    assert "业绩" in item.question
    assert item.answer


@pytest.mark.asyncio
async def test_cninfo_interactive_qa_as_of_excludes_future_answers() -> None:
    early = datetime(2024, 1, 15, 8, 30, tzinfo=UTC)  # before answered_at 10:00 UTC
    adapter = CninfoAShareAdapter(
        FixtureHttpTransport(vendor="cninfo", operation="interactive_qa", case="success"),
        clock=FixedClock(AS_OF),
        org_id_map={"000001": "gssz0000001"},
    )
    result = await adapter.get_interactive_qa(_equity(), limit=10, as_of=early)
    assert result.value == ()


@pytest.mark.asyncio
async def test_cninfo_interactive_qa_no_data_drift_rate_limit() -> None:
    empty = await _cninfo_qa("no_data").get_interactive_qa(_equity(), limit=5, as_of=AS_OF)
    assert empty.value == ()
    with pytest.raises(DataContractError):
        await _cninfo_qa("contract_drift").get_interactive_qa(_equity(), limit=5, as_of=AS_OF)
    with pytest.raises(ProviderRateLimitError):
        await _cninfo_qa("rate_limit").get_interactive_qa(_equity(), limit=5, as_of=AS_OF)


@pytest.mark.asyncio
async def test_secret_safe_error_repr_for_adapters() -> None:
    """Errors must not embed cookies/tokens or raw secret-like headers."""
    adapter = _em_sentiment("rate_limit")
    with pytest.raises(ProviderRateLimitError) as exc:
        await adapter.get_sentiment_signals(
            None,
            trade_date=TRADE_DATE,
            sources=(SentimentSourceType.EASTMONEY_HOT,),
            as_of=AS_OF,
        )
    text = repr(exc.value) + str(exc.value)
    assert "cookie" not in text.lower()
    assert "authorization" not in text.lower()
    assert "api_key" not in text.lower()
