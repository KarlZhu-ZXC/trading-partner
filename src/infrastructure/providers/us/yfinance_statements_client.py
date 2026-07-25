"""Async boundary around yfinance's normalized financial-statement tables."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Protocol, cast

import yfinance as yf
from yfinance.exceptions import YFRateLimitError, YFTickerMissingError

from domain.common.errors import (
    NoMarketData,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class YahooStatementsClient(Protocol):
    async def get_tables(
        self, symbol: str, *, frequency: str, timeout_seconds: float
    ) -> Mapping[str, tuple[tuple[date, Mapping[str, object]], ...]]: ...


class YFinanceStatementsClient:
    async def get_tables(
        self, symbol: str, *, frequency: str, timeout_seconds: float
    ) -> Mapping[str, tuple[tuple[date, Mapping[str, object]], ...]]:
        try:
            tables = await asyncio.wait_for(
                asyncio.to_thread(self._get_tables_sync, symbol, frequency),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            raise ProviderTimeoutError(
                "yfinance statements request timed out",
                details={"vendor": "yfinance", "operation": "statements"},
            ) from None
        except YFRateLimitError:
            raise ProviderRateLimitError(
                "yfinance statements request was rate limited",
                details={"vendor": "yfinance", "operation": "statements"},
            ) from None
        except YFTickerMissingError:
            raise NoMarketData(
                "yfinance returned no statements for ticker",
                details={"vendor": "yfinance", "operation": "statements"},
            ) from None
        except Exception as exc:
            raise ProviderUnavailableError(
                f"yfinance statements request failed: {type(exc).__name__}",
                details={
                    "vendor": "yfinance",
                    "operation": "statements",
                    "error_type": type(exc).__name__,
                },
            ) from None
        if not any(tables.values()):
            raise NoMarketData(
                "yfinance returned no financial statement rows",
                details={"vendor": "yfinance", "operation": "statements"},
            )
        return tables

    @classmethod
    def _get_tables_sync(
        cls, symbol: str, frequency: str
    ) -> Mapping[str, tuple[tuple[date, Mapping[str, object]], ...]]:
        ticker = yf.Ticker(symbol)
        raw = {
            "income": ticker.get_income_stmt(freq=frequency),
            "balance_sheet": ticker.get_balance_sheet(freq=frequency),
            "cash_flow": ticker.get_cash_flow(freq=frequency),
        }
        return {name: cls._plain_table(table) for name, table in raw.items()}

    @staticmethod
    def _plain_table(table: object) -> tuple[tuple[date, Mapping[str, object]], ...]:
        if table is None or not hasattr(table, "columns") or not hasattr(table, "index"):
            return ()
        frame = cast(Any, table)
        rows: list[tuple[date, Mapping[str, object]]] = []
        for column in frame.columns:
            if isinstance(column, datetime):
                period_end = column.date()
            elif isinstance(column, date):
                period_end = column
            elif hasattr(column, "date"):
                period_end = column.date()
            else:
                continue
            values: dict[str, object] = {}
            for index in frame.index:
                value = frame.at[index, column]
                # pandas/numpy NaN is the only common value unequal to itself.
                try:
                    if value != value:  # noqa: PLR0124
                        value = None
                except Exception:
                    value = None
                if value is not None and hasattr(value, "item"):
                    try:
                        value = value.item()
                    except Exception:
                        value = None
                values[str(index)] = value
            rows.append((period_end, values))
        rows.sort(key=lambda item: item[0], reverse=True)
        return tuple(rows)
