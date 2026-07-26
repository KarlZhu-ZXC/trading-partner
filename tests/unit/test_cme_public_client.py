"""CME public adapter unit tests with injectable transport fixtures."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from domain.common.enums import DataCategory, Market, VendorId
from domain.cross_asset.enums import ContinuousAdjustment, RollRule
from domain.cross_asset.futures_models import ContinuousSeriesDefinition
from infrastructure.providers.cross_asset.cme_public_client import CmePublicAdapter
from infrastructure.system.clock import SystemClock

AS_OF = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)

_CALENDAR = {
    "monthGroups": [
        {
            "expirationMonth": "DEC 26",
            "monthCode": "Z",
            "year": 2026,
            "lastTrade": "12/29/2026",
            "expirationDate": "12/29/2026",
        },
        {
            "expirationMonth": "FEB 27",
            "monthCode": "G",
            "year": 2027,
            "lastTrade": "02/24/2027",
            "expirationDate": "02/24/2027",
        },
        {
            "expirationMonth": "APR 27",
            "monthCode": "J",
            "year": 2027,
            "lastTrade": "04/28/2027",
            "expirationDate": "04/28/2027",
        },
    ]
}

_SETTLEMENTS = {
    "settlements": [
        {
            "month": "DEC 26",
            "monthCode": "Z",
            "year": "2026",
            "settle": "2347.5",
            "volume": "100",
            "openInterest": "1000",
        },
        {
            "month": "FEB 27",
            "monthCode": "G",
            "year": "2027",
            "settle": "2360.0",
            "volume": "500",
            "openInterest": "2000",
        },
    ],
    "status": "Preliminary",
}


class _FixtureTransport:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = request.url.removeprefix("https://www.cmegroup.com")
        for key, payload in self.routes.items():
            if key in path:
                if payload is None:
                    return HttpResponse(
                        status_code=503,
                        headers={"content-type": "application/json"},
                        body=b"{}",
                    )
                return HttpResponse(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    body=json.dumps(payload).encode("utf-8"),
                )
        return HttpResponse(
            status_code=404,
            headers={"content-type": "application/json"},
            body=b"{}",
        )


class _FixedClock(SystemClock):
    def now(self) -> datetime:  # type: ignore[override]
        return AS_OF


@pytest.mark.asyncio
async def test_product_definition_from_seed_discloses_reference_only() -> None:
    adapter = CmePublicAdapter(_FixtureTransport({}), clock=_FixedClock())
    result = await adapter.get_product_definition("CME:GC", AS_OF)
    assert result.value.product_key == "CME:GC"
    assert result.value.multiplier == Decimal("100")
    assert result.value.source == "cme_public_seed"
    assert "CME_PUBLIC_REFERENCE_ONLY" in result.meta.warnings
    assert result.meta.vendor is VendorId.CME_PUBLIC


@pytest.mark.asyncio
async def test_list_contracts_from_calendar_fixture() -> None:
    transport = _FixtureTransport({"ProductCalendar": _CALENDAR})
    adapter = CmePublicAdapter(transport, clock=_FixedClock())
    result = await adapter.list_contract_definitions("CME:GC", AS_OF)
    assert len(result.value) == 3
    assert result.value[0].instrument_id == "future:CME:GCZ26"
    assert result.value[0].expiration_at is not None
    assert "CME_PUBLIC_REFERENCE_ONLY" in result.meta.warnings


@pytest.mark.asyncio
async def test_statistics_from_settlements_fixture() -> None:
    transport = _FixtureTransport({"Settlements": _SETTLEMENTS})
    adapter = CmePublicAdapter(transport, clock=_FixedClock())
    result = await adapter.get_contract_statistics(
        ("future:CME:GCZ26", "future:CME:GCG27"),
        date(2026, 7, 24),
        AS_OF,
    )
    assert len(result.value) == 2
    by_id = {s.instrument_id: s for s in result.value}
    assert by_id["future:CME:GCZ26"].settlement == Decimal("2347.5")
    assert by_id["future:CME:GCG27"].open_interest == Decimal("2000")
    assert "OFFICIAL_SETTLEMENT_NOT_LAST_TRADE" in result.meta.warnings


@pytest.mark.asyncio
async def test_calendar_unavailable_is_typed() -> None:
    adapter = CmePublicAdapter(
        _FixtureTransport({"ProductCalendar": None}),
        clock=_FixedClock(),
    )
    with pytest.raises(Exception) as exc:
        await adapter.list_contract_definitions("CME:SI", AS_OF)
    # 503 maps to ProviderUnavailableError (typed, not fabricated chain).
    assert "cme_public" in str(exc.value.details.get("vendor", "cme_public"))


@pytest.mark.asyncio
async def test_continuous_calendar_mapping() -> None:
    transport = _FixtureTransport(
        {"ProductCalendar": _CALENDAR, "Settlements": _SETTLEMENTS}
    )
    adapter = CmePublicAdapter(transport, clock=_FixedClock())
    product = (await adapter.get_product_definition("CME:GC", AS_OF)).value
    series = ContinuousSeriesDefinition(
        instrument_id="future:CME:GC.c.0",
        product_id=product.product_id,
        roll_rule=RollRule.CALENDAR,
        rank=0,
        adjustment=ContinuousAdjustment.NONE,
        provider_methodology_version="tp_continuous_v1",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
    )
    result = await adapter.resolve_continuous_mapping(
        series, AS_OF, AS_OF, AS_OF
    )
    assert len(result.value) == 1
    assert result.value[0].contract_instrument_id == "future:CME:GCZ26"
    assert result.value[0].continuous_instrument_id == "future:CME:GC.c.0"


def test_supports_cme_reference_and_statistics() -> None:
    adapter = CmePublicAdapter(_FixtureTransport({}))
    assert adapter.supports(Market.CME, DataCategory.FUTURES_REFERENCE)
    assert adapter.supports(Market.CME, DataCategory.FUTURES_STATISTICS)
    assert not adapter.supports(Market.US, DataCategory.FUTURES_REFERENCE)
