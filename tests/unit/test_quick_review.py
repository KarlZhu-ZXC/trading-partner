from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from application.dto.quick_review import QuickReviewInput
from application.dto.research_changes import ResearchChangesBaselineDTO, ResearchChangesDTO
from application.services.quick_review_service import QuickReviewService
from domain.common.enums import DecisionType
from domain.common.errors import DataContractError, IdempotencyConflict

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def setup():
    baseline = NS(
        decision_id="d1",
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        strategy_code=None,
        strategy_version=None,
        scenario=None,
        trade_plan_id=None,
        trade_plan_version=None,
    )
    stored = {}
    uow = NS(
        subjects=NS(get=lambda _: NS(title="Synthetic", primary_instrument_id="equity:US:SYNTH")),
        theses=NS(list_by_subject=lambda _: ()),
        trade_plans=NS(get_current_by_subject=lambda _: None),
        decisions=NS(get_by_idempotency_key=lambda k: stored.get(k), get=lambda _: baseline),
    )
    projection = ResearchChangesDTO(
        subject_id="s1",
        as_of=NOW,
        baseline=ResearchChangesBaselineDTO(
            decision_id="d1",
            title="Prior view",
            recorded_at=NOW,
            decided_at=NOW,
            theses=[],
            plan=None,
        ),
        coverage={"MONITOR": "COMPLETE"},
        warning_codes=[],
        total=0,
        offset=0,
        limit=100,
        has_more=False,
        items=[],
    )
    changes = NS(get_for_review=lambda *a, **k: projection)
    reader = NS(get=lambda _: [])
    clock = NS(now=lambda: NOW)

    def append(**values):
        result = NS(**values, decision_id="d2")
        stored[values["idempotency_key"]] = result
        return NS(ok=True, data=result)

    writer = NS(append=Mock(side_effect=append))
    service = QuickReviewService(
        lambda: nullcontext(uow),
        changes,
        reader,
        NS(latest_accounts=lambda: ()),
        NS(list_latest=lambda **k: (), latest_for_revision=lambda _: None),
        writer,
        clock,
        NS(redact_text=lambda t: t),
    )
    return service, projection, writer, reader, clock, uow


def request(card, **changes):
    return QuickReviewInput(
        **(
            dict(
                review_token=card["review_token"],
                baseline_decision_id="d1",
                action="maintain",
                rationale="Keep the reviewed view",
                review_due_at=None,
                idempotency_key="once",
                confirmed=True,
            )
            | changes
        )
    )


def test_read_only_then_exact_no_action_and_restart_retry():
    svc, projection, writer, reader, clock, uow = setup()
    card = svc.get("s1")
    writer.append.assert_not_called()
    req = request(card)
    first = svc.submit("s1", req)
    sent = writer.append.call_args.kwargs
    assert sent["decision_type"] == DecisionType.NO_ACTION
    assert sent["supersedes_decision_id"] == "d1" and sent["thesis_revision_ids"] == ()
    assert sent["decided_by"] == "user"
    svc._cards.clear()
    assert svc.submit("s1", req) == first
    assert writer.append.call_count == 1
    with pytest.raises(IdempotencyConflict):
        svc.submit("s1", req.model_copy(update={"rationale": "Changed"}))


def test_note_change_or_formal_revision_change_invalidates_card():
    svc, _, writer, reader, _, uow = setup()
    card = svc.get("s1")
    reader.get = lambda _: [{"note_revision_id": "new"}]
    with pytest.raises(DataContractError):
        svc.submit("s1", request(card))
    writer.append.assert_not_called()
    reader.get = lambda _: []
    card = svc.get("s1")
    uow.revisions = NS(
        get=lambda _: NS(
            revision_id="r2",
            revision_no=2,
            title="Updated",
            statement="Changed",
            rationale="Changed",
        )
    )
    uow.theses.list_by_subject = lambda _: (
        NS(
            thesis_id="t1",
            latest_revision_id="r2",
            status="active",
            role="primary",
            title="Changed",
        ),
    )
    with pytest.raises(DataContractError):
        svc.submit("s1", request(card))
    writer.append.assert_not_called()


def test_defer_requires_gap_and_future_date_and_preserves_baseline():
    svc, _, writer, *_ = setup()
    card = svc.get("s1")
    with pytest.raises(DataContractError):
        svc.submit("s1", request(card, action="defer"))
    with pytest.raises(DataContractError):
        svc.submit("s1", request(card, action="defer", review_due_at=NOW))
    result = svc.submit("s1", request(card, action="defer", review_due_at=NOW + timedelta(days=3)))
    assert result["review_due_at"] == (NOW + timedelta(days=3)).isoformat()
    assert writer.append.call_args.kwargs["decision_type"] == DecisionType.RESEARCH_MORE
    assert writer.append.call_args.kwargs["supersedes_decision_id"] == "d1"


def test_expired_wrong_subject_or_wrong_baseline_cannot_write():
    svc, _, writer, _, clock, _ = setup()
    card = svc.get("s1")
    with pytest.raises(DataContractError):
        svc.submit("s2", request(card))
    with pytest.raises(DataContractError):
        svc.submit("s1", request(card, baseline_decision_id="wrong"))
    clock.now = lambda: NOW + timedelta(hours=1)
    with pytest.raises(DataContractError):
        svc.submit("s1", request(card))
    writer.append.assert_not_called()


def test_closed_explicit_request_cannot_supply_facts_or_skip_confirmation():
    with pytest.raises(ValidationError):
        request({"review_token": "token"}, confirmed=False)
    with pytest.raises(ValidationError):
        request({"review_token": "token"}, rationale=" ")
    with pytest.raises(ValidationError):
        request({"review_token": "token"}, thesis_revision_ids=["r9"])
    with pytest.raises(ValidationError):
        request({"review_token": "token"}, review_due_at="2026-12-01T00:00:00")


def test_submission_status_is_read_only_and_recovers_exact_scope():
    svc, _, writer, *_ = setup()
    card = svc.get("s1")
    req = request(card)
    assert svc.submission_status("s1", "once") == {"status": "NOT_FOUND"}
    writer.append.assert_not_called()
    saved = svc.submit("s1", req)
    svc._cards.clear()
    assert svc.submission_status("s1", "once") == saved
    assert svc.submission_status("s2", "once") == {"status": "NOT_FOUND"}
    assert writer.append.call_count == 1
