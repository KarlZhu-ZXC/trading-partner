from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from application.services.retro_followup_progress import followup_progress
from domain.common.enums import OpenQuestionStatus
from domain.retro.enums import TradeRetroFindingReviewStatus

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def setup_fixture():
    repo, uow = MagicMock(), MagicMock()
    uow.__enter__.return_value = uow
    choice = NS(
        finding_key="finding",
        followup="QUESTION",
        question_id="question",
        question_version=2,
        note=None,
        status=TradeRetroFindingReviewStatus.ACCEPTED,
    )
    review = NS(version=1, created_at=NOW, finding_reviews=(choice,))
    repo.list_reviews.return_value = (review,)
    repo.list_runs.return_value = ()
    question = NS(
        subject_id="subject",
        version=2,
        asked_at=NOW - timedelta(days=7),
        due_at=NOW - timedelta(days=1),
        status=OpenQuestionStatus.ANSWERED,
        answered_at=NOW - timedelta(hours=1),
        answer_summary="Synthetic answer",
        source_refs=(NS(kind="EVIDENCE", source_id="evidence", monitor_id=None, rule_code=None),),
    )
    uow.questions.get.return_value = question
    uow.research_sources.get.return_value = NS()
    run = NS(run_id="run", period_end=NOW - timedelta(days=6), findings=())
    return repo, uow, run, choice, question, review


def test_answer_with_sources_requires_separate_human_resolution():
    repo, uow, run, choice, question, _ = setup_fixture()
    data = followup_progress(run, repo, lambda: uow, NOW)
    assert data["due_count"] == 1 and data["resolved_due_count"] == 0
    assert data["rows"][0]["state"] == "READY_FOR_REVIEW"
    choice.status = TradeRetroFindingReviewStatus.RESOLVED
    data = followup_progress(run, repo, lambda: uow, NOW)
    assert data["resolved_due_count"] == 1
    uow.research_sources.get.assert_called_with(
        "subject", question.source_refs[0], as_of=question.answered_at
    )
    uow.questions.answer.assert_not_called()
    repo.append_review.assert_not_called()
    uow.commit.assert_not_called()


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"version": 3}, "QUESTION_CHANGED"),
        ({"source_refs": ()}, "EVIDENCE_MISSING"),
        ({"status": OpenQuestionStatus.CLOSED_WITHOUT_ANSWER}, "CLOSED_QUESTION_NEEDS_REVIEW"),
        ({"status": OpenQuestionStatus.OPEN, "due_at": None}, "NO_DEADLINE"),
        ({"status": OpenQuestionStatus.OPEN, "due_at": NOW + timedelta(days=1)}, "NOT_DUE"),
    ],
)
def test_changed_or_closed_questions_are_not_automatically_completed(change, expected):
    repo, uow, run, choice, question, _ = setup_fixture()
    choice.status = TradeRetroFindingReviewStatus.RESOLVED
    for key, value in change.items():
        setattr(question, key, value)
    data = followup_progress(run, repo, lambda: uow, NOW)
    assert data["rows"][0]["state"] == expected
    assert not data["rows"][0]["closed"]
    if expected == "QUESTION_CHANGED":
        assert data["due_count"] is None
    if expected in {"NO_DEADLINE", "NOT_DUE"}:
        assert data["due_count"] == 0


def test_missing_sources_history_and_changed_deadlines_never_become_zero():
    repo, uow, run, choice, _, review = setup_fixture()
    uow.research_sources.get.side_effect = RuntimeError("private")
    data = followup_progress(run, repo, lambda: uow, NOW)
    assert data["due_count"] is None and data["rows"][0]["state"] == "SOURCE_UNAVAILABLE"
    assert "private" not in str(data)
    uow.research_sources.get.side_effect = None
    repo.list_reviews.return_value = (review,) * 101
    assert followup_progress(run, repo, lambda: uow, NOW)["selected_count"] is None
    choice.followup = "NO_FOLLOWUP"
    choice.note = "Already addressed elsewhere"
    original = NS(
        version=0,
        created_at=NOW - timedelta(days=2),
        finding_reviews=(NS(finding_key="finding", followup="QUESTION"),),
    )
    repo.list_reviews.return_value = (review, original)
    data = followup_progress(run, repo, lambda: uow, NOW)
    assert data["rows"][0]["state"] == "NO_FOLLOWUP_RECORDED"
    assert data["due_count"] is None  # an old mutable deadline cannot be reconstructed
    repo.list_reviews.side_effect = RuntimeError("private")
    assert followup_progress(run, repo, lambda: uow, NOW)["coverage"] == "UNAVAILABLE"
