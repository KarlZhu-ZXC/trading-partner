"""Explicit Router-backed future Catalyst Agenda synchronization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from application.dto.catalyst_agenda_sync import (
    CatalystAgendaSyncInput,
    CatalystAgendaSyncReceiptDTO,
)
from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult, ToolDataPolicy
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.catalyst_agenda_scope_reader import CatalystAgendaScopeReader
from application.ports.catalyst_agenda_sync_repository import CatalystAgendaSyncRepository
from application.ports.catalyst_calendar_provider import CatalystCalendarProvider
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.provider_router import ProviderRouter
from domain.catalyst_agenda.calendar import (
    CatalystAgendaProviderSyncResult,
    CatalystAgendaSyncReceipt,
    CatalystCalendarBatch,
    CatalystCalendarCandidate,
)
from domain.catalyst_agenda.enums import (
    AgendaItemStatus,
    AgendaSourceType,
    AgendaSyncProviderStatus,
    AgendaSyncStatus,
)
from domain.catalyst_agenda.models import (
    CatalystAgendaIdentity,
    CatalystAgendaVersion,
    agenda_request_fingerprint,
)
from domain.common.enums import AssetType, DataCategory, Market, VendorId
from domain.common.errors import DataContractError, IdempotencyConflict, InputValidationError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument

_YAHOO_POLICY = ToolDataPolicy(
    tool_name="trading-partner-catalyst-sync",
    required_categories=(DataCategory.CORPORATE_ACTIONS,),
    optional_categories=(),
    category_chain_overrides={DataCategory.CORPORATE_ACTIONS: (VendorId.YFINANCE,)},
)
_FRED_POLICY = ToolDataPolicy(
    tool_name="trading-partner-catalyst-sync",
    required_categories=(DataCategory.MACRO,),
    optional_categories=(),
    category_chain_overrides={DataCategory.MACRO: (VendorId.FRED,)},
)
_DATE_MATCH_WINDOW = timedelta(days=45)
_REVERIFY_AFTER = timedelta(days=7)
_MAX_CONCURRENCY = 4

InstrumentUowFactory = Callable[[], InstrumentUnitOfWork]


class CatalystAgendaSyncService:
    def __init__(
        self,
        *,
        router: ProviderRouter,
        agenda_repository: CatalystAgendaRepository,
        sync_repository: CatalystAgendaSyncRepository,
        scope_reader: CatalystAgendaScopeReader,
        instrument_uow_factory: InstrumentUowFactory,
        calendar_codec: ProviderCacheCodec[CatalystCalendarBatch],
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._router = router
        self._agenda = agenda_repository
        self._receipts = sync_repository
        self._scope = scope_reader
        self._instrument_uow_factory = instrument_uow_factory
        self._codec = calendar_codec
        self._clock = clock
        self._ids = id_generator

    async def sync(self, request: CatalystAgendaSyncInput) -> CatalystAgendaSyncReceiptDTO:
        started_at = self._clock.now()
        as_of = request.as_of or started_at
        require_aware_datetime(as_of, field_name="as_of")
        if as_of > started_at:
            raise InputValidationError("as_of must not be in the future")
        instrument_ids = tuple(sorted({*self._durable_instrument_ids(), *request.instrument_ids}))
        fingerprint = agenda_request_fingerprint(
            {
                "instrument_ids": instrument_ids,
                "fred_release_ids": request.fred_release_ids,
                "window_days": request.window_days,
                "as_of": as_of.isoformat(),
            }
        )
        run_id = self._ids.new(EntityIdPrefix.RUN)
        key = request.idempotency_key or f"catalyst-sync:{run_id}"
        existing = self._receipts.get_by_idempotency_key(key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict("Catalyst Agenda sync idempotency key was reused")
            return CatalystAgendaSyncReceiptDTO.from_domain(existing)

        instruments, skipped = self._instruments(instrument_ids)
        start_day = as_of.date()
        end_day = start_day + timedelta(days=request.window_days)
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def yahoo(
            instrument: Instrument,
        ) -> tuple[str, RouterExecutionResult[CatalystCalendarBatch]]:
            async with semaphore:
                return instrument.instrument_id, await self._route_yahoo(
                    instrument, start_day=start_day, end_day=end_day, as_of=as_of
                )

        async def fred(release_id: int) -> tuple[str, RouterExecutionResult[CatalystCalendarBatch]]:
            async with semaphore:
                return f"fred:{release_id}", await self._route_fred(
                    release_id, start_day=start_day, end_day=end_day, as_of=as_of
                )

        routed = await asyncio.gather(
            *(yahoo(instrument) for instrument in instruments),
            *(fred(release_id) for release_id in request.fred_release_ids),
        )
        provider_results: list[CatalystAgendaProviderSyncResult] = list(skipped)
        candidates: list[CatalystCalendarCandidate] = []
        limitation_codes: set[str] = {code for result in skipped for code in result.warning_codes}
        if not request.fred_release_ids:
            fred_limitation = "AGENDA_FRED_RELEASE_IDS_UNCONFIGURED"
            limitation_codes.add(fred_limitation)
            provider_results.append(
                CatalystAgendaProviderSyncResult(
                    vendor=VendorId.FRED,
                    scope_ref="fred:release_ids",
                    status=AgendaSyncProviderStatus.SKIPPED,
                    candidate_count=0,
                    warning_codes=(fred_limitation,),
                )
            )
        if not routed:
            limitation_codes.add("AGENDA_SYNC_SCOPE_EMPTY")
        succeeded = 0
        failed = 0
        for scope_ref, result in routed:
            if result.ok and result.value is not None and result.meta is not None:
                succeeded += 1
                candidates.extend(result.value.candidates)
                warnings = tuple(
                    dict.fromkeys(
                        (
                            *result.value.limitation_codes,
                            *result.meta.warnings,
                            *(warning.code for warning in result.warnings),
                        )
                    )
                )
                limitation_codes.update(warnings)
                provider_results.append(
                    CatalystAgendaProviderSyncResult(
                        vendor=result.meta.vendor,
                        scope_ref=scope_ref,
                        status=AgendaSyncProviderStatus.SUCCESS,
                        candidate_count=len(result.value.candidates),
                        warning_codes=warnings,
                    )
                )
            else:
                failed += 1
                error_code = (
                    result.error.code if result.error is not None else "PROVIDER_UNAVAILABLE_ERROR"
                )
                limitation_codes.add(error_code)
                vendor = VendorId.FRED if scope_ref.startswith("fred:") else VendorId.YFINANCE
                provider_results.append(
                    CatalystAgendaProviderSyncResult(
                        vendor=vendor,
                        scope_ref=scope_ref,
                        status=AgendaSyncProviderStatus.FAILED,
                        candidate_count=0,
                        error_code=error_code,
                        warning_codes=tuple(w.code for w in result.warnings),
                    )
                )

        appended = revised = drifted = unchanged = 0
        visible = list(self._agenda.list_visible(as_of=started_at))
        current = self._current_versions(visible)
        for candidate in sorted(
            candidates,
            key=lambda item: (item.vendor.value, item.instrument_id or "", item.upstream_event_key),
        ):
            outcome, uncertain = self._persist_candidate(
                candidate,
                run_id=run_id,
                current=current,
            )
            if uncertain:
                limitation_codes.add("AGENDA_EVENT_IDENTITY_UNCERTAIN")
            if outcome == "appended":
                appended += 1
            elif outcome == "drifted":
                revised += 1
                drifted += 1
            elif outcome == "reverified":
                revised += 1
            else:
                unchanged += 1

        completed_at = self._clock.now()
        attempted = len(routed)
        if attempted == 0 or (failed and not succeeded):
            status = AgendaSyncStatus.FAILED
        elif failed or skipped or not request.fred_release_ids:
            status = AgendaSyncStatus.PARTIAL
        else:
            status = AgendaSyncStatus.COMPLETE
        receipt = CatalystAgendaSyncReceipt(
            receipt_id=run_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status=status,
            as_of=as_of,
            window_start=datetime.combine(start_day, time.min, tzinfo=as_of.tzinfo),
            window_end=datetime.combine(end_day, time.max, tzinfo=as_of.tzinfo),
            scope_count=len(instrument_ids) + len(request.fred_release_ids),
            eligible_instrument_count=len(instruments),
            succeeded_scope_count=succeeded,
            failed_scope_count=failed,
            candidate_count=len(candidates),
            appended_count=appended,
            revised_count=revised,
            date_drift_count=drifted,
            unchanged_count=unchanged,
            provider_results=tuple(provider_results),
            limitation_codes=tuple(sorted(limitation_codes)),
            started_at=started_at,
            completed_at=completed_at,
        )
        return CatalystAgendaSyncReceiptDTO.from_domain(self._receipts.append(receipt))

    def latest(self) -> CatalystAgendaSyncReceiptDTO | None:
        value = self._receipts.latest()
        return CatalystAgendaSyncReceiptDTO.from_domain(value) if value is not None else None

    def _durable_instrument_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    entry.instrument_id
                    for entry in self._scope.read_current().entries
                    if entry.instrument_id is not None
                }
            )
        )

    def _instruments(
        self, instrument_ids: tuple[str, ...]
    ) -> tuple[tuple[Instrument, ...], tuple[CatalystAgendaProviderSyncResult, ...]]:
        values: list[Instrument] = []
        skipped: list[CatalystAgendaProviderSyncResult] = []
        with self._instrument_uow_factory() as uow:
            for instrument_id in instrument_ids:
                instrument = uow.instruments.get_by_id(instrument_id)
                if instrument is None:
                    raise InputValidationError(
                        "instrument_id does not identify a durable Instrument",
                        details={"instrument_id": instrument_id},
                    )
                if instrument.market is Market.US and instrument.asset_type in {
                    AssetType.EQUITY,
                    AssetType.ETF,
                }:
                    values.append(instrument)
                else:
                    skipped.append(
                        CatalystAgendaProviderSyncResult(
                            vendor=VendorId.YFINANCE,
                            scope_ref=instrument_id,
                            status=AgendaSyncProviderStatus.SKIPPED,
                            candidate_count=0,
                            warning_codes=("AGENDA_INSTRUMENT_UNSUPPORTED",),
                        )
                    )
        return tuple(values), tuple(skipped)

    async def _route_yahoo(
        self,
        instrument: Instrument,
        *,
        start_day: date,
        end_day: date,
        as_of: datetime,
    ) -> RouterExecutionResult[CatalystCalendarBatch]:
        async def call(adapter: CategoryProvider) -> ProviderSuccess[CatalystCalendarBatch]:
            if not isinstance(adapter, CatalystCalendarProvider):
                raise DataContractError("adapter does not implement CatalystCalendarProvider")
            return await adapter.get_catalyst_calendar(
                instrument, start=start_day, end=end_day, as_of=as_of
            )

        return await self._router.execute(
            market=Market.US,
            category=DataCategory.CORPORATE_ACTIONS,
            call=call,
            operation_name="catalyst.yahoo_calendar.v1",
            request_fingerprint=agenda_request_fingerprint(
                {
                    "instrument_id": instrument.instrument_id,
                    "start": str(start_day),
                    "end": str(end_day),
                }
            ),
            instrument=instrument,
            as_of=as_of,
            tool_policy=_YAHOO_POLICY,
            cache_codec=self._codec,
            result_validator=lambda success: self._validate_batch(
                success,
                vendor=VendorId.YFINANCE,
                category=DataCategory.CORPORATE_ACTIONS,
            ),
        )

    async def _route_fred(
        self,
        release_id: int,
        *,
        start_day: date,
        end_day: date,
        as_of: datetime,
    ) -> RouterExecutionResult[CatalystCalendarBatch]:
        async def call(adapter: CategoryProvider) -> ProviderSuccess[CatalystCalendarBatch]:
            if not isinstance(adapter, CatalystCalendarProvider):
                raise DataContractError("adapter does not implement CatalystCalendarProvider")
            return await adapter.get_catalyst_calendar(
                None,
                start=start_day,
                end=end_day,
                as_of=as_of,
                release_ids=(release_id,),
            )

        return await self._router.execute(
            market=Market.US,
            category=DataCategory.MACRO,
            call=call,
            operation_name="catalyst.fred_release_dates.v1",
            request_fingerprint=agenda_request_fingerprint(
                {"release_id": release_id, "start": str(start_day), "end": str(end_day)}
            ),
            instrument=None,
            as_of=as_of,
            tool_policy=_FRED_POLICY,
            cache_codec=self._codec,
            result_validator=lambda success: self._validate_batch(
                success,
                vendor=VendorId.FRED,
                category=DataCategory.MACRO,
            ),
        )

    @staticmethod
    def _validate_batch(
        success: ProviderSuccess[CatalystCalendarBatch],
        *,
        vendor: VendorId,
        category: DataCategory,
    ) -> None:
        if success.meta.vendor is not vendor or success.meta.category is not category:
            raise DataContractError("Catalyst calendar Provider metadata does not match route")
        if (
            not isinstance(success.value, CatalystCalendarBatch)
            or success.value.vendor is not vendor
        ):
            raise DataContractError("Catalyst calendar Provider value is invalid")

    @staticmethod
    def _current_versions(
        visible: list[CatalystAgendaVersion],
    ) -> dict[str, CatalystAgendaVersion]:
        output: dict[str, CatalystAgendaVersion] = {}
        for value in visible:
            prior = output.get(value.agenda_item_id)
            if prior is None or value.version > prior.version:
                output[value.agenda_item_id] = value
        return output

    def _persist_candidate(
        self,
        candidate: CatalystCalendarCandidate,
        *,
        run_id: str,
        current: dict[str, CatalystAgendaVersion],
    ) -> tuple[str, bool]:
        logical_key = self._logical_key(candidate)
        existing = self._agenda.get_current_by_logical_key(logical_key)
        uncertain = False
        if existing is None:
            neighbors = self._neighbor_candidates(candidate, tuple(current.values()))
            if len(neighbors) == 1:
                existing = neighbors[0]
                candidate = replace(
                    candidate,
                    upstream_event_key=existing.upstream_event_key or candidate.upstream_event_key,
                    fiscal_period=(
                        existing.fiscal_period
                        if candidate.vendor is VendorId.YFINANCE
                        else candidate.fiscal_period
                    ),
                )
            else:
                uncertain = True
        now = self._clock.now()
        if existing is None:
            agenda_item_id = self._ids.new(EntityIdPrefix.AGENDA)
            value = self._version(candidate, agenda_item_id, 1, None, run_id, now, None)
            self._agenda.append_initial(
                CatalystAgendaIdentity(agenda_item_id, logical_key, now), value
            )
            current[agenda_item_id] = value
            return "appended", uncertain
        date_changed = self._date_changed(existing, candidate)
        metadata_changed = self._metadata_changed(existing, candidate)
        if (
            not date_changed
            and not metadata_changed
            and now - existing.last_verified_at < _REVERIFY_AFTER
        ):
            return "unchanged", uncertain
        if date_changed:
            note = "Provider event date/window changed"
        elif metadata_changed:
            note = "Provider event metadata changed"
        else:
            note = "Provider date reverified"
        value = self._version(
            candidate,
            existing.agenda_item_id,
            existing.version + 1,
            existing.version,
            run_id,
            now,
            note,
        )
        self._agenda.append_version(value, expected_version=existing.version)
        current[existing.agenda_item_id] = value
        return ("drifted" if date_changed else "reverified"), uncertain

    @staticmethod
    def _neighbor_candidates(
        candidate: CatalystCalendarCandidate,
        current: tuple[CatalystAgendaVersion, ...],
    ) -> tuple[CatalystAgendaVersion, ...]:
        if candidate.window_start is None:
            return ()
        family = CatalystAgendaSyncService._identity_family(candidate.upstream_event_key)
        eligible = tuple(
            value
            for value in current
            if value.status is AgendaItemStatus.UPCOMING
            and value.source_type is AgendaSourceType.PROVIDER
            and value.source_vendor == candidate.vendor.value
            and value.instrument_id == candidate.instrument_id
            and value.kind is candidate.kind
            and value.window_start is not None
            and abs(value.window_start - candidate.window_start) <= _DATE_MATCH_WINDOW
            and CatalystAgendaSyncService._identity_family(value.upstream_event_key or "") == family
        )
        if not eligible:
            return ()
        nearest = min(
            abs(value.window_start - candidate.window_start)
            for value in eligible
            if value.window_start is not None
        )
        return tuple(
            value
            for value in eligible
            if value.window_start is not None
            and abs(value.window_start - candidate.window_start) == nearest
        )

    @staticmethod
    def _identity_family(key: str) -> str:
        parts = key.split(":")
        if "dividend" in parts:
            index = parts.index("dividend")
            return ":".join(parts[: index + 2])
        if "earnings" in parts:
            return ":".join(parts[: parts.index("earnings") + 1])
        if "split" in parts:
            return ":".join(parts[: parts.index("split") + 1])
        if parts and parts[0] == "fred" and len(parts) >= 2:
            return ":".join(parts[:2])
        return key

    @staticmethod
    def _logical_key(candidate: CatalystCalendarCandidate) -> str:
        return ":".join(
            (
                AgendaSourceType.PROVIDER.value,
                candidate.vendor.value,
                candidate.kind.value,
                candidate.upstream_event_key,
            )
        )

    @staticmethod
    def _date_changed(current: CatalystAgendaVersion, candidate: CatalystCalendarCandidate) -> bool:
        return any(
            (
                current.window_start != candidate.window_start,
                current.window_end != candidate.window_end,
                current.date_certainty is not candidate.date_certainty,
            )
        )

    @staticmethod
    def _metadata_changed(
        current: CatalystAgendaVersion, candidate: CatalystCalendarCandidate
    ) -> bool:
        return any(
            (
                current.title != candidate.title,
                current.fiscal_period != candidate.fiscal_period,
                current.timezone != candidate.timezone,
                current.source_reference != candidate.source_reference,
                current.historical_vintage is not candidate.historical_vintage,
            )
        )

    @staticmethod
    def _version(
        candidate: CatalystCalendarCandidate,
        agenda_item_id: str,
        version: int,
        supersedes_version: int | None,
        run_id: str,
        recorded_at: datetime,
        revision_note: str | None,
    ) -> CatalystAgendaVersion:
        key_seed = f"{run_id}|{agenda_item_id}|{version}|{candidate.upstream_event_key}"
        idempotency_key = f"provider-sync:{agenda_request_fingerprint(key_seed)[:48]}"
        payload = {
            "vendor": candidate.vendor.value,
            "instrument_id": candidate.instrument_id,
            "kind": candidate.kind.value,
            "title": candidate.title,
            "fiscal_period": candidate.fiscal_period,
            "upstream_event_key": candidate.upstream_event_key,
            "window_start": candidate.window_start.isoformat(),
            "window_end": candidate.window_end.isoformat(),
            "date_certainty": candidate.date_certainty.value,
            "source_reference": candidate.source_reference,
        }
        return CatalystAgendaVersion(
            agenda_item_id=agenda_item_id,
            version=version,
            supersedes_version=supersedes_version,
            instrument_id=candidate.instrument_id,
            subject_id=None,
            kind=candidate.kind,
            title=candidate.title,
            fiscal_period=candidate.fiscal_period,
            upstream_event_key=candidate.upstream_event_key,
            window_start=candidate.window_start,
            window_end=candidate.window_end,
            timezone=candidate.timezone,
            date_certainty=candidate.date_certainty,
            status=AgendaItemStatus.UPCOMING,
            source_type=AgendaSourceType.PROVIDER,
            source_vendor=candidate.vendor.value,
            source_reference=candidate.source_reference,
            source_visible_at=candidate.source_visible_at,
            last_verified_at=candidate.last_verified_at,
            expected_question=None,
            linked_event_id=None,
            linked_report_id=None,
            revision_note=revision_note,
            created_by="system",
            confirmed_by="system",
            authorization_note=f"provider_sync:{run_id}",
            idempotency_key=idempotency_key,
            request_fingerprint=agenda_request_fingerprint(payload),
            historical_vintage=candidate.historical_vintage,
            recorded_at=max(recorded_at, candidate.last_verified_at),
        )
