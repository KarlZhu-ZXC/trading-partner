from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from application.services.judgment_calibration_service import JudgmentCalibrationService
from domain.common.enums import DecisionType
from domain.common.errors import DataContractError
from domain.scorecard.enums import ScorecardDimensionStatus
from domain.scorecard.models import ScorecardDimension, ScorecardSourceRef

NOW = datetime(2026, 9, 14, tzinfo=UTC)
BEFORE = NOW - timedelta(days=10)


def decision(**updates):
    return NS(
        **(
            dict(
                decision_id="d1",
                subject_id="s1",
                decided_by="user",
                recorded_at=BEFORE,
                decision_type=DecisionType.NO_ACTION,
                title="Synthetic reviewed wait",
                thesis_revision_ids=("r1",),
                trade_plan_id="p1",
                trade_plan_version=1,
                review_due_at=NOW + timedelta(days=1),
            )
            | updates
        )
    )


def service(decisions=None, runs=(), agenda=(), retro_runs=(), snapshots=None):
    uow = NS(
        subjects=NS(get=lambda _: NS()),
        decisions=NS(list_by_subject=lambda _: (decision(),) if decisions is None else decisions),
        revisions=NS(get=lambda _: NS(subject_id="s1", confirmed_at=BEFORE)),
        trade_plans=NS(get_version=lambda p, v: NS(subject_id="s1", created_at=BEFORE)),
    )
    cards = NS(list=lambda **k: (runs, len(runs)))
    agenda_repo = NS(list_visible=lambda **k: agenda)
    retro = NS(list_runs=lambda **k: retro_runs, get_plan_snapshots=lambda ids: snapshots or {})
    return (
        JudgmentCalibrationService(
            lambda: nullcontext(uow), agenda_repo, cards, retro, NS(now=lambda: NOW)
        ),
        uow,
        cards,
        agenda_repo,
    )


def card(code="ASSUMPTION_OUTCOME", refs=()):
    return ScorecardDimension(
        code=code,
        status=ScorecardDimensionStatus.NOT_EVALUATED,
        result_code="UNKNOWN",
        title="Synthetic observation",
        summary="Not enough evidence.",
        source_refs=refs,
        limitation_codes=("SOURCE_UNAVAILABLE",),
    )


def run(identity="score1", revision="r1", generated_at=NOW, dimensions=None):
    return NS(
        scorecard_id=identity,
        subject_id="s1",
        thesis_revision_id=revision,
        generated_at=generated_at,
        warning_codes=(),
        dimensions=(card(),) if dimensions is None else dimensions,
    )


def test_no_action_future_review_keeps_unknown_and_four_separate_dimensions():
    s, *_ = service()
    result = s.get("s1")
    assert result.baseline.decision_type == "no_action"
    assert result.review_status == "NOT_DUE"
    assert "NO_MATCHING_SCORECARD" in result.warning_codes
    assert len(result.dimensions) == 4
    assert all(d.status == "UNKNOWN" for d in result.dimensions)
    assert result.dimensions[-1].limitation_codes == ("ATTRIBUTION_NOT_AVAILABLE",)
    assert "score" not in result.model_dump()
    assert result.execution_effect is False


def test_latest_user_decision_only_and_explicit_cross_subject_rejected():
    s, *_ = service(
        decisions=(
            decision(),
            decision(decision_id="other", decided_by="external_agent", recorded_at=NOW),
        )
    )
    assert s.get("s1").baseline.decision_id == "d1"
    with pytest.raises(DataContractError):
        s.get("s1", decision_id="other")
    s, *_ = service(decisions=())
    assert s.get("s1").baseline is None
    assert s.get("s1").review_status == "NO_BASELINE"


def test_only_exact_revision_eligible_time_and_latest_run_survive():
    s, *_ = service(
        runs=(
            run("old", generated_at=BEFORE - timedelta(seconds=1)),
            run("future", generated_at=NOW + timedelta(seconds=1)),
            run("current", revision="r2"),
            run("valid"),
        )
    )
    result = s.get("s1")
    assert [c.scorecard_id for c in result.dimensions[0].cards] == ["valid"]
    assert result.dimensions[0].cards[0].dimension.limitation_codes == ("SOURCE_UNAVAILABLE",)
    assert "SCORECARD_REVISION_OR_WINDOW_EXCLUSIONS" in result.warning_codes


def test_plan_cards_cannot_substitute_current_plan_version():
    wrong = card("PLAN_MONITOR_COVERAGE", (ScorecardSourceRef("TRADE_PLAN", "p1", 2),))
    exact = card("PLAN_BEFORE_ACTION_INTENT", (ScorecardSourceRef("TRADE_PLAN", "p1", 1),))
    s, *_ = service(runs=(run(dimensions=(wrong, exact)),))
    result = s.get("s1")
    assert not result.dimensions[1].cards
    assert result.dimensions[2].cards[0].dimension.code == "PLAN_BEFORE_ACTION_INTENT"


def test_invalid_reviewed_version_fails_without_current_fallback():
    s, uow, *_ = service()
    uow.trade_plans.get_version = lambda *a: None
    with pytest.raises(DataContractError, match="Trade Plan"):
        s.get("s1")


def test_source_failure_is_explicit_not_empty_success():
    s, _, cards, _ = service()

    def fail(**kwargs):
        raise RuntimeError("must not expose source payload")

    cards.list = fail
    result = s.get("s1")
    assert result.coverage["SCORECARD"] == "UNAVAILABLE"
    assert result.coverage["AGENDA"] == "COMPLETE"
    assert "must not expose" not in result.model_dump_json()


def test_agenda_outcome_before_review_remains_out_of_window():
    row = NS(
        subject_id="s1",
        recorded_at=NOW,
        agenda_item_id="a1",
        version=2,
        outcome_occurred_at=BEFORE - timedelta(days=1),
        title="Synthetic event",
        outcome_note="Observed before review",
        linked_evidence_id="e1",
        linked_event_id=None,
        linked_report_id=None,
    )
    s, *_ = service(agenda=(row,))
    result = s.get("s1")
    assert result.dimensions[0].observations[0].status == "OUT_OF_WINDOW"
    assert result.dimensions[0].observations[0].source_refs[-1].entity_id == "e1"


def test_retro_requires_exact_decision_and_preperiod_plan_snapshot():
    finding = NS(
        plan_id="p1",
        title="Synthetic finding",
        detail="Check plan discipline",
        severity=NS(value="INFO"),
    )
    retro = NS(
        run_id="retro1",
        generated_at=NOW,
        period_start=BEFORE + timedelta(days=1),
        period_end=NOW,
        plan_snapshot_id="snap1",
        findings=(finding,),
        warning_codes=(),
    )
    entry = NS(
        subject_id="s1",
        plan_id="p1",
        plan_version=1,
        decision_records=(("d1", "NO_ACTION", "", None),),
    )
    snapshot = NS(captured_at=BEFORE, entries=(entry,))
    s, *_ = service(retro_runs=(retro,), snapshots={"snap1": snapshot})
    assert len(s.get("s1").dimensions[2].observations) == 1
    entry.plan_version = 2
    assert not s.get("s1").dimensions[2].observations
    entry.plan_version = 1
    snapshot.captured_at = NOW
    assert not s.get("s1").dimensions[2].observations


def test_bounded_history_is_not_claimed_complete():
    s, _, cards, _ = service(runs=(run(),))
    cards.list = lambda **kwargs: ((run(),), 101)
    result = s.get("s1")
    assert result.coverage["SCORECARD"] == "PARTIAL"
    assert "SCORECARD_HISTORY_BOUNDED" in result.warning_codes


def test_conditions_reuse_exact_plan_projection_and_exclude_other_versions():
    svc, *_ = service()

    def row(**changes):
        return NS(
            **(
                dict(
                    kind="MONITOR",
                    relation="EXACT_PLAN",
                    plan_id="p1",
                    plan_version=1,
                    occurred_at=NOW,
                    recorded_at=NOW,
                    source_id="run1",
                    source_version="2",
                    monitor_id="m1",
                    title="Price condition",
                    old_value="not triggered",
                    new_value="triggered",
                    warning_codes=[],
                )
                | changes
            )
        )

    svc._changes = NS(
        get=lambda *a, **k: NS(
            items=[row(), row(plan_version=2), row(occurred_at=BEFORE - timedelta(days=1))],
            has_more=False,
            coverage={"MONITOR": "COMPLETE"},
            warning_codes=[],
        )
    )
    result = svc.get("s1")
    condition = next(d for d in result.dimensions if d.code == "PLAN_CONDITIONS")
    assert len(condition.observations) == 1
    assert "triggered" in condition.observations[0].summary
    assert result.coverage["CONDITIONS"] == "COMPLETE"
