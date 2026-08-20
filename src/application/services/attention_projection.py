"""Typed, side-effect-free Attention projectors and digest assembly."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime

from application.dto.attention import (
    AttentionClosureConditionDTO,
    AttentionCoverageDTO,
    AttentionDigestDTO,
    AttentionItemDTO,
    AttentionMetricsDTO,
    AttentionNextReadDTO,
    AttentionQueryInput,
)
from application.dto.catalyst_agenda import AgendaItemDTO
from application.dto.data_quality import DataQualityIssueDTO, MonitorQualityDTO
from application.dto.judgment_scorecard import JudgmentScorecardRunDTO
from application.dto.research import CandidateRevisionDTO
from application.dto.review_item import ReviewItemDTO
from application.dto.trade_retro import TradeRetroRunDTO
from domain.agent.models import AgentPendingAction
from domain.attention.enums import (
    ATTENTION_DEFAULT_LIMIT,
    ATTENTION_MAX_LIMIT,
    AttentionClosureCode,
    AttentionCoverageSource,
    AttentionCoverageState,
    AttentionScope,
    AttentionSeverity,
    AttentionSourceType,
    AttentionStatus,
    AttentionTrackingKind,
)
from domain.common.enums import CandidateStatus
from domain.common.errors import DataContractError
from domain.review_item.enums import ReviewItemSourceType

_REVIEW_ITEM_SOURCE: dict[str, AttentionSourceType] = {
    ReviewItemSourceType.CATALYST_AGENDA.value: AttentionSourceType.CATALYST_AGENDA,
    ReviewItemSourceType.TRADE_RETRO.value: AttentionSourceType.TRADE_RETRO,
    ReviewItemSourceType.SCORECARD_GAP.value: AttentionSourceType.SCORECARD_GAP,
    ReviewItemSourceType.AGENT_PENDING_ACTION.value: AttentionSourceType.AGENT_PENDING_ACTION,
    ReviewItemSourceType.BROKER_ORDER_INTENT.value: AttentionSourceType.BROKER_ORDER_INTENT,
}

_CLOSURE: dict[AttentionSourceType, tuple[AttentionClosureCode, str]] = {
    AttentionSourceType.RESEARCH_CANDIDATE: (
        AttentionClosureCode.CANDIDATE_RESOLVED,
        "Exact candidate is confirmed, rejected, or withdrawn.",
    ),
    AttentionSourceType.CATALYST_AGENDA: (
        AttentionClosureCode.AGENDA_OUTCOME_CLOSED,
        "Outcome is linked, or the item is revised or cancelled so it is no longer overdue.",
    ),
    AttentionSourceType.TRADE_RETRO: (
        AttentionClosureCode.RETRO_STATE_SATISFIED,
        "Review or action durable state satisfies the original projection condition.",
    ),
    AttentionSourceType.SCORECARD_GAP: (
        AttentionClosureCode.SCORECARD_GAP_CLEARED,
        "A later Scorecard no longer shows the same gap, or an existing review disposes it.",
    ),
    AttentionSourceType.MONITOR_BLIND_SPOT: (
        AttentionClosureCode.MONITOR_COVERAGE_RESTORED,
        "A later successful run or observation restores coverage.",
    ),
    AttentionSourceType.BROKER_ORDER_INTENT: (
        AttentionClosureCode.BROKER_OBSERVATION_RECONCILED,
        "A new durable broker observation completes reconciliation; UNKNOWN is not retried.",
    ),
    AttentionSourceType.AGENT_PENDING_ACTION: (
        AttentionClosureCode.AGENT_ACTION_TERMINAL,
        "The pending action reaches a durable terminal result.",
    ),
    AttentionSourceType.DATA_QUALITY: (
        AttentionClosureCode.DATA_QUALITY_ISSUE_CLEARED,
        "A later durable fact or receipt clears the corresponding issue.",
    ),
}

_SEVERITY_ORDER = {
    AttentionSeverity.ERROR: 0,
    AttentionSeverity.ATTENTION: 1,
    AttentionSeverity.INFO: 2,
}


def validate_attention_query(request: AttentionQueryInput) -> AttentionQueryInput:
    if not 1 <= request.limit <= ATTENTION_MAX_LIMIT:
        raise DataContractError("Attention limit must be between 1 and 100")
    return request


def closure_for(source_type: AttentionSourceType) -> AttentionClosureConditionDTO:
    code, description = _CLOSURE[source_type]
    return AttentionClosureConditionDTO(code=code, description=description)


def next_read_for(
    source_type: AttentionSourceType,
    *,
    source_ref: str,
    subject_id: str | None,
) -> AttentionNextReadDTO | None:
    if source_type is AttentionSourceType.RESEARCH_CANDIDATE and subject_id:
        return AttentionNextReadDTO(
            tool="research_judgment_get",
            request={"operation": "state", "case_id": subject_id},
        )
    if source_type is AttentionSourceType.CATALYST_AGENDA:
        return AttentionNextReadDTO(
            tool="research_memory_get",
            request={"operation": "agenda", "agenda_item_id": source_ref},
        )
    if source_type is AttentionSourceType.TRADE_RETRO:
        return AttentionNextReadDTO(
            tool="portfolio_analyze",
            request={"operation": "retro_history", "run_id": source_ref},
        )
    if source_type is AttentionSourceType.SCORECARD_GAP:
        request: dict[str, object] = {"operation": "scorecard_history"}
        if subject_id:
            request["case_id"] = subject_id
        if source_ref.startswith("thesis_"):
            request["thesis_id"] = source_ref
        return AttentionNextReadDTO(tool="research_judgment_get", request=request)
    if source_type is AttentionSourceType.MONITOR_BLIND_SPOT:
        return AttentionNextReadDTO(
            tool="monitor_read",
            request={"operation": "runs", "monitor_id": source_ref},
        )
    if source_type is AttentionSourceType.BROKER_ORDER_INTENT:
        return AttentionNextReadDTO(
            tool="broker_order_manage",
            request={"operation": "status", "order_intent_id": source_ref},
        )
    if source_type is AttentionSourceType.DATA_QUALITY:
        return AttentionNextReadDTO(tool="system_health", request={})
    return None


def project_review_item(item: ReviewItemDTO) -> AttentionItemDTO | None:
    source_type = _REVIEW_ITEM_SOURCE.get(item.source_type)
    if source_type is None:
        return None
    if item.status not in {AttentionStatus.OPEN.value, AttentionStatus.ACKNOWLEDGED.value}:
        return None
    try:
        severity = AttentionSeverity(item.severity)
    except ValueError:
        severity = AttentionSeverity.ATTENTION
    return AttentionItemDTO(
        key=item.source_key,
        tracking_kind=AttentionTrackingKind.REVIEW_ITEM,
        review_item_id=item.review_item_id,
        source_type=source_type,
        source_ref=item.source_ref,
        subject_id=item.subject_id,
        title=item.title,
        detail=item.detail,
        severity=severity,
        recommended_action=item.recommended_action,
        status=AttentionStatus(item.status),
        first_seen_at=item.first_seen_at,
        last_seen_at=item.last_seen_at,
        due_at=item.due_at,
        occurrence_count=item.occurrence_count,
        closure_condition=closure_for(source_type),
        next_read=next_read_for(
            source_type, source_ref=item.source_ref, subject_id=item.subject_id
        ),
    )


def _live_item(
    *,
    key: str,
    source_type: AttentionSourceType,
    source_ref: str,
    title: str,
    detail: str,
    recommended_action: str,
    subject_id: str | None = None,
    severity: AttentionSeverity = AttentionSeverity.ATTENTION,
    due_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> AttentionItemDTO:
    return AttentionItemDTO(
        key=key,
        tracking_kind=AttentionTrackingKind.LIVE_PROJECTION,
        source_type=source_type,
        source_ref=source_ref,
        subject_id=subject_id,
        title=title,
        detail=detail,
        severity=severity,
        recommended_action=recommended_action,
        status=AttentionStatus.OPEN,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        due_at=due_at,
        closure_condition=closure_for(source_type),
        next_read=next_read_for(source_type, source_ref=source_ref, subject_id=subject_id),
    )


def project_pending_candidate(item: CandidateRevisionDTO) -> AttentionItemDTO | None:
    status = item.status.value if hasattr(item.status, "value") else item.status
    if status != CandidateStatus.PROPOSED.value:
        return None
    kind = item.kind.value if hasattr(item.kind, "value") else str(item.kind)
    return _live_item(
        key=f"research-candidate-{item.candidate_id}",
        source_type=AttentionSourceType.RESEARCH_CANDIDATE,
        source_ref=item.candidate_id,
        subject_id=item.subject_id,
        title=f"Pending candidate · {kind}",
        detail=item.proposed_by_rationale,
        recommended_action="CONFIRM_OR_REJECT_CANDIDATE",
        first_seen_at=item.proposed_at,
        due_at=item.expires_at,
    )


def project_agenda_overdue_fields(
    *,
    agenda_item_id: str,
    title: str,
    limitation_codes: Sequence[str],
    subject_id: str | None = None,
    window_end: datetime | None = None,
    recorded_at: datetime | None = None,
) -> AttentionItemDTO | None:
    if "AGENDA_OUTCOME_UNVERIFIED" not in limitation_codes:
        return None
    return _live_item(
        key=f"agenda-overdue-{agenda_item_id}",
        source_type=AttentionSourceType.CATALYST_AGENDA,
        source_ref=agenda_item_id,
        subject_id=subject_id,
        title=f"Catalyst outcome overdue · {title}",
        detail="The event window passed without a linked durable outcome fact.",
        recommended_action="LINK_OUTCOME_OR_REVISE",
        due_at=window_end,
        first_seen_at=recorded_at,
    )


def project_agenda_overdue(item: AgendaItemDTO) -> AttentionItemDTO | None:
    return project_agenda_overdue_fields(
        agenda_item_id=item.agenda_item_id,
        title=item.title,
        limitation_codes=item.limitation_codes,
        subject_id=item.subject_id,
        window_end=item.window_end,
        recorded_at=item.recorded_at,
    )


def project_trade_retro_fields(
    *,
    run_id: str,
    finding_count: int,
    review_status: str,
    action_items: Sequence[str],
    subject_ids: Sequence[str | None],
    generated_at: datetime | None = None,
) -> tuple[AttentionItemDTO, ...]:
    normalized_status = review_status.upper() if review_status else "UNREVIEWED"
    normalized_actions = tuple(
        dict.fromkeys(
            " ".join(action.split())
            for action in action_items
            if isinstance(action, str) and action.strip()
        )
    )
    scopes = tuple(subject_ids) or (None,)
    projected: list[AttentionItemDTO] = []
    for subject_id in scopes:
        scope_key = subject_id or "global"
        if normalized_status != "RESOLVED":
            for action in normalized_actions:
                action_key = hashlib.sha256(action.encode("utf-8")).hexdigest()[:16]
                projected.append(
                    _live_item(
                        key=f"retro-action-{run_id}-{scope_key}-{action_key}",
                        source_type=AttentionSourceType.TRADE_RETRO,
                        source_ref=run_id,
                        subject_id=subject_id,
                        title="Trade Retro follow-up action",
                        detail=action,
                        recommended_action="COMPLETE_RETRO_ACTION",
                        first_seen_at=generated_at,
                    )
                )
        if normalized_status not in {"UNREVIEWED", "OPEN", "DISPUTED"}:
            continue
        detail = f"{finding_count} deterministic finding(s)"
        if normalized_actions:
            detail += f" · {len(normalized_actions)} recorded follow-up action(s)"
        projected.append(
            _live_item(
                key=f"retro-review-{run_id}-{scope_key}",
                source_type=AttentionSourceType.TRADE_RETRO,
                source_ref=run_id,
                subject_id=subject_id,
                title=f"Trade Retro · {normalized_status.lower().replace('_', ' ')}",
                detail=detail,
                recommended_action="REVIEW_RETRO",
                first_seen_at=generated_at,
            )
        )
    return tuple(projected)


def project_trade_retro(item: TradeRetroRunDTO) -> tuple[AttentionItemDTO, ...]:
    latest = item.latest_review
    return project_trade_retro_fields(
        run_id=item.run_id,
        finding_count=len(item.findings),
        review_status=latest.status if latest is not None else "UNREVIEWED",
        action_items=latest.action_items if latest is not None else (),
        subject_ids=item.subject_ids,
        generated_at=item.generated_at,
    )


def _scorecard_gap_codes(run: JudgmentScorecardRunDTO | None) -> set[str]:
    if run is None:
        return set()
    return {
        dimension.code
        for dimension in run.dimensions
        if dimension.status.upper() != "EVALUATED"
        or dimension.result_code.upper() in {"FAIL", "PARTIAL", "NOT_EVALUATED"}
    }


def project_scorecard_gaps(
    runs: Sequence[JudgmentScorecardRunDTO],
) -> tuple[AttentionItemDTO, ...]:
    grouped: dict[str, list[JudgmentScorecardRunDTO]] = {}
    for run in runs:
        grouped.setdefault(run.thesis_id, []).append(run)
    items: list[AttentionItemDTO] = []
    for thesis_id, thesis_runs in grouped.items():
        ordered = sorted(
            thesis_runs,
            key=lambda item: (item.generated_at, item.scorecard_id),
            reverse=True,
        )
        latest = ordered[0]
        previous_gaps = _scorecard_gap_codes(ordered[1] if len(ordered) > 1 else None)
        latest_gaps = _scorecard_gap_codes(latest)
        for dimension in latest.dimensions:
            if dimension.code not in latest_gaps:
                continue
            persistent = dimension.code in previous_gaps
            items.append(
                _live_item(
                    key=f"scorecard-gap-{thesis_id}-{dimension.code}",
                    source_type=AttentionSourceType.SCORECARD_GAP,
                    source_ref=thesis_id,
                    subject_id=latest.subject_id,
                    title=(
                        f"{'Persistent' if persistent else 'New'} Scorecard gap · "
                        f"{dimension.title or dimension.code}"
                    ),
                    detail=(
                        f"{dimension.summary or dimension.result_code or dimension.code} · "
                        f"Thesis revision v{latest.thesis_revision_no}"
                    ),
                    recommended_action="REVIEW_SCORECARD_GAP",
                    first_seen_at=latest.generated_at,
                )
            )
    return tuple(items)


def project_monitor_blind_spot(item: MonitorQualityDTO) -> AttentionItemDTO | None:
    if item.current_version_evaluated and item.not_evaluated_count == 0:
        return None
    if not item.current_version_evaluated:
        title = f"Monitor never evaluated · {item.name}"
        detail = "The current Monitor version has no durable evaluation."
        action = "INSPECT_MONITOR_RUN"
    else:
        title = f"Monitor not evaluated · {item.name}"
        detail = (
            f"{item.not_evaluated_count} rule(s) were NOT_EVALUATED on the latest run."
        )
        action = "INSPECT_MONITOR_RUN"
    return _live_item(
        key=f"monitor-blind-{item.monitor_id}",
        source_type=AttentionSourceType.MONITOR_BLIND_SPOT,
        source_ref=item.monitor_id,
        subject_id=None,
        title=title,
        detail=detail,
        recommended_action=action,
        first_seen_at=item.latest_run_at,
    )


def project_unresolved_broker(
    *,
    order_intent_id: str,
    status: str,
    symbol: str,
    provider_status: str | None = None,
) -> AttentionItemDTO:
    if status in {"SUBMITTING", "UNKNOWN"}:
        detail = (
            "Submission was claimed but has no durable broker outcome; do not retry. "
            "Reconcile against Schwab activity."
        )
        severity = AttentionSeverity.ERROR
    else:
        detail = (
            "Cancellation was requested but has not yet been confirmed "
            "by a durable Provider observation."
        )
        severity = AttentionSeverity.ATTENTION
    if provider_status:
        detail += f" · Provider status: {provider_status}"
    return _live_item(
        key=f"broker-unresolved-{order_intent_id}",
        source_type=AttentionSourceType.BROKER_ORDER_INTENT,
        source_ref=order_intent_id,
        title=f"{symbol} order · {status}",
        detail=detail,
        recommended_action="RECONCILE_BROKER_ORDER",
        severity=severity,
    )


def project_unresolved_agent_fields(
    *,
    action_id: str,
    status: str,
    capability: str,
    operation: str,
    created_at: datetime | None = None,
) -> AttentionItemDTO:
    return _live_item(
        key=f"agent-unresolved-{action_id}",
        source_type=AttentionSourceType.AGENT_PENDING_ACTION,
        source_ref=action_id,
        title=f"Agent action requires reconciliation · {status}",
        detail=(
            f"{capability}/{operation} was not safely resolved; "
            "it will not retry automatically."
        ),
        recommended_action="RECONCILE_AGENT_ACTION",
        severity=AttentionSeverity.ERROR if status == "UNKNOWN" else AttentionSeverity.ATTENTION,
        first_seen_at=created_at,
    )


def project_unresolved_agent(action: AgentPendingAction) -> AttentionItemDTO:
    return project_unresolved_agent_fields(
        action_id=action.action_id,
        status=action.status.value,
        capability=action.capability,
        operation=action.operation,
        created_at=action.created_at,
    )


def project_data_quality_issue(issue: DataQualityIssueDTO) -> AttentionItemDTO:
    raw_severity = (
        issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity)
    )
    try:
        severity = AttentionSeverity(raw_severity)
    except ValueError:
        severity = (
            AttentionSeverity.ERROR
            if raw_severity == "error"
            else AttentionSeverity.ATTENTION
        )
    subject = issue.subject_ref or "global"
    return _live_item(
        key=f"data-quality-{issue.code}-{subject}",
        source_type=AttentionSourceType.DATA_QUALITY,
        source_ref=issue.subject_ref or issue.code,
        title=issue.code,
        detail=issue.detail,
        recommended_action=issue.recommended_action_code or "INSPECT_DATA_QUALITY",
        severity=severity,
        first_seen_at=issue.observed_at,
    )


def filter_subject_scope(
    items: Iterable[AttentionItemDTO],
    subject_id: str | None,
) -> tuple[AttentionItemDTO, ...]:
    if subject_id is None:
        return tuple(items)
    return tuple(item for item in items if item.subject_id == subject_id)


def merge_attention_items(
    *,
    review_items: Sequence[AttentionItemDTO],
    live_items: Sequence[AttentionItemDTO],
) -> tuple[AttentionItemDTO, ...]:
    review_keys = {item.key for item in review_items}
    merged = list(review_items)
    for item in live_items:
        if item.key not in review_keys:
            merged.append(item)
    return tuple(merged)


def _is_overdue(item: AttentionItemDTO, now: datetime) -> bool:
    return item.due_at is not None and item.due_at < now


def sort_attention_items(
    items: Sequence[AttentionItemDTO],
    *,
    now: datetime,
) -> tuple[AttentionItemDTO, ...]:
    def sort_key(item: AttentionItemDTO) -> tuple[object, ...]:
        severity = (
            AttentionSeverity(item.severity)
            if not isinstance(item.severity, AttentionSeverity)
            else item.severity
        )
        return (
            _SEVERITY_ORDER.get(severity, 9),
            0 if _is_overdue(item, now) else 1,
            0 if severity is AttentionSeverity.ATTENTION else 1,
            item.due_at or datetime.max.replace(tzinfo=now.tzinfo),
            item.first_seen_at or datetime.max.replace(tzinfo=now.tzinfo),
            item.key,
        )

    return tuple(sorted(items, key=sort_key))


def metrics_for(items: Sequence[AttentionItemDTO], *, now: datetime) -> AttentionMetricsDTO:
    by_source: dict[str, int] = {}
    open_count = 0
    acknowledged_count = 0
    overdue_count = 0
    unknown_execution_count = 0
    for item in items:
        source = (
            item.source_type.value
            if isinstance(item.source_type, AttentionSourceType)
            else str(item.source_type)
        )
        by_source[source] = by_source.get(source, 0) + 1
        if item.status == AttentionStatus.ACKNOWLEDGED.value:
            acknowledged_count += 1
        else:
            open_count += 1
        if _is_overdue(item, now):
            overdue_count += 1
        if source in {
            AttentionSourceType.BROKER_ORDER_INTENT.value,
            AttentionSourceType.AGENT_PENDING_ACTION.value,
        } and (
            item.severity == AttentionSeverity.ERROR.value
            or item.severity is AttentionSeverity.ERROR
        ):
            unknown_execution_count += 1
    return AttentionMetricsDTO(
        open_count=open_count,
        acknowledged_count=acknowledged_count,
        overdue_count=overdue_count,
        unknown_execution_count=unknown_execution_count,
        by_source=by_source,
    )


def highest_severity(items: Sequence[AttentionItemDTO]) -> AttentionSeverity | None:
    if not items:
        return None
    return min(
        (
            AttentionSeverity(item.severity)
            if not isinstance(item.severity, AttentionSeverity)
            else item.severity
            for item in items
        ),
        key=lambda value: _SEVERITY_ORDER[value],
    )


def assemble_attention_digest(
    *,
    generated_at: datetime,
    request: AttentionQueryInput,
    review_items: Sequence[AttentionItemDTO],
    live_items: Sequence[AttentionItemDTO],
    coverage: Sequence[AttentionCoverageDTO],
    extra_limitations: Sequence[str] = (),
) -> AttentionDigestDTO:
    validate_attention_query(request)
    merged = merge_attention_items(review_items=review_items, live_items=live_items)
    scoped = filter_subject_scope(merged, request.case_id)
    ordered = sort_attention_items(scoped, now=generated_at)
    limit = request.limit or ATTENTION_DEFAULT_LIMIT
    clipped = ordered[:limit]
    limitations = tuple(dict.fromkeys(extra_limitations))
    coverage_limits = tuple(
        dict.fromkeys(
            code
            for item in coverage
            for code in item.limitation_codes
            if str(item.state) != "COMPLETE"
        )
    )
    return AttentionDigestDTO(
        generated_at=generated_at,
        scope=AttentionScope.SUBJECT if request.case_id else AttentionScope.GLOBAL,
        subject_id=request.case_id,
        case_id=request.case_id,
        total_count=len(ordered),
        returned_count=len(clipped),
        truncated=len(clipped) < len(ordered),
        highest_severity=highest_severity(ordered),
        limitations=tuple(dict.fromkeys((*limitations, *coverage_limits))),
        coverage=tuple(coverage),
        items=clipped,
        metrics=metrics_for(ordered, now=generated_at),
    )


def console_attention_payload(item: AttentionItemDTO) -> dict[str, object]:
    """Console ReviewItem materialization payload without a page href."""

    return {
        "key": item.key,
        "severity": str(item.severity),
        "title": item.title,
        "detail": item.detail,
        "source_type": str(item.source_type),
        "source_ref": item.source_ref,
        "subject_id": item.subject_id,
        "recommended_action": item.recommended_action,
    }


def coverage(
    source: AttentionCoverageSource,
    state: str,
    *,
    observed_at: datetime | None = None,
    limitation_codes: Sequence[str] = (),
) -> AttentionCoverageDTO:
    return AttentionCoverageDTO(
        source=source,
        state=AttentionCoverageState(state),
        observed_at=observed_at,
        limitation_codes=tuple(limitation_codes),
    )
