"""Lean FRED historical-vintage contract."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from infrastructure.providers.us.fred import FredMacroAdapter

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
API_KEY = "fred-test-secret"


class FredTransport:
    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.url.endswith("/series/observations"):
            payload = {
                "observations": [
                    {
                        "date": "2026-06-01",
                        "realtime_start": "2026-07-18",
                        "value": "4.25",
                    },
                    {
                        "date": "2026-07-01",
                        "realtime_start": "2026-07-18",
                        "value": ".",
                    },
                    {
                        "date": "2026-07-01",
                        "realtime_start": "2026-07-19",
                        "value": "9.99",
                    },
                ]
            }
        else:
            payload = {
                "seriess": [
                    {
                        "title": "Federal Funds Effective Rate",
                        "units_short": "Percent",
                        "frequency_short": "Monthly",
                    }
                ]
            }
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode(),
        )


@pytest.mark.asyncio
async def test_fred_sets_observation_and_vintage_cutoffs_and_ignores_missing_values() -> None:
    transport = FredTransport()
    success = await FredMacroAdapter(
        transport, api_key=API_KEY, clock=FixedClock(NOW)
    ).get_macro_context(series_ids=("FEDFUNDS",), lookback_days=365, as_of=NOW)

    snapshot = success.value.series[0]
    assert snapshot.latest_value == Decimal("4.25")
    assert len(snapshot.observations) == 1
    for request in transport.requests:
        assert request.params["realtime_start"] == "2026-07-18"
        assert request.params["realtime_end"] == "2026-07-18"
        assert API_KEY not in repr(request)
    assert transport.requests[1].params["observation_end"] == "2026-07-18"
