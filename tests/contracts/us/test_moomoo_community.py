"""Focused contract tests for the Moomoo OpenD community hot list."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from application.dto.provider_state import CacheEntry
from conftest import FixedClock
from domain.common.enums import DataCategory, Freshness, Market, VendorId
from domain.common.errors import ProviderUnavailableError
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDOperation
from infrastructure.providers.us.context_codecs import us_community_heat_codec
from infrastructure.providers.us.moomoo_community import MoomooCommunityHeatAdapter

NOW = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)


class _Context:
    def __init__(self, *, ok: bool, payload: object) -> None:
        self.ok = ok
        self.payload = payload
        self.closed = False

    def get_hot_list(self, *, count: int) -> tuple[bool, object]:
        assert count == 2
        return self.ok, self.payload

    def close(self) -> None:
        self.closed = True


class _Limiter:
    def __init__(self) -> None:
        self.operations: list[MoomooOpenDOperation] = []

    def wait(self, operation: MoomooOpenDOperation, *, scope: str | None = None) -> None:
        assert scope is None
        self.operations.append(operation)


@pytest.mark.asyncio
async def test_hot_list_normalizes_rows_and_uses_shared_limiter(tmp_path: Path) -> None:
    del tmp_path  # pytest-owned isolation marker; adapter itself performs no file writes.
    context = _Context(
        ok=True,
        payload=(
            2,
            [
                {
                    "security": "US.NVDA",
                    "name": "NVIDIA",
                    "trade_heat": 98.5,
                    "trade_heat_change": "2.1",
                    "search_heat": 97,
                    "search_heat_change": -1,
                    "news_heat": 88,
                    "news_heat_change": 4,
                    "average_heat": 94.5,
                    "average_heat_change": 1.7,
                    "news_type": "news",
                    "news_title": "  NVIDIA   update  ",
                    "news_url": "https://example.test/nvda",
                },
                {
                    "security": "US.TSLA",
                    "name": "Tesla",
                    "trade_heat": None,
                },
            ],
        ),
    )
    limiter = _Limiter()
    adapter = MoomooCommunityHeatAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=FixedClock(NOW),
        context_factory=lambda _host, _port: context,
        opend_rate_limiter=limiter,
    )

    success = await adapter.get_community_heat(limit=2, as_of=NOW)

    assert adapter.supports(Market.US, DataCategory.COMMUNITY_HEAT)
    assert success.value.items[0].provider_code == "US.NVDA"
    assert str(success.value.items[0].average_heat) == "94.5"
    assert success.value.items[0].related_title == "NVIDIA update"
    assert success.value.items[1].rank == 2
    assert success.meta.category is DataCategory.COMMUNITY_HEAT
    assert "MOOMOO_COMMUNITY_HEAT_IS_ATTENTION_NOT_DIRECTION" in success.meta.warnings
    assert limiter.operations == [MoomooOpenDOperation.COMMUNITY_HOT_LIST]
    assert context.closed is True

    codec = us_community_heat_codec()
    payload_json = codec.encode(success)
    cached = codec.decode(
        CacheEntry(
            key="v1|US|community_heat|2026-07-21T04:00:00+00:00|abcdef0123456789",
            market=Market.US,
            category=DataCategory.COMMUNITY_HEAT,
            instrument_id=None,
            as_of=NOW,
            fetched_at=NOW,
            expires_at=NOW,
            freshness=Freshness.FRESH,
            vendor=VendorId.MOOMOO,
            payload_json=payload_json,
        )
    )
    assert cached.value == success.value
    assert '"average_heat":"94.5"' in payload_json


@pytest.mark.asyncio
async def test_old_opend_protocol_returns_specific_non_retryable_error() -> None:
    context = _Context(ok=False, payload="Unknown protocol ID.")
    adapter = MoomooCommunityHeatAdapter(
        enabled=True,
        host="localhost",
        port=11111,
        clock=FixedClock(NOW),
        context_factory=lambda _host, _port: context,
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        await adapter.get_community_heat(limit=2, as_of=NOW)

    assert caught.value.code == "MOOMOO_OPEND_VERSION_UNSUPPORTED"
    assert caught.value.retryable is False
    assert caught.value.details["minimum_opend_version"] == "10.9"
    assert context.closed is True
