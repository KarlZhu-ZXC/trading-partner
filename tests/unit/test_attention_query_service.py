from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from application.dto.attention import AttentionQueryInput
from application.dto.review_item import ReviewItemDTO
from application.services.attention_query_service import AttentionQueryService
from domain.attention.enums import AttentionCoverageState
from domain.common.errors import DataContractError


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 17, 12, tzinfo=UTC)


class _ReviewItems:
    def __init__(self) -> None:
        self.reconcile_calls = 0
        self.open: tuple[ReviewItemDTO, ...] = ()

    def reconcile(self, *args: object, **kwargs: object) -> None:
        self.reconcile_calls += 1
        raise DataContractError("Attention query must not reconcile")

    def list_open(
        self, *, subject_id: str | None = None, limit: int = 100
    ) -> tuple[ReviewItemDTO, ...]:
        return self.open

    def latest_observed_at(self) -> datetime | None:
        return None


class _Candidates:
    def list(self, **kwargs: object) -> tuple[()]:
        return ()


class _Uow:
    def __init__(self) -> None:
        self.candidates = _Candidates()

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _EmptyEnvelope:
    def __init__(self, data: object) -> None:
        self.ok = True
        self.data = data


def _service(
    review_items: _ReviewItems | None = None,
) -> tuple[AttentionQueryService, _ReviewItems]:
    reviews = review_items or _ReviewItems()
    quality = SimpleNamespace(
        check=lambda: _EmptyEnvelope(
            SimpleNamespace(
                issues=(),
                monitors=(),
                limitations=("CATALYST_AGENDA_SYNC_RECEIPT_MISSING",),
            )
        )
    )
    service = AttentionQueryService(
        clock=_Clock(),
        review_items=reviews,  # type: ignore[arg-type]
        research_uow_factory=lambda: _Uow(),
        catalyst_agenda=SimpleNamespace(
            query=lambda request: _EmptyEnvelope(
                SimpleNamespace(items=(), limitation_codes=(), has_more=False)
            )
        ),  # type: ignore[arg-type]
        trade_retro=SimpleNamespace(
            history=lambda request: _EmptyEnvelope(SimpleNamespace(runs=()))
        ),  # type: ignore[arg-type]
        scorecards=SimpleNamespace(
            history=lambda request: _EmptyEnvelope(SimpleNamespace(runs=(), has_more=False))
        ),  # type: ignore[arg-type]
        data_quality=quality,  # type: ignore[arg-type]
        broker_orders=SimpleNamespace(list_unresolved=lambda limit=100: ()),  # type: ignore[arg-type]
        agent_pending_actions=SimpleNamespace(list_unresolved=lambda now, limit=100: ()),  # type: ignore[arg-type]
    )
    return service, reviews


def test_list_digest_is_read_only_and_keeps_missing_sync_limitation() -> None:
    service, reviews = _service()
    digest = service.list_digest(AttentionQueryInput())
    assert reviews.reconcile_calls == 0
    assert digest.mode == "durable_only_read"
    assert "CATALYST_AGENDA_SYNC_RECEIPT_MISSING" in digest.limitations
    assert digest.total_count == 0


def test_source_failure_does_not_clear_other_sources() -> None:
    service, reviews = _service()

    def boom(**kwargs: object) -> None:
        raise RuntimeError("broker down")

    service._broker_orders = SimpleNamespace(list_unresolved=boom)  # type: ignore[method-assign]
    digest = service.list_digest(AttentionQueryInput())
    assert reviews.reconcile_calls == 0
    broker = next(item for item in digest.coverage if item.source == "broker_orders")
    assert broker.state == "UNAVAILABLE"
    review_coverage = next(item for item in digest.coverage if item.source == "review_items")
    assert review_coverage.state == "COMPLETE"


def test_health_summary_does_not_impersonate_full_inbox() -> None:
    service, reviews = _service()
    summary = service.health_summary()
    assert reviews.reconcile_calls == 0
    assert summary.basis == "materialized_review_items"
    assert summary.live_projections_not_included is True
    assert summary.coverage_status == AttentionCoverageState.UNKNOWN.value
    assert summary.catalyst_sync_receipt_missing is True
    assert summary.open_review_item_count == 0
