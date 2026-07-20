"""Async, raw-payload-free boundary for current Yahoo breadth and sector indexes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from domain.common.errors import (
    NoMarketData,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from domain.us_market.models import USBreadthSnapshot, USSectorRotation

_EXCHANGES = ("NYQ", "NMS", "ASE", "NCM", "NGM")
_UNIVERSE = (
    "Yahoo US listed securities on NYSE/Nasdaq/NYSE American exchange codes; "
    "may include ETFs and ADRs"
)
_SECTOR_INDEXES = (
    ("basic-materials", "^YH101"),
    ("communication-services", "^YH308"),
    ("consumer-cyclical", "^YH102"),
    ("consumer-defensive", "^YH205"),
    ("energy", "^YH309"),
    ("financial-services", "^YH103"),
    ("healthcare", "^YH206"),
    ("industrials", "^YH310"),
    ("real-estate", "^YH104"),
    ("technology", "^YH311"),
    ("utilities", "^YH207"),
)


class YahooBreadthClient(Protocol):
    async def get_current(self, *, timeout_seconds: float) -> USBreadthSnapshot: ...


class YFinanceBreadthClient:
    async def get_current(self, *, timeout_seconds: float) -> USBreadthSnapshot:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._get_current_sync), timeout=timeout_seconds
            )
        except TimeoutError:
            raise ProviderTimeoutError(
                "yfinance market breadth request timed out",
                details={"vendor": "yfinance", "operation": "market_breadth"},
            ) from None
        except YFRateLimitError:
            raise ProviderRateLimitError(
                "yfinance market breadth request was rate limited",
                details={"vendor": "yfinance", "operation": "market_breadth"},
            ) from None
        except (NoMarketData, ProviderRateLimitError, ProviderTimeoutError):
            raise
        except Exception as exc:
            raise ProviderUnavailableError(
                f"yfinance market breadth request failed: {type(exc).__name__}",
                details={
                    "vendor": "yfinance",
                    "operation": "market_breadth",
                    "error_type": type(exc).__name__,
                },
            ) from None

    @staticmethod
    def _count(operator: str, value: int) -> int:
        query = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("eq", ["region", "us"]),
                yf.EquityQuery("is-in", ["exchange", *_EXCHANGES]),
                yf.EquityQuery(operator, ["percentchange", value]),
            ],
        )
        result = yf.screen(query, size=1)
        if not isinstance(result, Mapping) or type(result.get("total")) is not int:
            raise NoMarketData(
                "yfinance screener omitted breadth total",
                details={"vendor": "yfinance", "operation": "market_breadth"},
            )
        return int(result["total"])

    @staticmethod
    def _return(closes: object, sessions: int) -> Decimal | None:
        try:
            if len(closes) <= sessions:  # type: ignore[arg-type]
                return None
            latest = Decimal(str(closes.iloc[-1]))  # type: ignore[attr-defined]
            prior = Decimal(str(closes.iloc[-(sessions + 1)]))  # type: ignore[attr-defined]
        except Exception:
            return None
        if prior == 0 or not latest.is_finite() or not prior.is_finite():
            return None
        return ((latest / prior) - Decimal("1")) * Decimal("100")

    @classmethod
    def _get_current_sync(cls) -> USBreadthSnapshot:
        advancing = cls._count("gt", 0)
        declining = cls._count("lt", 0)
        unchanged = cls._count("eq", 0)

        symbols = ["SPY", *(symbol for _, symbol in _SECTOR_INDEXES)]
        # One bounded batch avoids 12 separate Sector/Ticker setup paths. Keep
        # internal threads disabled because workflow-level Yahoo calls may overlap.
        histories = yf.download(
            symbols,
            period="3mo",
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            threads=False,
            progress=False,
        )
        spy_closes = histories["SPY"]["Close"].dropna()
        spy_20d = cls._return(spy_closes, 20)
        rows: list[USSectorRotation] = []
        observed_at: datetime | None = None
        for sector_key, symbol in _SECTOR_INDEXES:
            closes = histories[symbol]["Close"].dropna()
            if closes.empty:
                continue
            last_index = closes.index[-1]
            timestamp = last_index.to_pydatetime()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            observed_at = timestamp if observed_at is None else max(observed_at, timestamp)
            return_20d = cls._return(closes, 20)
            rows.append(
                USSectorRotation(
                    sector=sector_key,
                    index_symbol=symbol,
                    return_1d=cls._return(closes, 1),
                    return_5d=cls._return(closes, 5),
                    return_20d=return_20d,
                    relative_spy_20d=(
                        return_20d - spy_20d
                        if return_20d is not None and spy_20d is not None
                        else None
                    ),
                )
            )
        if observed_at is None:
            raise NoMarketData(
                "yfinance returned no sector index history",
                details={"vendor": "yfinance", "operation": "market_breadth"},
            )
        return USBreadthSnapshot(
            observed_at=observed_at,
            advancing_count=advancing,
            declining_count=declining,
            unchanged_count=unchanged,
            basis="YAHOO_SCREENER_AND_SECTOR_INDEXES",
            universe=_UNIVERSE,
            sector_rotation=tuple(rows),
        )
