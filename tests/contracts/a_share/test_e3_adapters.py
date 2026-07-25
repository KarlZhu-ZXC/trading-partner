"""Phase 1E E3 adapter fixture contracts (offline, deterministic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from a_share_fixture_transport import FixtureHttpTransport, ScriptedHttpTransport
from application.ports.http_transport import HttpResponse
from conftest import FixedClock
from domain.a_share.enums import FinancialStatementType
from domain.common.enums import AssetType, DataCategory, Market, VendorId
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.cls import CLSAShareAdapter
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.exchanges import (
    SseAShareDisclosureAdapter,
    SzseAShareDisclosureAdapter,
)
from infrastructure.providers.a_share.iwencai import IwencaiAShareAdapter
from infrastructure.providers.a_share.sina import SinaAShareAdapter
from infrastructure.providers.a_share.ths import ThsAShareAdapter
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
_CALENDAR_PATH = Path(__file__).resolve().parents[3] / "config" / "a_share_trading_calendar.v1.json"
_JSON = {"content-type": "application/json; charset=utf-8"}
_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
)


def _equity(symbol: str = "600519.SH", exchange: str = "SSE") -> Instrument:
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


def _em(operation: str, case: str) -> EastmoneyAShareAdapter:
    transport = FixtureHttpTransport(vendor="eastmoney", operation=operation, case=case)
    return EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


# --- Sina statements ----------------------------------------------------------


@pytest.mark.asyncio
async def test_sina_statements_success() -> None:
    transport = FixtureHttpTransport(vendor="sina", operation="statements", case="success")
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_financial_statements(
        _equity(),
        statement_types=(FinancialStatementType.INCOME_STATEMENT,),
        periods=4,
        as_of=AS_OF,
    )
    assert result.meta.vendor is VendorId.SINA
    assert result.value
    assert {line.item_code for line in result.value} == {"total_revenue", "net_income"}
    assert all(type(line.value) is Decimal or line.value is None for line in result.value)
    assert all(line.published_at is None or line.published_at <= AS_OF for line in result.value)
    assert transport.requests[0].params["source"] == "lrb"


@pytest.mark.asyncio
async def test_sina_statements_no_data() -> None:
    transport = FixtureHttpTransport(vendor="sina", operation="statements", case="no_data")
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(NoMarketData):
        await adapter.get_financial_statements(
            _equity(),
            statement_types=(FinancialStatementType.INCOME_STATEMENT,),
            periods=4,
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_sina_invalid_input_zero_network() -> None:
    transport = FixtureHttpTransport(vendor="sina", operation="statements", case="success")
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError):
        await adapter.get_financial_statements(
            _equity(),
            statement_types=(),
            periods=4,
            as_of=AS_OF,
        )
    assert transport.requests == []


# --- Eastmoney fundamentals / f10 / statements / reports / news --------------


@pytest.mark.asyncio
async def test_em_fundamentals_success() -> None:
    result = await _em("fundamentals", "success").get_fundamentals(_equity(), AS_OF)
    assert result.value
    assert any(m.name == "eps" for m in result.value)
    assert result.meta.category is DataCategory.FUNDAMENTALS


@pytest.mark.asyncio
async def test_em_fundamentals_no_data() -> None:
    with pytest.raises(NoMarketData):
        await _em("fundamentals", "no_data").get_fundamentals(_equity(), AS_OF)


@pytest.mark.asyncio
async def test_em_f10_success() -> None:
    result = await _em("f10", "success").get_f10_sections(
        _equity(), sections=("company",), as_of=AS_OF
    )
    assert result.value
    assert result.value[0].section == "company"
    assert "ORG_NAME" in result.value[0].body


@pytest.mark.asyncio
async def test_em_statements_fallback_success() -> None:
    result = await _em("statements", "success").get_financial_statements(
        _equity(),
        statement_types=(FinancialStatementType.BALANCE_SHEET,),
        periods=4,
        as_of=AS_OF,
    )
    assert result.value
    assert result.meta.vendor is VendorId.EASTMONEY


@pytest.mark.asyncio
async def test_em_corporate_actions_success_empty_ok() -> None:
    # Scripted unlock + dividend responses.
    root = _FIXTURE_ROOT / "eastmoney" / "corporate_actions"
    transport = ScriptedHttpTransport(
        responses=[
            HttpResponse(200, _JSON, (root / "unlock_success.json").read_bytes()),
            HttpResponse(200, _JSON, (root / "dividend_success.json").read_bytes()),
        ]
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    result = await adapter.get_corporate_actions(_equity(), start=None, end=None, as_of=AS_OF)
    assert result.ok if hasattr(result, "ok") else True
    assert any(type(r).__name__ == "UnlockRecord" for r in result.value)
    assert any(type(r).__name__ == "DividendRecord" for r in result.value)


@pytest.mark.asyncio
async def test_em_corporate_actions_empty_success() -> None:
    root = _FIXTURE_ROOT / "eastmoney" / "corporate_actions"
    transport = ScriptedHttpTransport(
        responses=[
            HttpResponse(200, _JSON, (root / "no_data.json").read_bytes()),
            HttpResponse(200, _JSON, (root / "no_data.json").read_bytes()),
        ]
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    result = await adapter.get_corporate_actions(_equity(), start=None, end=None, as_of=AS_OF)
    assert result.value == ()


@pytest.mark.asyncio
async def test_em_reports_success_and_cutoff() -> None:
    result = await _em("reports", "success").search_reports(
        text=None,
        instrument=_equity(),
        industry_code=None,
        published_from=None,
        published_to=None,
        limit=20,
        offset=0,
        as_of=AS_OF,
    )
    assert result.value
    assert result.value[0].published_at <= AS_OF
    assert result.value[0].source_url is not None
    assert "token" not in (result.value[0].source_url or "").lower()


@pytest.mark.asyncio
async def test_em_reports_empty_success() -> None:
    result = await _em("reports", "no_data").search_reports(
        text="茅台",
        instrument=None,
        industry_code=None,
        published_from=None,
        published_to=None,
        limit=20,
        offset=0,
        as_of=AS_OF,
    )
    assert result.value == ()


@pytest.mark.asyncio
async def test_em_consensus_success() -> None:
    result = await _em("consensus", "success").get_consensus(_equity(), as_of=AS_OF)
    assert result.value
    assert result.value[0].metric == "eps"


@pytest.mark.asyncio
async def test_em_news_window_inclusive() -> None:
    start = AS_OF - timedelta(days=7)
    result = await _em("news", "success").get_news(
        _equity(), start=start, end=AS_OF, limit=20, as_of=AS_OF
    )
    assert result.value
    for item in result.value:
        assert start <= item.published_at <= AS_OF


# --- Cninfo / exchanges announcements ----------------------------------------


@pytest.mark.asyncio
async def test_cninfo_announcements_success() -> None:
    transport = FixtureHttpTransport(vendor="cninfo", operation="announcements", case="success")
    adapter = CninfoAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_announcements(_equity(), limit=10, as_of=AS_OF)
    assert result.value
    assert result.meta.vendor is VendorId.CNINFO
    assert result.value[0].published_at <= AS_OF
    assert result.value[0].source_url.startswith("http")


@pytest.mark.asyncio
async def test_cninfo_announcements_empty_success() -> None:
    transport = FixtureHttpTransport(vendor="cninfo", operation="announcements", case="no_data")
    adapter = CninfoAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_announcements(_equity(), limit=10, as_of=AS_OF)
    assert result.value == ()


@pytest.mark.asyncio
async def test_cninfo_rate_limit() -> None:
    transport = FixtureHttpTransport(vendor="cninfo", operation="announcements", case="rate_limit")
    adapter = CninfoAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(ProviderRateLimitError):
        await adapter.get_announcements(_equity(), limit=10, as_of=AS_OF)


@pytest.mark.asyncio
async def test_cninfo_does_not_claim_corporate_actions() -> None:
    transport = FixtureHttpTransport(vendor="cninfo", operation="announcements", case="success")
    adapter = CninfoAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as exc:
        await adapter.get_corporate_actions(_equity(), start=None, end=None, as_of=AS_OF)
    assert exc.value.details.get("rule") == "unsupported"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_sse_announcements_success() -> None:
    transport = FixtureHttpTransport(vendor="sse", operation="announcements", case="success")
    adapter = SseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_announcements(_equity("600519.SH", "SSE"), limit=10, as_of=AS_OF)
    assert result.value
    assert result.meta.vendor is VendorId.SSE


@pytest.mark.asyncio
async def test_sse_rejects_sz_symbol() -> None:
    transport = FixtureHttpTransport(vendor="sse", operation="announcements", case="success")
    adapter = SseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError):
        await adapter.get_announcements(_equity("000001.SZ", "SZSE"), limit=10, as_of=AS_OF)
    assert transport.requests == []


@pytest.mark.asyncio
async def test_szse_announcements_success() -> None:
    transport = FixtureHttpTransport(vendor="szse", operation="announcements", case="success")
    adapter = SzseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_announcements(_equity("000001.SZ", "SZSE"), limit=10, as_of=AS_OF)
    assert result.value
    assert result.meta.vendor is VendorId.SZSE


# --- CLS / THS / iwencai ------------------------------------------------------


@pytest.mark.asyncio
async def test_cls_news_success() -> None:
    transport = FixtureHttpTransport(vendor="cls", operation="news", case="success")
    adapter = CLSAShareAdapter(transport, clock=FixedClock(AS_OF))
    start = AS_OF - timedelta(days=7)
    result = await adapter.get_news(None, start=start, end=AS_OF, limit=20, as_of=AS_OF)
    assert result.value
    assert result.value[0].source_name == "财联社"
    for item in result.value:
        assert start <= item.published_at <= AS_OF


@pytest.mark.asyncio
async def test_cls_news_empty_success() -> None:
    transport = FixtureHttpTransport(vendor="cls", operation="news", case="no_data")
    adapter = CLSAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_news(
        None, start=AS_OF - timedelta(days=1), end=AS_OF, limit=10, as_of=AS_OF
    )
    assert result.value == ()


@pytest.mark.asyncio
async def test_ths_consensus_success() -> None:
    transport = FixtureHttpTransport(vendor="ths", operation="consensus", case="success")
    adapter = ThsAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_consensus(_equity(), as_of=AS_OF)
    assert result.value
    assert result.value[0].metric == "eps"
    assert type(result.value[0].mean) is Decimal


@pytest.mark.asyncio
async def test_ths_consensus_no_data() -> None:
    transport = FixtureHttpTransport(vendor="ths", operation="consensus", case="no_data")
    adapter = ThsAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(NoMarketData):
        await adapter.get_consensus(_equity(), as_of=AS_OF)


@pytest.mark.asyncio
async def test_ths_does_not_search_reports() -> None:
    transport = FixtureHttpTransport(vendor="ths", operation="consensus", case="success")
    adapter = ThsAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as exc:
        await adapter.search_reports(
            text="x",
            instrument=None,
            industry_code=None,
            published_from=None,
            published_to=None,
            limit=10,
            offset=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "unsupported"


@pytest.mark.asyncio
async def test_iwencai_disabled_explicit() -> None:
    transport = FixtureHttpTransport(vendor="iwencai", operation="reports", case="success")
    adapter = IwencaiAShareAdapter(
        transport, clock=FixedClock(AS_OF), enabled=False, api_key="secret-key"
    )
    assert adapter.is_configured() is False
    with pytest.raises(ProviderNotConfigured):
        await adapter.search_reports(
            text="白酒",
            instrument=None,
            industry_code=None,
            published_from=None,
            published_to=None,
            limit=10,
            offset=0,
            as_of=AS_OF,
        )
    assert transport.requests == []


@pytest.mark.asyncio
async def test_iwencai_blank_key_not_configured() -> None:
    adapter = IwencaiAShareAdapter(
        FixtureHttpTransport(vendor="iwencai", operation="reports", case="success"),
        enabled=True,
        api_key="   ",
    )
    assert adapter.is_configured() is False


@pytest.mark.asyncio
async def test_iwencai_success_key_not_in_source_url() -> None:
    secret = "super-secret-iwencai-key-xyz"
    transport = FixtureHttpTransport(vendor="iwencai", operation="reports", case="success")
    adapter = IwencaiAShareAdapter(
        transport,
        clock=FixedClock(AS_OF),
        enabled=True,
        api_key=secret,
    )
    result = await adapter.search_reports(
        text="白酒",
        instrument=None,
        industry_code=None,
        published_from=None,
        published_to=None,
        limit=10,
        offset=0,
        as_of=AS_OF,
    )
    assert result.value
    blob = repr(result) + "".join((r.source_url or "") + r.title for r in result.value)
    assert secret not in blob
    assert secret not in repr(adapter)


@pytest.mark.asyncio
async def test_iwencai_bad_host_rejected() -> None:
    with pytest.raises(DataContractError):
        IwencaiAShareAdapter(
            FixtureHttpTransport(vendor="iwencai", operation="reports", case="success"),
            enabled=True,
            api_key="k",
            base_url="https://evil.example.com",
        )


# --- Publication cutoff equality / future / unknown historical ---------------


@pytest.mark.asyncio
async def test_publication_cutoff_at_equality_kept() -> None:
    # Notice date exactly AS_OF must be kept (published_at <= as_of).
    body = {
        "version": "test",
        "result": {
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "REPORT_DATE": "2023-12-31",
                    "NOTICE_DATE": AS_OF.isoformat(),
                    "EPSJB": "1.00",
                    "BPS": "1.00",
                    "ROE_WEIGHT": "1.00",
                    "MGJYXJJE": "1.00",
                    "XSMLL": "1.00",
                    "TOTALOPERATEREVE": "1",
                    "PARENTNETPROFIT": "1",
                    "KCFJCXSYJLR": "1",
                }
            ]
        },
        "success": True,
        "code": 0,
    }
    import json

    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="fundamentals",
        case="success",
        body_override=json.dumps(body).encode(),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    result = await adapter.get_fundamentals(_equity(), AS_OF)
    assert result.value


@pytest.mark.asyncio
async def test_future_publication_excluded() -> None:
    future = AS_OF + timedelta(days=10)
    body = {
        "version": "test",
        "result": {
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "REPORT_DATE": "2023-12-31",
                    "NOTICE_DATE": future.isoformat(),
                    "EPSJB": "1.00",
                    "BPS": "1.00",
                    "ROE_WEIGHT": "1.00",
                    "MGJYXJJE": "1.00",
                    "XSMLL": "1.00",
                    "TOTALOPERATEREVE": "1",
                    "PARENTNETPROFIT": "1",
                    "KCFJCXSYJLR": "1",
                }
            ]
        },
        "success": True,
        "code": 0,
    }
    import json

    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="fundamentals",
        case="success",
        body_override=json.dumps(body).encode(),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(NoMarketData):
        await adapter.get_fundamentals(_equity(), AS_OF)


@pytest.mark.asyncio
async def test_unknown_publication_historical_excluded_warning() -> None:
    historical_as_of = datetime(2020, 1, 1, tzinfo=UTC)
    clock = FixedClock(AS_OF)  # now is 2024; as_of is historical
    body = {
        "version": "test",
        "result": {
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "REPORT_DATE": "2019-12-31",
                    "NOTICE_DATE": None,
                    "EPSJB": "1.00",
                    "BPS": "1.00",
                    "ROE_WEIGHT": "1.00",
                    "MGJYXJJE": "1.00",
                    "XSMLL": "1.00",
                    "TOTALOPERATEREVE": "1",
                    "PARENTNETPROFIT": "1",
                    "KCFJCXSYJLR": "1",
                }
            ]
        },
        "success": True,
        "code": 0,
    }
    import json

    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="fundamentals",
        case="success",
        body_override=json.dumps(body).encode(),
    )
    adapter = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=clock)
    # All rows excluded → NoMarketData; if partial remains, warning set.
    with pytest.raises(NoMarketData):
        await adapter.get_fundamentals(_equity(), historical_as_of)
