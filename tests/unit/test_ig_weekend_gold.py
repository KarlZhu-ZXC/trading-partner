"""Focused IG Weekend Gold browser fallback coverage."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    Market,
    SourceRole,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.cross_asset.enums import SpotVenueBasis
from domain.instruments.models import Instrument
from infrastructure.providers.cross_asset.ig_weekend_gold import (
    IGWeekendGoldApifyAdapter,
)

NOW = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)
XAU = Instrument(
    instrument_id="commodity_spot:OTC:XAUUSD",
    symbol="XAUUSD",
    name="OTC Gold",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


class _Transport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.popleft()


def _response(value: object) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(value).encode(),
    )


@pytest.mark.asyncio
async def test_ig_weekend_gold_validates_page_and_caches_quote() -> None:
    clock = _Clock()
    transport = _Transport(
        [
            _response({"data": {"id": "run_1", "status": "READY"}}),
            _response(
                {
                    "data": {
                        "id": "run_1",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "dataset_1",
                    }
                }
            ),
            _response(
                [
                    {
                        "url": (
                            "https://www.ig.com/en/indices/markets-indices/"
                            "weekend-gold"
                        ),
                        "title": "Weekend Gold | IG International",
                        "upstreamStatus": 200,
                        "identity": True,
                        "marketCode": True,
                        "sell": "4,041.9",
                        "buy": "4,047.9",
                    }
                ]
            ),
        ]
    )
    adapter = IGWeekendGoldApifyAdapter(
        transport,
        clock=clock,
        enabled=True,
        api_token="test-token",
        poll_interval_seconds=0.001,
    )

    first = await adapter.get_quote(XAU, NOW)
    clock.value = datetime(2026, 8, 1, 11, 5, tzinfo=UTC)
    cached = await adapter.get_quote(XAU, clock.value)

    assert first.value.bid == Decimal("4041.9")
    assert first.value.ask == Decimal("4047.9")
    assert first.value.mid == Decimal("4044.9")
    assert first.value.venue_basis is SpotVenueBasis.IG_WEEKEND_CFD
    assert first.meta.vendor is VendorId.IG_WEEKEND_GOLD
    assert first.meta.role is SourceRole.FALLBACK
    assert "WEEKEND_PROXY_NOT_SPOT" in first.meta.warnings
    assert cached.meta.cache_disposition is CacheDisposition.HIT
    assert cached.meta.data_delay_seconds == 300
    assert len(transport.requests) == 3
    assert "test-token" not in repr(transport.requests[0])


@pytest.mark.asyncio
async def test_ig_weekend_gold_rejects_unverified_page() -> None:
    transport = _Transport(
        [
            _response(
                {
                    "data": {
                        "id": "run_2",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "dataset_2",
                    }
                }
            ),
            _response(
                [
                    {
                        "url": "https://example.com/",
                        "title": "Not IG",
                        "upstreamStatus": 200,
                        "identity": False,
                        "marketCode": False,
                        "sell": "4041.9",
                        "buy": "4047.9",
                    }
                ]
            ),
        ]
    )
    adapter = IGWeekendGoldApifyAdapter(
        transport,
        clock=_Clock(),
        enabled=True,
        api_token="test-token",
    )

    with pytest.raises(DataContractError, match="identity"):
        await adapter.get_quote(XAU, NOW)
