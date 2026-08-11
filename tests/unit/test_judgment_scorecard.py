from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from application.services.judgment_scorecard_service import JudgmentScorecardService
from conftest import FixedClock, SequentialIdGenerator
from domain.catalyst_agenda.enums import AgendaItemStatus
from domain.common.enums import EvidenceStance
from domain.common.errors import DataContractError
from domain.scorecard.enums import ScorecardDimensionStatus, ScorecardStatus
from domain.scorecard.models import (
    JUDGMENT_SCORECARD_S0_ALGORITHM_VERSION,
    JUDGMENT_SCORECARD_S0_DIMENSION_CODES,
    JUDGMENT_SCORECARD_S0_SCHEMA_VERSION,
    JudgmentScorecardRun,
    ScorecardDimension,
    ScorecardSourceRef,
)
from infrastructure.persistence.judgment_scorecard_repository import (
    SqlAlchemyJudgmentScorecardRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 8, 9, 8, tzinfo=UTC)


def _dimensions() -> tuple[ScorecardDimension, ...]:
    return tuple(
        ScorecardDimension(
            code=code,
            status=ScorecardDimensionStatus.NOT_EVALUATED,
            result_code="NOT_AVAILABLE",
            title=code,
            summary="No source fact is available in this focused test.",
            source_refs=(
                ScorecardSourceRef(
                    "THESIS_REVISION",
                    "rev_00000000-0000-7000-8000-000000000001",
                    1,
                ),
            ),
            limitation_codes=("SOURCE_UNAVAILABLE",),
        )
        for code in JUDGMENT_SCORECARD_S0_DIMENSION_CODES
    )


def _run(*, key: str = "scorecard-test") -> JudgmentScorecardRun:
    return JudgmentScorecardRun(
        scorecard_id="scorecard_00000000-0000-7000-8000-000000000002",
        subject_id="case_00000000-0000-7000-8000-000000000003",
        subject_title="NVDA research",
        thesis_id="thesis_00000000-0000-7000-8000-000000000004",
        thesis_title="Growth remains durable",
        thesis_revision_id="rev_00000000-0000-7000-8000-000000000001",
        thesis_revision_no=1,
        generated_at=NOW,
        status=ScorecardStatus.NOT_EVALUATED,
        dimensions=_dimensions(),
        warning_codes=("JUDGMENT_SCORECARD_NOT_EVALUATED",),
        input_fingerprint="0" * 64,
        idempotency_key=key,
        algorithm_version=JUDGMENT_SCORECARD_S0_ALGORITHM_VERSION,
        schema_version=JUDGMENT_SCORECARD_S0_SCHEMA_VERSION,
    )


def _revision() -> SimpleNamespace:
    return SimpleNamespace(
        revision_id="rev_00000000-0000-7000-8000-000000000001",
        thesis_id="thesis_00000000-0000-7000-8000-000000000004",
        subject_id="case_00000000-0000-7000-8000-000000000003",
        revision_no=1,
        statement="Growth remains durable",
        rationale="Evidence and cash generation support the view.",
    )


class _Uow:
    def __init__(
        self,
        *,
        mismatched_plan: bool = False,
        assessments: tuple[object, ...] = (),
        events: tuple[object, ...] = (),
        reports: tuple[object, ...] = (),
    ) -> None:
        subject_id = "case_00000000-0000-7000-8000-000000000003"
        thesis_id = "thesis_00000000-0000-7000-8000-000000000004"
        self.subjects = SimpleNamespace(
            get=lambda _subject_id: SimpleNamespace(subject_id=subject_id, title="NVDA research")
        )
        self.theses = SimpleNamespace(
            get=lambda _thesis_id: SimpleNamespace(
                subject_id=subject_id,
                title="Growth remains durable",
                latest_revision_id="rev_00000000-0000-7000-8000-000000000001",
            )
        )
        self.revisions = SimpleNamespace(get=lambda _revision_id: _revision())
        self.assumptions = SimpleNamespace(list_by_revision=lambda *_args: ())
        self.invalidations = SimpleNamespace(list_by_revision=lambda *_args: ())
        self.evidence_assessments = SimpleNamespace(
            list_for_thesis=lambda *_args, **_kwargs: assessments
        )
        self.evidence = SimpleNamespace(
            get=lambda evidence_id: SimpleNamespace(
                evidence_id=evidence_id,
                observed_at=NOW,
            )
        )
        self.events = SimpleNamespace(
            get=lambda event_id: next(item for item in events if item.event_id == event_id)
        )
        self.reports = SimpleNamespace(
            get=lambda report_id: next(item for item in reports if item.report_id == report_id)
        )
        self.decisions = SimpleNamespace(list_by_subject=lambda *_args, **_kwargs: ())
        self.trade_plans = SimpleNamespace(
            get_current_by_subject=lambda _subject_id: (
                SimpleNamespace(
                    plan_id="trade_plan_00000000-0000-7000-8000-000000000005",
                    version=1,
                    thesis_id=(
                        "thesis_00000000-0000-7000-8000-000000000099"
                        if mismatched_plan
                        else thesis_id
                    ),
                    conditions=(),
                )
                if mismatched_plan
                else None
            ),
        )

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Repository:
    def __init__(self) -> None:
        self.values: list[JudgmentScorecardRun] = []

    def append(self, value: JudgmentScorecardRun) -> JudgmentScorecardRun:
        self.values.append(value)
        return value

    def get_by_idempotency_key(self, key: str) -> JudgmentScorecardRun | None:
        return next((item for item in self.values if item.idempotency_key == key), None)

    def list(self, **_kwargs: object) -> tuple[tuple[JudgmentScorecardRun, ...], int]:
        return tuple(self.values), len(self.values)


class _Monitors:
    def list_current(self) -> tuple[object, ...]:
        return ()


class _Retros:
    def list_runs(self, limit: int) -> tuple[object, ...]:
        return ()


class _Agendas:
    def __init__(self, values: tuple[object, ...] = ()) -> None:
        self.values = values

    def list_visible(self, *, as_of: datetime) -> tuple[object, ...]:
        return self.values


def _service(
    uow: _Uow,
    *,
    agendas: tuple[object, ...] = (),
) -> JudgmentScorecardService:
    return JudgmentScorecardService(
        _Repository(),  # type: ignore[arg-type]
        _Agendas(agendas),  # type: ignore[arg-type]
        lambda: uow,  # type: ignore[arg-type,return-value]
        _Monitors(),  # type: ignore[arg-type]
        _Retros(),  # type: ignore[arg-type]
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )


def test_domain_requires_canonical_eight_dimensions() -> None:
    with pytest.raises(DataContractError, match="exactly 8"):
        replace(
            _run(),
            algorithm_version=JUDGMENT_SCORECARD_S0_ALGORITHM_VERSION,
            schema_version=JUDGMENT_SCORECARD_S0_SCHEMA_VERSION,
            dimensions=_dimensions()[:-1],
        )


def test_legacy_s0_model_remains_readable() -> None:
    value = _run()
    assert value.schema_version == 1
    assert value.algorithm_version == "judgment-scorecard-s0-v1"
    assert len(value.dimensions) == 8


def test_service_locks_exact_revision_and_idempotency() -> None:
    repository = _Repository()
    service = JudgmentScorecardService(
        repository,  # type: ignore[arg-type]
        _Agendas(),  # type: ignore[arg-type]
        lambda: _Uow(),  # type: ignore[arg-type,return-value]
        _Monitors(),  # type: ignore[arg-type]
        _Retros(),  # type: ignore[arg-type]
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )

    first = service.run(
        "case_00000000-0000-7000-8000-000000000003",
        "thesis_00000000-0000-7000-8000-000000000004",
        "scorecard-1",
    )
    second = service.run(
        "case_00000000-0000-7000-8000-000000000003",
        "thesis_00000000-0000-7000-8000-000000000004",
        "scorecard-1",
    )
    assert first.ok and first.data is not None
    assert first.data.thesis_revision_no == 1
    assert second.ok and second.data is not None
    assert second.data.scorecard_id == first.data.scorecard_id
    assert second.warnings[0].code == "DUPLICATE_IDEMPOTENCY_KEY"
    assert len(repository.values) == 1
    conflict = service.run(
        "case_00000000-0000-7000-8000-000000000003",
        "thesis_00000000-0000-7000-8000-000000000099",
        "scorecard-1",
    )
    assert not conflict.ok
    assert conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"


def test_service_does_not_use_plan_from_another_thesis() -> None:
    repository = _Repository()
    service = JudgmentScorecardService(
        repository,  # type: ignore[arg-type]
        _Agendas(),  # type: ignore[arg-type]
        lambda: _Uow(mismatched_plan=True),  # type: ignore[arg-type,return-value]
        _Monitors(),  # type: ignore[arg-type]
        _Retros(),  # type: ignore[arg-type]
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )
    result = service.run(
        "case_00000000-0000-7000-8000-000000000003",
        "thesis_00000000-0000-7000-8000-000000000004",
        "scorecard-plan-mismatch",
    )
    assert result.ok and result.data is not None
    assert all(
        item.result_code == "NO_TRADE_PLAN"
        for item in result.data.dimensions
        if item.code in {
            "PLAN_MONITOR_COVERAGE",
            "PLAN_BEFORE_ACTION_INTENT",
            "TRADE_RETRO_DISCIPLINE",
        }
    )


def test_s1_catalyst_dimension_is_not_evaluated_without_agenda() -> None:
    service = _service(_Uow())
    result = service.run(
        "case_00000000-0000-7000-8000-000000000003",
        "thesis_00000000-0000-7000-8000-000000000004",
        "scorecard-no-agenda",
    )
    assert result.ok and result.data is not None
    assert result.data.schema_version == 1
    assert result.data.algorithm_version == "judgment-scorecard-s1-v1"
    dimension = result.data.dimensions[-1]
    assert dimension.code == "CATALYST_OUTCOME_CALIBRATION"
    assert dimension.status == ScorecardDimensionStatus.NOT_EVALUATED.value
    assert dimension.result_code == "NO_RELEVANT_AGENDA"


def test_s1_catalyst_dimension_uses_exact_revision_assessment() -> None:
    event_id = "event_00000000-0000-7000-8000-000000000006"
    evidence_id = "evidence_00000000-0000-7000-8000-000000000007"
    assessment_id = "rev_00000000-0000-7000-8000-000000000008"
    agenda_id = "agenda_00000000-0000-7000-8000-000000000009"
    event = SimpleNamespace(event_id=event_id, evidence_ids=(evidence_id,))
    assessment = SimpleNamespace(
        assessment_id=assessment_id,
        evidence_id=evidence_id,
        subject_id="case_00000000-0000-7000-8000-000000000003",
        thesis_id="thesis_00000000-0000-7000-8000-000000000004",
        thesis_revision_id="rev_00000000-0000-7000-8000-000000000001",
        stance=EvidenceStance.SUPPORTS,
        assessed_at=NOW,
    )
    agenda = SimpleNamespace(
        agenda_item_id=agenda_id,
        version=2,
        source_visible_at=NOW,
        recorded_at=NOW,
        subject_id="case_00000000-0000-7000-8000-000000000003",
        instrument_id=None,
        status=AgendaItemStatus.OCCURRED,
        linked_event_id=event_id,
        linked_report_id=None,
        window_end=NOW,
    )
    pre_outcome = SimpleNamespace(
        agenda_item_id=agenda_id,
        version=1,
        source_visible_at=NOW,
        recorded_at=NOW,
        subject_id="case_00000000-0000-7000-8000-000000000003",
        instrument_id=None,
        status=AgendaItemStatus.UPCOMING,
        expected_question="Did growth remain durable?",
        window_start=NOW,
        window_end=NOW,
    )
    service = _service(
        _Uow(assessments=(assessment,), events=(event,)),
        agendas=(pre_outcome, agenda),
    )
    result = service.run(
        "case_00000000-0000-7000-8000-000000000003",
        "thesis_00000000-0000-7000-8000-000000000004",
        "scorecard-assessed-outcome",
    )
    assert result.ok and result.data is not None
    dimension = result.data.dimensions[-1]
    assert dimension.status == ScorecardDimensionStatus.EVALUATED.value
    assert dimension.result_code == "SUPPORTS"
    assert ("SUPPORTS_COUNT", "1") in dimension.facts
    assert ("NO_CALIBRATABLE_OUTCOME_COUNT", "0") in dimension.facts
    assert {(ref.kind, ref.entity_id, ref.version) for ref in dimension.source_refs} >= {
        ("AGENDA", agenda_id, 2),
        ("EVENT", event_id, None),
        ("EVIDENCE_ASSESSMENT", assessment_id, None),
    }


def test_repository_roundtrip_and_pagination(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'scorecard.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyJudgmentScorecardRepository(engine)
    value = _run()
    repository.append(value)
    restored, total = repository.list(limit=1, offset=0)
    assert total == 1
    assert restored[0] == value
    assert repository.get_by_idempotency_key(value.idempotency_key) == value
    assert repository.list(limit=1, offset=1) == ((), 1)
