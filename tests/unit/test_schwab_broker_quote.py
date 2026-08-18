"""Schwab broker-quote timestamp normalization for guarded SGOV execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domain.common.errors import DataContractError
from infrastructure.providers.account.schwab import SchwabBrokerQuoteAdapter


class _QuoteClient:
    def __init__(self, quote_at: datetime) -> None:
        self._quote_at = quote_at

    def quote(self, symbol: str) -> object:
        assert symbol == "SGOV"
        return {
            "SGOV": {
                "quote": {
                    "quoteTime": int(self._quote_at.timestamp() * 1000),
                    "bidPrice": 100.55,
                    "askPrice": 100.56,
                    "lastPrice": 100.55,
                }
            }
        }


def _adapter(quote_at: datetime) -> SchwabBrokerQuoteAdapter:
    client = _QuoteClient(quote_at)
    return SchwabBrokerQuoteAdapter(
        enabled=True,
        client_id="test-client",
        client_secret="test-secret",
        redirect_uri="https://127.0.0.1/callback",
        token_path=Path("unused-in-factory-test"),
        client_factory=lambda: client,
    )


@pytest.mark.asyncio
async def test_schwab_quote_uses_retrieval_time_for_bounded_session_boundary_skew() -> None:
    as_of = datetime(2026, 8, 17, 19, 55, 9, tzinfo=UTC)
    quote = await _adapter(as_of + timedelta(minutes=4, seconds=51)).get_quote(
        instrument_id="etf:US:SGOV",
        as_of=as_of,
    )

    assert quote.quote_at == as_of
    assert quote.source == "schwab_retrieval_time"
    assert quote.bid is not None and quote.ask is not None


@pytest.mark.asyncio
async def test_schwab_quote_rejects_future_timestamp_beyond_bounded_skew() -> None:
    as_of = datetime(2026, 8, 17, 19, 55, 9, tzinfo=UTC)

    with pytest.raises(DataContractError, match="timestamp exceeds as_of") as captured:
        await _adapter(as_of + timedelta(minutes=6, seconds=1)).get_quote(
            instrument_id="etf:US:SGOV",
            as_of=as_of,
        )
    assert captured.value.code == "SCHWAB_QUOTE_TIMESTAMP_FUTURE"


@pytest.mark.asyncio
async def test_schwab_quote_keeps_provider_time_when_not_in_future() -> None:
    as_of = datetime(2026, 8, 17, 19, 55, 9, tzinfo=UTC)
    provider_time = as_of - timedelta(seconds=2)
    quote = await _adapter(provider_time).get_quote(
        instrument_id="etf:US:SGOV",
        as_of=as_of,
    )

    assert quote.quote_at == provider_time
    assert quote.source == "schwab"
