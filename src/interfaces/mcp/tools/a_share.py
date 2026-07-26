"""Compact A-share fact operation adapters."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.a_share import (
    AShareGetCapitalSnapshotInput,
    AShareGetCompanyOperatingMetricsInput,
    AShareGetEtfOptionSnapshotInput,
    AShareGetFinancialStatementsInput,
    AShareGetIndustryCycleInput,
    AShareGetLimitUpContextInput,
    AShareGetMarketStructureInput,
    AShareGetSentimentSnapshotInput,
    AShareGetSnapshotInput,
    ResearchSearchReportsInput,
)
from bootstrap import ApplicationContainer
from domain.a_share.enums import FinancialStatementType
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_a_share_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact A-share operation adapters."""

    # ---------------------------------------------------------- Phase 1E A-share
    async def a_share_get_facts(
        operation: Literal[
            "snapshot",
            "market_structure",
            "capital",
            "limit_up",
            "sentiment",
            "etf_option",
            "financials",
            "industry_cycle",
            "company_operating_metrics",
        ] = "snapshot",
        instrument_id: str | None = None,
        as_of: datetime | None = None,
        detail: str = "summary",
        scope: str = "instrument",
        trade_date: date | None = None,
        start: date | None = None,
        end: date | None = None,
        interval: str = "1d",
        adjustment: str = "forward_adjusted",
        include_bars: bool | None = None,
        include_order_book: bool | None = None,
        include_ticks: bool = False,
        include_industries: bool | None = None,
        include_market_board: bool | None = None,
        industry_limit: int = 20,
        tick_limit: int = 100,
        metrics: list[str] | None = None,
        pools: list[str] | None = None,
        sentiment_sources: list[str] | None = None,
        expiry: date | None = None,
        strike_center: str | None = None,
        strike_count_each_side: int = 5,
        cycle: Literal["hog"] = "hog",
        lookback_months: int = 12,
        document_limit: int = 10,
        view: Literal["compact", "series"] = "compact",
        metric_codes: list[str] | None = None,
        statement_types: list[str] | None = None,
        periods: int = 8,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read one A-share fact family selected by a closed operation name."""
        if operation == "snapshot":
            if instrument_id is None:
                raise ValueError("instrument_id is required for operation=snapshot")
            return await a_share_get_snapshot(instrument_id, as_of, detail)
        if operation == "market_structure":
            return await a_share_get_market_structure(
                scope,
                instrument_id,
                trade_date,
                start,
                end,
                interval,
                adjustment,
                include_bars,
                include_order_book,
                include_ticks,
                include_industries,
                include_market_board,
                industry_limit,
                tick_limit,
                as_of,
            )
        if operation == "capital":
            return await a_share_get_capital_snapshot(instrument_id, metrics, start, end, as_of)
        if operation == "limit_up":
            if trade_date is None:
                raise ValueError("trade_date is required for operation=limit_up")
            return await a_share_get_limit_up_context(trade_date, pools, as_of)
        if operation == "sentiment":
            return await a_share_get_sentiment_snapshot(
                instrument_id, sentiment_sources, trade_date, as_of
            )
        if operation == "etf_option":
            return await a_share_get_etf_option_snapshot(
                instrument_id or "", expiry, strike_center, strike_count_each_side, as_of
            )
        if operation == "financials":
            if instrument_id is None:
                raise ValueError("instrument_id is required for operation=financials")
            try:
                financial_input = AShareGetFinancialStatementsInput.model_validate(
                    {
                        "instrument_id": instrument_id,
                        "statement_types": tuple(statement_types or FinancialStatementType),
                        "periods": periods,
                        "metric_codes": tuple(metric_codes or ()),
                        "as_of": as_of,
                    }
                )
                return (
                    await container.a_share_tool_coordinator.get_financial_statements(
                        financial_input
                    )
                ).model_dump(mode="json")
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                return _unexpected_failure(container, exc)
        if operation == "industry_cycle":
            try:
                industry_cycle_input = AShareGetIndustryCycleInput.model_validate(
                    {
                        "cycle": cycle,
                        "lookback_months": lookback_months,
                        "view": view,
                        "metric_codes": tuple(metric_codes or ()),
                        "offset": offset,
                        "limit": limit,
                        "as_of": as_of,
                    }
                )
                return (
                    await container.a_share_tool_coordinator.get_industry_cycle(
                        industry_cycle_input
                    )
                ).model_dump(mode="json")
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                return _unexpected_failure(container, exc)
        if operation == "company_operating_metrics":
            if instrument_id is None:
                raise ValueError(
                    "instrument_id is required for operation=company_operating_metrics"
                )
            try:
                company_metrics_input = AShareGetCompanyOperatingMetricsInput.model_validate(
                    {
                        "instrument_id": instrument_id,
                        "lookback_months": lookback_months,
                        "document_limit": document_limit,
                        "metric_codes": tuple(metric_codes or ()),
                        "as_of": as_of,
                    }
                )
                return (
                    await container.a_share_tool_coordinator.get_company_operating_metrics(
                        company_metrics_input
                    )
                ).model_dump(mode="json")
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                return _unexpected_failure(container, exc)
        raise ValueError(
            "operation must be snapshot, market_structure, capital, limit_up, "
            "sentiment, etf_option, financials, industry_cycle, or "
            "company_operating_metrics"
        )

    async def a_share_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        detail: str = "summary",
    ) -> dict[str, Any]:
        try:
            inp = AShareGetSnapshotInput.model_validate(
                {"instrument_id": instrument_id, "as_of": as_of, "detail": detail}
            )
            return (await container.a_share_tool_coordinator.get_snapshot(inp)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_market_structure(
        scope: str = "instrument",
        instrument_id: str | None = None,
        trade_date: date | None = None,
        start: date | None = None,
        end: date | None = None,
        interval: str = "1d",
        adjustment: str = "forward_adjusted",
        include_bars: bool | None = None,
        include_order_book: bool | None = None,
        include_ticks: bool = False,
        include_industries: bool | None = None,
        include_market_board: bool | None = None,
        industry_limit: int = 20,
        tick_limit: int = 100,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share market-structure snapshot Tool Envelope."""
        try:
            inp = AShareGetMarketStructureInput.model_validate(
                {
                    "scope": scope,
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "start": start,
                    "end": end,
                    "interval": interval,
                    "adjustment": adjustment,
                    "include_bars": include_bars,
                    "include_order_book": include_order_book,
                    "include_ticks": include_ticks,
                    "include_industries": include_industries,
                    "include_market_board": include_market_board,
                    "industry_limit": industry_limit,
                    "tick_limit": tick_limit,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_market_structure(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_capital_snapshot(
        instrument_id: str | None = None,
        metrics: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share capital/flow snapshot Tool Envelope."""
        try:
            inp = AShareGetCapitalSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "metrics": tuple(metrics or ()),
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_capital_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_limit_up_context(
        trade_date: date,
        pools: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return A-share limit-up / limit-down context Tool Envelope."""
        try:
            inp = AShareGetLimitUpContextInput.model_validate(
                {
                    "trade_date": trade_date,
                    "pools": tuple(pools or ()),
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_limit_up_context(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_sentiment_snapshot(
        instrument_id: str | None = None,
        sources: list[str] | None = None,
        trade_date: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share sentiment/heat snapshot Tool Envelope."""
        try:
            inp = AShareGetSentimentSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "sources": tuple(sources or ()),
                    "trade_date": trade_date,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_sentiment_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_etf_option_snapshot(
        underlying_instrument_id: str,
        expiry: date | None = None,
        strike_center: str | None = None,
        strike_count_each_side: int = 5,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share ETF option chain snapshot Tool Envelope."""
        try:
            inp = AShareGetEtfOptionSnapshotInput.model_validate(
                {
                    "underlying_instrument_id": underlying_instrument_id,
                    "expiry": expiry,
                    "strike_center": strike_center,
                    "strike_count_each_side": strike_count_each_side,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_etf_option_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def research_search_reports(
        text: str | None = None,
        instrument_id: str | None = None,
        industry_code: str | None = None,
        published_from: date | None = None,
        published_to: date | None = None,
        include_consensus: bool = True,
        as_of: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search external A-share research reports (no ResearchReport archive write)."""
        try:
            inp = ResearchSearchReportsInput.model_validate(
                {
                    "text": text,
                    "instrument_id": instrument_id,
                    "industry_code": industry_code,
                    "published_from": published_from,
                    "published_to": published_to,
                    "include_consensus": include_consensus,
                    "as_of": as_of,
                    "limit": limit,
                    "offset": offset,
                }
            )
            envelope = await container.a_share_tool_coordinator.search_reports(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        a_share_get_facts=a_share_get_facts,
        research_search_reports=research_search_reports,
    )
