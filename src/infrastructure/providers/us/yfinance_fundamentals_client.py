"""Async boundary around yfinance's cookie/crumb-managed fundamentals client."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

import yfinance as yf
from yfinance.exceptions import YFRateLimitError, YFTickerMissingError

from domain.common.errors import (
    NoMarketData,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class YahooFundamentalsClient(Protocol):
    async def get_info(self, symbol: str, *, timeout_seconds: float) -> Mapping[str, object]: ...


class YFinanceFundamentalsClient:
    """Run yfinance off the event loop and expose only a plain mapping."""

    async def get_info(
        self, symbol: str, *, timeout_seconds: float
    ) -> Mapping[str, object]:
        try:
            value = await asyncio.wait_for(
                asyncio.to_thread(self._get_info_sync, symbol),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            raise ProviderTimeoutError(
                "yfinance fundamentals request timed out",
                details={"vendor": "yfinance", "operation": "fundamentals"},
            ) from None
        except YFRateLimitError:
            raise ProviderRateLimitError(
                "yfinance fundamentals request was rate limited",
                details={"vendor": "yfinance", "operation": "fundamentals"},
            ) from None
        except YFTickerMissingError:
            raise NoMarketData(
                "yfinance returned no fundamentals for ticker",
                details={"vendor": "yfinance", "operation": "fundamentals"},
            ) from None
        except Exception as exc:
            raise ProviderUnavailableError(
                f"yfinance fundamentals request failed: {type(exc).__name__}",
                details={
                    "vendor": "yfinance",
                    "operation": "fundamentals",
                    "error_type": type(exc).__name__,
                },
            ) from None
        if not isinstance(value, Mapping) or not value:
            raise NoMarketData(
                "yfinance returned no fundamentals",
                details={"vendor": "yfinance", "operation": "fundamentals"},
            )
        return dict(value)

    @staticmethod
    def _get_info_sync(symbol: str) -> object:
        return yf.Ticker(symbol).get_info()
