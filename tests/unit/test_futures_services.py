"""Focused futures contract and curve service tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from application.services.futures_contract_service import FuturesContractService
from application.services.futures_curve_service import FuturesCurveService
from domain.cross_asset.enums import CurveCompleteness, CurveShape, PriceBasis
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.sqlalchemy_futures_definition_repository import (
    SqlAlchemyFuturesDefinitionRepository,
)
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
        {
            "expirationMonth": "JUN 27",
            "monthCode": "M",
            "year": 2027,
            "lastTrade": "06/28/2027",
            "expirationDate": "06/28/2027",
        },
        {
            "expirationMonth": "AUG 27",
            "monthCode": "Q",
            "year": 2027,
            "lastTrade": "08/27/2027",
            "expirationDate": "08/27/2027",
        },
        {
            "expirationMonth": "OCT 27",
            "monthCode": "V",
            "year": 2027,
            "lastTrade": "10/27/2027",
            "expirationDate": "10/27/2027",
        },
    ]
}

_SETTLEMENTS = {
    "settlements": [
        {
            "month": "DEC 26",
            "monthCode": "Z",
            "year": "2026",
            "settle": "100",
            "volume": "10",
            "openInterest": "100",
        },
        {
            "month": "FEB 27",
            "monthCode": "G",
            "year": "2027",
            "settle": "110",
            "volume": "20",
            "openInterest": "200",
        },
        {
            "month": "APR 27",
            "monthCode": "J",
            "year": "2027",
            "settle": "120",
            "volume": "30",
            "openInterest": "300",
        },
        {
            "month": "JUN 27",
            "monthCode": "M",
            "year": "2027",
            "settle": "130",
            "volume": "40",
            "openInterest": "400",
        },
        {
            "month": "AUG 27",
            "monthCode": "Q",
            "year": "2027",
            "settle": "140",
            "volume": "50",
            "openInterest": "500",
        },
        {
            "month": "OCT 27",
            "monthCode": "V",
            "year": "2027",
            "settle": "150",
            "volume": "60",
            "openInterest": "600",
        },
    ],
    "status": "Final",
}


class _FixtureTransport:
    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = request.url
        body: object
        if "ProductCalendar" in path:
            body = _CALENDAR
        elif "Settlements" in path:
            body = _SETTLEMENTS
        else:
            return HttpResponse(status_code=404, headers={}, body=b"{}")
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
        )


class _FixedClock(SystemClock):
    def now(self) -> datetime:
        return AS_OF


def _services() -> tuple[
    FuturesContractService,
    FuturesCurveService,
    SqlAlchemyFuturesDefinitionRepository,
]:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = SqlAlchemyFuturesDefinitionRepository(engine)
    adapter = CmePublicAdapter(_FixtureTransport(), clock=_FixedClock())
    contracts = FuturesContractService(
        reference_provider=adapter,
        statistics_provider=adapter,
        repository=repo,
        clock=_FixedClock(),
    )
    curve = FuturesCurveService(contract_service=contracts, clock=_FixedClock())
    return contracts, curve, repo


@pytest.mark.asyncio
async def test_contract_service_caches_product_and_chain() -> None:
    contracts, _, repo = _services()
    product = await contracts.get_product("CME:GC", AS_OF)
    assert product.ok and product.data is not None
    assert product.data.root == "GC"
    assert any(w.code == "CME_PUBLIC_REFERENCE_ONLY" for w in product.warnings)

    chain = await contracts.list_contracts("CME:GC", AS_OF, refresh=True)
    assert chain.ok and chain.data is not None
    assert len(chain.data) == 6
    assert chain.data[0].instrument_id == "future:CME:GCZ26"

    # Second read hits durable cache.
    cached = await contracts.list_contracts("CME:GC", AS_OF, refresh=False)
    assert cached.ok and cached.from_cache is True
    assert repo.get_product("CME:GC", AS_OF) is not None


@pytest.mark.asyncio
async def test_curve_service_settlement_completeness_and_spread() -> None:
    contracts, curve, _ = _services()
    await contracts.get_product("CME:GC", AS_OF)
    await contracts.list_contracts("CME:GC", AS_OF, refresh=True)
    result = await curve.build_curve(
        "CME:GC",
        price_basis=PriceBasis.SETTLEMENT,
        as_of=AS_OF,
        contract_limit=6,
        trade_date=date(2026, 7, 24),
    )
    assert result.ok and result.data is not None
    assert result.data.completeness is CurveCompleteness.COMPLETE
    assert result.data.curve_shape is CurveShape.CONTANGO
    assert result.data.front_next_spread == Decimal("10")
    assert len(result.data.contracts) == 6
    assert result.data.contracts[0].instrument_id == "future:CME:GCZ26"
    assert result.data.contracts[1].price == Decimal("110")
