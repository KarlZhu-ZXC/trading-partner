"""Explain accepted Retro follow-ups without inferring closure from question status."""

from datetime import datetime
from typing import Any

from application.ports.trade_retro_repository import TradeRetroRepository
from application.services._research_support import UowFactory
from domain.common.enums import OpenQuestionStatus
from domain.retro.models import TradeRetroRun, trade_retro_finding_key


def followup_progress(
    run: TradeRetroRun,
    repository: TradeRetroRepository,
    uow_factory: UowFactory,
    now: datetime,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        history = repository.list_reviews(run.run_id, limit=101)
        complete = len(history) <= 100
        if not complete:
            warnings.append("REVIEW_HISTORY_TRUNCATED")
        history = tuple(r for r in history[:100] if r.created_at <= now)
        selected = {
            item.finding_key
            for review in history
            for item in review.finding_reviews
            if item.followup == "QUESTION"
        }
        latest = max(history, key=lambda r: r.version, default=None)
        choices = {item.finding_key: item for item in latest.finding_reviews} if latest else {}
    except Exception:  # noqa: BLE001 - no history is not evidence of no tasks
        return {
            "coverage": "UNAVAILABLE",
            "selected_count": None,
            "due_count": None,
            "resolved_due_count": None,
            "rows": [],
            "warning_codes": ["REVIEW_HISTORY_UNAVAILABLE"],
        }
    findings = {trade_retro_finding_key(f): f for f in run.findings}
    for key in sorted(selected):
        finding = findings.get(key)
        choice = choices.get(key)
        row: dict[str, Any] = {
            "finding_key": key,
            "title": finding.title if finding else key,
            "finding_code": finding.code if finding else None,
            "instrument_id": finding.instrument_id if finding else None,
            "state": "REVIEW_CHANGED",
            "question_id": None,
            "subject_id": None,
            "question_version": None,
            "due_at": None,
            "due": None,
            "closed": False,
            "source_refs": [],
            "later_similar_runs": [],
        }
        if choice and choice.followup == "NO_FOLLOWUP" and choice.note:
            # The reason is explicit; an old mutable deadline cannot be reconstructed.
            row["state"] = "NO_FOLLOWUP_RECORDED"
        elif choice and choice.followup == "DEFER":
            row["state"] = "DEFERRED"
        elif choice and choice.followup == "QUESTION":
            row.update(question_id=choice.question_id, question_version=choice.question_version)
            try:
                with uow_factory() as uow:
                    assert choice.question_id is not None
                    question = uow.questions.get(choice.question_id)
                    row["subject_id"] = question.subject_id
                    if question.version != choice.question_version or question.asked_at > now:
                        row["state"] = "QUESTION_CHANGED"
                    else:
                        row["due_at"] = question.due_at
                        row["due"] = question.due_at <= now if question.due_at else False
                        row["state"] = (
                            "NO_DEADLINE"
                            if not question.due_at
                            else ("DUE" if row["due"] else "NOT_DUE")
                        )
                        if question.status is OpenQuestionStatus.ANSWERED:
                            answered_at = question.answered_at
                            sources = question.source_refs
                            traceable = bool(
                                answered_at
                                and answered_at <= now
                                and sources
                                and question.answer_summary
                            )
                            if traceable:
                                assert answered_at is not None
                                traceable = all(
                                    uow.research_sources.get(
                                        question.subject_id, ref, as_of=answered_at
                                    )
                                    is not None
                                    for ref in sources
                                )
                            row["source_refs"] = [
                                {
                                    "kind": ref.kind,
                                    "source_id": ref.source_id,
                                    "monitor_id": ref.monitor_id,
                                    "rule_code": ref.rule_code,
                                }
                                for ref in sources
                            ]
                            reviewed = bool(
                                latest
                                and answered_at
                                and latest.created_at >= answered_at
                                and choice.status.value == "RESOLVED"
                            )
                            row["state"] = (
                                "REVIEWED_WITH_SOURCES"
                                if traceable and reviewed
                                else "READY_FOR_REVIEW"
                                if traceable
                                else "EVIDENCE_MISSING"
                            )
                            row["closed"] = traceable and reviewed
                        elif question.status is OpenQuestionStatus.CLOSED_WITHOUT_ANSWER:
                            row["state"] = "CLOSED_QUESTION_NEEDS_REVIEW"
            except Exception:  # noqa: BLE001 - preserve the task and its reference
                row["state"] = "SOURCE_UNAVAILABLE"
                row["due"] = None
        rows.append(row)
    try:
        later = repository.list_runs(limit=51)
        if len(later) > 50:
            warnings.append("LATER_RUNS_TRUNCATED")
        for row in rows:
            finding = findings.get(row["finding_key"])
            if finding is None:
                continue
            row["later_similar_runs"] = [
                r.run_id
                for r in later[:50]
                if r.period_start >= run.period_end
                and r.generated_at <= now
                and any(
                    (f.code, f.instrument_id, f.plan_id)
                    == (finding.code, finding.instrument_id, finding.plan_id)
                    for f in r.findings
                )
            ]
    except Exception:  # noqa: BLE001 - absent later evidence never means fixed
        warnings.append("LATER_RUNS_UNAVAILABLE")
    unknown = sum(r["due"] is None for r in rows)
    return {
        "coverage": "COMPLETE" if complete and not unknown else "PARTIAL",
        "selected_count": len(rows) if complete else None,
        "known_selected_count": len(rows),
        "due_count": sum(r["due"] is True for r in rows) if complete and not unknown else None,
        "known_due_count": sum(r["due"] is True for r in rows),
        "resolved_due_count": sum(r["due"] is True and r["closed"] for r in rows)
        if complete and not unknown
        else None,
        "unknown_deadline_count": unknown,
        "basis": "LATEST_EXPLICIT_REVIEW_AND_EXACT_QUESTION_VERSION",
        "rows": rows,
        "warning_codes": warnings,
    }
