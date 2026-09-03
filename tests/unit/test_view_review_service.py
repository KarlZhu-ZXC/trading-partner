from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import application.services.view_review_service as view_review_module
from application.services.view_review_service import ViewReviewService
from domain.common.errors import DataContractError
from domain.external_note.enums import ExternalNoteReviewStatus, NoteCoverage
from domain.external_note.models import (
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteReview,
    ExternalNoteRevision,
)

NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)
REVISION_ID = "external_note_revision_view"
NOTE_ID = "external_note_view"
REVIEW_ID = "external_note_review_view"
SUBJECT_ID = "case_view"


def _payload() -> dict[str, object]:
    return {
        "change_relation": "REVISION",
        "material_change_summary": "The user withdrew the add plan.",
        "viewpoints": [
            {
                "speaker_kind": "USER",
                "speaker_label": "USER",
                "summary": "Wait for guidance.",
                "direction": "SIDEWAYS",
            },
            {
                "speaker_kind": "NAMED_PERSON",
                "speaker_label": "姜汁汽水",
                "summary": "Buy the dip.",
                "direction": "UP",
            },
        ],
        "user_scenarios": [
            {
                "scenario": scenario,
                "action": "NO_ACTION" if scenario != "INVALIDATION" else "EXIT",
                "condition": f"{scenario} condition",
                "confirmation": f"{scenario} confirmation",
                "loss_boundary": "145",
            }
            for scenario in ("UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION")
        ],
        "contradictions": ["Old add plan conflicts with the withdrawal."],
        "missing_evidence": ["Updated guidance"],
        "suggested_next_step": "PROPOSE_PLAN",
    }


class _Notes:
    def __init__(self, payload: dict[str, object]) -> None:
        self.identity = ExternalNoteIdentity(
            note_id=NOTE_ID,
            source="MOOMOO_NOTE",
            external_id="view-test",
            title="AMD",
            primary_instrument_id="equity:US:AMD",
            created_at=NOW,
            last_seen_at=NOW,
        )
        self.revision = ExternalNoteRevision(
            note_revision_id=REVISION_ID,
            note_id=NOTE_ID,
            version=2,
            content_sha256="a" * 64,
            source_revision_key="view-review-source",
            title="AMD",
            summary="Withdraw add plan",
            full_body="Wait for guidance.",
            coverage=NoteCoverage.FULL,
            source_timestamp=NOW,
            observed_at=NOW,
            visibility="SELF",
            related_provider_stock_ids=(),
            related_provider_codes=("US.AMD",),
            blocks=(),
        )
        self.interpretation = ExternalNoteInterpretation(
            interpretation_id="external_note_interpretation_view",
            note_revision_id=REVISION_ID,
            status="SUCCEEDED",
            provider="test",
            model="test",
            reasoning_effort="max",
            schema_version="test-v1",
            payload_json=json.dumps(payload),
            error_code=None,
            created_at=NOW,
        )

    def get(self, note_id: str):
        return self.identity if note_id == NOTE_ID else None

    def revision_by_id(self, revision_id: str):
        return self.revision if revision_id == REVISION_ID else None

    def interpretation_for_revision(self, revision_id: str):
        return self.interpretation if revision_id == REVISION_ID else None


class _Reviews:
    def __init__(self, *, subject_id: str | None) -> None:
        self.value = ExternalNoteReview(
            review_id=REVIEW_ID,
            note_revision_id=REVISION_ID,
            note_id=NOTE_ID,
            version=1,
            status=ExternalNoteReviewStatus.PENDING,
            subject_id=subject_id,
            decision_id=None,
            due_at=None,
            actor="system",
            authorization_note="Await review.",
            idempotency_key="view-review-pending",
            created_at=NOW,
        )

    def latest_for_revision(self, revision_id: str):
        return self.value if revision_id == REVISION_ID else None

    def list_latest(self, **_kwargs: object):
        return (self.value,)


class _Uow:
    def __init__(self, decisions=()) -> None:
        self.decisions = SimpleNamespace(
            list_by_subject=lambda _subject_id: decisions,
            get=lambda decision_id: next(
                item for item in decisions if item.decision_id == decision_id
            ),
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_unmapped_review_is_safe_and_keeps_only_defer_action() -> None:
    service = ViewReviewService(
        _Notes(_payload()),  # type: ignore[arg-type]
        _Reviews(subject_id=None),  # type: ignore[arg-type]
        lambda: _Uow(),  # type: ignore[arg-type]
        SimpleNamespace(latest_accounts=lambda: ()),  # type: ignore[arg-type]
        SimpleNamespace(list_current=lambda: ()),  # type: ignore[arg-type]
    )

    result = service.get(REVISION_ID)

    assert result.subject_id is None
    assert result.allowed_actions == ("DEFER",)
    assert result.deterministic_flags == ("UNMAPPED_RESEARCH_SUBJECT",)
    assert result.coverage["research"] == "NOT_MAPPED"
    assert [item.speaker_label for item in result.external_viewpoints] == ["姜汁汽水"]
    inbox = service.inbox(limit=10)
    assert inbox.returned_count == 1
    assert inbox.items[0].allowed_actions == ("MAP_RESEARCH_SUBJECT", "DEFER")
    assert inbox.items[0].material_change_summary == "The user withdrew the add plan."


def test_mapped_review_composes_confirmed_baseline_and_durable_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SimpleNamespace(
        subject=SimpleNamespace(title="AMD research", status="ACTIVE"),
        theses=(
            SimpleNamespace(
                thesis_id="thesis_amd",
                title="AMD primary",
                status="ACTIVE",
                role="PRIMARY",
            ),
        ),
        latest_revisions=(
            SimpleNamespace(
                thesis_id="thesis_amd",
                revision_id="rev_amd",
                statement="Guidance must recover.",
                rating="HOLD",
                confidence_band="MEDIUM",
            ),
        ),
        current_trade_plan=SimpleNamespace(
            plan_id="trade_plan_amd",
            version=3,
            status="ACTIVE",
            instrument_id="equity:US:AMD",
        ),
    )
    monkeypatch.setattr(view_review_module, "build_research_state", lambda *_a, **_k: state)
    decision = SimpleNamespace(
        decision_id="decision_amd",
        decision_type="hold",
        title="Hold",
        rationale="Wait for evidence.",
        decided_at=NOW,
        external_note_revision_id="external_note_revision_old",
    )
    account = SimpleNamespace(
        account_ref="acct",
        provider=SimpleNamespace(value="MOOMOO"),
        account_as_of=NOW,
        positions=(
            SimpleNamespace(
                instrument_id="equity:US:AMD",
                quantity=Decimal("10"),
                currency="USD",
            ),
        ),
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_amd",
        version=2,
        name="AMD invalidation",
        subject_id=SUBJECT_ID,
        status=SimpleNamespace(value="ACTIVE"),
    )
    service = ViewReviewService(
        _Notes(_payload()),  # type: ignore[arg-type]
        _Reviews(subject_id=SUBJECT_ID),  # type: ignore[arg-type]
        lambda: _Uow((decision,)),  # type: ignore[arg-type]
        SimpleNamespace(latest_accounts=lambda: (account,)),  # type: ignore[arg-type]
        SimpleNamespace(list_current=lambda: (monitor,)),  # type: ignore[arg-type]
    )

    result = service.get(REVISION_ID)

    assert result.thesis is not None and result.thesis.revision_id == "rev_amd"
    assert result.trade_plan is not None and result.trade_plan.version == 3
    assert result.latest_decision is not None
    assert result.positions[0].quantity == Decimal("10")
    assert result.monitors[0].monitor_id == "monitor_amd"
    assert "REVIEW_THESIS_IMPACT" in result.deterministic_flags
    assert result.allowed_actions == (
        "DEFER",
        "RECORD_DECISION",
        "RECORD_NO_ACTION",
        "PROPOSE_THESIS_REVISION",
        "PROPOSE_TRADE_PLAN",
        "PREFILL_MONITOR",
    )


def test_review_package_rejects_incomplete_scenario_contract() -> None:
    payload = _payload()
    payload["user_scenarios"] = list(payload["user_scenarios"])[:3]  # type: ignore[arg-type]
    service = ViewReviewService(
        _Notes(payload),  # type: ignore[arg-type]
        _Reviews(subject_id=None),  # type: ignore[arg-type]
        lambda: _Uow(),  # type: ignore[arg-type]
        SimpleNamespace(latest_accounts=lambda: ()),  # type: ignore[arg-type]
        SimpleNamespace(list_current=lambda: ()),  # type: ignore[arg-type]
    )

    with pytest.raises(DataContractError, match="four USER scenarios"):
        service.get(REVISION_ID)


def test_terminal_review_exposes_no_repeat_actions() -> None:
    reviews = _Reviews(subject_id=SUBJECT_ID)
    reviews.value = replace(
        reviews.value,
        version=2,
        status=ExternalNoteReviewStatus.ADOPTED,
        decision_id="decision_adopted",
        actor="user",
        authorization_note="Already adopted.",
        idempotency_key="view-review-adopted",
    )
    service = ViewReviewService(
        _Notes(_payload()),  # type: ignore[arg-type]
        reviews,  # type: ignore[arg-type]
        lambda: (_ for _ in ()).throw(RuntimeError("must degrade")),  # type: ignore[arg-type]
        SimpleNamespace(latest_accounts=lambda: ()),  # type: ignore[arg-type]
        SimpleNamespace(list_current=lambda: ()),  # type: ignore[arg-type]
    )

    result = service.get(REVISION_ID)

    assert result.coverage["research"] == "UNAVAILABLE"
    assert "RESEARCH_CONTEXT_UNAVAILABLE" in result.deterministic_flags
    assert result.allowed_actions == ()


def test_current_view_is_derived_from_exact_confirmed_review_and_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SimpleNamespace(
        subject=SimpleNamespace(title="AMD research", status="ACTIVE"),
        theses=(),
        latest_revisions=(),
        current_trade_plan=None,
    )
    monkeypatch.setattr(view_review_module, "build_research_state", lambda *_a, **_k: state)
    decision = SimpleNamespace(
        decision_id="decision_current",
        subject_id=SUBJECT_ID,
        decision_type="no_action",
        title="Wait",
        rationale="No action until guidance.",
        decided_at=NOW,
        external_note_revision_id=REVISION_ID,
    )
    reviews = _Reviews(subject_id=SUBJECT_ID)
    reviews.value = replace(
        reviews.value,
        version=2,
        status=ExternalNoteReviewStatus.NO_ACTION,
        decision_id=decision.decision_id,
        actor="user",
        authorization_note="Confirmed no action.",
        idempotency_key="view-review-no-action",
    )
    service = ViewReviewService(
        _Notes(_payload()),  # type: ignore[arg-type]
        reviews,  # type: ignore[arg-type]
        lambda: _Uow((decision,)),  # type: ignore[arg-type]
        SimpleNamespace(latest_accounts=lambda: ()),  # type: ignore[arg-type]
        SimpleNamespace(list_current=lambda: ()),  # type: ignore[arg-type]
    )

    result = service.current(SUBJECT_ID)

    assert result is not None
    assert result.source_note_revision_id == REVISION_ID
    assert result.decision.decision_id == "decision_current"
    assert result.review.status == "NO_ACTION"
