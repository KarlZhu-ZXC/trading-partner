"""Focused projection tests for reviewed Evidence Impact links."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from typing import cast

from application.dto.research_changes import (
    BaselineThesisDTO,
    ResearchChangeDTO,
    ResearchChangesBaselineDTO,
)
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.research_changes_service import (
    ResearchChangesService,
    _attach_reviewed_impacts,
    _latest_active_impacts,
)
from domain.external_note.enums import NoteCoverage
from domain.research.evidence_impact import EvidenceImpact, ResearchSourceRef
from domain.research.models import DecisionRecord

NOW = datetime(2026, 9, 14, tzinfo=UTC)
BEFORE = NOW - timedelta(days=2)
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000001"
THESIS_ID = "thesis_00000000-0000-7000-8000-000000000001"
CURRENT_REVISION_ID = "rev_00000000-0000-7000-8000-000000000001"
HISTORICAL_REVISION_ID = "rev_00000000-0000-7000-8000-000000000002"
ASSUMPTION_ID = "rev_00000000-0000-7000-8000-000000000011"
LATER_ASSUMPTION_ID = "rev_00000000-0000-7000-8000-000000000012"
RUN_ID = "run_00000000-0000-7000-8000-000000000001"
OBSERVATION_1 = "external_note_revision_00000000-0000-7000-8000-000000000001"
OBSERVATION_2 = "external_note_revision_00000000-0000-7000-8000-000000000002"
MONITOR_RUN = "monitor_run_00000000-0000-7000-8000-000000000001"
MONITOR_ID = "monitor_00000000-0000-7000-8000-000000000001"


def _impact(
    impact_id: str,
    source: ResearchSourceRef,
    *,
    version: int = 1,
    state: str = "ACTIVE",
    thesis_revision_id: str = CURRENT_REVISION_ID,
    assumption_id: str = ASSUMPTION_ID,
    confirmed_at: datetime = BEFORE,
) -> EvidenceImpact:
    return EvidenceImpact(
        impact_id=impact_id,
        version=version,
        subject_id=SUBJECT_ID,
        thesis_id=THESIS_ID,
        thesis_revision_id=thesis_revision_id,
        assumption_id=assumption_id,
        source=source,
        stance="supports",
        rationale="Reviewed source impact.",
        state=state,
        confirmed_at=confirmed_at,
        confirmed_by="user",
        candidate_id=RUN_ID,
    )


def _change(
    change_id: str,
    *,
    kind: str = "OBSERVATION",
    note_revision_id: str | None = None,
    monitor_id: str | None = None,
    source_id: str = "note-1",
) -> ResearchChangeDTO:
    return ResearchChangeDTO(
        change_id=change_id,
        kind=kind,  # type: ignore[arg-type]
        title="Synthetic change",
        occurred_at=BEFORE,
        recorded_at=BEFORE,
        instrument_id="equity:US:TEST",
        source_id=source_id,
        source_version="1",
        old_value=None,
        new_value=None,
        relation="SUBJECT_SCOPE",
        relation_detail="Explicit scope",
        note_revision_id=note_revision_id,
        monitor_id=monitor_id,
    )


def test_exact_source_matching_keeps_historical_revision_links_explicit() -> None:
    observation_one = _impact(
        "impact_00000000-0000-7000-8000-000000000001",
        ResearchSourceRef("OBSERVATION", OBSERVATION_1),
    )
    observation_two = _impact(
        "impact_00000000-0000-7000-8000-000000000002",
        ResearchSourceRef("OBSERVATION", OBSERVATION_2),
        thesis_revision_id=HISTORICAL_REVISION_ID,
    )
    baseline = ResearchChangesBaselineDTO(
        decision_id="decision_00000000-0000-7000-8000-000000000001",
        title="Reviewed",
        recorded_at=BEFORE,
        decided_at=BEFORE,
        theses=[
            BaselineThesisDTO(
                thesis_id=THESIS_ID,
                revision_id=CURRENT_REVISION_ID,
                statement="Current Thesis",
                assumptions=[
                    {"assumption_id": ASSUMPTION_ID, "statement": "Baseline premise"}
                ],
            )
        ],
        plan=None,
    )

    rows = _attach_reviewed_impacts(
        [
            _change("observation:one", note_revision_id=OBSERVATION_1),
            _change("observation:two", note_revision_id=OBSERVATION_2),
        ],
        (observation_one, observation_two),
        baseline,
    )

    assert [impact.impact_id for impact in rows[0].reviewed_impacts] == [
        observation_one.impact_id
    ]
    assert rows[0].reviewed_impacts[0].baseline_match is True
    assert [impact.impact_id for impact in rows[1].reviewed_impacts] == [
        observation_two.impact_id
    ]
    assert rows[1].reviewed_impacts[0].baseline_match is False


def test_retracted_and_future_latest_versions_are_excluded() -> None:
    impact_id = "impact_00000000-0000-7000-8000-000000000003"
    future_id = "impact_00000000-0000-7000-8000-000000000004"
    rows = _latest_active_impacts(
        (
            _impact(impact_id, ResearchSourceRef("OBSERVATION", OBSERVATION_1)),
            _impact(
                impact_id,
                ResearchSourceRef("OBSERVATION", OBSERVATION_1),
                version=2,
                state="RETRACTED",
            ),
            _impact(
                future_id,
                ResearchSourceRef("OBSERVATION", OBSERVATION_2),
                confirmed_at=NOW + timedelta(minutes=1),
            ),
        ),
        subject_id=SUBJECT_ID,
        now=NOW,
    )

    assert rows == ()


def test_monitor_matching_requires_monitor_and_run_rule_source_identity() -> None:
    impact = _impact(
        "impact_00000000-0000-7000-8000-000000000005",
        ResearchSourceRef("MONITOR", MONITOR_RUN, MONITOR_ID, "rule"),
    )
    rows = _attach_reviewed_impacts(
        [
            _change(
                "monitor:exact",
                kind="MONITOR",
                monitor_id=MONITOR_ID,
                source_id=f"{MONITOR_RUN}:rule",
            ),
            _change(
                "monitor:wrong-rule",
                kind="MONITOR",
                monitor_id=MONITOR_ID,
                source_id=f"{MONITOR_RUN}:other",
            ),
        ],
        (impact,),
        None,
    )

    assert len(rows[0].reviewed_impacts) == 1
    assert rows[1].reviewed_impacts == []


def test_later_assumption_same_revision_is_not_in_old_baseline_but_link_stays_visible() -> None:
    from application.services.research_changes_service import ResearchChangesService

    decision = cast(
        DecisionRecord,
        NS(
        subject_id=SUBJECT_ID,
        decision_id="decision_00000000-0000-7000-8000-000000000001",
        title="Reviewed",
        rationale="Original reviewed rationale",
        recorded_at=BEFORE,
        decided_at=BEFORE,
        external_note_revision_id=None,
        thesis_revision_ids=(CURRENT_REVISION_ID,),
            trade_plan_id=None,
            trade_plan_version=None,
        ),
    )
    uow = NS(
        revisions=NS(
            get=lambda _revision_id: NS(
                subject_id=SUBJECT_ID,
                thesis_id=THESIS_ID,
                revision_no=1,
                revision_id=CURRENT_REVISION_ID,
                statement="Current Thesis",
                confirmed_at=BEFORE,
            )
        ),
        assumptions=NS(
            list_by_revision=lambda *args: (
                NS(
                    subject_id=SUBJECT_ID,
                    thesis_id=THESIS_ID,
                    revision_no=1,
                    assumption_id=ASSUMPTION_ID,
                    statement="Baseline premise",
                    confirmed_at=BEFORE,
                ),
                NS(
                    subject_id=SUBJECT_ID,
                    thesis_id=THESIS_ID,
                    revision_no=1,
                    assumption_id=LATER_ASSUMPTION_ID,
                    statement="Later premise",
                    confirmed_at=NOW,
                ),
            )
        ),
        invalidations=NS(list_by_revision=lambda *args: ()),
        trade_plans=NS(get_version=lambda *args: None),
    )
    baseline = ResearchChangesService._baseline(uow, decision)
    rows = _attach_reviewed_impacts(
        [_change("observation:one", note_revision_id=OBSERVATION_1)],
        (
            _impact(
                "impact_00000000-0000-7000-8000-000000000006",
                ResearchSourceRef("OBSERVATION", OBSERVATION_1),
                assumption_id=ASSUMPTION_ID,
            ),
            _impact(
                "impact_00000000-0000-7000-8000-000000000007",
                ResearchSourceRef("OBSERVATION", OBSERVATION_1),
                assumption_id=LATER_ASSUMPTION_ID,
            ),
        ),
        baseline,
    )

    assert [item["assumption_id"] for item in baseline.theses[0].assumptions] == [
        ASSUMPTION_ID
    ]
    assert [impact.impact_id for impact in rows[0].reviewed_impacts] == [
        "impact_00000000-0000-7000-8000-000000000006",
        "impact_00000000-0000-7000-8000-000000000007",
    ]
    assert [impact.baseline_match for impact in rows[0].reviewed_impacts] == [True, False]


def test_impact_repository_failure_preserves_source_changes() -> None:
    revision = NS(
        note_revision_id=OBSERVATION_1,
        note_id="note-1",
        version=1,
        title="Synthetic note",
        coverage=NoteCoverage.SUMMARY_ONLY,
        source_timestamp=None,
        observed_at=NOW - timedelta(hours=1),
        blocks=(),
    )
    notes = NS(
        get=lambda _note_id: NS(
            primary_instrument_id="equity:US:TEST",
            source="LOCAL",
        ),
        list_revisions_for_instrument=lambda *args, **kwargs: (revision,),
        previous_revision=lambda *args, **kwargs: None,
    )
    uow = NS(
        subjects=NS(get=lambda _subject_id: NS(primary_instrument_id="equity:US:TEST")),
        decisions=NS(list_by_subject=lambda _subject_id: ()),
        evidence_impacts=NS(
            list_by_subject=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("private detail")
            )
        ),
    )
    uow_factory = cast(Callable[[], ResearchUnitOfWork], lambda: nullcontext(uow))
    service = ResearchChangesService(
        notes,
        uow_factory,
        NS(list_current=lambda: ()),
        NS(list_visible=lambda **kwargs: ()),
        NS(now=lambda: NOW),
    )

    result = service.get(SUBJECT_ID)

    assert result.coverage["IMPACT_LINKS"] == "UNAVAILABLE"
    assert result.total == 1
    assert result.items[0].note_revision_id == OBSERVATION_1
    assert "private detail" not in result.model_dump_json()
