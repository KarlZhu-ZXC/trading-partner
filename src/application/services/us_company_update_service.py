"""Compose filings, insider transactions, and actions into one event timeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from application.dto.provider_routing import RouterExecutionResult
from application.services.us_context_services import USNewsService
from application.services.us_filing_service import USFilingService
from application.services.us_fundamental_service import USFundamentalService
from domain.instruments.models import Instrument
from domain.us_context.models import USNewsArticle
from domain.us_research.enums import USExternalEventType
from domain.us_research.models import (
    USCompanyUpdate,
    USCorporateAction,
    USExternalEvent,
    USFiling,
    USInsiderTransaction,
)


@dataclass(frozen=True, slots=True)
class USCompanyUpdateResult:
    update: USCompanyUpdate
    component_results: tuple[RouterExecutionResult[object], ...]


class USCompanyUpdateService:
    def __init__(
        self,
        fundamental_service: USFundamentalService,
        filing_service: USFilingService,
        news_service: USNewsService,
    ) -> None:
        self._fundamental = fundamental_service
        self._filing = filing_service
        self._news = news_service

    async def get_update(
        self,
        instrument: Instrument,
        *,
        since: datetime | None,
        as_of: datetime,
        limit: int,
    ) -> USCompanyUpdateResult:
        zone = ZoneInfo(instrument.timezone)
        start = since.astimezone(zone).date() if since is not None else None
        filings_coro = self._filing.get_filings(
            instrument,
            forms=(),
            start=start,
            end=None,
            include_sections=False,
            limit=limit,
            as_of=as_of,
        )
        insiders_coro = self._filing.get_insider_activity(
            instrument, start=start, end=None, limit=limit, as_of=as_of
        )
        actions_coro = self._fundamental.get_corporate_actions(
            instrument, start=start, end=None, as_of=as_of
        )
        news_coro = self._news.get_news(
            instrument, query=None, start=start, end=None, limit=limit, as_of=as_of
        )
        filings, insiders, actions, news = await asyncio.gather(
            filings_coro, insiders_coro, actions_coro, news_coro
        )
        results: tuple[RouterExecutionResult[object], ...] = (
            filings,
            insiders,
            actions,
            news,
        )
        events: list[USExternalEvent] = []
        if filings.ok and isinstance(filings.value, tuple):
            events.extend(self._filing_events(filings.value, zone))
        if insiders.ok and isinstance(insiders.value, tuple):
            events.extend(self._insider_events(insiders.value, as_of, zone))
        if actions.ok and isinstance(actions.value, tuple):
            events.extend(self._action_events(actions.value, as_of, zone))
        if news.ok and news.value is not None:
            events.extend(self._news_events(news.value.articles))
        if since is not None:
            events = [event for event in events if event.visible_time >= since]
        unique = {event.dedupe_key: event for event in events}
        ordered = tuple(
            sorted(
                unique.values(),
                key=lambda event: (event.visible_time, event.dedupe_key),
                reverse=True,
            )[:limit]
        )
        failed = sum(not result.ok for result in results)
        warning_codes = ("US_UPDATE_PARTIAL",) if failed else ()
        update = USCompanyUpdate(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            events=ordered,
            degraded=bool(failed),
            warning_codes=warning_codes,
        )
        return USCompanyUpdateResult(update=update, component_results=results)

    @staticmethod
    def _filing_events(filings: tuple[USFiling, ...], zone: ZoneInfo) -> list[USExternalEvent]:
        events: list[USExternalEvent] = []
        for filing in filings:
            visible = filing.accepted_at or datetime.combine(
                filing.filed_date, time(16), tzinfo=zone
            )
            events.append(
                USExternalEvent(
                    instrument_id=filing.instrument_id,
                    event_type=USExternalEventType.FILING,
                    event_time=visible,
                    visible_time=visible,
                    title=f"SEC {filing.form.value} filed",
                    summary=None,
                    source_reference=filing.url,
                    dedupe_key=f"filing:{filing.accession}",
                    filing=filing,
                    insider_transaction=None,
                    corporate_action=None,
                )
            )
        return events

    @staticmethod
    def _insider_events(
        rows: tuple[USInsiderTransaction, ...], as_of: datetime, zone: ZoneInfo
    ) -> list[USExternalEvent]:
        events: list[USExternalEvent] = []
        for row in rows:
            event_time = (
                datetime.combine(row.transaction_date, time.min, tzinfo=zone)
                if row.transaction_date is not None
                else as_of
            )
            visible = row.accepted_at or row.filed_at or as_of
            fingerprint = ":".join(
                str(value)
                for value in (
                    row.owner_name,
                    row.transaction_date,
                    row.transaction_code,
                    row.shares,
                    row.price,
                )
            )
            events.append(
                USExternalEvent(
                    instrument_id=row.instrument_id,
                    event_type=USExternalEventType.INSIDER_TRANSACTION,
                    event_time=event_time,
                    visible_time=visible,
                    title=f"Insider transaction: {row.owner_name}",
                    summary=None,
                    source_reference=None,
                    dedupe_key=f"insider:{fingerprint}",
                    filing=None,
                    insider_transaction=row,
                    corporate_action=None,
                )
            )
        return events

    @staticmethod
    def _action_events(
        actions: tuple[USCorporateAction, ...], as_of: datetime, zone: ZoneInfo
    ) -> list[USExternalEvent]:
        events: list[USExternalEvent] = []
        for action in actions:
            event_time = (
                datetime.combine(action.effective_date, time.min, tzinfo=zone)
                if action.effective_date is not None
                else as_of
            )
            fingerprint = ":".join(
                str(value)
                for value in (
                    action.action_type.value,
                    action.effective_date,
                    action.amount,
                    action.ratio,
                )
            )
            events.append(
                USExternalEvent(
                    instrument_id=action.instrument_id,
                    event_type=USExternalEventType.CORPORATE_ACTION,
                    event_time=event_time,
                    visible_time=as_of,
                    title=f"Corporate action: {action.action_type.value}",
                    summary=action.description,
                    source_reference=None,
                    dedupe_key=f"action:{fingerprint}",
                    filing=None,
                    insider_transaction=None,
                    corporate_action=action,
                )
            )
        return events

    @staticmethod
    def _news_events(articles: tuple[USNewsArticle, ...]) -> list[USExternalEvent]:
        return [
            USExternalEvent(
                instrument_id=article.instrument_id,
                event_type=USExternalEventType.NEWS,
                event_time=article.published_at,
                visible_time=article.published_at,
                title=article.title,
                summary=article.summary,
                source_reference=article.url,
                dedupe_key=f"news:{article.dedupe_key}",
                filing=None,
                insider_transaction=None,
                corporate_action=None,
                news_article=article,
            )
            for article in articles
            if article.instrument_id is not None
        ]
