"""Offline E4d adapter contracts: exact concept body and strict envelope."""
# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from application.ports.http_transport import HttpResponse
from conftest import FixedClock
from domain.a_share.enums import SentimentSourceType
from domain.common.enums import AssetType, Market
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

NOW = datetime(2026, 7, 17, 7, tzinfo=UTC)
CALENDAR = JsonAShareTradingCalendar.load(
    Path(__file__).resolve().parents[3] / "config" / "a_share_trading_calendar.v1.json"
)


class _Transport:
    def __init__(
        self, body: object, *, status: int = 200, content_type: str = "application/json"
    ) -> None:
        self.body = body
        self.status = status
        self.content_type = content_type
        self.requests: list[object] = []

    async def send(self, request: object) -> HttpResponse:
        self.requests.append(request)
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return HttpResponse(
            status_code=self.status, headers={"content-type": self.content_type}, body=raw
        )


def _instrument(symbol: str = "600519.SH") -> Instrument:
    code, suffix = symbol.split(".")
    return Instrument(
        instrument_id=f"equity:A_SHARE:{symbol}",
        market=Market.A_SHARE,
        symbol=symbol,
        name="test",
        exchange="SSE" if suffix == "SH" else "SZSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )


def _concept_row(
    *,
    code: str = "SH600519",
    name: str = "新能源",
    concept_id: str = "BK123",
    hit: int = 4,
    calc_time: str = "2026-07-17 15:00:00",
) -> dict[str, object]:
    return {
        "calcTime": calc_time,
        "srcSecurityCode": code,
        "conceptName": name,
        "conceptId": concept_id,
        "hitCount": hit,
        "flag": 0,
    }


def _adapter(transport: _Transport) -> EastmoneyAShareAdapter:
    return EastmoneyAShareAdapter(
        transport,
        create_isolated_eastmoney_request_gate_for_tests(min_interval_seconds=0.001),
        calendar=CALENDAR,
        clock=FixedClock(NOW),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


def _payload(rows: list[object]) -> dict[str, object]:
    return {
        "globalId": None,
        "message": "OK",
        "status": 0,
        "code": 0,
        "data": rows,
        "stack": None,
    }


def _chip_days() -> tuple[date, ...]:
    days: list[date] = []
    day = NOW.date()
    while len(days) < 120:
        days.append(day)
        if len(days) < 120:
            day = CALENDAR.previous_trading_day(day)
    return tuple(reversed(days))


def _chip_row(day: date) -> str:
    return f"{day.isoformat()},10,11,12,9,100,1000,1,2,0.2,3"


def _chip_payload(
    rows: list[str] | None, *, code: object = "600519", market: object = 1
) -> dict[str, object]:
    data = None if rows is None else {"code": code, "market": market, "klines": rows}
    return {"rc": 0, "data": data}


@pytest.mark.asyncio
async def test_concept_heat_exact_single_key_body_and_identity_mapping() -> None:
    transport = _Transport(_payload([_concept_row()]))
    result = await _adapter(transport).get_sentiment_signals(
        _instrument(),
        trade_date=NOW.date(),
        sources=(SentimentSourceType.CONCEPT_HEAT,),
        as_of=NOW,
    )
    request = transport.requests[0]
    assert request.method == "POST" and request.body == b'{"srcSecurityCode":"SH600519"}'
    assert b"cookie" not in request.body.lower() and b"token" not in request.body.lower()
    assert b"globalId" not in request.body
    signal = result.value[0]
    assert signal.source_item_id == "BK123" and signal.instrument_id is None
    assert signal.observed_at is not None and signal.rank == 1


@pytest.mark.asyncio
async def test_concept_heat_empty_success_and_shenzhen_live_shape() -> None:
    empty = await _adapter(_Transport(_payload([]))).get_sentiment_signals(
        _instrument(), trade_date=NOW.date(), sources=(SentimentSourceType.CONCEPT_HEAT,), as_of=NOW
    )
    assert empty.value == ()

    transport = _Transport(_payload([_concept_row(code="SZ000001", concept_id="BK9", name="银行")]))
    result = await _adapter(transport).get_sentiment_signals(
        _instrument("000001.SZ"),
        trade_date=NOW.date(),
        sources=(SentimentSourceType.CONCEPT_HEAT,),
        as_of=NOW,
    )
    assert transport.requests[0].body == b'{"srcSecurityCode":"SZ000001"}'
    assert result.value[0].source_item_id == "BK9"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        {
            "calcTime": "2026-07-17 15:00:00",
            "srcSecurityCode": "SH600519",
            "conceptName": "x",
            "conceptId": "BK1",
            "hitCount": True,
            "flag": 0,
        },
        {
            "calcTime": "2026-07-17 15:00:00",
            "srcSecurityCode": "SH600519",
            "conceptName": "x",
            "conceptId": "BK1",
            "hitCount": -1,
            "flag": 0,
        },
        {
            "calcTime": "2026-07-17 15:00:00",
            "srcSecurityCode": "SH600519",
            "conceptName": "x",
            "conceptId": "BK1",
            "hitCount": 1,
            "flag": 1,
        },
        _concept_row(code="SZ600519"),
        _concept_row(concept_id="NOT_BK"),
    ],
)
async def test_concept_heat_rejects_row_drift(row: object) -> None:
    with pytest.raises(DataContractError):
        await _adapter(_Transport(_payload([row]))).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )


@pytest.mark.asyncio
async def test_concept_heat_rejects_bad_envelope_and_stale_time() -> None:
    with pytest.raises(DataContractError):
        await _adapter(_Transport({"status": 0})).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )
    stale = {
        "calcTime": "2026-07-17 13:00:00",
        "srcSecurityCode": "SH600519",
        "conceptName": "x",
        "conceptId": "BK1",
        "hitCount": 1,
        "flag": 0,
    }
    with pytest.raises(DataContractError):
        await _adapter(_Transport(_payload([stale]))).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["missing_global_id", "non_null_global_id", "extra_key"])
async def test_concept_heat_rejects_exact_six_key_envelope_drift(drift: str) -> None:
    payload = _payload([])
    if drift == "missing_global_id":
        payload.pop("globalId")
    elif drift == "non_null_global_id":
        payload["globalId"] = "must-not-propagate"
    else:
        payload["unexpected"] = None
    with pytest.raises(DataContractError) as exc:
        await _adapter(_Transport(payload)).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )
    assert exc.value.details.get("rule") == "envelope"


@pytest.mark.asyncio
async def test_concept_heat_separates_business_failure_from_empty_success() -> None:
    payload = _payload([])
    payload["status"] = 1
    with pytest.raises(ProviderUnavailableError):
        await _adapter(_Transport(payload)).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )


@pytest.mark.asyncio
async def test_concept_heat_rejects_rate_limit_content_type_and_invalid_json() -> None:
    with pytest.raises(ProviderRateLimitError):
        await _adapter(_Transport(b"", status=429)).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )
    with pytest.raises(DataContractError):
        await _adapter(_Transport(_payload([]), content_type="text/html")).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )
    with pytest.raises(DataContractError):
        await _adapter(_Transport(b"{not-json")).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [_concept_row(concept_id="BK1", name="a"), _concept_row(concept_id="BK1", name="b")],
        [_concept_row(concept_id="BK1", name="a"), _concept_row(concept_id="BK2", name="a")],
        [
            _concept_row(concept_id="BK1", name="a"),
            _concept_row(concept_id="BK2", name="b", calc_time="2026-07-17 14:59:59"),
        ],
        [_concept_row(calc_time="2026-07-17 15:00:01")],
        [
            _concept_row(concept_id="BK1", name="a", hit=1),
            _concept_row(concept_id="BK2", name="b", hit=2),
        ],
    ],
)
async def test_concept_heat_rejects_identity_time_and_order_drift(
    rows: list[dict[str, object]],
) -> None:
    with pytest.raises(DataContractError):
        await _adapter(_Transport(_payload(rows))).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW,
        )


@pytest.mark.asyncio
async def test_concept_heat_pre_network_rejections() -> None:
    no_instrument = _Transport(_payload([]))
    with pytest.raises(DataContractError):
        await _adapter(no_instrument).get_sentiment_signals(
            None, trade_date=NOW.date(), sources=(SentimentSourceType.CONCEPT_HEAT,), as_of=NOW
        )
    assert no_instrument.requests == []

    historical = _Transport(_payload([]))
    with pytest.raises(StaleMarketData):
        await _adapter(historical).get_sentiment_signals(
            _instrument(),
            trade_date=NOW.date(),
            sources=(SentimentSourceType.CONCEPT_HEAT,),
            as_of=NOW - timedelta(hours=2),
        )
    assert historical.requests == []

    unsupported = _Transport(_payload([]))
    option = replace(
        _instrument(),
        instrument_id="option:A_SHARE:10000000",
        symbol="10000000",
        asset_type=AssetType.OPTION,
    )
    with pytest.raises(DataContractError):
        await _adapter(unsupported).get_sentiment_signals(
            option, trade_date=NOW.date(), sources=(SentimentSourceType.CONCEPT_HEAT,), as_of=NOW
        )
    assert unsupported.requests == []


@pytest.mark.asyncio
async def test_chip_accepts_exact_120_sessions_and_samples_clock_once() -> None:
    class _CountingClock:
        calls = 0

        def now(self) -> datetime:
            self.calls += 1
            return NOW

    class _CountingCalendar:
        previous_calls = 0

        def is_trading_day(self, day: date) -> bool:
            return CALENDAR.is_trading_day(day)

        def sessions_for(self, day: date):
            return CALENDAR.sessions_for(day)

        def previous_trading_day(self, day: date) -> date:
            self.previous_calls += 1
            return CALENDAR.previous_trading_day(day)

    rows = [_chip_row(day) for day in _chip_days()]
    transport = _Transport(_chip_payload(rows))
    clock = _CountingClock()
    calendar = _CountingCalendar()
    adapter = EastmoneyAShareAdapter(
        transport,
        create_isolated_eastmoney_request_gate_for_tests(min_interval_seconds=0.001),
        calendar=calendar,
        clock=clock,
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    result = await adapter.get_chip_distribution(_instrument(), NOW)
    assert result.value.bar_trade_date == NOW.date()
    assert result.value.as_of == NOW
    assert result.meta.adjustment.value == "forward_adjusted"
    assert clock.calls == 1
    assert calendar.previous_calls == 119


@pytest.mark.asyncio
async def test_chip_data_null_is_stable_missing_session_error() -> None:
    with pytest.raises(DataContractError) as exc:
        await _adapter(_Transport(_chip_payload(None))).get_chip_distribution(_instrument(), NOW)
    assert exc.value.details.get("rule") == "missing_session"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "market"),
    [("000001", 1), ("600519", 0), ("600519", True), (600519, 1)],
)
async def test_chip_rejects_data_identity_and_market_drift(code: object, market: object) -> None:
    rows = [_chip_row(day) for day in _chip_days()]
    with pytest.raises(DataContractError) as exc:
        await _adapter(
            _Transport(_chip_payload(rows, code=code, market=market))
        ).get_chip_distribution(_instrument(), NOW)
    assert exc.value.details.get("rule") == "identity_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["short", "extra", "duplicate", "reverse", "future"])
async def test_chip_rejects_nonexact_session_sequence(mutation: str) -> None:
    days = list(_chip_days())
    rows = [_chip_row(day) for day in days]
    if mutation == "short":
        rows.pop()
    elif mutation == "extra":
        rows.append(_chip_row(days[-1]))
    elif mutation == "duplicate":
        rows[-2] = rows[-1]
    elif mutation == "reverse":
        rows.reverse()
    else:
        rows[-1] = _chip_row(date(2026, 7, 20))
    with pytest.raises(DataContractError):
        await _adapter(_Transport(_chip_payload(rows))).get_chip_distribution(_instrument(), NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        "{day},10,11,12,9,100,1000,1,2,0.2,-1",
        "{day},10,11,12,9,-1,1000,1,2,0.2,3",
        "{day},10,11,12,9,100,-1,1,2,0.2,3",
        "{day},10,11,NaN,9,100,1000,1,2,0.2,3",
        "{day},10,13,12,9,100,1000,1,2,0.2,3",
        "{day},8,11,12,9,100,1000,1,2,0.2,3",
        "{day},10,11,12,9,100,1000,Infinity,2,0.2,3",
        "{day},10,11,12,9,100,1000,1,2,0.2",
    ],
)
async def test_chip_strictly_validates_every_numeric_field(row: str) -> None:
    days = _chip_days()
    rows = [_chip_row(day) for day in days]
    rows[0] = row.format(day=days[0].isoformat())
    with pytest.raises(DataContractError):
        await _adapter(_Transport(_chip_payload(rows))).get_chip_distribution(_instrument(), NOW)


@pytest.mark.asyncio
async def test_chip_shenzhen_live_shape_success() -> None:
    rows = [_chip_row(day) for day in _chip_days()]
    transport = _Transport(_chip_payload(rows, code="000001", market=0))
    result = await _adapter(transport).get_chip_distribution(_instrument("000001.SZ"), NOW)
    assert transport.requests[0].params["secid"] == "0.000001"
    assert result.value.bar_trade_date == NOW.date()
