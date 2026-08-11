"""Async, typed boundary around yfinance future calendars."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import yfinance as yf
from curl_cffi.requests import Session
from yfinance.exceptions import YFRateLimitError, YFTickerMissingError

from domain.common.errors import (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@dataclass(frozen=True, slots=True)
class YahooSplitCalendarRow:
    symbol: str
    event_date: date


@dataclass(frozen=True, slots=True)
class YahooCalendarPayload:
    earnings_dates: tuple[date, ...]
    ex_dividend_dates: tuple[date, ...]
    dividend_dates: tuple[date, ...]
    splits: tuple[YahooSplitCalendarRow, ...]


class YahooCalendarClient(Protocol):
    async def get_calendar(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        timeout_seconds: float,
    ) -> YahooCalendarPayload: ...


class YFinanceCalendarClient:
    """Keep one in-process split-calendar request per bounded date window."""

    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url.strip() if proxy_url and proxy_url.strip() else None
        self._split_cache: dict[tuple[date, date], tuple[YahooSplitCalendarRow, ...]] = {}
        self._split_lock = asyncio.Lock()

    async def get_calendar(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        timeout_seconds: float,
    ) -> YahooCalendarPayload:
        ticker_task = asyncio.create_task(
            self._run(
                self._get_ticker_calendar_sync,
                symbol,
                self._proxy_url,
                timeout_seconds=timeout_seconds,
            )
        )
        split_task = asyncio.create_task(self._splits(start, end, timeout_seconds=timeout_seconds))
        calendar, splits = await asyncio.gather(ticker_task, split_task)
        assert isinstance(calendar, tuple)
        return YahooCalendarPayload(
            earnings_dates=calendar[0],
            ex_dividend_dates=calendar[1],
            dividend_dates=calendar[2],
            splits=tuple(item for item in splits if item.symbol == symbol.upper()),
        )

    async def _splits(
        self, start: date, end: date, *, timeout_seconds: float
    ) -> tuple[YahooSplitCalendarRow, ...]:
        key = (start, end)
        cached = self._split_cache.get(key)
        if cached is not None:
            return cached
        async with self._split_lock:
            cached = self._split_cache.get(key)
            if cached is not None:
                return cached
            value = await self._run(
                self._get_splits_sync,
                start,
                end,
                self._proxy_url,
                timeout_seconds=timeout_seconds,
            )
            assert isinstance(value, tuple)
            self._split_cache[key] = value
            return value

    @staticmethod
    async def _run(
        function: object,
        *args: object,
        timeout_seconds: float,
    ) -> object:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(function, *args),  # type: ignore[arg-type]
                timeout=timeout_seconds,
            )
        except TimeoutError:
            raise ProviderTimeoutError(
                "yfinance calendar request timed out",
                details={"vendor": "yfinance", "operation": "catalyst_calendar"},
            ) from None
        except YFRateLimitError:
            raise ProviderRateLimitError(
                "yfinance calendar request was rate limited",
                details={"vendor": "yfinance", "operation": "catalyst_calendar"},
            ) from None
        except YFTickerMissingError:
            raise ProviderUnavailableError(
                "yfinance calendar ticker is unavailable",
                details={"vendor": "yfinance", "operation": "catalyst_calendar"},
            ) from None
        except Exception as exc:
            raise ProviderUnavailableError(
                "yfinance calendar request failed",
                details={
                    "vendor": "yfinance",
                    "operation": "catalyst_calendar",
                    "error_type": type(exc).__name__,
                },
            ) from None

    @classmethod
    def _get_ticker_calendar_sync(
        cls, symbol: str, proxy_url: str | None
    ) -> tuple[tuple[date, ...], tuple[date, ...], tuple[date, ...]]:
        session = Session(proxy=proxy_url) if proxy_url else Session()
        try:
            raw = yf.Ticker(symbol, session=session).get_calendar()
        finally:
            session.close()
        calendar = raw if isinstance(raw, Mapping) else {}
        return (
            cls._dates(calendar.get("Earnings Date")),
            cls._dates(calendar.get("Ex-Dividend Date")),
            cls._dates(calendar.get("Dividend Date")),
        )

    @classmethod
    def _get_splits_sync(
        cls, start: date, end: date, proxy_url: str | None
    ) -> tuple[YahooSplitCalendarRow, ...]:
        session = Session(proxy=proxy_url) if proxy_url else Session()
        try:
            frame: Any = yf.Calendars(
                start=start,
                end=end,
                session=session,
            ).get_splits_calendar(limit=100)
        finally:
            session.close()
        if frame is None or frame.empty:
            return ()
        rows: list[YahooSplitCalendarRow] = []
        for raw in frame.reset_index().to_dict(orient="records"):
            if not isinstance(raw, Mapping):
                continue
            symbol = cls._text(raw.get("Symbol") or raw.get("symbol") or raw.get("ticker"))
            event_date = cls._first_date(
                raw,
                ("Event Start Date", "Start Date", "Split Date", "Payable On", "Date"),
            )
            if symbol is not None and event_date is not None and start <= event_date <= end:
                rows.append(YahooSplitCalendarRow(symbol.upper(), event_date))
        return tuple(sorted(set(rows), key=lambda item: (item.event_date, item.symbol)))

    @classmethod
    def _first_date(cls, raw: Mapping[str, object], keys: tuple[str, ...]) -> date | None:
        for key in keys:
            values = cls._dates(raw.get(key))
            if values:
                return values[0]
        return None

    @staticmethod
    def _dates(value: object) -> tuple[date, ...]:
        values = value if isinstance(value, (list, tuple)) else (value,)
        output: list[date] = []
        for raw in values:
            converted = raw.to_pydatetime() if hasattr(raw, "to_pydatetime") else raw
            if isinstance(converted, datetime):
                output.append(converted.date())
            elif isinstance(converted, date):
                output.append(converted)
            elif isinstance(converted, str):
                try:
                    output.append(date.fromisoformat(converted[:10]))
                except ValueError:
                    continue
        return tuple(output)

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None
