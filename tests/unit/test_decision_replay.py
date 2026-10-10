"""Focused historical-vintage tests for DecisionReplayService."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from application.services.decision_replay_service import DecisionReplayService
from domain.common.enums import (
    AssumptionStatus,
    ConfidenceBand,
    ConfirmationMode,
    DecisionType,
    InvestmentRating,
    OpenQuestionStatus,
)
from domain.common.errors import DataContractError
from domain.research.evidence_impact import (
    EvidenceImpact,
    ResearchSourceRef,
    ResearchSourceSnapshot,
)
from domain.research.models import Assumption, DecisionRecord, OpenQuestion, ThesisRevision

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
CUTOFF = datetime(2026, 9, 10, 12, tzinfo=UTC)
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000001"
OTHER_SUBJECT_ID = "case_00000000-0000-7000-8000-000000000002"
THESIS_ID = "thesis_00000000-0000-7000-8000-000000000001"
REVISION_ID = "rev_00000000-0000-7000-8000-000000000001"
LATE_REVISION_ID = "rev_00000000-0000-7000-8000-000000000002"
ASSUMPTION_ID = "assumption_00000000-0000-7000-8000-000000000001"
EVIDENCE_ID = "evidence_00000000-0000-7000-8000-000000000001"
EXTRA_EVIDENCE_ID = "evidence_00000000-0000-7000-8000-000000000002"
LATE_EVIDENCE_ID = "evidence_00000000-0000-7000-8000-000000000003"
NOTE_REVISION_ID = "external_note_revision_00000000-0000-7000-8000-000000000001"
MONITOR_RUN_ID = "monitor_run_00000000-0000-7000-8000-000000000001"
MONITOR_ID = "monitor_00000000-0000-7000-8000-000000000001"
IMPACT_ID = "impact_00000000-0000-7000-8000-000000000001"
CANDIDATE_ID = "run_00000000-0000-7000-8000-000000000001"
DECISION_ID = "decision_00000000-0000-7000-8000-000000000001"


class _Clock:
    def now(self) -> datetime:
        return NOW


def _decision(**updates: object) -> DecisionRecord:
    values: dict[str, object] = {
        "decision_id": DECISION_ID,
        "subject_id": SUBJECT_ID,
        "decision_type": DecisionType.NO_ACTION,
        "title": "Backdated review",
        "rationale": "Synthetic reviewed rationale",
        "decided_at": CUTOFF,
        "recorded_at": CUTOFF + timedelta(days=2),
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "primary_instrument_id": None,
        "thesis_revision_ids": (REVISION_ID,),
        "evidence_ids": (EVIDENCE_ID,),
        "report_ids": (),
        "supersedes_decision_id": None,
        "position_context_snapshot_id": None,
        "schema_version": 1,
        "external_note_revision_id": None,
    }
    values.update(updates)
    return DecisionRecord(**values)  # type: ignore[arg-type]


def _revision(
    revision_id: str = REVISION_ID,
    *,
    subject_id: str = SUBJECT_ID,
    confirmed_at: datetime = CUTOFF - timedelta(days=1),
) -> ThesisRevision:
    return ThesisRevision(
        revision_id=revision_id,
        thesis_id=THESIS_ID,
        subject_id=subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Demand remains durable",
        rationale="Long-term demand is intact",
        confidence_band=ConfidenceBand.MEDIUM,
        rating=InvestmentRating.BUY,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=confirmed_at - timedelta(hours=1),
        confirmed_at=confirmed_at,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="Watch demand",
        schema_version=1,
    )


def _assumption(*, status: AssumptionStatus = AssumptionStatus.ACCEPTED) -> Assumption:
    retired_at = CUTOFF + timedelta(days=5) if status is AssumptionStatus.RETIRED else None
    return Assumption(
        assumption_id=ASSUMPTION_ID,
        thesis_id=THESIS_ID,
        subject_id=SUBJECT_ID,
        revision_no=1,
        statement="Capacity remains constrained",
        basis="Supplier commentary",
        falsifiability="Capacity expands materially",
        status=status,
        proposed_at=CUTOFF - timedelta(days=2),
        confirmed_at=CUTOFF - timedelta(days=1),
        proposed_by="codex",
        confirmed_by="user",
        retired_at=retired_at,
        retired_reason="Later evidence changed the view" if retired_at else None,
    )


def _source(
    ref: ResearchSourceRef,
    *,
    recorded_at: datetime,
    fact_at: datetime | None = None,
) -> ResearchSourceSnapshot:
    return ResearchSourceSnapshot(
        source=ref,
        title=f"Source {ref.source_id}",
        fact_at=fact_at or recorded_at,
        recorded_at=recorded_at,
        origin="SYNTHETIC",
    )


def _impact(version: int, *, state: str, confirmed_at: datetime) -> EvidenceImpact:
    return EvidenceImpact(
        impact_id=IMPACT_ID,
        version=version,
        subject_id=SUBJECT_ID,
        thesis_id=THESIS_ID,
        thesis_revision_id=REVISION_ID,
        assumption_id=ASSUMPTION_ID,
        source=ResearchSourceRef("EVIDENCE", EVIDENCE_ID),
        stance="supports",
        rationale="Source supports the assumption",
        state=state,
        confirmed_at=confirmed_at,
        confirmed_by="user",
        candidate_id=CANDIDATE_ID,
    )


def _service(
    *,
    decision: DecisionRecord | None = None,
    revisions: tuple[ThesisRevision, ...] = (),
    assumptions: tuple[Assumption, ...] = (),
    impacts: tuple[EvidenceImpact, ...] = (),
    sources: tuple[ResearchSourceSnapshot, ...] = (),
    questions: tuple[OpenQuestion, ...] = (),
) -> DecisionReplayService:
    selected_decision = decision or _decision()
    uow = NS(
        subjects=NS(get=lambda _: NS(subject_id=SUBJECT_ID)),
        decisions=NS(get=lambda _: selected_decision),
        revisions=NS(
            get=lambda revision_id: next(
                revision for revision in revisions if revision.revision_id == revision_id
            )
        ),
        assumptions=NS(list_by_revision=lambda *_: assumptions),
        evidence_impacts=NS(list_by_subject=lambda *_args, **_kwargs: impacts),
        research_sources=NS(
            list_for_subject=lambda *_args, **_kwargs: sources,
            get=lambda _subject_id, ref, **_kwargs: next(
                (source for source in sources if source.source == ref), None
            ),
        ),
        questions=NS(list_by_subject=lambda _: questions),
    )
    return DecisionReplayService(
        lambda: nullcontext(uow),  # type: ignore[arg-type,return-value]
        _Clock(),
    )


def test_replay_uses_decided_cutoff_and_restores_pre_retraction_impact() -> None:
    active = _impact(1, state="ACTIVE", confirmed_at=CUTOFF - timedelta(days=2))
    later_retraction = _impact(
        2,
        state="RETRACTED",
        confirmed_at=CUTOFF + timedelta(days=3),
    )
    late_revision = _revision(
        LATE_REVISION_ID,
        confirmed_at=CUTOFF + timedelta(hours=1),
    )
    service = _service(
        decision=_decision(
            thesis_revision_ids=(REVISION_ID, LATE_REVISION_ID),
            evidence_ids=(EVIDENCE_ID, LATE_EVIDENCE_ID),
        ),
        revisions=(_revision(), late_revision),
        assumptions=(_assumption(status=AssumptionStatus.RETIRED),),
        impacts=(later_retraction, active),
        sources=(
            _source(ResearchSourceRef("EVIDENCE", EVIDENCE_ID), recorded_at=CUTOFF),
            _source(
                ResearchSourceRef("EVIDENCE", LATE_EVIDENCE_ID),
                recorded_at=CUTOFF + timedelta(days=1),
            ),
        ),
        questions=(
            OpenQuestion(
                question_id="question_00000000-0000-7000-8000-000000000001",
                subject_id=SUBJECT_ID,
                text="Did demand hold?",
                status=OpenQuestionStatus.ANSWERED,
                asked_at=CUTOFF - timedelta(days=1),
                answered_at=CUTOFF + timedelta(days=4),
                answer_summary="Later answer",
                closed_without_answer_reason=None,
                proposed_by="codex",
            ),
        ),
    )

    replay = service.get(SUBJECT_ID, DECISION_ID)

    assert replay.cutoff == CUTOFF
    assert [item.revision_id for item in replay.revisions] == [REVISION_ID]
    assumption = replay.revisions[0].assumptions[0]
    assert assumption.status is None
    assert assumption.retired_at is None
    assert assumption.retired_reason is None
    assert [(item.impact_id, item.version) for item in replay.impacts] == [(IMPACT_ID, 1)]
    assert replay.questions[0].text == "Did demand hold?"
    assert not hasattr(replay.questions[0], "answer_summary")
    assert replay.coverage["questions"] == "PARTIAL"
    assert "QUESTION_STATE_HISTORY_UNAVAILABLE" in replay.warnings
    assert any(
        item.reference_id == LATE_REVISION_ID and item.reason == "REVISION_AFTER_CUTOFF"
        for item in replay.excluded_references
    )
    assert any(
        item.reference_id == LATE_EVIDENCE_ID
        and item.reason == "SOURCE_NOT_AVAILABLE_AT_CUTOFF"
        for item in replay.excluded_references
    )


def test_replay_keeps_all_scoped_evidence_but_exact_observation_only() -> None:
    observation = ResearchSourceRef("OBSERVATION", NOTE_REVISION_ID)
    unrelated_observation = ResearchSourceRef(
        "OBSERVATION",
        "external_note_revision_00000000-0000-7000-8000-000000000002",
    )
    service = _service(
        sources=(
            _source(ResearchSourceRef("EVIDENCE", EVIDENCE_ID), recorded_at=CUTOFF),
            _source(ResearchSourceRef("EVIDENCE", EXTRA_EVIDENCE_ID), recorded_at=CUTOFF),
            _source(observation, recorded_at=CUTOFF),
            _source(unrelated_observation, recorded_at=CUTOFF),
        ),
        decision=_decision(external_note_revision_id=NOTE_REVISION_ID),
    )

    replay = service.get(SUBJECT_ID, DECISION_ID)
    refs = {(item.source.kind, item.source.source_id) for item in replay.sources}

    assert ("EVIDENCE", EVIDENCE_ID) in refs
    assert ("EVIDENCE", EXTRA_EVIDENCE_ID) in refs
    assert ("OBSERVATION", NOTE_REVISION_ID) in refs
    assert ("OBSERVATION", unrelated_observation.source_id) not in refs
    assert replay.coverage["sources"] == "PARTIAL"
    assert "OBSERVATION_SCOPE_UNAVAILABLE" in replay.warnings


def test_replay_rejects_cross_scope_and_future_decisions() -> None:
    service = _service(decision=_decision(subject_id=OTHER_SUBJECT_ID))
    with pytest.raises(DataContractError, match="does not belong"):
        service.get(SUBJECT_ID, DECISION_ID)

    future = _decision(
        decided_at=NOW + timedelta(minutes=1),
        recorded_at=NOW + timedelta(minutes=1),
    )
    service = _service(decision=future)
    with pytest.raises(DataContractError, match="future"):
        service.get(SUBJECT_ID, DECISION_ID)
