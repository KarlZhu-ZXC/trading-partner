from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from application.dto.catalyst_agenda import (
    AgendaCancelPayload,
    AgendaMutationAction,
    AgendaMutationInput,
    AgendaOutcomeLinkPayload,
    AgendaQueryFilters,
    AgendaQueryInput,
    AgendaUpsertPayload,
)
from application.ports.catalyst_agenda_outcome_reader import AgendaOutcomeSnapshot
from application.ports.catalyst_agenda_scope_reader import (
    AgendaScopeEntry,
    AgendaScopeSnapshot,
)
from application.services.catalyst_agenda_service import CatalystAgendaService
from conftest import FixedClock, SequentialIdGenerator
from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaItemStatus,
    AgendaScopeReason,
    AgendaSourceType,
)
from domain.catalyst_agenda.models import CatalystAgendaIdentity, CatalystAgendaVersion
from domain.common.enums import ResearchSubjectType
from domain.common.errors import (
    DataContractError,
    HistoricalVisibilityViolation,
    ImmutableResearchRecord,
    InvalidResearchLink,
)
from infrastructure.persistence.catalyst_agenda_outcome_reader import (
    SqlAlchemyCatalystAgendaOutcomeReader,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm import CatalystAgendaVersionRow
from infrastructure.persistence.repositories.catalyst_agenda import (
    SqlAlchemyCatalystAgendaRepository,
)
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 8, 9, 8, tzinfo=UTC)
INSTRUMENT = "equity:US:NVDA"
ITEM_ID = "agenda_00000000-0000-7000-8000-000000000010"
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000010"
EVENT_ID = "event_00000000-0000-7000-8000-000000000010"
REPORT_ID = "report_00000000-0000-7000-8000-000000000010"
EVIDENCE_ID = "evidence_00000000-0000-7000-8000-000000000010"


def _payload(
    *,
    title: str = "NVDA earnings",
    start: datetime | None = None,
    end: datetime | None = None,
    fiscal_period: str | None = "FY2026Q2",
    verified_at: datetime | None = None,
) -> AgendaUpsertPayload:
    start = start or NOW + timedelta(days=3)
    end = end or start
    return AgendaUpsertPayload(
        instrument_id=INSTRUMENT,
        kind=AgendaItemKind.EARNINGS,
        title=title,
        fiscal_period=fiscal_period,
        window_start=start,
        window_end=end,
        timezone="America/New_York",
        date_certainty=AgendaDateCertainty.CONFIRMED,
        source_visible_at=verified_at,
        last_verified_at=verified_at,
        expected_question="Did data-center growth remain durable?",
    )


def _mutation(
    *,
    action: AgendaMutationAction = AgendaMutationAction.CREATE,
    payload: AgendaUpsertPayload | AgendaCancelPayload | AgendaOutcomeLinkPayload | None = None,
    item_id: str | None = None,
    expected_version: int | None = None,
    key: str = "agenda-create-1",
) -> AgendaMutationInput:
    return AgendaMutationInput(
        action=action,
        agenda_item_id=item_id,
        expected_version=expected_version,
        confirmed_by="user",
        authorization_note="Track this known catalyst.",
        idempotency_key=key,
        payload=payload or _payload(),
    )


def _version(
    *,
    item_id: str = ITEM_ID,
    version: int = 1,
    status: AgendaItemStatus = AgendaItemStatus.UPCOMING,
    source_visible_at: datetime = NOW,
    recorded_at: datetime = NOW,
    key: str = "key-1",
) -> CatalystAgendaVersion:
    return CatalystAgendaVersion(
        agenda_item_id=item_id,
        version=version,
        supersedes_version=None if version == 1 else version - 1,
        instrument_id=INSTRUMENT,
        subject_id=None,
        kind=AgendaItemKind.EARNINGS,
        title="NVDA earnings",
        fiscal_period="FY2026Q2",
        upstream_event_key=None,
        window_start=NOW + timedelta(days=3),
        window_end=NOW + timedelta(days=3),
        timezone="America/New_York",
        date_certainty=AgendaDateCertainty.CONFIRMED,
        status=status,
        source_type=AgendaSourceType.USER_CONFIRMED,
        source_vendor="USER_CONFIRMED",
        source_reference=None,
        source_visible_at=source_visible_at,
        last_verified_at=source_visible_at,
        expected_question=None,
        linked_event_id=None,
        linked_report_id=None,
        revision_note=None,
        created_by="user",
        confirmed_by="user",
        authorization_note="Track this known catalyst.",
        idempotency_key=key,
        request_fingerprint="0" * 64,
        historical_vintage=True,
        recorded_at=recorded_at,
    )


class _Repository:
    def __init__(self) -> None:
        self.values: list[CatalystAgendaVersion] = []

    def get_by_idempotency_key(self, key: str) -> CatalystAgendaVersion | None:
        return next((item for item in self.values if item.idempotency_key == key), None)

    def get_by_logical_key(self, key: str) -> CatalystAgendaVersion | None:
        for item in self.values:
            if item.upstream_event_key:
                candidate = f"{item.source_type.value}:{item.kind.value}:{item.upstream_event_key}"
            else:
                candidate = ":".join(
                    (
                        item.instrument_id or "-",
                        item.subject_id or "-",
                        item.kind.value,
                        item.fiscal_period or item.title.strip(),
                    )
                )
            if candidate == key:
                return self.get_current(item.agenda_item_id)
        return None

    def get_current(self, agenda_item_id: str) -> CatalystAgendaVersion | None:
        matches = [item for item in self.values if item.agenda_item_id == agenda_item_id]
        return max(matches, key=lambda item: item.version) if matches else None

    def get_current_by_logical_key(self, logical_key: str) -> CatalystAgendaVersion | None:
        del logical_key
        return None

    def append_initial(
        self, identity: CatalystAgendaIdentity, value: CatalystAgendaVersion
    ) -> CatalystAgendaVersion:
        assert identity.agenda_item_id == value.agenda_item_id
        self.values.append(value)
        return value

    def append_version(
        self, value: CatalystAgendaVersion, *, expected_version: int
    ) -> CatalystAgendaVersion:
        assert self.get_current(value.agenda_item_id).version == expected_version  # type: ignore[union-attr]
        self.values.append(value)
        return value

    def list_visible(self, *, as_of: datetime) -> tuple[CatalystAgendaVersion, ...]:
        return tuple(
            item
            for item in self.values
            if item.source_visible_at <= as_of and item.recorded_at <= as_of
        )


class _Scope:
    def read_current(self) -> AgendaScopeSnapshot:
        return AgendaScopeSnapshot(
            entries=(
                AgendaScopeEntry(
                    instrument_id=INSTRUMENT,
                    subject_id=None,
                    reasons=(AgendaScopeReason.PORTFOLIO, AgendaScopeReason.WATCHLIST),
                ),
            )
        )

    def subject_exists(self, subject_id: str) -> bool:
        return True

    def instrument_exists(self, instrument_id: str) -> bool:
        return instrument_id == INSTRUMENT


class _OutcomeReader:
    def __init__(self, snapshot: AgendaOutcomeSnapshot | None = None) -> None:
        self.snapshot = snapshot or AgendaOutcomeSnapshot(
            subject_id=SUBJECT_ID,
            subject_type=ResearchSubjectType.COMPANY,
            event_instrument_ids=(INSTRUMENT,),
            report_instrument_ids=(INSTRUMENT,),
            evidence_instrument_ids=(INSTRUMENT,),
            resolved_evidence_ids=(EVIDENCE_ID,),
            fact_visible_at=NOW,
            event_occurred_at=NOW - timedelta(hours=1),
        )

    def resolve(
        self,
        *,
        event_id: str | None,
        report_id: str | None,
        evidence_id: str | None,
        subject_id: str | None,
        as_of: datetime,
    ) -> AgendaOutcomeSnapshot:
        assert event_id is not None or report_id is not None or evidence_id is not None
        del subject_id
        assert as_of >= self.snapshot.fact_visible_at
        return (
            self.snapshot
            if event_id is not None
            else replace(self.snapshot, event_occurred_at=None)
        )


class _Values:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get(self, value_id: str) -> object:
        return self.values[value_id]


class _Links:
    def __init__(self, links: dict[tuple[str, str], object]) -> None:
        self.links = links

    def exists(self, subject_id: str, evidence_id: str) -> bool:
        return (subject_id, evidence_id) in self.links

    def get(self, subject_id: str, evidence_id: str) -> object:
        return self.links[(subject_id, evidence_id)]

    def list_subjects(self, evidence_id: str) -> tuple[str, ...]:
        return tuple(
            subject_id
            for subject_id, linked_evidence_id in self.links
            if linked_evidence_id == evidence_id
        )


class _OutcomeUow:
    def __init__(
        self,
        *,
        subject: object,
        event: object,
        report: object,
        evidence: object,
        link: object,
    ) -> None:
        self.subjects = _Values({SUBJECT_ID: subject})
        self.events = _Values({EVENT_ID: event})
        self.reports = _Values({REPORT_ID: report})
        self.evidence = _Values({EVIDENCE_ID: evidence})
        self.subject_evidence_links = _Links({(SUBJECT_ID, EVIDENCE_ID): link})

    def __enter__(self) -> _OutcomeUow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _service(
    repository: _Repository,
    clock: FixedClock | None = None,
    outcome_reader: _OutcomeReader | None = None,
) -> CatalystAgendaService:
    return CatalystAgendaService(
        repository,
        _Scope(),
        outcome_reader or _OutcomeReader(),
        clock or FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )


def test_unknown_may_omit_window_but_confirmed_may_not() -> None:
    unknown = AgendaUpsertPayload(
        instrument_id=INSTRUMENT,
        kind=AgendaItemKind.USER_DEFINED,
        title="Date not yet known",
        date_certainty=AgendaDateCertainty.UNKNOWN,
    )
    assert unknown.window_start is None
    with pytest.raises(ValidationError, match="only UNKNOWN"):
        AgendaUpsertPayload(
            instrument_id=INSTRUMENT,
            kind=AgendaItemKind.EARNINGS,
            title="Missing window",
            date_certainty=AgendaDateCertainty.CONFIRMED,
        )


def test_domain_requires_scope_and_append_only_version_chain() -> None:
    with pytest.raises(DataContractError, match="MACRO_RELEASE or POLICY"):
        replace(_version(), instrument_id=None)
    with pytest.raises(DataContractError, match="immediately prior"):
        replace(_version(version=2), supersedes_version=None)


def test_mutation_shape_is_closed_by_action() -> None:
    with pytest.raises(ValidationError, match="CREATE cannot supply"):
        _mutation(item_id=ITEM_ID, expected_version=1)
    with pytest.raises(ValidationError, match="CANCEL requires"):
        _mutation(
            action=AgendaMutationAction.CANCEL,
            item_id=ITEM_ID,
            expected_version=1,
            payload=_payload(),
        )


def test_create_is_user_confirmed_and_idempotent() -> None:
    repository = _Repository()
    service = _service(repository)
    request = _mutation()
    first = service.manage(request)
    duplicate = service.manage(request)
    assert first.ok and first.data is not None
    assert first.data.source_type is AgendaSourceType.USER_CONFIRMED
    assert first.data.historical_vintage is True
    assert duplicate.ok and duplicate.data == first.data
    assert duplicate.warnings[0].code == "DUPLICATE_IDEMPOTENCY_KEY"
    assert len(repository.values) == 1
    duplicate_logical_item = service.manage(_mutation(key="agenda-create-duplicate-logical"))
    assert not duplicate_logical_item.ok
    assert duplicate_logical_item.errors[0].code == "CATALYST_AGENDA_VERSION_CONFLICT"


def test_revise_keeps_identity_and_history_projects_superseded() -> None:
    repository = _Repository()
    service = _service(repository)
    created = service.manage(_mutation())
    assert created.data is not None
    revised = service.manage(
        _mutation(
            action=AgendaMutationAction.REVISE,
            item_id=created.data.agenda_item_id,
            expected_version=1,
            payload=_payload(title="Updated earnings title"),
            key="agenda-revise-1",
        )
    )
    assert revised.ok and revised.data is not None and revised.data.version == 2
    history = service.query(
        AgendaQueryInput(agenda_item_id=created.data.agenda_item_id, include_history=True)
    )
    assert history.ok and history.data is not None
    assert [item.status for item in history.data.items] == [
        AgendaItemStatus.SUPERSEDED,
        AgendaItemStatus.UPCOMING,
    ]
    assert history.data.summary.upcoming_count == 1
    assert history.data.summary.upcoming_7d_count == 1
    rejected = service.manage(
        _mutation(
            action=AgendaMutationAction.REVISE,
            item_id=created.data.agenda_item_id,
            expected_version=2,
            payload=_payload(fiscal_period="FY2026Q3"),
            key="agenda-revise-bad-identity",
        )
    )
    assert not rejected.ok
    assert rejected.errors[0].code == "CATALYST_AGENDA_VERSION_CONFLICT"


def test_cancel_appends_and_stale_writer_is_rejected() -> None:
    repository = _Repository()
    service = _service(repository)
    created = service.manage(_mutation())
    assert created.data is not None
    cancelled = service.manage(
        _mutation(
            action=AgendaMutationAction.CANCEL,
            item_id=created.data.agenda_item_id,
            expected_version=1,
            payload=AgendaCancelPayload(cancellation_reason="Company cancelled the event."),
            key="agenda-cancel-1",
        )
    )
    assert cancelled.ok and cancelled.data is not None
    assert cancelled.data.status is AgendaItemStatus.CANCELLED
    assert len(repository.values) == 2
    stale = service.manage(
        _mutation(
            action=AgendaMutationAction.REVISE,
            item_id=created.data.agenda_item_id,
            expected_version=1,
            key="agenda-stale",
        )
    )
    assert not stale.ok
    assert stale.errors[0].code == "CATALYST_AGENDA_VERSION_CONFLICT"


def test_link_outcome_is_append_only_idempotent_and_resolves_evidence() -> None:
    with pytest.raises(ValidationError, match="event_id, report_id, or evidence_id"):
        AgendaOutcomeLinkPayload(
            outcome_occurred_at=NOW,
            outcome_note="No durable fact supplied.",
        )
    with pytest.raises(ValidationError, match="outcome_occurred_at"):
        AgendaOutcomeLinkPayload(
            evidence_id=EVIDENCE_ID,
            outcome_note="Direct evidence requires an explicit occurrence time.",
        )

    repository = _Repository()
    service = _service(repository)
    created = service.manage(_mutation())
    assert created.data is not None
    request = _mutation(
        action=AgendaMutationAction.LINK_OUTCOME,
        item_id=created.data.agenda_item_id,
        expected_version=1,
        payload=AgendaOutcomeLinkPayload(
            event_id=EVENT_ID,
            report_id=REPORT_ID,
            outcome_note="The scheduled earnings release occurred.",
        ),
        key="agenda-link-outcome-1",
    )
    linked = service.manage(request)
    replay = service.manage(request)

    assert linked.ok and linked.data is not None
    assert linked.data.version == 2
    assert linked.data.status is AgendaItemStatus.OCCURRED
    assert linked.data.linked_event_id == EVENT_ID
    assert linked.data.linked_report_id == REPORT_ID
    assert linked.data.outcome_occurred_at == NOW - timedelta(hours=1)
    assert linked.data.outcome_note == "The scheduled earnings release occurred."
    assert linked.data.resolved_evidence_ids == (EVIDENCE_ID,)
    assert replay.ok and replay.data == linked.data
    assert replay.warnings[0].code == "DUPLICATE_IDEMPOTENCY_KEY"
    assert len(repository.values) == 2

    immutable = service.manage(
        _mutation(
            action=AgendaMutationAction.REVISE,
            item_id=created.data.agenda_item_id,
            expected_version=2,
            key="agenda-revise-occurred",
        )
    )
    assert not immutable.ok
    assert immutable.errors[0].code == "CATALYST_AGENDA_VERSION_CONFLICT"

    revised_outcome = service.manage(
        _mutation(
            action=AgendaMutationAction.LINK_OUTCOME,
            item_id=created.data.agenda_item_id,
            expected_version=2,
            payload=AgendaOutcomeLinkPayload(
                evidence_id=EVIDENCE_ID,
                outcome_occurred_at=NOW - timedelta(hours=2),
                outcome_note="Corrected outcome links after human review.",
            ),
            key="agenda-link-outcome-revision",
        )
    )
    assert revised_outcome.ok and revised_outcome.data is not None
    assert revised_outcome.data.version == 3
    assert revised_outcome.data.linked_event_id is None
    assert revised_outcome.data.linked_evidence_id == EVIDENCE_ID


def test_link_outcome_rejects_cross_subject_and_instrument_ownership() -> None:
    subject_repository = _Repository()
    subject_service = _service(
        subject_repository,
        outcome_reader=_OutcomeReader(
            AgendaOutcomeSnapshot(
                subject_id="case_00000000-0000-7000-8000-000000000099",
                subject_type=ResearchSubjectType.COMPANY,
                event_instrument_ids=(INSTRUMENT,),
                report_instrument_ids=(INSTRUMENT,),
                evidence_instrument_ids=(),
                resolved_evidence_ids=(),
                fact_visible_at=NOW,
                event_occurred_at=NOW - timedelta(hours=1),
            )
        ),
    )
    created = subject_service.manage(
        _mutation(payload=_payload().model_copy(update={"subject_id": SUBJECT_ID}))
    )
    assert created.data is not None
    rejected_subject = subject_service.manage(
        _mutation(
            action=AgendaMutationAction.LINK_OUTCOME,
            item_id=created.data.agenda_item_id,
            expected_version=1,
            payload=AgendaOutcomeLinkPayload(
                event_id=EVENT_ID,
                outcome_note="Wrong Research Subject.",
            ),
            key="agenda-link-wrong-subject",
        )
    )
    assert not rejected_subject.ok
    assert rejected_subject.errors[0].code == "INVALID_RESEARCH_LINK"

    instrument_repository = _Repository()
    instrument_service = _service(
        instrument_repository,
        outcome_reader=_OutcomeReader(
            AgendaOutcomeSnapshot(
                subject_id=SUBJECT_ID,
                subject_type=ResearchSubjectType.COMPANY,
                event_instrument_ids=(),
                report_instrument_ids=("equity:US:AMD",),
                evidence_instrument_ids=(),
                resolved_evidence_ids=(),
                fact_visible_at=NOW,
                event_occurred_at=None,
            )
        ),
    )
    instrument_created = instrument_service.manage(_mutation())
    assert instrument_created.data is not None
    rejected_instrument = instrument_service.manage(
        _mutation(
            action=AgendaMutationAction.LINK_OUTCOME,
            item_id=instrument_created.data.agenda_item_id,
            expected_version=1,
            payload=AgendaOutcomeLinkPayload(
                report_id=REPORT_ID,
                outcome_occurred_at=NOW - timedelta(hours=1),
                outcome_note="Wrong Instrument ownership.",
            ),
            key="agenda-link-wrong-instrument",
        )
    )
    assert not rejected_instrument.ok
    assert rejected_instrument.errors[0].code == "INVALID_RESEARCH_LINK"


def test_global_macro_outcome_requires_a_macro_research_subject() -> None:
    repository = _Repository()
    service = _service(repository)
    payload = AgendaUpsertPayload(
        kind=AgendaItemKind.MACRO_RELEASE,
        title="US CPI release",
        upstream_event_key="fred:10:2026-08:1",
        window_start=NOW + timedelta(days=3),
        window_end=NOW + timedelta(days=3, hours=23),
        timezone="America/Chicago",
        date_certainty=AgendaDateCertainty.CONFIRMED,
    )
    created = service.manage(_mutation(payload=payload, key="agenda-global-macro"))
    assert created.data is not None

    rejected = service.manage(
        _mutation(
            action=AgendaMutationAction.LINK_OUTCOME,
            item_id=created.data.agenda_item_id,
            expected_version=1,
            payload=AgendaOutcomeLinkPayload(
                event_id=EVENT_ID,
                outcome_note="This company event is not a macro outcome.",
            ),
            key="agenda-global-macro-link",
        )
    )

    assert not rejected.ok
    assert rejected.errors[0].code == "INVALID_RESEARCH_LINK"


def test_query_returns_overdue_item_and_reverify_warning() -> None:
    repository = _Repository()
    clock = FixedClock(NOW)
    service = _service(repository, clock)
    old = NOW - timedelta(days=10)
    created = service.manage(
        _mutation(payload=_payload(start=old, end=old, verified_at=old))
    )
    assert created.ok
    result = service.query(AgendaQueryInput())
    assert result.ok and result.data is not None
    assert len(result.data.items) == 1
    assert result.data.items[0].limitation_codes == (
        "AGENDA_EVENT_OUTCOME_UNVERIFIED",
        "AGENDA_DATE_REVERIFY_REQUIRED",
    )
    assert result.data.summary.overdue_count == 1
    assert result.data.summary.upcoming_count == 0
    assert {warning.code for warning in result.warnings} >= {
        "AGENDA_EVENT_OUTCOME_UNVERIFIED",
        "AGENDA_DATE_REVERIFY_REQUIRED",
    }


def test_no_visible_item_is_coverage_unavailable_not_no_catalysts() -> None:
    repository = _Repository()
    repository.values.append(
        _version(
            source_visible_at=NOW + timedelta(hours=1),
            recorded_at=NOW + timedelta(hours=1),
        )
    )
    result = _service(repository).query(
        AgendaQueryInput(
            filters=AgendaQueryFilters(scopes=(AgendaScopeReason.PORTFOLIO,))
        )
    )
    assert result.ok and result.data is not None
    assert result.data.items == ()
    assert result.data.coverage[0].status.value == "UNAVAILABLE"
    assert result.data.summary.coverage_gap_count == 1
    assert "AGENDA_COVERAGE_UNAVAILABLE" in result.data.limitation_codes


def test_outcome_reader_validates_fact_and_evidence_visibility() -> None:
    subject = SimpleNamespace(
        primary_instrument_id=INSTRUMENT,
        subject_type=ResearchSubjectType.COMPANY,
    )
    event = SimpleNamespace(
        event_id=EVENT_ID,
        subject_id=SUBJECT_ID,
        recorded_at=NOW - timedelta(hours=1),
        occurred_at=NOW - timedelta(hours=2),
        instrument_ids=(INSTRUMENT,),
        evidence_ids=(EVIDENCE_ID,),
    )
    report = SimpleNamespace(
        report_id=REPORT_ID,
        subject_id=SUBJECT_ID,
        created_at=NOW - timedelta(minutes=30),
        as_of=NOW - timedelta(hours=2),
        evidence_ids=(EVIDENCE_ID,),
    )
    evidence = SimpleNamespace(
        observed_at=NOW - timedelta(hours=3),
        instrument_ids=(INSTRUMENT,),
    )
    link = SimpleNamespace(linked_at=NOW - timedelta(hours=1))
    uow = _OutcomeUow(
        subject=subject,
        event=event,
        report=report,
        evidence=evidence,
        link=link,
    )
    reader = SqlAlchemyCatalystAgendaOutcomeReader(lambda: uow)  # type: ignore[arg-type]

    result = reader.resolve(
        event_id=EVENT_ID,
        report_id=REPORT_ID,
        evidence_id=None,
        subject_id=SUBJECT_ID,
        as_of=NOW,
    )
    assert result.subject_id == SUBJECT_ID
    assert result.event_instrument_ids == (INSTRUMENT,)
    assert result.report_instrument_ids == (INSTRUMENT,)
    assert result.resolved_evidence_ids == (EVIDENCE_ID,)
    assert result.fact_visible_at == report.created_at
    assert result.event_occurred_at == event.occurred_at

    direct_evidence = reader.resolve(
        event_id=None,
        report_id=None,
        evidence_id=EVIDENCE_ID,
        subject_id=None,
        as_of=NOW,
    )
    assert direct_evidence.subject_id == SUBJECT_ID
    assert direct_evidence.evidence_instrument_ids == (INSTRUMENT,)
    assert direct_evidence.event_occurred_at is None

    future_evidence_uow = _OutcomeUow(
        subject=subject,
        event=event,
        report=report,
        evidence=SimpleNamespace(
            observed_at=NOW + timedelta(minutes=1),
            instrument_ids=(INSTRUMENT,),
        ),
        link=link,
    )
    future_reader = SqlAlchemyCatalystAgendaOutcomeReader(
        lambda: future_evidence_uow  # type: ignore[arg-type]
    )
    with pytest.raises(HistoricalVisibilityViolation, match="Evidence is not visible"):
        future_reader.resolve(
            event_id=EVENT_ID,
            report_id=REPORT_ID,
            evidence_id=None,
            subject_id=SUBJECT_ID,
            as_of=NOW,
        )

    cross_subject_uow = _OutcomeUow(
        subject=subject,
        event=event,
        report=SimpleNamespace(
            **{
                **report.__dict__,
                "subject_id": "case_00000000-0000-7000-8000-000000000099",
            }
        ),
        evidence=evidence,
        link=link,
    )
    cross_reader = SqlAlchemyCatalystAgendaOutcomeReader(
        lambda: cross_subject_uow  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidResearchLink, match="same Research Subject"):
        cross_reader.resolve(
            event_id=EVENT_ID,
            report_id=REPORT_ID,
            evidence_id=None,
            subject_id=SUBJECT_ID,
            as_of=NOW,
        )


def test_sql_repository_persists_versions_and_blocks_updates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyCatalystAgendaRepository(engine)
    first = _version()
    repository.append_initial(
        CatalystAgendaIdentity(ITEM_ID, "manual-v1:nvda-fy2026q2", NOW), first
    )
    second = replace(
        first,
        version=2,
        supersedes_version=1,
        title="Revised title",
        idempotency_key="key-2",
        request_fingerprint="1" * 64,
    )
    repository.append_version(second, expected_version=1)
    assert [item.version for item in repository.list_visible(as_of=NOW)] == [1, 2]
    with Session(engine) as session:
        row = session.scalar(select(CatalystAgendaVersionRow).where(
            CatalystAgendaVersionRow.agenda_item_id == ITEM_ID,
            CatalystAgendaVersionRow.version == 1,
        ))
        assert row is not None
        row.title = "Forbidden mutation"
        with pytest.raises(ImmutableResearchRecord, match="immutable"):
            session.commit()
