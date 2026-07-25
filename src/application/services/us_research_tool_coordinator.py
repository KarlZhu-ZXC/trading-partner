"""MCP-facing coordinator for the six Phase 1G US research tools."""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.dto.us_research import (
    EventsSearchInput,
    FundamentalGetSnapshotInput,
    FundamentalGetStatementsInput,
    ResearchGetCompanyUpdatesInput,
    USCompanyUpdateDTO,
    USExternalEventDTO,
    USFilingDTO,
    USFinancialStatementsDTO,
    USFundamentalSnapshotDTO,
    USGetFilingsInput,
    USGetInsiderActivityInput,
    USInsiderTransactionDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.instrument_master_service import InstrumentMasterService
from application.services.us_company_update_service import USCompanyUpdateService
from application.services.us_filing_service import USFilingService
from application.services.us_fundamental_service import USFundamentalService
from domain.common.enums import Freshness, Market, SourceRole
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.us_research.models import USFundamentalSnapshot

_FRESHNESS_ORDER = {
    Freshness.FRESH: 0,
    Freshness.DELAYED: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 3,
}


class USResearchToolCoordinator:
    def __init__(
        self,
        *,
        instrument_master: InstrumentMasterService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        fundamental_service: USFundamentalService,
        filing_service: USFilingService,
        company_update_service: USCompanyUpdateService,
    ) -> None:
        self._master = instrument_master
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor
        self._fundamental = fundamental_service
        self._filing = filing_service
        self._updates = company_update_service

    async def get_fundamental_snapshot(
        self, request: FundamentalGetSnapshotInput
    ) -> ToolEnvelope[USFundamentalSnapshotDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = self._master.get(request.instrument_id)
            base, official, actions = await asyncio.gather(
                self._fundamental.get_snapshot(instrument, as_of),
                self._fundamental.get_official_snapshot(instrument, as_of),
                self._fundamental.get_corporate_actions(
                    instrument, start=None, end=None, as_of=as_of
                ),
            )
            if not base.ok or not isinstance(base.value, USFundamentalSnapshot):
                return self._router_failure(request_id, as_of, base)
            action_rows = actions.value if actions.ok and isinstance(actions.value, tuple) else ()
            source = base.value
            warning_codes = list(source.warning_codes)
            if not actions.ok:
                warning_codes.append("US_ACTIONS_UNAVAILABLE")
            if not official.ok:
                warning_codes.append("SEC_REPORTED_METRICS_UNAVAILABLE")
            reported_metrics = source.reported_metrics
            if official.ok and isinstance(official.value, USFundamentalSnapshot):
                reported_metrics = official.value.reported_metrics or official.value.metrics
            snapshot = USFundamentalSnapshot(
                instrument_id=source.instrument_id,
                as_of=source.as_of,
                profile=source.profile,
                metrics=source.metrics,
                corporate_actions=action_rows,
                degraded=source.degraded or not actions.ok or not official.ok,
                warning_codes=tuple(dict.fromkeys(warning_codes)),
                reported_metrics=reported_metrics,
            )
            return self._success(
                request_id,
                as_of,
                USFundamentalSnapshotDTO.from_domain(snapshot),
                (base, official, actions),
                extra_codes=snapshot.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_fundamental_statements(
        self, request: FundamentalGetStatementsInput
    ) -> ToolEnvelope[USFinancialStatementsDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = self._master.get(request.instrument_id)
            result = await self._fundamental.get_statements(
                instrument,
                frequency=request.frequency,
                view=request.view,
                limit=request.limit,
                as_of=as_of,
            )
            if not result.ok or result.value is None:
                return self._router_failure(request_id, as_of, result)
            return self._success(
                request_id,
                as_of,
                USFinancialStatementsDTO.from_domain(result.value),
                (result,),
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_filings(
        self, request: USGetFilingsInput
    ) -> ToolEnvelope[tuple[USFilingDTO, ...]]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = self._master.get(request.instrument_id)
            result = await self._filing.get_filings(
                instrument,
                forms=request.forms,
                start=request.start,
                end=request.end,
                include_sections=request.include_sections,
                limit=request.limit,
                as_of=as_of,
            )
            if not result.ok or result.value is None:
                return self._router_failure(request_id, as_of, result)
            data = tuple(USFilingDTO.from_domain(row) for row in result.value)
            return self._success(request_id, as_of, data, (result,))
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_insider_activity(
        self, request: USGetInsiderActivityInput
    ) -> ToolEnvelope[tuple[USInsiderTransactionDTO, ...]]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = self._master.get(request.instrument_id)
            result = await self._filing.get_insider_activity(
                instrument,
                start=request.start,
                end=request.end,
                limit=request.limit,
                as_of=as_of,
            )
            if not result.ok or result.value is None:
                return self._router_failure(request_id, as_of, result)
            data = tuple(USInsiderTransactionDTO.from_domain(row) for row in result.value)
            return self._success(request_id, as_of, data, (result,))
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def get_company_updates(
        self, request: ResearchGetCompanyUpdatesInput
    ) -> ToolEnvelope[USCompanyUpdateDTO]:
        request_id, as_of = self._begin(request.as_of)
        try:
            instrument = self._master.get(request.instrument_id)
            result = await self._updates.get_update(
                instrument,
                since=request.since,
                as_of=as_of,
                limit=request.limit,
            )
            return self._success(
                request_id,
                as_of,
                USCompanyUpdateDTO.from_domain(result.update),
                result.component_results,
                extra_codes=result.update.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception(request_id, as_of, exc)

    async def search_events(
        self, request: EventsSearchInput
    ) -> ToolEnvelope[tuple[USExternalEventDTO, ...]]:
        request_id, as_of = self._begin(request.as_of)
        try:
            if request.instrument_id is None:
                return self._success(
                    request_id,
                    as_of,
                    (),
                    (),
                    extra_codes=("EVENTS_INSTRUMENT_REQUIRED_FOR_PROVIDER_SEARCH",),
                )
            instrument = self._master.get(request.instrument_id)
            zone = ZoneInfo(instrument.timezone)
            since = (
                datetime.combine(request.start, time.min, tzinfo=zone)
                if request.start is not None
                else None
            )
            result = await self._updates.get_update(instrument, since=since, as_of=as_of, limit=100)
            allowed = set(request.event_types)
            rows = tuple(
                event
                for event in result.update.events
                if (not allowed or event.event_type in allowed)
                and (
                    request.start is None
                    or event.event_time.astimezone(zone).date() >= request.start
                )
                and (request.end is None or event.event_time.astimezone(zone).date() <= request.end)
            )[: request.limit]
            data = tuple(USExternalEventDTO.from_domain(row) for row in rows)
            return self._success(
                request_id,
                as_of,
                data,
                result.component_results,
                extra_codes=result.update.warning_codes,
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

    def _warnings(
        self,
        results: tuple[RouterExecutionResult[object], ...],
        metas: tuple[ProviderResultMeta, ...],
        extra_codes: tuple[str, ...],
    ) -> tuple[WarningInfo, ...]:
        warnings: list[WarningInfo] = []
        seen: set[str] = set()
        codes = [warning.code for result in results for warning in result.warnings]
        codes.extend(extra_codes)
        codes.extend("FALLBACK_US_SOURCE" for meta in metas if meta.role is SourceRole.FALLBACK)
        for code in codes:
            if code not in seen:
                seen.add(code)
                warnings.append(
                    WarningInfo(code=code, message="US research data warning.", details={})
                )
        return tuple(warnings)

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
