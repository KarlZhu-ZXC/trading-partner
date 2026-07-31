"""MCP-facing coordinator for four Phase 1H US context tools."""

from __future__ import annotations

from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
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
from application.services.instrument_access_service import InstrumentAccessService
from application.services.us_context_services import (
    USMacroService,
    USNewsService,
    USPredictionMarketService,
    USSentimentService,
)
from domain.common.enums import Freshness, Market, SourceRole
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix

_FRESHNESS_ORDER = {
    Freshness.FRESH: 0,
    Freshness.DELAYED: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 3,
}


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
        now = self._clock.now()
        as_of = requested or now
        if as_of > now:
            raise DataContractError("as_of must not be in the future")
        return self._ids.new(EntityIdPrefix.REQ), as_of

    def _success[T](
        self,
        request_id: str,
        as_of: datetime,
        data: T,
        results: tuple[RouterExecutionResult[object], ...],
        *,
        extra_codes: tuple[str, ...] = (),
    ) -> ToolEnvelope[T]:
        metas = tuple(result.meta for result in results if result.meta is not None)
        warnings = self._warnings(results, metas, extra_codes)
        fetched_at = max((meta.fetched_at for meta in metas), default=self._clock.now())
        freshness = max(
            (meta.freshness for meta in metas),
            key=lambda value: _FRESHNESS_ORDER[value],
            default=Freshness.UNKNOWN,
        )
        sources = tuple(
            SourceReference(
                name=meta.vendor.value,
                role=meta.role,
                url=None,
                retrieved_at=meta.fetched_at,
                data_delay_seconds=meta.data_delay_seconds,
            )
            for meta in dict.fromkeys(metas)
        )
        return ToolEnvelope.success(
            request_id=request_id,
            market=Market.US,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            sources=sources,
            data=data,
            degraded=bool(warnings),
            warnings=warnings,
        )

    @staticmethod
    def _warnings(
        results: tuple[RouterExecutionResult[object], ...],
        metas: tuple[ProviderResultMeta, ...],
        extra_codes: tuple[str, ...],
    ) -> tuple[WarningInfo, ...]:
        codes = [warning.code for result in results for warning in result.warnings]
        codes.extend(extra_codes)
        codes.extend("FALLBACK_US_SOURCE" for meta in metas if meta.role is SourceRole.FALLBACK)
        return tuple(
            WarningInfo(code=code, message="US context data warning.", details={})
            for code in dict.fromkeys(codes)
        )

    def _router_failure[T](
        self,
        request_id: str,
        as_of: datetime,
        result: RouterExecutionResult[object],
    ) -> ToolEnvelope[T]:
        error = result.error
        mapped = (
            to_error_info(error, self._redactor)
            if isinstance(error, TradingPartnerError)
            else to_error_info_from_exception(
                error or RuntimeError("router failure"), self._redactor
            )
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=Market.US,
            as_of=as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[mapped],
            degraded=True,
            warnings=result.warnings,
            data=None,
        )

    def _exception[T](
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[T]:
        mapped = (
            to_error_info(exc, self._redactor)
            if isinstance(exc, TradingPartnerError)
            else to_error_info_from_exception(exc, self._redactor)
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=Market.US,
            as_of=as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[mapped],
            degraded=True,
            warnings=(),
            data=None,
        )
