"""DCE official adapter unit tests with injectable transport fixtures."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from application.services.futures_contract_service import FuturesContractService
from application.services.futures_curve_service import FuturesCurveService
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import ProviderUnavailableError
from domain.cross_asset.enums import PriceBasis
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.sqlalchemy_futures_definition_repository import (
    SqlAlchemyFuturesDefinitionRepository,
)
from infrastructure.providers.cross_asset.dce_official_client import DceOfficialAdapter
from infrastructure.system.clock import SystemClock

AS_OF = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)

_CONTRACT_INFO = {
    "success": True,
    "data": [
        {
            "contractId": "lh2609",
            "variety": "生猪",
            "varietyOrder": "lh",
            "unit": 16,
            "tick": 5,
            "startTradeDate": "20250916",
            "endTradeDate": "20260914",
            "endDeliveryDate": "20260918",
            "tradeType": "1",
        },
        {
            "contractId": "LH2611",
            "variety": "生猪",
            "varietyOrder": "lh",
            "unit": 16,
            "tick": 5,
            "startTradeDate": "20251114",
            "endTradeDate": "20261112",
            "endDeliveryDate": "20261116",
            "tradeType": "1",
        },
        {
            "contractId": "LH2701",
            "variety": "生猪",
            "varietyOrder": "lh",
            "unit": 16,
            "tick": 5,
            "startTradeDate": "20260116",
            "endTradeDate": "20270114",
            "endDeliveryDate": "20270118",
            "tradeType": "1",
        },
    ],
}

_DAY_QUOTES = {
    "success": True,
    "data": [
        {
            "variety": "生猪",
            "contractId": "lh2609",
            "open": "14000",
            "high": "14100",
            "low": "13900",
            "close": "14050",
            "clearPrice": "14040",
            "volumn": "100",
            "openInterest": "1000",
            "tradeType": "1",
        },
        {
            "variety": "生猪",
            "contractId": "LH2611",
            "open": "13800",
            "high": "13900",
            "low": "13700",
            "close": "13850",
            "clearPrice": "13820",
            "volumn": "200",
            "openInterest": "2000",
            "tradeType": "1",
        },
        {
            "variety": "生猪",
            "contractId": "LH2701",
            "open": "13600",
            "high": "13700",
            "low": "13500",
            "close": "13650",
            "clearPrice": "13620",
            "volumn": "300",
            "openInterest": "3000",
            "tradeType": "1",
        },
    ],
}


class _FixtureTransport:
    def __init__(
        self,
        *,
        contract_info: object | None = _CONTRACT_INFO,
        day_quotes: object | None = _DAY_QUOTES,
        status_by_path: dict[str, int] | None = None,
    ) -> None:
        self.contract_info = contract_info
        self.day_quotes = day_quotes
        self.status_by_path = status_by_path or {}
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = request.url.removeprefix("http://www.dce.com.cn")
        status = self.status_by_path.get(path, 200)
        if status != 200:
            return HttpResponse(
                status_code=status,
                headers={"content-type": "text/html"},
                body=b"<!DOCTYPE html><html>blocked</html>",
            )
        if "contractInfo" in path:
            payload = self.contract_info
        elif "dayQuotes" in path:
            payload = self.day_quotes
        else:
            return HttpResponse(
                status_code=404,
                headers={"content-type": "application/json"},
                body=b"{}",
            )
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


class _FixedClock(SystemClock):
    def now(self) -> datetime:  # type: ignore[override]
        return AS_OF


@pytest.mark.asyncio
async def test_product_definition_from_lh_seed() -> None:
    adapter = DceOfficialAdapter(_FixtureTransport(), clock=_FixedClock())
    result = await adapter.get_product_definition("DCE:LH", AS_OF)
    assert result.value.product_key == "DCE:LH"
    assert result.value.multiplier == Decimal("16")
    assert result.value.tick_size == Decimal("5")
    assert result.value.currency == "CNY"
    assert result.value.price_unit == "CNY/tonne"
    assert result.value.source == "dce_official_seed"
    assert "DCE_OFFICIAL_REFERENCE_ONLY" in result.meta.warnings
    assert result.meta.vendor is VendorId.DCE_OFFICIAL


@pytest.mark.asyncio
async def test_list_contracts_from_contract_info_fixture() -> None:
    transport = _FixtureTransport()
    adapter = DceOfficialAdapter(transport, clock=_FixedClock())
    result = await adapter.list_contract_definitions("DCE:LH", AS_OF)
    assert len(result.value) == 3
    assert result.value[0].instrument_id == "future:DCE:LH2609"
    assert result.value[0].expiration_at is not None
    assert result.value[0].last_trade_at is not None
    assert result.value[0].delivery_start == date(2026, 9, 18)
    assert result.value[0].source == VendorId.DCE_OFFICIAL.value
    assert "DCE_OFFICIAL_EOD_ONLY" in result.meta.warnings
    # Fixed host/path + POST JSON body.
    assert transport.requests[0].method == "POST"
    assert transport.requests[0].url.endswith(
        "/dcereport/publicweb/tradepara/contractInfo"
    )
    body = json.loads(transport.requests[0].body or b"{}")
    assert body == {"lang": "zh", "tradeType": "1", "varietyId": "all"}


@pytest.mark.asyncio
async def test_statistics_from_day_quotes_fixture() -> None:
    transport = _FixtureTransport()
    adapter = DceOfficialAdapter(transport, clock=_FixedClock())
    result = await adapter.get_contract_statistics(
        ("future:DCE:LH2609", "future:DCE:LH2611"),
        date(2026, 7, 24),
        AS_OF,
    )
    assert len(result.value) == 2
    by_id = {s.instrument_id: s for s in result.value}
    assert by_id["future:DCE:LH2609"].settlement == Decimal("14040")
    assert by_id["future:DCE:LH2609"].session_volume == Decimal("100")
    assert by_id["future:DCE:LH2611"].open_interest == Decimal("2000")
    assert "OFFICIAL_SETTLEMENT_NOT_LAST_TRADE" in result.meta.warnings
    assert result.meta.session.value == "closed"
    body = json.loads(transport.requests[0].body or b"{}")
    assert body["tradeDate"] == "20260724"
    assert body["statisticsType"] == "0"


@pytest.mark.asyncio
async def test_http_412_is_typed_access_restricted() -> None:
    adapter = DceOfficialAdapter(
        _FixtureTransport(
            status_by_path={
                "/dcereport/publicweb/tradepara/contractInfo": 412,
            }
        ),
        clock=_FixedClock(),
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await adapter.list_contract_definitions("DCE:LH", AS_OF)
    assert exc.value.retryable is False
    assert exc.value.code == "DCE_OFFICIAL_ACCESS_RESTRICTED"
    assert exc.value.details.get("code") == "DCE_OFFICIAL_ACCESS_RESTRICTED"
    assert exc.value.details.get("vendor") == "dce_official"


@pytest.mark.asyncio
async def test_http_429_remains_retryable_rate_limit() -> None:
    from domain.common.errors import ProviderRateLimitError

    adapter = DceOfficialAdapter(
        _FixtureTransport(
            status_by_path={
                "/dcereport/publicweb/dailystat/dayQuotes": 429,
            }
        ),
        clock=_FixedClock(),
    )
    with pytest.raises(ProviderRateLimitError) as exc:
        await adapter.get_contract_statistics(
            ("future:DCE:LH2609",),
            date(2026, 7, 24),
            AS_OF,
        )
    assert exc.value.retryable is True


def test_supports_dce_reference_and_statistics() -> None:
    adapter = DceOfficialAdapter(_FixtureTransport())
    assert adapter.supports(Market.DCE, DataCategory.FUTURES_REFERENCE)
    assert adapter.supports(Market.DCE, DataCategory.FUTURES_STATISTICS)
    assert not adapter.supports(Market.CME, DataCategory.FUTURES_REFERENCE)


@pytest.mark.asyncio
async def test_curve_service_reuses_settlement_basis_and_last_trade_expiry() -> None:
    """FuturesCurveService reuses DCE adapter without main-continuous substitution."""
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = SqlAlchemyFuturesDefinitionRepository(engine)
    adapter = DceOfficialAdapter(_FixtureTransport(), clock=_FixedClock())
    contracts = FuturesContractService(
        reference_provider=adapter,
        statistics_provider=adapter,
        repository=repo,
        clock=_FixedClock(),
    )
    curve = FuturesCurveService(contract_service=contracts, clock=_FixedClock())

    product = await contracts.get_product("DCE:LH", AS_OF)
    assert product.ok and product.data is not None
    assert any(w.code == "DCE_OFFICIAL_REFERENCE_ONLY" for w in product.warnings)

    chain = await contracts.list_contracts("DCE:LH", AS_OF, refresh=True)
    assert chain.ok and chain.data is not None
    assert chain.data[0].instrument_id == "future:DCE:LH2609"
    assert chain.data[0].expiration_at is not None

    result = await curve.build_curve(
        "DCE:LH",
        price_basis=PriceBasis.SETTLEMENT,
        as_of=AS_OF,
        contract_limit=3,
        trade_date=date(2026, 7, 24),
    )
    assert result.ok and result.data is not None
    assert len(result.data.contracts) == 3
    assert result.data.contracts[0].instrument_id == "future:DCE:LH2609"
    assert result.data.contracts[0].price == Decimal("14040")
    # Curve nodes use actual last-trade expiry from contractInfo, not main-cont.
    assert result.data.contracts[0].expiration_at is not None
    assert result.data.front_next_spread == Decimal("-220")
    assert any(w.code == "DCE_OFFICIAL_EOD_ONLY" for w in result.warnings)
