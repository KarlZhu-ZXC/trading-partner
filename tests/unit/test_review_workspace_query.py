"""Read-only and coverage contracts for the Console review workspace query."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from application.ports.clock import Clock
from application.ports.research_attention_reader import (
    ResearchAttentionReader,
    ResearchAttentionSummary,
)
from application.services.catalyst_agenda_service import CatalystAgendaService
from application.services.judgment_scorecard_service import JudgmentScorecardService
from application.services.review_item_refresh_service import (
    ReviewItemRefreshBatch,
    ReviewItemRefreshService,
)
from application.services.review_item_service import ReviewItemService
from application.services.review_workspace_query_service import ReviewWorkspaceQueryService
from application.services.trade_retro_service import TradeRetroService
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection

_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
_SOURCE_TYPES = frozenset(
    {
        ReviewItemSourceType.DECISION_REVIEW_DUE,
        ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
        ReviewItemSourceType.AGENT_PENDING_ACTION,
        ReviewItemSourceType.BROKER_ORDER_INTENT,
    }
)


@dataclass(frozen=True)
class _Dumpable:
    payload: dict[str, Any]

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


def _batch(*projections: ReviewItemProjection) -> ReviewItemRefreshBatch:
    return ReviewItemRefreshBatch(
        projections=tuple(projections),
        observed_source_types=_SOURCE_TYPES,
        authoritative_source_refs=frozenset(),
        fully_observed_source_types=_SOURCE_TYPES,
    )


def _projection(key: str) -> ReviewItemProjection:
    return ReviewItemProjection(
        source_key=key,
        source_type=ReviewItemSourceType.DECISION_REVIEW_DUE,
        source_ref=f"decision-{key}",
        title=f"Review {key}",
        detail="Review fixture",
        severity=ReviewItemSeverity.ATTENTION,
        recommended_action="review",
        href="/review",
        subject_id="case_query",
    )


def _build_service(
    *,
    batch: ReviewItemRefreshBatch,
    known_keys: frozenset[str] = frozenset(),
    research_error: bool = False,
) -> tuple[ReviewWorkspaceQueryService, MagicMock, MagicMock, MagicMock]:
    clock = MagicMock(spec=Clock)
    clock.now.return_value = _NOW

    research = MagicMock(spec=ResearchAttentionReader)
    if research_error:
        research.pending_by_subject.side_effect = RuntimeError("fixture failure")
    else:
        research.pending_by_subject.return_value = (
            ResearchAttentionSummary(
                subject_id="case_query",
                title="Query fixture",
                pending_count=2,
            ),
        )

    agenda = MagicMock(spec=CatalystAgendaService)
    agenda.query.return_value = _Dumpable({"ok": True, "data": {"items": []}})
    retro = MagicMock(spec=TradeRetroService)
    retro.history.return_value = _Dumpable({"ok": True, "data": {"runs": []}})
    scorecards = MagicMock(spec=JudgmentScorecardService)
    scorecards.history.return_value = _Dumpable(
        {"ok": True, "data": {"runs": [], "has_more": False}}
    )

    refresh = MagicMock(spec=ReviewItemRefreshService)
    refresh.collect.return_value = batch
    refresh.apply.return_value = ()

    review_items = MagicMock(spec=ReviewItemService)
    review_items.list_open.return_value = ()
    review_items.list_recent.return_value = ()
    review_items.metrics.return_value = _Dumpable(
        {
            "open_count": 0,
            "acknowledged_count": 0,
        }
    )
    review_items.latest_observed_at.return_value = None
    review_items.known_active_source_keys.return_value = known_keys

    service = ReviewWorkspaceQueryService(
        clock=clock,
        research=research,
        agenda=agenda,
        retro=retro,
        scorecards=scorecards,
        refresh=refresh,
        review_items=review_items,
    )
    return service, refresh, review_items, research


def test_collect_reports_failed_and_truncated_sources_as_partial() -> None:
    batch = _batch()
    service, refresh, _review_items, _research = _build_service(batch=batch)
    service._agenda.query.return_value = _Dumpable(  # type: ignore[attr-defined]
        {"ok": True, "data": {"items": [{} for _ in range(200)]}}
    )
    service._retro.history.side_effect = RuntimeError("retro unavailable")  # type: ignore[attr-defined]
    service._scorecards.history.return_value = _Dumpable(  # type: ignore[attr-defined]
        {"ok": True, "data": {"runs": [], "has_more": True}}
    )

    result = service.collect()

    assert result.coverage["agenda"] == "PARTIAL"
    assert result.coverage["retro"] == "UNAVAILABLE"
    assert result.coverage["scorecards"] == "PARTIAL"
    assert result.coverage["DECISION_REVIEW_DUE"] == "COMPLETE"
    assert result.coverage["OBSERVATION_REVIEW_DUE"] == "COMPLETE"
    refresh.collect.assert_called_once()
    agenda, retro, scorecards = refresh.collect.call_args.args
    assert len(agenda["data"]["items"]) == 200
    assert retro["ok"] is False
    assert scorecards["data"]["has_more"] is True


def test_read_is_read_only_and_filters_only_exact_active_source_keys() -> None:
    batch = _batch(
        _projection("active-key"),
        _projection("unseen-key"),
        _projection("reappeared-key"),
    )
    service, refresh, review_items, _research = _build_service(
        batch=batch,
        known_keys=frozenset({"active-key"}),
    )

    result = service.read()

    assert [row["key"] for row in result["workflow_attention_projection"]] == [
        "active-key",
        "unseen-key",
        "reappeared-key",
    ]
    assert [row["key"] for row in result["workflow_attention"]] == [
        "unseen-key",
        "reappeared-key",
    ]
    review_items.known_active_source_keys.assert_called_once_with(
        ("active-key", "unseen-key", "reappeared-key")
    )
    refresh.apply.assert_not_called()
    review_items.reconcile.assert_not_called()


def test_read_does_not_treat_failed_research_as_empty_complete() -> None:
    service, _refresh, _review_items, _research = _build_service(
        batch=_batch(),
        research_error=True,
    )

    result = service.read()

    assert result["research_attention"] == []
    assert result["review_coverage"]["sources"]["research"] == "UNAVAILABLE"
    assert result["review_coverage"]["complete"] is False


def test_notes_read_keeps_live_recovery_without_archive_or_materialized_queue_reads() -> None:
    service, refresh, items, research = _build_service(batch=_batch())
    projection = ReviewItemProjection(
        source_key="broker-order-intent-order_1",
        source_type=ReviewItemSourceType.BROKER_ORDER_INTENT,
        source_ref="order_1", title="Unknown order outcome", detail="Inspect receipt",
        severity=ReviewItemSeverity.ATTENTION, recommended_action="INSPECT", href="/capabilities",
    )
    refresh.collect_operations.return_value = ReviewItemRefreshBatch(
        projections=(projection,),
        observed_source_types=frozenset({ReviewItemSourceType.BROKER_ORDER_INTENT}),
        fully_observed_source_types=frozenset(), authoritative_source_refs=frozenset(),
    )
    result = service.read_operations()
    assert result["workflow_attention_projection"][0]["source_ref"] == "order_1"
    assert result["review_coverage"]["scope"] == "operations"
    assert result["review_coverage"]["complete"] is False
    assert result["review_coverage"]["sources"] == {
        "AGENT_PENDING_ACTION": "UNAVAILABLE", "BROKER_ORDER_INTENT": "PARTIAL",
    }
    refresh.collect.assert_not_called()
    refresh.apply.assert_not_called()
    assert items.mock_calls == research.mock_calls == []
    assert service._agenda.mock_calls == []  # type: ignore[attr-defined]
    assert service._retro.mock_calls == []  # type: ignore[attr-defined]
    assert service._scorecards.mock_calls == []  # type: ignore[attr-defined]
