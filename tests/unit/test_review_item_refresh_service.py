from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from application.services.review_item_refresh_service import ReviewItemRefreshService
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return NOW


class _Decisions:
    def __init__(self, values: tuple[Any, ...] = (), error: Exception | None = None) -> None:
        self.values = values
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def list_review_due(self, **kwargs: Any) -> tuple[Any, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.values


class _ObservationReviews:
    def __init__(
        self,
        values: tuple[ReviewItemProjection, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.values = values
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def pending_projections(self, **kwargs: Any) -> tuple[ReviewItemProjection, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.values


class _AgentActions:
    def __init__(self, values: tuple[Any, ...] = (), error: Exception | None = None) -> None:
        self.values = values
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def list_unresolved(self, **kwargs: Any) -> tuple[Any, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.values


class _BrokerOrders:
    def __init__(self, values: tuple[Any, ...] = (), error: Exception | None = None) -> None:
        self.values = values
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def list_unresolved(self, **kwargs: Any) -> tuple[Any, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.values


class _ReviewItems:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[ReviewItemProjection, ...], dict[str, Any]]] = []

    def reconcile(
        self,
        projections: tuple[ReviewItemProjection, ...],
        **kwargs: Any,
    ) -> tuple[Any, ...]:
        self.calls.append((projections, kwargs))
        return ()


def _service(
    *,
    decisions: _Decisions | None = None,
    observations: _ObservationReviews | None = None,
    agents: _AgentActions | None = None,
    brokers: _BrokerOrders | None = None,
) -> tuple[ReviewItemRefreshService, _Clock, _ReviewItems]:
    clock = _Clock()
    review_items = _ReviewItems()
    return (
        ReviewItemRefreshService(
            clock,
            decisions or _Decisions(),
            observations or _ObservationReviews(),
            agents or _AgentActions(),
            brokers or _BrokerOrders(),
            review_items,
        ),
        clock,
        review_items,
    )


def _observation_projection(*, subject_id: str | None = None) -> ReviewItemProjection:
    return ReviewItemProjection(
        source_key="OBSERVATION_REVIEW_DUE:review_1",
        source_type=ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
        source_ref="note_revision_1",
        subject_id=subject_id,
        title="View review due · Note",
        detail="Review the Observation.",
        severity=ReviewItemSeverity.ATTENTION,
        recommended_action="REVIEW_OBSERVATION",
        href="/decision-workbench#notes",
    )


def _global_sources() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"ok": True, "data": {"items": []}},
        {"ok": True, "data": {"runs": []}},
        {"ok": True, "data": {"runs": []}},
    )


def test_collect_is_read_only_and_apply_forwards_the_immutable_batch() -> None:
    decisions = _Decisions(
        (
            SimpleNamespace(
                decision_id="decision_1",
                subject_id="case_1",
                title="Decision",
                review_due_at=NOW,
            ),
        )
    )
    observations = _ObservationReviews((_observation_projection(),))
    agents = _AgentActions(
        (
            SimpleNamespace(
                action_id="action_1",
                status=SimpleNamespace(value="PENDING"),
                capability="watchlist_manage",
                operation="add",
            ),
        )
    )
    brokers = _BrokerOrders(
        (
            SimpleNamespace(
                order_intent_id="order_1",
                status="SUBMITTING",
                symbol="SGOV",
                instrument_id="etf:US:SGOV",
                provider_status=None,
            ),
        )
    )
    service, _clock, review_items = _service(
        decisions=decisions,
        observations=observations,
        agents=agents,
        brokers=brokers,
    )
    agenda, retro, scorecards = _global_sources()

    batch = service.collect(agenda, retro, scorecards)

    assert review_items.calls == []
    assert [item.source_type for item in batch.projections] == [
        ReviewItemSourceType.DECISION_REVIEW_DUE,
        ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
        ReviewItemSourceType.AGENT_PENDING_ACTION,
        ReviewItemSourceType.BROKER_ORDER_INTENT,
    ]
    assert batch.subject_scope is None
    service.apply(batch)
    assert len(review_items.calls) == 1
    projections, kwargs = review_items.calls[0]
    assert projections == batch.projections
    assert kwargs == {
        "observed_source_types": batch.observed_source_types,
        "authoritative_source_refs": batch.authoritative_source_refs,
        "fully_observed_source_types": batch.fully_observed_source_types,
        "subject_scope": None,
    }


def test_collect_failure_and_bound_rules_never_mark_a_source_fully_observed() -> None:
    decisions = _Decisions(error=RuntimeError("decision read failed"))
    observations = _ObservationReviews(error=RuntimeError("observation read failed"))
    agents = _AgentActions(
        tuple(
            SimpleNamespace(
                action_id=f"action_{index}",
                status=SimpleNamespace(value="PENDING"),
                capability="watchlist_manage",
                operation="add",
            )
            for index in range(100)
        )
    )
    brokers = _BrokerOrders(
        (
            SimpleNamespace(
                order_intent_id="order_1",
                status="UNKNOWN",
                symbol="SGOV",
                instrument_id="etf:US:SGOV",
                provider_status=None,
            ),
        )
    )
    service, _clock, _review_items = _service(
        decisions=decisions,
        observations=observations,
        agents=agents,
        brokers=brokers,
    )

    batch = service.collect(*_global_sources())

    assert batch.partial_failures == ()
    assert batch.observed_source_types == frozenset(
        {
            ReviewItemSourceType.CATALYST_AGENDA,
            ReviewItemSourceType.TRADE_RETRO,
            ReviewItemSourceType.SCORECARD_GAP,
            ReviewItemSourceType.AGENT_PENDING_ACTION,
            ReviewItemSourceType.BROKER_ORDER_INTENT,
        }
    )
    assert batch.fully_observed_source_types == frozenset(
        {ReviewItemSourceType.BROKER_ORDER_INTENT}
    )
    assert ReviewItemSourceType.AGENT_PENDING_ACTION not in batch.fully_observed_source_types
    assert decisions.calls[0] == {"now": NOW, "limit": 100}
    assert observations.calls == [{"limit": 100}]
    assert agents.calls == [{"now": NOW, "limit": 100}]
    assert brokers.calls == [{"limit": 100}]


def test_scoped_collect_only_reads_exact_subject_decisions_and_filters_projections() -> None:
    decisions = _Decisions(
        (
            SimpleNamespace(
                decision_id="decision_case_1",
                subject_id="case_1",
                title="Decision",
                review_due_at=NOW,
            ),
        )
    )
    observations = _ObservationReviews((_observation_projection(subject_id="case_2"),))
    agents = _AgentActions(
        (SimpleNamespace(action_id="action_1", status=SimpleNamespace(value="PENDING")),)
    )
    brokers = _BrokerOrders((SimpleNamespace(order_intent_id="order_1", status="UNKNOWN"),))
    service, _clock, _review_items = _service(
        decisions=decisions,
        observations=observations,
        agents=agents,
        brokers=brokers,
    )
    retro = {
        "ok": True,
        "data": {
            "runs": [
                {
                    "run_id": "retro_1",
                    "subject_ids": ["case_1", "case_2"],
                    "findings": [{"code": "MISSING_PLAN"}],
                    "latest_review": None,
                }
            ]
        },
    }

    batch = service.collect(
        {"ok": True, "data": {"items": []}},
        retro,
        {"ok": True, "data": {"runs": []}},
        subject_id="case_1",
    )

    assert batch.subject_scope == "case_1"
    assert all(item.subject_id == "case_1" for item in batch.projections)
    assert [item.source_type for item in batch.projections] == [
        ReviewItemSourceType.TRADE_RETRO,
        ReviewItemSourceType.DECISION_REVIEW_DUE,
    ]
    assert decisions.calls[0] == {"now": NOW, "limit": 100, "subject_id": "case_1"}
    assert observations.calls == []
    assert agents.calls == []
    assert brokers.calls == []


def test_scoped_decision_failure_is_reported_before_apply() -> None:
    service, _clock, review_items = _service(
        decisions=_Decisions(error=RuntimeError("decision read failed")),
    )

    batch = service.collect(*_global_sources(), subject_id="case_1")

    assert batch.partial_failures == ("decision_review_due",)
    assert batch.projections == ()
    assert batch.observed_source_types == frozenset(
        {
            ReviewItemSourceType.CATALYST_AGENDA,
            ReviewItemSourceType.TRADE_RETRO,
            ReviewItemSourceType.SCORECARD_GAP,
        }
    )
    assert review_items.calls == []


def test_operational_read_preserves_unknown_orders_and_does_not_read_research() -> None:
    decisions, observations = _Decisions(), _ObservationReviews()
    agents = _AgentActions(error=RuntimeError("unavailable"))
    brokers = _BrokerOrders((SimpleNamespace(
        order_intent_id="order_unknown", status="UNKNOWN", symbol="SYNTH",
        provider_status=None,
    ),))
    service, _, items = _service(
        decisions=decisions, observations=observations, agents=agents, brokers=brokers,
    )
    batch = service.collect_operations()
    assert decisions.calls == observations.calls == items.calls == []
    assert len(agents.calls) == len(brokers.calls) == 1
    assert [p.source_ref for p in batch.projections] == ["order_unknown"]
    assert batch.observed_source_types == frozenset({ReviewItemSourceType.BROKER_ORDER_INTENT})
    assert batch.fully_observed_source_types == batch.observed_source_types


def test_operational_source_at_bound_remains_partial() -> None:
    agents = _AgentActions(tuple(SimpleNamespace(
        action_id=f"action_{i}", status=SimpleNamespace(value="PENDING"),
        capability="watchlist_manage", operation="add",
    ) for i in range(100)))
    service, _, items = _service(agents=agents)
    batch = service.collect_operations()
    assert len(batch.projections) == 100
    assert ReviewItemSourceType.AGENT_PENDING_ACTION in batch.observed_source_types
    assert ReviewItemSourceType.AGENT_PENDING_ACTION not in batch.fully_observed_source_types
    assert items.calls == []
