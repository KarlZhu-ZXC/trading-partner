"""Bounded read-time Retro coverage, kept separate from the immutable audit."""

from collections import Counter
from datetime import datetime
from typing import Any

from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.activity_annotation_repository import ActivityAnnotationRepository
from application.ports.trade_retro_repository import TradeRetroRepository
from application.services._research_support import UowFactory
from application.services.retro_followup_progress import followup_progress
from domain.common.errors import TradeRetroRunNotFound
from domain.portfolio.enums import AccountTransactionKind
from domain.retro.models import trade_retro_finding_key


def retro_workflow(
    run_id: str,
    repository: TradeRetroRepository,
    transactions: AccountTransactionRepository,
    uow_factory: UowFactory,
    annotations: ActivityAnnotationRepository | None,
    now: datetime,
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    if run is None:
        raise TradeRetroRunNotFound("Trade Retro run was not found")
    warnings: list[str] = []
    snapshot = repository.get_plan_snapshot(run.plan_snapshot_id) if run.plan_snapshot_id else None
    valid_snapshot = (
        snapshot is not None
        and snapshot.captured_at <= run.period_start
        and snapshot.period_start <= run.period_start
        and snapshot.period_end >= run.period_end
    )
    entries = snapshot.entries if valid_snapshot and snapshot is not None else ()
    metrics: dict[str, Any] = {
        "recorded_trade_references": len(run.transaction_ids),
        "resolved_trade_count": None,
        "unique_active_snapshot_match_count": None,
        "compatible_preperiod_decision_count": None,
        "unmatched_snapshot_trade_count": None,
        "ambiguous_snapshot_trade_count": None,
        "currently_unlinked_trade_count": None,
        "new_plan_versions_during_period": None,
    }
    try:
        rows = transactions.list(
            providers=(), start=run.period_start, end=run.period_end, limit=5001
        )
        counts = Counter(
            r.provider_transaction_id for r in rows if r.kind is AccountTransactionKind.TRADE
        )
        ids = set(run.transaction_ids)
        if (
            len(rows) > 5000
            or len(ids) != len(run.transaction_ids)
            or any(counts[i] != 1 for i in ids)
        ):
            warnings.append("TRADE_COHORT_UNRESOLVED")
        else:
            trades = tuple(
                r
                for r in rows
                if r.provider_transaction_id in ids and r.kind is AccountTransactionKind.TRADE
            )
            metrics["resolved_trade_count"] = len(trades)
            matches = missing = ambiguous = decisions = 0
            for trade in trades:
                plans = [p for p in entries if p.instrument_id == trade.instrument_id]
                if not plans:
                    missing += 1
                elif len(plans) > 1:
                    ambiguous += 1
                elif plans[0].status == "ACTIVE":
                    matches += 1
                    expected = (
                        {"initiate_intent", "add_intent"}
                        if trade.side and trade.side.value == "BUY"
                        else {"reduce_intent", "exit_intent"}
                    )
                    decisions += any(
                        kind.lower() in expected
                        and snapshot is not None
                        and datetime.fromisoformat(at) <= snapshot.captured_at
                        and datetime.fromisoformat(at) <= trade.occurred_at
                        and instrument in (None, trade.instrument_id)
                        for _, kind, at, instrument in plans[0].decision_records
                    )
            if valid_snapshot:
                metrics.update(
                    unique_active_snapshot_match_count=matches,
                    compatible_preperiod_decision_count=decisions,
                    unmatched_snapshot_trade_count=missing,
                    ambiguous_snapshot_trade_count=ambiguous,
                )
            if annotations is not None:
                unlinked = 0
                for trade in trades:
                    annotation = annotations.get_latest(
                        provider=trade.provider,
                        account_ref=trade.account_ref,
                        provider_transaction_id=trade.provider_transaction_id,
                    )
                    unlinked += (
                        annotation is None
                        or annotation.status.value != "LINKED_DECISION_PLAN"
                        or not (annotation.decision_id or annotation.trade_plan_id)
                    )
                metrics["currently_unlinked_trade_count"] = unlinked
    except Exception:  # noqa: BLE001 - partial facts cannot imply zero activity
        warnings.append("TRADE_OR_ATTRIBUTION_READ_UNAVAILABLE")
    subjects_out = []
    try:
        with uow_factory() as uow:
            subjects = uow.subjects.list(include_archived=True, limit=501)
            new_plans = 0
            if len(subjects) > 500:
                warnings.append("SUBJECT_COHORT_TRUNCATED")
            for subject in subjects[:500]:
                plan = uow.trade_plans.get_current_by_subject(subject.subject_id)
                versions = uow.trade_plans.list_versions(plan.plan_id) if plan else ()
                new_plans += sum(
                    run.period_start <= p.created_at < run.period_end and p.created_at <= now
                    for p in versions
                )
                questions = uow.questions.list_by_subject(subject.subject_id)
                subjects_out.append(
                    {
                        "subject_id": subject.subject_id,
                        "title": subject.title,
                        "instrument_id": subject.primary_instrument_id,
                        "plan_id": plan.plan_id if plan else None,
                        "questions": [
                            {
                                "question_id": q.question_id,
                                "version": q.version,
                                "text": q.text,
                                "status": q.status.value,
                            }
                            for q in questions[:100]
                            if q.asked_at <= now
                        ],
                        "questions_complete": len(questions) <= 100,
                    }
                )
            if len(subjects) <= 500:
                metrics["new_plan_versions_during_period"] = new_plans
    except Exception:  # noqa: BLE001 - independent source failures remain explicit
        warnings.append("RESEARCH_COHORT_UNAVAILABLE")
    if not valid_snapshot:
        warnings.append("NO_ELIGIBLE_PREPERIOD_SNAPSHOT")
    return {
        "run_id": run_id,
        "as_of": now,
        "original_run_status": run.status.value,
        "snapshot_id": snapshot.snapshot_id if valid_snapshot and snapshot else None,
        "snapshot_captured_at": snapshot.captured_at if valid_snapshot and snapshot else None,
        "metrics": metrics,
        "followup_progress": followup_progress(run, repository, uow_factory, now),
        "warning_codes": warnings,
        "basis": "READ_TIME_PROJECTION_OF_ORIGINAL_RUN_REFERENCES",
        "findings": [
            {
                "finding_key": trade_retro_finding_key(f),
                "title": f.title,
                "detail": f.detail,
                "instrument_id": f.instrument_id,
                "subjects": [
                    s
                    for s in subjects_out
                    if (f.instrument_id is not None and s["instrument_id"] == f.instrument_id)
                    or (f.plan_id is not None and s.get("plan_id") == f.plan_id)
                ],
            }
            for f in run.findings
        ],
        "execution_effect": False,
    }
