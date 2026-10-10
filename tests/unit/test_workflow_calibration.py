from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from application.dto.trade_retro import TradeRetroFindingReviewInput, TradeRetroReviewInput
from application.services.evidence_cohorts import reviewed_impact_cohort
from application.services.retro_workflow_query import retro_workflow
from application.services.trade_retro_service import TradeRetroService
from conftest import FixedClock, SequentialIdGenerator
from domain.portfolio.enums import AccountTransactionKind, AccountTransactionSide
from domain.research.evidence_impact import ResearchSourceRef
from domain.retro.enums import TradeRetroSeverity, TradeRetroStatus
from domain.retro.models import TradeRetroFinding, TradeRetroRun, trade_retro_finding_key
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.trade_retro_repository import SqlAlchemyTradeRetroRepository
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 10, 4, tzinfo=UTC)
SUFFIX = "00000000-0000-7000-8000-000000000001"


def test_impact_counts_deduplicate_sources_and_do_not_sum_assessments():
    uow = MagicMock()
    source = ResearchSourceRef("EVIDENCE", f"evidence_{SUFFIX}")

    def row(id, version=1, **kw):
        return NS(
            impact_id=id,
            version=version,
            subject_id="subject",
            thesis_revision_id="rev",
            confirmed_at=kw.get("at", NOW),
            state=kw.get("state", "ACTIVE"),
            source=source,
            stance="supports",
        )

    uow.evidence_impacts.list_by_subject.return_value = (
        row("one"),
        row("one", 2),
        row("two"),
        row("future", at=NOW + timedelta(seconds=1)),
        row("retracted"),
        row("retracted", 2, state="RETRACTED"),
    )
    uow.research_sources.get.return_value = None
    data = reviewed_impact_cohort(uow, "subject", "rev", NOW, {source.source_id})
    assert (data["link_count"], data["source_count"], data["overlap_with_assessments"]) == (2, 1, 1)
    assert data["missing_sources"] == 1
    assert data["stances"]["supports"] == 2
    uow.evidence_impacts.list_by_subject.return_value = (row("one"),) * 501
    assert reviewed_impact_cohort(uow, "subject", "rev", NOW, set())["source_count"] is None
    uow.evidence_impacts.list_by_subject.side_effect = RuntimeError("private source")
    failed = reviewed_impact_cohort(uow, "subject", "rev", NOW, set())
    assert failed["coverage"] == "UNAVAILABLE" and failed["link_count"] is None
    assert "private source" not in str(failed)


def run_fixture():
    finding = TradeRetroFinding(
        "NO_PRETRADE_PLAN",
        TradeRetroSeverity.HIGH,
        "Check plan",
        "Synthetic finding",
        "equity:US:TEST",
        ("trade",),
    )
    return TradeRetroRun(
        f"retro_{SUFFIX}",
        NOW - timedelta(days=7),
        NOW,
        NOW,
        TradeRetroStatus.INCOMPLETE,
        None,
        ("trade",),
        (finding,),
        (),
        "Synthetic audit",
        None,
        None,
        "synthetic-run",
    )


def uow_fixture():
    uow = MagicMock()
    uow.__enter__.return_value = uow
    subject = NS(
        subject_id=f"case_{SUFFIX}", title="Synthetic", primary_instrument_id="equity:US:TEST"
    )
    uow.subjects.get.return_value = subject
    uow.subjects.list.return_value = (subject,)
    uow.questions.list_by_subject.return_value = ()
    uow.trade_plans.get_current_by_subject.return_value = NS(plan_id="plan")
    uow.trade_plans.list_versions.return_value = (NS(created_at=NOW - timedelta(days=2)),)
    return uow


def test_new_plan_never_backfills_missing_snapshot_and_ambiguous_trade_ids_are_unknown():
    run = run_fixture()
    repo, transactions, annotations = MagicMock(), MagicMock(), MagicMock()
    repo.get_run.return_value = run
    trade = NS(
        provider_transaction_id="trade",
        provider="synthetic",
        account_ref="test",
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        instrument_id="equity:US:TEST",
        occurred_at=NOW - timedelta(days=3),
    )
    transactions.list.return_value = (trade,)
    annotations.get_latest.return_value = None
    uow = uow_fixture()
    data = retro_workflow(run.run_id, repo, transactions, lambda: uow, annotations, NOW)
    assert data["metrics"]["new_plan_versions_during_period"] == 1
    assert data["metrics"]["unique_active_snapshot_match_count"] is None
    assert data["metrics"]["currently_unlinked_trade_count"] == 1
    assert data["metrics"]["resolved_trade_count"] == 1
    assert "NO_ELIGIBLE_PREPERIOD_SNAPSHOT" in data["warning_codes"]
    transactions.list.return_value = (trade, trade)
    ambiguous = retro_workflow(run.run_id, repo, transactions, lambda: uow, annotations, NOW)
    assert ambiguous["metrics"]["resolved_trade_count"] is None
    assert "TRADE_COHORT_UNRESOLVED" in ambiguous["warning_codes"]
    repo.append_run.assert_not_called()
    uow.commit.assert_not_called()


def test_followup_exact_question_round_trip_conflict_scope_and_idempotency(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retro.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyTradeRetroRepository(engine)
    run = run_fixture()
    repository.append_run(run)
    uow = uow_fixture()
    question = NS(
        question_id=f"question_{SUFFIX}", version=1, subject_id=f"case_{SUFFIX}", asked_at=NOW
    )
    uow.questions.get.return_value = question
    service = TradeRetroService(
        repository,
        MagicMock(),
        lambda: uow,
        MagicMock(),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )
    request = TradeRetroReviewInput(
        run_id=run.run_id,
        expected_version=0,
        status="ACCEPTED",
        finding_reviews=(
            TradeRetroFindingReviewInput(
                finding_key=trade_retro_finding_key(run.findings[0]),
                status="ACCEPTED",
                followup="QUESTION",
                question_id=question.question_id,
                question_version=1,
            ),
        ),
        confirmed_by="user",
        authorization_note="Explicit synthetic review",
        idempotency_key="followup",
    )
    question.version = 2
    assert not service.review(request).ok
    question.version = 1
    uow.subjects.get.return_value.primary_instrument_id = "equity:US:OTHER"
    assert not service.review(request).ok
    uow.subjects.get.return_value.primary_instrument_id = "equity:US:TEST"
    saved = service.review(request)
    assert saved.ok
    assert service.detail(run.run_id).run_id == run.run_id
    assert service.review_receipt(run.run_id, "missing-key") is None
    assert (
        service.review_receipt(run.run_id, request.idempotency_key).review_id
        == saved.data.review_id
    )
    from domain.common.errors import DataContractError

    with pytest.raises(DataContractError):
        service.review_receipt("retro_wrong_scope", request.idempotency_key)
    row = repository.latest_review(run.run_id)
    assert row.finding_reviews[0].question_version == 1
    question.version = 2
    duplicate = service.review(request)
    assert duplicate.ok and duplicate.data.review_id == saved.data.review_id
    assert len(repository.list_reviews(run.run_id)) == 1
    different = request.model_copy(
        update={
            "finding_reviews": (
                request.finding_reviews[0].model_copy(update={"question_version": 2}),
            )
        }
    )
    assert not service.review(different).ok
    uow.questions.add.assert_not_called()


def test_no_followup_requires_reason_and_old_review_payload_still_works():
    base = {"finding_key": "finding_" + "a" * 64, "status": "ACCEPTED"}
    assert TradeRetroFindingReviewInput(**base).followup is None
    with pytest.raises(ValueError):
        TradeRetroFindingReviewInput(**base, followup="NO_FOLLOWUP")
    assert TradeRetroFindingReviewInput(
        **base, followup="NO_FOLLOWUP", note="Already addressed"
    ).note


def test_preperiod_decisions_and_retrospective_links_have_separate_time_bases():
    from dataclasses import replace

    from domain.portfolio.enums import ActivityAnnotationStatus

    run = replace(run_fixture(), plan_snapshot_id=f"retro_plan_{SUFFIX}")
    repo, transactions, annotations = MagicMock(), MagicMock(), MagicMock()
    repo.get_run.return_value = run
    entry = NS(
        instrument_id="equity:US:TEST",
        status="ACTIVE",
        decision_records=(
            (
                "decision",
                "initiate_intent",
                (run.period_start + timedelta(hours=1)).isoformat(),
                "equity:US:TEST",
            ),
        ),
    )
    snapshot = NS(
        snapshot_id=run.plan_snapshot_id,
        captured_at=run.period_start - timedelta(hours=1),
        period_start=run.period_start,
        period_end=run.period_end,
        entries=(entry,),
    )
    repo.get_plan_snapshot.return_value = snapshot
    transactions.list.return_value = (
        NS(
            provider_transaction_id="trade",
            provider="synthetic",
            account_ref="test",
            kind=AccountTransactionKind.TRADE,
            side=AccountTransactionSide.BUY,
            instrument_id="equity:US:TEST",
            occurred_at=run.period_start + timedelta(days=1),
        ),
    )
    annotations.get_latest.return_value = NS(
        status=ActivityAnnotationStatus.LINKED_DECISION_PLAN,
        decision_id="later-link",
        trade_plan_id=None,
    )
    uow = uow_fixture()
    data = retro_workflow(run.run_id, repo, transactions, lambda: uow, annotations, NOW)
    assert data["metrics"]["unique_active_snapshot_match_count"] == 1
    assert data["metrics"]["compatible_preperiod_decision_count"] == 0
    assert data["metrics"]["currently_unlinked_trade_count"] == 0
    snapshot.entries = (entry, entry)
    data = retro_workflow(run.run_id, repo, transactions, lambda: uow, annotations, NOW)
    assert data["metrics"]["ambiguous_snapshot_trade_count"] == 1
    assert data["metrics"]["unique_active_snapshot_match_count"] == 0
    snapshot.captured_at = NOW
    data = retro_workflow(run.run_id, repo, transactions, lambda: uow, annotations, NOW)
    assert data["metrics"]["unique_active_snapshot_match_count"] is None
