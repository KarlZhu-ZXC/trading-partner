"""Phase 4B Unlinked Activity and append-only annotation contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from application.dto.activity_annotations import ActivityAnnotationAppendInput
from application.services.activity_annotation_service import (
    ActivityAnnotationService,
    unlinked_activity_source_key,
)
from application.services.review_item_service import ReviewItemService
from domain.common.enums import AssetType, Market, VendorId
from domain.common.errors import (
    ActivityAnnotationVersionConflict,
    IdempotencyConflict,
    InputValidationError,
    InvalidResearchLink,
)
from domain.common.ids import EntityIdPrefix
from domain.common.values import build_instrument_id
from domain.portfolio.enums import (
    AccountTransactionKind,
    AccountTransactionSide,
    TradeCycleClassification,
)
from domain.portfolio.models import AccountTransaction
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.activity_annotation_repository import (
    SqlAlchemyActivityAnnotationRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 21, 10, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: EntityIdPrefix) -> str:
        self.value += 1
        return f"{prefix.value}_00000000-0000-7000-8000-{self.value:012d}"


def _transaction(transaction_id: str = "tx-1") -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=transaction_id,
        account_ref="acct-1",
        provider=VendorId.BROKER,
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "AAPL"),
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        fees=Decimal("1"),
        currency="USD",
        occurred_at=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )


def _service(
    *,
    transactions: tuple[AccountTransaction, ...] = (_transaction(),),
    research_uow_factory: object | None = None,
    broker_orders: object | None = None,
) -> tuple[ActivityAnnotationService, _Clock, SqlAlchemyAccountTransactionRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    transaction_repository = SqlAlchemyAccountTransactionRepository(engine)
    transaction_repository.append_many(transactions)
    clock = _Clock()
    ids = _Ids()
    review_items = ReviewItemService(SqlAlchemyReviewItemRepository(engine), clock, ids)
    service = ActivityAnnotationService(
        transaction_repository,
        SqlAlchemyActivityAnnotationRepository(engine),
        research_uow_factory,  # type: ignore[arg-type]
        clock,
        ids,
        review_items,
        broker_orders,  # type: ignore[arg-type]
    )
    return service, clock, transaction_repository


def _input(**updates: object) -> ActivityAnnotationAppendInput:
    values: dict[str, object] = {
        "provider": VendorId.BROKER,
        "account_ref": "acct-1",
        "provider_transaction_id": "tx-1",
        "status": "UNPLANNED",
        "note": "External terminal activity.",
        "actor": "user",
        "authorization_note": "User explicitly classified the activity.",
        "idempotency_key": "annotation-1",
    }
    values.update(updates)
    return ActivityAnnotationAppendInput(**values)


def test_unlinked_projection_is_stable_and_replayed_without_duplicate_items() -> None:
    service, _clock, _transactions = _service()

    first = service.list_unlinked()
    second = service.list_unlinked()

    assert first.activities[0].source_key == unlinked_activity_source_key(
        VendorId.BROKER, "acct-1", "tx-1"
    )
    assert second.activities[0].review_item is not None
    assert first.activities[0].review_item is not None
    assert (
        second.activities[0].review_item.review_item_id
        == first.activities[0].review_item.review_item_id
    )


def test_unplanned_and_cash_management_are_append_only_revisions() -> None:
    service, _clock, _transactions = _service()

    first = service.append_revision(_input(classification="ACTIVE_TRADE"))
    cash = service.append_revision(
        _input(
            status="CASH_MANAGEMENT",
            classification="CASH_MANAGEMENT",
            note="SGOV cash sweep.",
            idempotency_key="annotation-2",
            expected_version=first.version,
        )
    )

    assert (first.status, first.version) == ("UNPLANNED", 1)
    assert (cash.status, cash.version) == ("CASH_MANAGEMENT", 2)
    assert first.classification is TradeCycleClassification.ACTIVE_TRADE
    assert cash.classification is TradeCycleClassification.CASH_MANAGEMENT
    assert service.list_revisions(
        provider=VendorId.BROKER,
        account_ref="acct-1",
        provider_transaction_id="tx-1",
    )[-1] == cash


def test_activity_can_link_exact_matching_order_intent() -> None:
    broker_orders = SimpleNamespace(
        get=lambda _order_id: SimpleNamespace(
            account_ref="acct-1", instrument_id="equity:US:AAPL"
        )
    )
    service, _clock, _transactions = _service(broker_orders=broker_orders)

    value = service.append_revision(
        _input(order_intent_id="broker_order_1", classification="ACTIVE_TRADE")
    )

    assert value.order_intent_id == "broker_order_1"
    assert service.list_annotations()[0].order_intent_id == "broker_order_1"


def test_annotation_rejects_unknown_transaction_and_stale_or_conflicting_idempotency() -> None:
    service, _clock, _transactions = _service()

    with pytest.raises(InputValidationError):
        service.append_revision(_input(provider_transaction_id="missing"))
    first = service.append_revision(_input())
    with pytest.raises(ActivityAnnotationVersionConflict):
        service.append_revision(_input(idempotency_key="annotation-2", expected_version=0))
    with pytest.raises(IdempotencyConflict):
        service.append_revision(_input(note="different payload"))
    assert first.version == 1


def test_linked_decision_and_plan_must_share_subject() -> None:
    class _Decisions:
        def get(self, _decision_id: str) -> object:
            return SimpleNamespace(subject_id="case_a")

    class _Plans:
        def get_version(self, _plan_id: str, _version: int) -> object:
            return SimpleNamespace(subject_id="case_b")

    class _Uow:
        decisions = _Decisions()
        trade_plans = _Plans()

        def __enter__(self) -> _Uow:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    service, _clock, _transactions = _service(research_uow_factory=lambda: _Uow())
    with pytest.raises(InvalidResearchLink):
        service.append_revision(
            _input(
                status="LINKED_DECISION_PLAN",
                decision_id="decision_a",
                trade_plan_id="trade_plan_a",
                trade_plan_version=1,
                subject_id="case_a",
            )
        )


def test_limit_or_failed_read_does_not_auto_close_but_annotation_does() -> None:
    transactions = (_transaction("tx-1"), _transaction("tx-2"))
    service, clock, transaction_repository = _service(transactions=transactions)
    page = service.list_unlinked(limit=1)
    assert page.has_more is True
    clock.value += timedelta(hours=1)
    original_list = transaction_repository.list
    transaction_repository.list = lambda **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("provider read failed")
    )
    with pytest.raises(RuntimeError):
        service.list_unlinked(limit=1)
    transaction_repository.list = original_list  # type: ignore[method-assign]
    assert page.activities[0].review_item is not None

    first_id = page.activities[0].provider_transaction_id
    service.append_revision(_input(provider_transaction_id=first_id))
    remaining = service.list_unlinked(limit=10)
    assert all(item.provider_transaction_id != first_id for item in remaining.activities)
