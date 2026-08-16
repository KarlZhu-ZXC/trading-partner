"""Deterministic, durable-only Catalyst Agenda service."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta

from application.dto.catalyst_agenda import (
    AgendaCancelPayload,
    AgendaCoverageDTO,
    AgendaCoverageStatus,
    AgendaItemDTO,
    AgendaMutationAction,
    AgendaMutationInput,
    AgendaOutcomeLinkPayload,
    AgendaQueryDTO,
    AgendaQueryInput,
    AgendaSummaryDTO,
    AgendaUpsertPayload,
)
from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY, ToolEnvelope, WarningInfo
from application.ports.catalyst_agenda_outcome_reader import (
    AgendaOutcomeSnapshot,
    CatalystAgendaOutcomeReader,
)
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.catalyst_agenda_scope_reader import (
    AgendaScopeEntry,
    CatalystAgendaScopeReader,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    envelope_failure,
    envelope_success,
    normalize_idempotency_key,
    require_confirm_reviewer,
)
from domain.catalyst_agenda.enums import (
    AgendaItemKind,
    AgendaItemStatus,
    AgendaScopeReason,
    AgendaSourceType,
)
from domain.catalyst_agenda.models import (
    CATALYST_AGENDA_SCHEMA_VERSION,
    CatalystAgendaIdentity,
    CatalystAgendaVersion,
    agenda_request_fingerprint,
)
from domain.common.actor import ActorContext
from domain.common.enums import ResearchSubjectType
from domain.common.errors import (
    CatalystAgendaNotFound,
    CatalystAgendaVersionConflict,
    IdempotencyConflict,
    InputValidationError,
    InvalidResearchLink,
)
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime

_OUTCOME_UNVERIFIED = "AGENDA_EVENT_OUTCOME_UNVERIFIED"
_COVERAGE_UNAVAILABLE = "AGENDA_COVERAGE_UNAVAILABLE"
_DATE_REVERIFY_REQUIRED = "AGENDA_DATE_REVERIFY_REQUIRED"
_DATE_REVERIFY_AFTER = timedelta(days=7)


class CatalystAgendaService:
    def __init__(
        self,
        repository: CatalystAgendaRepository,
        scope_reader: CatalystAgendaScopeReader,
        outcome_reader: CatalystAgendaOutcomeReader,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._repository = repository
        self._scope_reader = scope_reader
        self._outcome_reader = outcome_reader
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    def manage(
        self,
        request: AgendaMutationInput,
        actor_context: ActorContext | None = None,
    ) -> ToolEnvelope[AgendaItemDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(
                request.confirmed_by,
                action=f"catalyst_agenda_{request.action.value.lower()}",
                actor_context=actor_context,
            )
            key = normalize_idempotency_key(request.idempotency_key)
            fingerprint = agenda_request_fingerprint(
                request.model_dump(mode="json", exclude={"idempotency_key"})
            )
            existing = self._repository.get_by_idempotency_key(key)
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyConflict("Catalyst Agenda idempotency key was reused")
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=self._item_dto(existing, as_of=self._clock.now()),
                    warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                    degraded=True,
                )
            now = self._clock.now()
            outcome_snapshot: AgendaOutcomeSnapshot | None = None
            if request.action is AgendaMutationAction.CREATE:
                assert isinstance(request.payload, AgendaUpsertPayload)
                self._validate_references(request.payload)
                agenda_item_id = self._ids.new(EntityIdPrefix.AGENDA)
                value = self._upsert_version(
                    agenda_item_id=agenda_item_id,
                    version=1,
                    supersedes_version=None,
                    payload=request.payload,
                    request=request,
                    key=key,
                    fingerprint=fingerprint,
                    recorded_at=now,
                    created_by=request.confirmed_by,
                )
                logical_key = self._logical_key(value)
                logical_existing = self._repository.get_by_logical_key(logical_key)
                if logical_existing is not None:
                    raise CatalystAgendaVersionConflict(
                        "Catalyst Agenda logical item already exists; revise its current version",
                        details={"agenda_item_id": logical_existing.agenda_item_id},
                    )
                self._repository.append_initial(
                    CatalystAgendaIdentity(
                        agenda_item_id=agenda_item_id,
                        logical_key=logical_key,
                        created_at=now,
                    ),
                    value,
                )
            else:
                assert request.agenda_item_id is not None
                assert request.expected_version is not None
                current = self._repository.get_current(request.agenda_item_id)
                if current is None:
                    raise CatalystAgendaNotFound(
                        "Catalyst Agenda item was not found",
                        details={"agenda_item_id": request.agenda_item_id},
                    )
                self._require_mutable(
                    current,
                    expected_version=request.expected_version,
                    action=request.action,
                )
                if request.action is AgendaMutationAction.REVISE:
                    assert isinstance(request.payload, AgendaUpsertPayload)
                    self._validate_references(request.payload)
                    self._validate_identity_fields(current, request.payload)
                    value = self._upsert_version(
                        agenda_item_id=current.agenda_item_id,
                        version=current.version + 1,
                        supersedes_version=current.version,
                        payload=request.payload,
                        request=request,
                        key=key,
                        fingerprint=fingerprint,
                        recorded_at=now,
                        created_by=current.created_by,
                    )
                elif request.action is AgendaMutationAction.CANCEL:
                    assert isinstance(request.payload, AgendaCancelPayload)
                    visible_at = request.payload.source_visible_at or now
                    verified_at = request.payload.last_verified_at or now
                    value = replace(
                        current,
                        version=current.version + 1,
                        supersedes_version=current.version,
                        status=AgendaItemStatus.CANCELLED,
                        source_visible_at=visible_at,
                        last_verified_at=verified_at,
                        revision_note=request.payload.cancellation_reason,
                        confirmed_by=request.confirmed_by,
                        authorization_note=request.authorization_note,
                        idempotency_key=key,
                        request_fingerprint=fingerprint,
                        recorded_at=now,
                    )
                else:
                    assert request.action is AgendaMutationAction.LINK_OUTCOME
                    assert isinstance(request.payload, AgendaOutcomeLinkPayload)
                    outcome_snapshot = self._outcome_reader.resolve(
                        event_id=request.payload.event_id,
                        report_id=request.payload.report_id,
                        evidence_id=request.payload.evidence_id,
                        subject_id=current.subject_id,
                        as_of=now,
                    )
                    self._validate_outcome_scope(
                        current,
                        outcome_snapshot,
                        request.payload,
                    )
                    outcome_occurred_at = self._resolve_outcome_occurred_at(
                        request.payload,
                        outcome_snapshot,
                        recorded_at=now,
                    )
                    value = replace(
                        current,
                        version=current.version + 1,
                        supersedes_version=current.version,
                        status=AgendaItemStatus.OCCURRED,
                        source_type=AgendaSourceType.USER_CONFIRMED,
                        source_vendor="USER_CONFIRMED",
                        linked_event_id=request.payload.event_id,
                        linked_report_id=request.payload.report_id,
                        linked_evidence_id=request.payload.evidence_id,
                        outcome_occurred_at=outcome_occurred_at,
                        outcome_note=request.payload.outcome_note,
                        confirmed_by=request.confirmed_by,
                        authorization_note=request.authorization_note,
                        idempotency_key=key,
                        request_fingerprint=fingerprint,
                        recorded_at=now,
                        schema_version=CATALYST_AGENDA_SCHEMA_VERSION,
                    )
                self._repository.append_version(value, expected_version=current.version)
            return envelope_success(
                request_id=request_id,
                clock=self._clock,
                data=self._item_dto(
                    value,
                    as_of=now,
                    outcome_snapshot=outcome_snapshot,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def query(self, request: AgendaQueryInput) -> ToolEnvelope[AgendaQueryDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            as_of = request.as_of or self._clock.now()
            require_aware_datetime(as_of, field_name="as_of")
            window_end = as_of + timedelta(days=request.window_days)
            visible = self._repository.list_visible(as_of=as_of)
            grouped: dict[str, list[CatalystAgendaVersion]] = defaultdict(list)
            for value in visible:
                grouped[value.agenda_item_id].append(value)
            for versions in grouped.values():
                versions.sort(key=lambda item: item.version)

            scope_entries = self._scope_entries(request)
            projected: list[AgendaItemDTO] = []
            overdue_found = False
            candidates = (
                grouped.get(request.agenda_item_id, [])
                if request.agenda_item_id is not None
                else [versions[-1] for versions in grouped.values()]
            )
            if request.agenda_item_id is not None and not candidates:
                raise CatalystAgendaNotFound(
                    "Catalyst Agenda item was not visible at as_of",
                    details={"agenda_item_id": request.agenda_item_id},
                )
            if request.include_history:
                candidates = (
                    grouped[request.agenda_item_id]
                    if request.agenda_item_id is not None
                    else [item for versions in grouped.values() for item in versions]
                )

            for value in candidates:
                latest_version = grouped[value.agenda_item_id][-1].version
                projected_status = (
                    AgendaItemStatus.SUPERSEDED
                    if value.version < latest_version
                    else value.status
                )
                reasons = self._matching_reasons(value, scope_entries)
                if request.agenda_item_id is None and not reasons:
                    continue
                if not self._matches_filters(value, projected_status, request):
                    continue
                overdue = (
                    projected_status is AgendaItemStatus.UPCOMING
                    and self._is_overdue_unverified(value, as_of)
                )
                overdue_found = overdue_found or overdue
                reverify = (
                    projected_status is AgendaItemStatus.UPCOMING
                    and self._requires_date_reverification(value, as_of)
                )
                if (
                    request.agenda_item_id is None
                    and not overdue
                    and not self._inside_window(value, as_of=as_of, window_end=window_end)
                ):
                    continue
                item_limitations = tuple(
                    code
                    for code, applies in (
                        (_OUTCOME_UNVERIFIED, overdue),
                        (_DATE_REVERIFY_REQUIRED, reverify),
                    )
                    if applies
                )
                projected.append(
                    self._item_dto(
                        value,
                        as_of=as_of,
                        projected_status=projected_status,
                        scope_reasons=reasons,
                        limitation_codes=item_limitations,
                    )
                )

            # Detect unresolved overdue items in selected current scope even though the
            # default forward window correctly excludes them from `items`.
            if request.agenda_item_id is None:
                for versions in grouped.values():
                    current = versions[-1]
                    if self._matching_reasons(
                        current, scope_entries
                    ) and self._is_overdue_unverified(current, as_of):
                        overdue_found = True
                        break

            projected.sort(
                key=lambda item: (
                    item.window_start or window_end,
                    item.agenda_item_id,
                    item.version,
                )
            )
            coverage = self._coverage(scope_entries, projected)
            limitations: list[str] = []
            if overdue_found:
                limitations.append(_OUTCOME_UNVERIFIED)
            if any(
                _DATE_REVERIFY_REQUIRED in item.limitation_codes for item in projected
            ):
                limitations.append(_DATE_REVERIFY_REQUIRED)
            if any(item.status is AgendaCoverageStatus.UNAVAILABLE for item in coverage):
                limitations.append(_COVERAGE_UNAVAILABLE)
            if not scope_entries and request.agenda_item_id is None:
                limitations.append("AGENDA_SCOPE_EMPTY")
            total = len(projected)
            page = tuple(projected[request.offset : request.offset + request.limit])
            latest_items = {
                item.agenda_item_id: item
                for item in sorted(projected, key=lambda value: value.version)
            }.values()
            overdue_count = sum(
                _OUTCOME_UNVERIFIED in item.limitation_codes for item in latest_items
            )
            upcoming_items = tuple(
                item
                for item in latest_items
                if item.status is AgendaItemStatus.UPCOMING
                and _OUTCOME_UNVERIFIED not in item.limitation_codes
            )
            seven_day_end = as_of + timedelta(days=7)
            summary = AgendaSummaryDTO(
                upcoming_7d_count=sum(
                    item.window_start is not None and item.window_start <= seven_day_end
                    for item in upcoming_items
                ),
                upcoming_count=len(upcoming_items),
                overdue_count=overdue_count,
                coverage_gap_count=sum(
                    item.status is AgendaCoverageStatus.UNAVAILABLE for item in coverage
                ),
            )
            warnings = tuple(
                WarningInfo(code=code, message=self._warning_message(code))
                for code in limitations
            )
            return envelope_success(
                request_id=request_id,
                clock=self._clock,
                data=AgendaQueryDTO(
                    items=page,
                    coverage=coverage,
                    summary=summary,
                    as_of=as_of,
                    window_end=window_end,
                    total=total,
                    has_more=request.offset + len(page) < total,
                    limitation_codes=tuple(limitations),
                ),
                warnings=warnings,
                degraded=bool(warnings),
            )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def _upsert_version(
        self,
        *,
        agenda_item_id: str,
        version: int,
        supersedes_version: int | None,
        payload: AgendaUpsertPayload,
        request: AgendaMutationInput,
        key: str,
        fingerprint: str,
        recorded_at: datetime,
        created_by: str,
    ) -> CatalystAgendaVersion:
        source_visible_at = payload.source_visible_at or recorded_at
        last_verified_at = payload.last_verified_at or recorded_at
        return CatalystAgendaVersion(
            agenda_item_id=agenda_item_id,
            version=version,
            supersedes_version=supersedes_version,
            instrument_id=payload.instrument_id,
            subject_id=payload.subject_id,
            kind=payload.kind,
            title=payload.title,
            fiscal_period=payload.fiscal_period,
            upstream_event_key=payload.upstream_event_key,
            window_start=payload.window_start,
            window_end=payload.window_end,
            timezone=payload.timezone,
            date_certainty=payload.date_certainty,
            status=AgendaItemStatus.UPCOMING,
            source_type=AgendaSourceType.USER_CONFIRMED,
            source_vendor="USER_CONFIRMED",
            source_reference=payload.source_reference,
            source_visible_at=source_visible_at,
            last_verified_at=last_verified_at,
            expected_question=payload.expected_question,
            linked_event_id=None,
            linked_report_id=None,
            revision_note=payload.revision_note,
            created_by=created_by,
            confirmed_by=request.confirmed_by,
            authorization_note=request.authorization_note,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            historical_vintage=True,
            recorded_at=recorded_at,
        )

    def _validate_references(self, payload: AgendaUpsertPayload) -> None:
        if payload.subject_id is not None and not self._scope_reader.subject_exists(
            payload.subject_id
        ):
            raise InputValidationError(
                "subject_id does not identify a durable Research Subject",
                details={"subject_id": payload.subject_id},
            )
        if payload.instrument_id is not None and not self._scope_reader.instrument_exists(
            payload.instrument_id
        ):
            raise InputValidationError(
                "instrument_id does not identify a durable Instrument",
                details={"instrument_id": payload.instrument_id},
            )

    @staticmethod
    def _validate_outcome_scope(
        current: CatalystAgendaVersion,
        outcome: AgendaOutcomeSnapshot,
        payload: AgendaOutcomeLinkPayload,
    ) -> None:
        if current.subject_id is not None and current.subject_id != outcome.subject_id:
            raise InvalidResearchLink(
                "Agenda outcome must belong to the Agenda Research Subject",
                details={
                    "agenda_item_id": current.agenda_item_id,
                    "subject_id": current.subject_id,
                    "outcome_subject_id": outcome.subject_id,
                },
            )
        if current.instrument_id is not None:
            coverage_by_fact = (
                ("Event", payload.event_id, outcome.event_instrument_ids),
                ("Report", payload.report_id, outcome.report_instrument_ids),
                ("Evidence", payload.evidence_id, outcome.evidence_instrument_ids),
            )
            for fact_type, fact_id, instrument_ids in coverage_by_fact:
                if fact_id is not None and current.instrument_id not in instrument_ids:
                    raise InvalidResearchLink(
                        f"Agenda outcome {fact_type} does not directly cover the Agenda Instrument",
                        details={
                            "agenda_item_id": current.agenda_item_id,
                            "instrument_id": current.instrument_id,
                            "fact_type": fact_type.upper(),
                            "fact_id": fact_id,
                        },
                    )
        if (
            current.instrument_id is None
            and current.subject_id is None
            and outcome.subject_type is not ResearchSubjectType.MACRO
        ):
            raise InvalidResearchLink(
                "global macro Agenda outcomes require a MACRO Research Subject",
                details={
                    "agenda_item_id": current.agenda_item_id,
                    "outcome_subject_id": outcome.subject_id,
                },
            )

    @staticmethod
    def _resolve_outcome_occurred_at(
        payload: AgendaOutcomeLinkPayload,
        outcome: AgendaOutcomeSnapshot,
        *,
        recorded_at: datetime,
    ) -> datetime:
        if outcome.event_occurred_at is not None:
            if (
                payload.outcome_occurred_at is not None
                and payload.outcome_occurred_at != outcome.event_occurred_at
            ):
                raise InvalidResearchLink(
                    "outcome_occurred_at must equal the linked Event occurred_at",
                    details={
                        "event_id": payload.event_id,
                        "event_occurred_at": outcome.event_occurred_at.isoformat(),
                    },
                )
            occurred_at = outcome.event_occurred_at
        else:
            if payload.outcome_occurred_at is None:
                raise InputValidationError(
                    "outcome_occurred_at is required without a linked Event"
                )
            occurred_at = payload.outcome_occurred_at
        require_aware_datetime(occurred_at, field_name="outcome_occurred_at")
        if occurred_at > recorded_at:
            raise InputValidationError("outcome_occurred_at must be <= recorded_at")
        return occurred_at

    def _item_dto(
        self,
        value: CatalystAgendaVersion,
        *,
        as_of: datetime,
        projected_status: AgendaItemStatus | None = None,
        scope_reasons: tuple[AgendaScopeReason, ...] = (),
        limitation_codes: tuple[str, ...] = (),
        outcome_snapshot: AgendaOutcomeSnapshot | None = None,
    ) -> AgendaItemDTO:
        snapshot = outcome_snapshot
        if snapshot is None and (
            value.linked_event_id
            or value.linked_report_id
            or value.linked_evidence_id
        ):
            snapshot = self._outcome_reader.resolve(
                event_id=value.linked_event_id,
                report_id=value.linked_report_id,
                evidence_id=value.linked_evidence_id,
                subject_id=value.subject_id,
                as_of=as_of,
            )
        return AgendaItemDTO.from_domain(
            value,
            projected_status=projected_status,
            scope_reasons=scope_reasons,
            limitation_codes=limitation_codes,
            resolved_evidence_ids=(
                snapshot.resolved_evidence_ids if snapshot is not None else ()
            ),
        )

    @staticmethod
    def _validate_identity_fields(
        current: CatalystAgendaVersion, payload: AgendaUpsertPayload
    ) -> None:
        if (
            current.instrument_id != payload.instrument_id
            or current.subject_id != payload.subject_id
            or current.kind is not payload.kind
            or current.fiscal_period != payload.fiscal_period
            or current.upstream_event_key != payload.upstream_event_key
            or current.fiscal_period != payload.fiscal_period
        ):
            raise CatalystAgendaVersionConflict(
                "Agenda identity fields cannot change during a revision"
            )

    @staticmethod
    def _logical_key(value: CatalystAgendaVersion) -> str:
        if value.upstream_event_key:
            vendor = (
                f":{value.source_vendor}"
                if value.source_type is AgendaSourceType.PROVIDER
                else ""
            )
            return (
                f"{value.source_type.value}{vendor}:{value.kind.value}:"
                f"{value.upstream_event_key}"
            )
        return ":".join(
            (
                value.instrument_id or "-",
                value.subject_id or "-",
                value.kind.value,
                value.fiscal_period or value.title.strip(),
            )
        )

    @staticmethod
    def _require_mutable(
        value: CatalystAgendaVersion,
        *,
        expected_version: int,
        action: AgendaMutationAction,
    ) -> None:
        if value.version != expected_version:
            raise CatalystAgendaVersionConflict(
                "Catalyst Agenda expected_version is stale",
                details={"expected_version": expected_version, "current_version": value.version},
            )
        if value.status is AgendaItemStatus.UPCOMING:
            return
        if (
            value.status is AgendaItemStatus.OCCURRED
            and action is AgendaMutationAction.LINK_OUTCOME
        ):
            return
        raise CatalystAgendaVersionConflict(
            "only UPCOMING items may be revised or cancelled; only LINK_OUTCOME "
            "may revise an OCCURRED item"
        )

    def _scope_entries(self, request: AgendaQueryInput) -> tuple[AgendaScopeEntry, ...]:
        selected = set(request.filters.scopes)
        durable = self._scope_reader.read_current().entries
        combined: dict[tuple[str | None, str | None], set[AgendaScopeReason]] = defaultdict(set)
        for entry in durable:
            for reason in entry.reasons:
                if reason in selected:
                    combined[(entry.instrument_id, entry.subject_id)].add(reason)
        if AgendaScopeReason.GLOBAL in selected:
            combined[(None, None)].add(AgendaScopeReason.GLOBAL)
        if AgendaScopeReason.EXPLICIT in selected:
            for instrument_id in request.filters.instrument_ids:
                combined[(instrument_id, None)].add(AgendaScopeReason.EXPLICIT)
            for subject_id in request.filters.subject_ids:
                combined[(None, subject_id)].add(AgendaScopeReason.EXPLICIT)
        if request.agenda_item_id is not None:
            for value in self._repository.list_visible(as_of=request.as_of or self._clock.now()):
                if value.agenda_item_id == request.agenda_item_id:
                    combined[(value.instrument_id, value.subject_id)].add(
                        AgendaScopeReason.EXPLICIT
                    )
        return tuple(
            AgendaScopeEntry(key[0], key[1], tuple(sorted(reasons, key=str)))
            for key, reasons in combined.items()
        )

    @staticmethod
    def _matching_reasons(
        value: CatalystAgendaVersion, entries: tuple[AgendaScopeEntry, ...]
    ) -> tuple[AgendaScopeReason, ...]:
        reasons: set[AgendaScopeReason] = set()
        for entry in entries:
            if (
                AgendaScopeReason.GLOBAL in entry.reasons
                and value.instrument_id is None
                and value.subject_id is None
                and value.kind in {AgendaItemKind.MACRO_RELEASE, AgendaItemKind.POLICY}
            ):
                reasons.add(AgendaScopeReason.GLOBAL)
            if entry.subject_id is not None and entry.subject_id == value.subject_id:
                reasons.update(entry.reasons)
            if entry.instrument_id is not None and entry.instrument_id == value.instrument_id:
                reasons.update(entry.reasons)
        return tuple(sorted(reasons, key=str))

    @staticmethod
    def _matches_filters(
        value: CatalystAgendaVersion,
        projected_status: AgendaItemStatus,
        request: AgendaQueryInput,
    ) -> bool:
        filters = request.filters
        if filters.instrument_ids and value.instrument_id not in filters.instrument_ids:
            return False
        if filters.subject_ids and value.subject_id not in filters.subject_ids:
            return False
        if filters.kinds and value.kind not in filters.kinds:
            return False
        return not filters.statuses or projected_status in filters.statuses

    @staticmethod
    def _inside_window(
        value: CatalystAgendaVersion, *, as_of: datetime, window_end: datetime
    ) -> bool:
        if value.window_start is None or value.window_end is None:
            return True
        return value.window_end >= as_of and value.window_start <= window_end

    @staticmethod
    def _is_overdue_unverified(value: CatalystAgendaVersion, as_of: datetime) -> bool:
        return bool(
            value.status is AgendaItemStatus.UPCOMING
            and value.window_end is not None
            and value.window_end < as_of
            and value.linked_event_id is None
            and value.linked_report_id is None
            and value.linked_evidence_id is None
        )

    @staticmethod
    def _requires_date_reverification(
        value: CatalystAgendaVersion, as_of: datetime
    ) -> bool:
        return bool(
            value.status is AgendaItemStatus.UPCOMING
            and as_of - value.last_verified_at > _DATE_REVERIFY_AFTER
        )

    @staticmethod
    def _coverage(
        entries: tuple[AgendaScopeEntry, ...], items: list[AgendaItemDTO]
    ) -> tuple[AgendaCoverageDTO, ...]:
        values: list[AgendaCoverageDTO] = []
        for entry in entries:
            matched_ids = {
                item.agenda_item_id
                for item in items
                if item.status is AgendaItemStatus.UPCOMING
                and _OUTCOME_UNVERIFIED not in item.limitation_codes
                and (
                    (
                        AgendaScopeReason.GLOBAL in entry.reasons
                        and item.instrument_id is None
                        and item.subject_id is None
                    )
                    or
                    (
                        entry.instrument_id is not None
                        and item.instrument_id == entry.instrument_id
                    )
                    or (
                        entry.subject_id is not None
                        and item.subject_id == entry.subject_id
                    )
                )
            }
            count = len(matched_ids)
            available = count > 0
            values.append(
                AgendaCoverageDTO(
                    instrument_id=entry.instrument_id,
                    subject_id=entry.subject_id,
                    scope_reasons=entry.reasons,
                    status=(
                        AgendaCoverageStatus.AVAILABLE
                        if available
                        else AgendaCoverageStatus.UNAVAILABLE
                    ),
                    matched_item_count=count,
                    limitation_codes=() if available else (_COVERAGE_UNAVAILABLE,),
                )
            )
        return tuple(values)

    @staticmethod
    def _warning_message(code: str) -> str:
        if code == _OUTCOME_UNVERIFIED:
            return "An UPCOMING item passed its window without a linked durable outcome fact."
        if code == _DATE_REVERIFY_REQUIRED:
            return "An UPCOMING item has not had its date verified in more than seven days."
        if code == "AGENDA_SCOPE_EMPTY":
            return (
                "No current durable global, Portfolio, Watchlist, Subject, "
                "or explicit scope exists."
            )
        return "No reliable Catalyst Agenda item covers at least one selected durable scope."
