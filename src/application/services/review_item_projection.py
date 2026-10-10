"""Durable ReviewItem projections shared by scoped and global materialization.

Links are part of the persisted ReviewItem contract. This module produces those
values without HTTP requests, repositories or provider access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from application.services.attention_projection import (
    console_attention_payload,
    project_agenda_overdue_fields,
    project_trade_retro_fields,
    project_unresolved_agent_fields,
    project_unresolved_broker,
)
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection


def console_row(item: Any, *, href: str) -> dict[str, Any]:
    payload = console_attention_payload(item)
    payload["href"] = href
    return payload


def workflow_attention_items(
    *,
    agenda: dict[str, Any],
    retro: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project every item from already-bounded durable workflow responses."""

    items: list[dict[str, Any]] = []
    agenda_data = agenda.get("data") if isinstance(agenda, dict) else None
    agenda_items = agenda_data.get("items") if isinstance(agenda_data, dict) else None
    for value in agenda_items if isinstance(agenda_items, list) else ():
        if not isinstance(value, dict):
            continue
        item_id = value.get("agenda_item_id")
        if not isinstance(item_id, str) or not item_id:
            continue
        limitation_codes = value.get("limitation_codes")
        projected = project_agenda_overdue_fields(
            agenda_item_id=item_id,
            title=str(value.get("title") or item_id),
            limitation_codes=(
                tuple(str(code) for code in limitation_codes)
                if isinstance(limitation_codes, list)
                else ()
            ),
            subject_id=(
                str(value["subject_id"])
                if isinstance(value.get("subject_id"), str) and value["subject_id"]
                else None
            ),
        )
        if projected is not None:
            items.append(console_row(projected, href=f"/agenda#agenda-{item_id}"))

    retro_data = retro.get("data") if isinstance(retro, dict) else None
    retro_runs = retro_data.get("runs") if isinstance(retro_data, dict) else None
    for value in retro_runs if isinstance(retro_runs, list) else ():
        if not isinstance(value, dict):
            continue
        run_id = value.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        latest_review = value.get("latest_review")
        review_status = (
            str(latest_review.get("status", "")).upper()
            if isinstance(latest_review, dict)
            else "UNREVIEWED"
        )
        findings = value.get("findings")
        subject_ids_value = value.get("subject_ids")
        subject_ids = tuple(
            dict.fromkeys(
                subject_id
                for subject_id in (
                    subject_ids_value if isinstance(subject_ids_value, (list, tuple)) else ()
                )
                if isinstance(subject_id, str) and subject_id
            )
        )
        action_items = (
            latest_review.get("action_items") if isinstance(latest_review, dict) else None
        )
        for projected in project_trade_retro_fields(
            run_id=run_id,
            finding_count=len(findings) if isinstance(findings, list) else 0,
            review_status=review_status,
            action_items=tuple(action_items) if isinstance(action_items, list) else (),
            subject_ids=subject_ids,
        ):
            items.append(console_row(projected, href=f"/retro#retro-{run_id}"))

    return items


def decision_review_attention_items(decisions: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Project due Decisions into stable ReviewItem source rows.

    The decision id is both the durable source reference and the stable key
    suffix.  A later exact superseder is intentionally handled by the
    repository query, so this projector only receives still-live Decisions.
    """

    items: list[dict[str, Any]] = []
    for decision in decisions:
        decision_id = str(getattr(decision, "decision_id", ""))
        subject_id = str(getattr(decision, "subject_id", ""))
        due_at = getattr(decision, "review_due_at", None)
        if not decision_id or not subject_id or not isinstance(due_at, datetime):
            continue
        title = str(getattr(decision, "title", "Decision review"))
        items.append(
            {
                "key": f"decision-review-due-{decision_id}",
                "source_type": ReviewItemSourceType.DECISION_REVIEW_DUE.value,
                "source_ref": decision_id,
                "subject_id": subject_id,
                "title": f"Decision review due · {title}",
                "detail": (
                    "The Decision review deadline has passed and no later exact "
                    "superseding Decision was recorded."
                ),
                "severity": "ATTENTION",
                "recommended_action": "REVIEW_DECISION",
                "href": (
                    f"/decision-workbench?subject_id={subject_id}"
                    f"&capture=decision&supersedes_decision_id={decision_id}"
                ),
                "due_at": due_at.isoformat(),
            }
        )
    return items


def operational_attention_items(
    *,
    agent_actions: tuple[Any, ...] = (),
    broker_intents: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Render unresolved execution-adjacent state without attempting recovery."""

    items: list[dict[str, Any]] = []
    for action in agent_actions:
        action_id = str(getattr(action, "action_id", ""))
        if not action_id:
            continue
        status = str(getattr(getattr(action, "status", None), "value", "UNKNOWN"))
        items.append(
            console_row(
                project_unresolved_agent_fields(
                    action_id=action_id,
                    status=status,
                    capability=str(getattr(action, "capability", "Agent action")),
                    operation=str(getattr(action, "operation", "")),
                ),
                href="/chat",
            )
        )
    for intent in broker_intents:
        intent_id = str(getattr(intent, "order_intent_id", ""))
        if not intent_id:
            continue
        items.append(
            console_row(
                project_unresolved_broker(
                    order_intent_id=intent_id,
                    status=str(getattr(intent, "status", "UNKNOWN")),
                    symbol=str(
                        getattr(intent, "symbol", None) or getattr(intent, "instrument_id", "Order")
                    ),
                    provider_status=getattr(intent, "provider_status", None),
                ),
                href="/capabilities",
            )
        )
    return items


def scorecard_attention_items(scorecards: dict[str, Any]) -> list[dict[str, Any]]:
    """Project latest Scorecard gaps and label consecutive recurrence."""

    data = scorecards.get("data") if scorecards.get("ok") is True else None
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for value in runs:
        if not isinstance(value, dict):
            continue
        thesis_id = value.get("thesis_id")
        if isinstance(thesis_id, str) and thesis_id:
            grouped.setdefault(thesis_id, []).append(value)

    items: list[dict[str, Any]] = []

    def gap_codes(run: dict[str, Any] | None) -> set[str]:
        dimensions = run.get("dimensions") if isinstance(run, dict) else None
        return {
            str(item.get("code"))
            for item in (dimensions if isinstance(dimensions, list) else ())
            if isinstance(item, dict)
            and (
                str(item.get("status", "")).upper() != "EVALUATED"
                or str(item.get("result_code", "")).upper() in {"FAIL", "PARTIAL", "NOT_EVALUATED"}
            )
            and item.get("code")
        }

    for thesis_id, thesis_runs in grouped.items():
        ordered = sorted(
            thesis_runs,
            key=lambda item: (str(item.get("generated_at", "")), str(item.get("scorecard_id", ""))),
            reverse=True,
        )
        latest = ordered[0]
        previous_gaps = gap_codes(ordered[1] if len(ordered) > 1 else None)
        dimensions = latest.get("dimensions")
        for dimension in dimensions if isinstance(dimensions, list) else ():
            if not isinstance(dimension, dict):
                continue
            code = str(dimension.get("code", ""))
            if code not in gap_codes(latest):
                continue
            persistent = code in previous_gaps
            subject_id = latest.get("subject_id")
            items.append(
                {
                    "key": f"scorecard-gap-{thesis_id}-{code}",
                    "severity": "ATTENTION",
                    "title": f"{'Persistent' if persistent else 'New'} Scorecard gap · "
                    f"{dimension.get('title') or code}",
                    "detail": (
                        f"{dimension.get('summary') or dimension.get('result_code') or code} · "
                        f"Thesis revision v{latest.get('thesis_revision_no') or '—'}"
                    ),
                    "href": (
                        f"/scorecards?subject_id={subject_id}#scorecard-"
                        f"{latest.get('scorecard_id')}"
                        if isinstance(subject_id, str) and subject_id
                        else f"/scorecards#scorecard-{latest.get('scorecard_id')}"
                    ),
                    "source_type": "SCORECARD_GAP",
                    # A newer scorecard changes the display anchor, not the
                    # durable issue scope.  Thesis is the authoritative source.
                    "source_ref": thesis_id,
                    "subject_id": subject_id if isinstance(subject_id, str) else None,
                    "recommended_action": "REVIEW_SCORECARD_GAP",
                }
            )
    return items


def authoritative_review_refs(
    *,
    agenda: dict[str, Any] | None = None,
    retro: dict[str, Any] | None = None,
    scorecards: dict[str, Any] | None = None,
) -> frozenset[tuple[ReviewItemSourceType, str]]:
    """Return exact source objects whose current gaps were fully inspected.

    A paginated response is never authoritative for objects omitted from that
    page.  Each object that is present, however, can safely close its own stale
    projections when its current representation contains no matching gap.
    """

    values: set[tuple[ReviewItemSourceType, str]] = set()
    agenda_data = agenda.get("data") if isinstance(agenda, dict) else None
    agenda_items = agenda_data.get("items") if isinstance(agenda_data, dict) else None
    for item in agenda_items if isinstance(agenda_items, list) else ():
        source_ref = item.get("agenda_item_id") if isinstance(item, dict) else None
        if isinstance(source_ref, str) and source_ref:
            values.add((ReviewItemSourceType.CATALYST_AGENDA, source_ref))

    retro_data = retro.get("data") if isinstance(retro, dict) else None
    retro_runs = retro_data.get("runs") if isinstance(retro_data, dict) else None
    for item in retro_runs if isinstance(retro_runs, list) else ():
        source_ref = item.get("run_id") if isinstance(item, dict) else None
        if isinstance(source_ref, str) and source_ref:
            values.add((ReviewItemSourceType.TRADE_RETRO, source_ref))

    scorecard_data = scorecards.get("data") if isinstance(scorecards, dict) else None
    scorecard_runs = scorecard_data.get("runs") if isinstance(scorecard_data, dict) else None
    for item in scorecard_runs if isinstance(scorecard_runs, list) else ():
        if not isinstance(item, dict):
            continue
        for field in ("thesis_id", "scorecard_id"):
            source_ref = item.get(field)
            if isinstance(source_ref, str) and source_ref:
                values.add((ReviewItemSourceType.SCORECARD_GAP, source_ref))
    return frozenset(values)


def review_item_projection(item: dict[str, Any]) -> ReviewItemProjection | None:
    try:
        source_type = ReviewItemSourceType(str(item["source_type"]))
        severity_wire = str(item.get("severity", "ATTENTION")).upper()
        severity = (
            ReviewItemSeverity.ERROR
            if severity_wire == "ERROR"
            else ReviewItemSeverity.INFO
            if severity_wire in {"INFO", "OBSERVE"}
            else ReviewItemSeverity.ATTENTION
        )
        return ReviewItemProjection(
            source_key=str(item["key"]),
            source_type=source_type,
            source_ref=str(item["source_ref"]),
            subject_id=(
                str(item["subject_id"])
                if isinstance(item.get("subject_id"), str) and item["subject_id"]
                else None
            ),
            title=str(item["title"]),
            detail=str(item["detail"]),
            severity=severity,
            recommended_action=str(item["recommended_action"]),
            href=str(item["href"]),
            due_at=(
                datetime.fromisoformat(str(item["due_at"]))
                if item.get("due_at") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class WorkflowReviewObservation:
    """Exact successful source objects, never a claim of complete history."""

    projections: tuple[ReviewItemProjection, ...]
    observed_source_types: frozenset[ReviewItemSourceType]
    authoritative_source_refs: frozenset[tuple[ReviewItemSourceType, str]]


def observe_workflow_reviews(
    *,
    agenda: dict[str, Any],
    retro: dict[str, Any],
    scorecards: dict[str, Any],
) -> WorkflowReviewObservation:
    """Project successful pages; failed sources cannot close earlier reminders."""

    observed: set[ReviewItemSourceType] = set()
    refs: set[tuple[ReviewItemSourceType, str]] = set()
    rows: list[dict[str, Any]] = []
    if agenda.get("ok") is True:
        observed.add(ReviewItemSourceType.CATALYST_AGENDA)
        rows.extend(workflow_attention_items(agenda=agenda, retro={}))
        refs.update(authoritative_review_refs(agenda=agenda))
    if retro.get("ok") is True:
        observed.add(ReviewItemSourceType.TRADE_RETRO)
        rows.extend(workflow_attention_items(agenda={}, retro=retro))
        refs.update(authoritative_review_refs(retro=retro))
    if scorecards.get("ok") is True:
        observed.add(ReviewItemSourceType.SCORECARD_GAP)
        rows.extend(scorecard_attention_items(scorecards))
        refs.update(authoritative_review_refs(scorecards=scorecards))
    return WorkflowReviewObservation(
        projections=tuple(
            projection for row in rows
            if (projection := review_item_projection(row)) is not None
        ),
        observed_source_types=frozenset(observed),
        authoritative_source_refs=frozenset(refs),
    )
