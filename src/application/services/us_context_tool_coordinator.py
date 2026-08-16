"""MCP-facing coordinator for four Phase 1H US context tools."""

from __future__ import annotations

from datetime import datetime

from application.dto.provider_routing import RouterExecutionResult
from application.dto.tool_envelope import ToolEnvelope
from application.dto.us_context import (
    MarketGetLiveNewsInput,
    USGetMacroContextInput,
    USGetPredictionMarketContextInput,
    USGetSentimentSnapshotInput,
    USMacroContextDTO,
    USNewsFeedDTO,
    USPredictionMarketContextDTO,
    USSentimentSnapshotDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._router_envelope_support import (
    begin_request,
    exception_envelope,
    router_failure_envelope,
    success_envelope,
)
from application.services.instrument_access_service import InstrumentAccessService
from application.services.us_context_services import (
    USMacroService,
    USNewsService,
    USPredictionMarketService,
    USSentimentService,
)
from domain.common.enums import Market


class USContextToolCoordinator:
    def __init__(
        self,
        *,
        instrument_access: InstrumentAccessService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        news_service: USNewsService,
        macro_service: USMacroService,
        sentiment_service: USSentimentService,
        prediction_service: USPredictionMarketService,
    ) -> None:
        self._instrument_access = instrument_access
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor
        self._news = news_service
        self._macro = macro_service
        self._sentiment = sentiment_service
        self._prediction = prediction_service

    async def get_live_news(self, request: MarketGetLiveNewsInput) -> ToolEnvelope[USNewsFeedDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = await self._instrument_access.get_optional(
                request.instrument_id, as_of=as_of
            )
            result = await self._news.get_news(
                instrument,
                query=request.query,
                start=request.start,
                end=request.end,
                limit=request.limit,
                as_of=as_of,
            )
            if not result.ok or result.value is None:
                return self._router_failure(request_id, as_of, result)
            return self._success(
                request_id,
                as_of,
                USNewsFeedDTO.from_domain(result.value),
                (result,),
                extra_codes=result.value.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_macro_context(
        self, request: USGetMacroContextInput
    ) -> ToolEnvelope[USMacroContextDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            result = await self._macro.get_context(
                series_ids=request.series_ids,
                lookback_days=request.lookback_days,
                as_of=as_of,
            )
            if not result.ok or result.value is None:
                return self._router_failure(request_id, as_of, result)
            return self._success(
                request_id,
                as_of,
                USMacroContextDTO.from_domain(result.value),
                (result,),
                extra_codes=result.value.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_sentiment_snapshot(
        self, request: USGetSentimentSnapshotInput
    ) -> ToolEnvelope[USSentimentSnapshotDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = await self._instrument_access.get(request.instrument_id, as_of=as_of)
            result = await self._sentiment.get_snapshot(
                instrument,
                start=request.start,
                end=request.end,
                limit=request.limit_per_source,
                as_of=as_of,
            )
            return self._success(
                request_id,
                as_of,
                USSentimentSnapshotDTO.from_domain(result.snapshot),
                result.component_results,
                extra_codes=result.snapshot.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_prediction_market_context(
        self, request: USGetPredictionMarketContextInput
    ) -> ToolEnvelope[USPredictionMarketContextDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            result = await self._prediction.get_context(
                topic=request.topic, limit=request.limit, as_of=as_of
            )
            if not result.ok or result.value is None:
                return self._router_failure(request_id, as_of, result)
            return self._success(
                request_id,
                as_of,
                USPredictionMarketContextDTO.from_domain(result.value),
                (result,),
                extra_codes=result.value.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    def _begin(self, requested: datetime | None) -> tuple[str, datetime]:
        return begin_request(requested, clock=self._clock, ids=self._ids)

    def _success[T](
        self,
        request_id: str,
        as_of: datetime,
        data: T,
        results: tuple[RouterExecutionResult[object], ...],
        *,
        extra_codes: tuple[str, ...] = (),
    ) -> ToolEnvelope[T]:
        return success_envelope(
            request_id=request_id,
            as_of=as_of,
            data=data,
            results=results,
            clock=self._clock,
            market=Market.US,
            extra_codes=extra_codes,
            warning_message="US context data warning.",
        )

    def _router_failure[T](
        self,
        request_id: str,
        as_of: datetime,
        result: RouterExecutionResult[object],
    ) -> ToolEnvelope[T]:
        return router_failure_envelope(
            request_id=request_id,
            as_of=as_of,
            result=result,
            clock=self._clock,
            redactor=self._redactor,
            market=Market.US,
        )

    def _exception[T](
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[T]:
        return exception_envelope(
            request_id=request_id,
            as_of=as_of,
            exc=exc,
            clock=self._clock,
            redactor=self._redactor,
            market=Market.US,
        )
