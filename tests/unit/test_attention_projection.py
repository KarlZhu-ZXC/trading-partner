from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.dto.attention import (
    AttentionClosureConditionDTO,
    AttentionItemDTO,
    AttentionNextReadDTO,
    AttentionQueryInput,
)
from application.dto.data_quality import DataQualityIssueDTO
from application.dto.review_item import ReviewItemDTO
from application.services.attention_projection import (
    assemble_attention_digest,
    coverage,
    filter_subject_scope,
    merge_attention_items,
    next_read_for,
    project_data_quality_issue,
    project_pending_candidate,
    project_review_item,
    project_unresolved_broker,
    sort_attention_items,
)
from domain.attention.enums import (
    AttentionClosureCode,
    AttentionCoverageSource,
    AttentionScope,
    AttentionSeverity,
    AttentionSourceType,
    AttentionStatus,
    AttentionTrackingKind,
)
from domain.common.enums import CandidateStatus, HealthState


def _now() -> datetime:
    return datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _item(
    *,
    key: str,
    source_type: AttentionSourceType = AttentionSourceType.CATALYST_AGENDA,
    subject_id: str | None = "case_1",
    severity: AttentionSeverity = AttentionSeverity.ATTENTION,
    status: AttentionStatus = AttentionStatus.OPEN,
    tracking: AttentionTrackingKind = AttentionTrackingKind.LIVE_PROJECTION,
    review_item_id: str | None = None,
    due_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> AttentionItemDTO:
    return AttentionItemDTO(
        key=key,
        tracking_kind=tracking,
        review_item_id=review_item_id,
        source_type=source_type,
        source_ref=key,
        subject_id=subject_id,
        title=key,
        detail="detail",
        severity=severity,
        recommended_action="LINK_OUTCOME_OR_REVISE",
        status=status,
        first_seen_at=first_seen_at or _now(),
        due_at=due_at,
        closure_condition=AttentionClosureConditionDTO(
            code=AttentionClosureCode.AGENDA_OUTCOME_CLOSED,
            description="closed when outcome is linked",
        ),
    )


def test_next_read_rejects_write_operations() -> None:
    with pytest.raises(ValidationError, match="write, sync, or evaluate"):
        AttentionNextReadDTO(
            tool="broker_order_manage",
            request={"operation": "submit", "order_intent_id": "order_1"},
        )
    with pytest.raises(ValidationError, match="operation=status"):
        AttentionNextReadDTO(
            tool="broker_order_manage",
            request={"operation": "runs"},
        )
    assert next_read_for(
        AttentionSourceType.BROKER_ORDER_INTENT,
        source_ref="order_1",
        subject_id=None,
    ) == AttentionNextReadDTO(
        tool="broker_order_manage",
        request={"operation": "status", "order_intent_id": "order_1"},
    )
    assert next_read_for(
        AttentionSourceType.OBSERVATION_REVIEW_DUE,
        source_ref="external_note_revision_1",
        subject_id="case_1",
    ) == AttentionNextReadDTO(
        tool="view_review_get",
        request={"note_revision_id": "external_note_revision_1"},
    )


def test_subject_scope_excludes_global_items() -> None:
    items = (
        _item(key="subject", subject_id="case_1"),
        _item(key="other", subject_id="case_2"),
        _item(key="global", subject_id=None),
    )
    scoped = filter_subject_scope(items, "case_1")
    assert [item.key for item in scoped] == ["subject"]


def test_merge_prefers_review_item_over_live_duplicate() -> None:
    review = _item(
        key="agenda-overdue-1",
        tracking=AttentionTrackingKind.REVIEW_ITEM,
        review_item_id="review_item_1",
        status=AttentionStatus.ACKNOWLEDGED,
    )
    live = _item(key="agenda-overdue-1").model_copy(
        update={
            "title": "Current escalation",
            "detail": "Current durable source detail",
            "severity": AttentionSeverity.ERROR,
            "recommended_action": "INSPECT_CURRENT_SOURCE",
        }
    )
    extra = _item(key="research-candidate-1")
    merged = merge_attention_items(review_items=(review,), live_items=(live, extra))
    assert [item.key for item in merged] == ["agenda-overdue-1", "research-candidate-1"]
    assert merged[0].tracking_kind == AttentionTrackingKind.REVIEW_ITEM.value
    assert merged[0].status == AttentionStatus.ACKNOWLEDGED.value
    assert merged[0].review_item_id == "review_item_1"
    assert merged[0].title == "Current escalation"
    assert merged[0].detail == "Current durable source detail"
    assert merged[0].severity == AttentionSeverity.ERROR.value
    assert merged[0].recommended_action == "INSPECT_CURRENT_SOURCE"


def test_expired_candidate_is_not_presented_as_confirmable() -> None:
    item = project_pending_candidate(
        SimpleNamespace(
            status=CandidateStatus.PROPOSED,
            kind="thesis_revision",
            candidate_id="candidate_expired",
            subject_id="case_1",
            proposed_by_rationale="Old proposal",
            proposed_at=_now() - timedelta(days=2),
            expires_at=_now() - timedelta(days=1),
        ),  # type: ignore[arg-type]
        now=_now(),
    )
    assert item is not None
    assert item.recommended_action == "RECONCILE_EXPIRED_CANDIDATE"
    assert item.severity == AttentionSeverity.ERROR.value
    assert "expired" in item.title.lower()
    assert "CONFIRM" not in item.recommended_action


def test_sort_orders_error_then_overdue_then_attention() -> None:
    now = _now()
    items = (
        _item(key="info", severity=AttentionSeverity.INFO),
        _item(key="attention", severity=AttentionSeverity.ATTENTION),
        _item(
            key="overdue",
            severity=AttentionSeverity.ATTENTION,
            due_at=now - timedelta(hours=1),
        ),
        _item(key="error", severity=AttentionSeverity.ERROR),
    )
    ordered = sort_attention_items(items, now=now)
    assert [item.key for item in ordered] == ["error", "overdue", "attention", "info"]


def test_assemble_counts_before_limit_and_does_not_mix_resolved_history() -> None:
    now = _now()
    review = _item(
        key="agenda-overdue-1",
        tracking=AttentionTrackingKind.REVIEW_ITEM,
        review_item_id="review_item_1",
    )
    live_dup = _item(key="agenda-overdue-1")
    extras = tuple(_item(key=f"live-{index}") for index in range(3))
    digest = assemble_attention_digest(
        generated_at=now,
        request=AttentionQueryInput(limit=2),
        review_items=(review,),
        live_items=(live_dup, *extras),
        coverage=(
            coverage(
                AttentionCoverageSource.CATALYST_AGENDA,
                "PARTIAL",
                limitation_codes=("CATALYST_AGENDA_SYNC_RECEIPT_MISSING",),
            ),
        ),
        extra_limitations=(),
    )
    assert digest.scope == AttentionScope.GLOBAL.value
    assert digest.total_count == 4
    assert digest.total_count_is_lower_bound is True
    assert digest.returned_count == 2
    assert digest.truncated is True
    assert digest.metrics.open_count == 4
    assert "CATALYST_AGENDA_SYNC_RECEIPT_MISSING" in digest.limitations
    assert [item.key for item in digest.items] == ["agenda-overdue-1", "live-0"]
    assert digest.items[0].tracking_kind == AttentionTrackingKind.REVIEW_ITEM.value


def test_assemble_subject_scope_sets_matching_ids() -> None:
    digest = assemble_attention_digest(
        generated_at=_now(),
        request=AttentionQueryInput(case_id="case_1", limit=25),
        review_items=(),
        live_items=(
            _item(key="keep", subject_id="case_1"),
            _item(key="drop", subject_id=None),
        ),
        coverage=(),
    )
    assert digest.scope == AttentionScope.SUBJECT.value
    assert digest.total_count_is_lower_bound is False
    assert digest.subject_id == digest.case_id == "case_1"
    assert [item.key for item in digest.items] == ["keep"]


def test_project_review_item_preserves_console_action_and_builds_read() -> None:
    item = ReviewItemDTO(
        review_item_id="review_item_1",
        source_key="agenda-overdue-agenda_1",
        source_type="CATALYST_AGENDA",
        source_ref="agenda_1",
        subject_id="case_1",
        title="Catalyst outcome overdue · Event",
        detail="The event window passed without a linked durable outcome fact.",
        severity="ATTENTION",
        recommended_action="LINK_OUTCOME_OR_REVISE",
        href="/agenda#agenda-agenda_1",
        status="OPEN",
        active_at_source=True,
        first_seen_at=_now(),
        last_seen_at=_now(),
        due_at=_now() - timedelta(days=1),
        resolved_at=None,
        resolved_by=None,
        resolution_note=None,
        resolution_ref=None,
        occurrence_count=2,
        version=1,
    )
    projected = project_review_item(item)
    assert projected is not None
    assert projected.tracking_kind == AttentionTrackingKind.REVIEW_ITEM.value
    assert projected.recommended_action == "LINK_OUTCOME_OR_REVISE"
    assert projected.next_read is not None
    assert projected.next_read.tool == "research_memory_get"
    assert projected.next_read.request == {
        "operation": "agenda",
        "agenda_item_id": "agenda_1",
    }


def test_project_unresolved_broker_unknown_is_error() -> None:
    item = project_unresolved_broker(
        order_intent_id="order_1",
        status="UNKNOWN",
        symbol="SGOV",
    )
    assert item.severity == AttentionSeverity.ERROR.value
    assert item.key == "broker-unresolved-order_1"
    assert item.next_read is not None
    assert item.next_read.request["operation"] == "status"


def test_project_data_quality_issue_keeps_existing_action() -> None:
    issue = DataQualityIssueDTO(
        code="ACCOUNT_SNAPSHOT_DEGRADED",
        severity=HealthState.DEGRADED,
        scope="account_snapshot",
        subject_ref="schwab_1",
        observed_at=_now(),
        detail="The latest durable account snapshot carries degraded=true.",
        recommended_action_code="SYNC_ACCOUNTS",
    )
    item = project_data_quality_issue(issue)
    assert item.recommended_action == "SYNC_ACCOUNTS"
    assert item.key == "data-quality-ACCOUNT_SNAPSHOT_DEGRADED-schwab_1"
    assert item.next_read is not None
    assert item.next_read.tool == "system_health"
