"""One no-live-side-effect Phase 4 Decision-to-Review acceptance flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine

from application.dto.account_transactions import (
    TradeCycleQueryInput,
)
from application.dto.activity_annotations import ActivityAnnotationAppendInput
from application.dto.behavior import BehaviorSummaryQueryInput
from application.dto.behavior_review import BehaviorActionInputDTO, BehaviorReviewRunInput
from application.dto.broker_execution import (
    BrokerOrderIntentPreviewInput,
    BrokerOrderSubmitInput,
)
from application.dto.performance import PerformanceSeriesQueryInput
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.activity_annotation_service import ActivityAnnotationService
from application.services.behavior_review_service import BehaviorReviewService
from application.services.broker_order_service import BrokerOrderService
from application.services.daily_equity_materialization_service import (
    DailyEquityMaterializationService,
)
from domain.behavior_review.enums import BehaviorActionStatus, BehaviorReviewPeriodKind
from domain.common.enums import (
    ConfirmationMode,
    DecisionScenario,
    DecisionType,
    VendorId,
)
from domain.common.ids import EntityIdPrefix
from domain.execution.models import (
    BrokerExecutionAccountState,
    BrokerOrderSubmission,
    BrokerQuoteObservation,
)
from domain.performance.enums import DailyEquityMaterializationMode
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountEnvironment,
    AccountTransactionKind,
    AccountTransactionSide,
    TradeCycleClassification,
)
from domain.portfolio.models import (
    AccountActivityCoverageReceipt,
    AccountSnapshot,
    AccountTransaction,
)
from domain.research.models import DecisionRecord
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.activity_annotation_repository import (
    SqlAlchemyActivityAnnotationRepository,
)
from infrastructure.persistence.behavior_review_repository import (
    SqlAlchemyBehaviorReviewRepository,
)
from infrastructure.persistence.broker_order_repository import (
    SqlAlchemyBrokerOrderRepository,
)
from infrastructure.persistence.daily_equity_repository import (
    SqlAlchemyDailyEquityRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.trade_cycle_override_repository import (
    SqlAlchemyTradeCycleOverrideRepository,
)
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.mcp.tools.portfolio import build_portfolio_adapters

PERIOD_START = datetime(2026, 8, 3, tzinfo=UTC)
BUY_AT = datetime(2026, 8, 4, 14, tzinfo=UTC)
SELL_AT = datetime(2026, 8, 7, 14, tzinfo=UTC)
PERIOD_END = datetime(2026, 8, 10, tzinfo=UTC)
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000001"
DECISION_ID = "decision_00000000-0000-7000-8000-000000000001"
PLAN_ID = "trade_plan_00000000-0000-7000-8000-000000000001"
INSTRUMENT_ID = "equity:US:AAPL"
ACCOUNT_REF = "account_phase4"


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, prefix: EntityIdPrefix) -> str:
        self.count += 1
        return f"{prefix.value}_00000000-0000-7000-8000-{self.count:012d}"


class _Audit:
    def append(self, *_args: object, **_kwargs: object) -> str:
        return "audit_phase4"


class _Broker:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.place_calls = 0

    async def get_account_state(
        self, *, account_ref: str, observed_at: datetime
    ) -> BrokerExecutionAccountState:
        return BrokerExecutionAccountState(
            account_ref=account_ref,
            observed_at=observed_at,
            cash_balance=Decimal("10000"),
            margin_balance=Decimal(0),
            open_buy_order_reserve=Decimal(0),
            positions={},
        )

    async def place_order(
        self, *, account_ref: str, order_payload: dict[str, object]
    ) -> BrokerOrderSubmission:
        del account_ref, order_payload
        self.place_calls += 1
        return BrokerOrderSubmission("schwab-order-1", self.clock.now(), 201)


class _Quotes:
    async def get_quote(
        self, *, instrument_id: str, as_of: datetime
    ) -> BrokerQuoteObservation:
        return BrokerQuoteObservation(
            instrument_id=instrument_id,
            symbol="AAPL",
            quote_at=as_of,
            bid=Decimal("99.9"),
            ask=Decimal("100"),
            last=Decimal("100"),
            source="fake_schwab",
        )


def _decision() -> DecisionRecord:
    return DecisionRecord(
        decision_id=DECISION_ID,
        subject_id=SUBJECT_ID,
        decision_type=DecisionType.INITIATE_INTENT,
        title="Open only after right-side confirmation",
        rationale="strategy_v1 confirmed structure and bounded loss.",
        decided_at=BUY_AT - timedelta(hours=2),
        recorded_at=BUY_AT - timedelta(hours=2),
        decided_by="user",
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        primary_instrument_id=INSTRUMENT_ID,
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=1,
        strategy_code="strategy_v1",
        strategy_version="1",
        scenario=DecisionScenario.PULLBACK,
        trade_plan_id=PLAN_ID,
        trade_plan_version=1,
        review_due_at=PERIOD_END,
    )


class _ResearchUow:
    def __init__(self, decision: DecisionRecord) -> None:
        self.subjects = SimpleNamespace(get=lambda _subject_id: object())
        self.decisions = SimpleNamespace(
            get=lambda _decision_id: decision,
            list_by_subject=lambda _subject_id: (decision,),
        )
        self.trade_plans = SimpleNamespace(
            get_version=lambda _plan_id, _version: SimpleNamespace(
                subject_id=SUBJECT_ID,
                instrument_id=INSTRUMENT_ID,
            )
        )

    def __enter__(self) -> _ResearchUow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _snapshot(snapshot_id: str, at: datetime, equity: str) -> AccountSnapshot:
    return AccountSnapshot(
        snapshot_id=snapshot_id,
        account_ref=ACCOUNT_REF,
        provider=VendorId.SCHWAB,
        environment=AccountEnvironment.REAL,
        base_currency="USD",
        account_as_of=at,
        fetched_at=at,
        cash=Decimal("5000"),
        buying_power=None,
        net_assets=Decimal(equity),
        margin_used=None,
        positions=(),
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )


def _trade(
    transaction_id: str,
    side: AccountTransactionSide,
    at: datetime,
    price: str,
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=transaction_id,
        account_ref=ACCOUNT_REF,
        provider=VendorId.SCHWAB,
        instrument_id=INSTRUMENT_ID,
        kind=AccountTransactionKind.TRADE,
        side=side,
        quantity=Decimal(10),
        price=Decimal(price),
        fees=Decimal(1),
        currency="USD",
        occurred_at=at,
    )


@pytest.mark.asyncio
async def test_decision_order_fill_cycle_performance_and_review_close_the_loop() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    clock = _Clock(BUY_AT - timedelta(minutes=5))
    ids = _Ids()
    redactor = DefaultSecretRedactor()
    decision = _decision()

    def uow_factory() -> _ResearchUow:
        return _ResearchUow(decision)

    broker_repository = SqlAlchemyBrokerOrderRepository(engine)
    broker = _Broker(clock)
    broker_service = BrokerOrderService(
        broker_repository,
        broker,
        _Quotes(),
        _Audit(),
        clock,
        ids,
        redactor,
        uow_factory,  # type: ignore[arg-type]
    )
    preview = await broker_service.preview(
        BrokerOrderIntentPreviewInput(
            account_ref=ACCOUNT_REF,
            instrument_id=INSTRUMENT_ID,
            instruction="BUY",
            quantity=10,
            order_type="LIMIT",
            limit_price=Decimal("100"),
            idempotency_key="phase4-order-preview",
            case_id=SUBJECT_ID,
            decision_id=DECISION_ID,
            trade_plan_id=PLAN_ID,
            trade_plan_version=1,
        )
    )
    assert preview.ok and preview.data is not None
    submitted = await broker_service.submit(
        BrokerOrderSubmitInput(
            order_intent_id=preview.data.order_intent_id,
            idempotency_key="phase4-order-submit",
            confirmed_by="user",
            submitted_via="mcp_chat",
            authorization_note="Submit only this fake acceptance-test order.",
        )
    )
    assert submitted.ok and broker.place_calls == 1

    transactions = SqlAlchemyAccountTransactionRepository(engine)
    buy = _trade("fill-buy", AccountTransactionSide.BUY, BUY_AT, "100")
    sell = _trade("fill-sell", AccountTransactionSide.SELL, SELL_AT, "110")
    transactions.append_many((buy, sell))
    annotations = SqlAlchemyActivityAnnotationRepository(engine)
    annotation_service = ActivityAnnotationService(
        transactions,
        annotations,
        uow_factory,  # type: ignore[arg-type]
        clock,
        ids,
        broker_orders=broker_repository,
    )
    for transaction, order_intent_id, key in (
        (buy, preview.data.order_intent_id, "phase4-link-buy"),
        (sell, None, "phase4-link-sell"),
    ):
        annotation_service.append_revision(
            ActivityAnnotationAppendInput(
                provider=VendorId.SCHWAB,
                account_ref=ACCOUNT_REF,
                provider_transaction_id=transaction.provider_transaction_id,
                status="LINKED_DECISION_PLAN",
                classification="ACTIVE_TRADE",
                order_intent_id=order_intent_id,
                decision_id=DECISION_ID,
                trade_plan_id=PLAN_ID,
                trade_plan_version=1,
                subject_id=SUBJECT_ID,
                actor="user",
                authorization_note="Link the exact fake fill to its recorded intent.",
                idempotency_key=key,
                expected_version=0,
            )
        )

    snapshots = SqlAlchemyAccountSnapshotRepository(engine)
    start_snapshot = _snapshot("snapshot_phase4_start", PERIOD_START, "10000")
    end_snapshot = _snapshot("snapshot_phase4_end", PERIOD_END, "10100")
    snapshots.append_account(start_snapshot)
    snapshots.append_account(end_snapshot)
    transactions.append_coverage(
        (
            AccountActivityCoverageReceipt(
                receipt_id="activity_coverage_phase4",
                provider=VendorId.SCHWAB,
                account_ref=ACCOUNT_REF,
                requested_start=PERIOD_START,
                requested_end=PERIOD_END,
                effective_start=PERIOD_START,
                effective_end=PERIOD_END,
                earliest_event_at=BUY_AT,
                latest_event_at=SELL_AT,
                event_count=2,
                inserted_count=2,
                duplicate_count=0,
                snapshot_count=2,
                earliest_snapshot_at=PERIOD_START,
                latest_snapshot_at=PERIOD_END,
                mapping_version="phase4_e2e_v1",
                supported_kinds=tuple(AccountTransactionKind),
                unavailable_kinds=(),
                status=AccountActivityCoverageStatus.COMPLETE,
                gap_codes=(),
                fetched_at=PERIOD_END,
            ),
        )
    )
    clock.value = PERIOD_END
    daily_repository = SqlAlchemyDailyEquityRepository(engine)
    daily_service = DailyEquityMaterializationService(
        daily_repository,
        activation_repository=daily_repository,
        clock=clock,
    )
    daily_service.activate(
        journal_activation_at=PERIOD_START,
        actor="user",
        idempotency_key="phase4-activation",
    )
    daily_receipt = daily_service.materialize(
        snapshots=(start_snapshot, end_snapshot),
        transactions=(buy, sell),
        mode=DailyEquityMaterializationMode.PERSIST,
    )
    assert daily_receipt.persisted and daily_receipt.inserted_count == 2

    overrides = SqlAlchemyTradeCycleOverrideRepository(engine)
    coordinator = AccountTransactionCoordinator(
        {},
        transactions,
        snapshots,
        clock,
        ids,
        redactor,
        uow_factory,  # type: ignore[arg-type]
        annotations,
        overrides,
        daily_repository,
    )
    cycles = coordinator.get_trade_cycles(
        TradeCycleQueryInput(
            account_refs=(ACCOUNT_REF,),
            instrument_ids=(INSTRUMENT_ID,),
            start=PERIOD_START,
            end=PERIOD_END,
            limit=20,
        )
    )
    assert cycles.ok and cycles.data is not None
    assert len(cycles.data.cycles) == 1
    cycle = cycles.data.cycles[0]
    assert cycle.status == "CLOSED"
    assert cycle.classification is TradeCycleClassification.ACTIVE_TRADE
    assert cycle.net_realized_pnl == Decimal(98)

    returns = coordinator.get_performance_series(
        PerformanceSeriesQueryInput(start=PERIOD_START, end=PERIOD_END)
    )
    assert returns.ok and returns.data is not None
    assert len(returns.data.series) == 1
    assert returns.data.series[0].twr == Decimal("0.01")
    assert returns.data.series[0].input_cycle_ids == (cycle.cycle_id,)

    behavior = coordinator.get_behavior_summary(
        BehaviorSummaryQueryInput(
            case_id=SUBJECT_ID,
            account_refs=(ACCOUNT_REF,),
            instrument_ids=(INSTRUMENT_ID,),
            strategy_code="strategy_v1",
            classifications=(TradeCycleClassification.ACTIVE_TRADE,),
            minimum_sample_size=1,
        )
    )
    assert behavior.ok and behavior.data is not None
    assert behavior.data.closed_active_trade_cycles.numerator == 1
    assert behavior.data.plan_coverage.value == Decimal(1)
    assert behavior.data.pre_fill_decision_coverage.value == Decimal(1)

    behavior_reviews = BehaviorReviewService(
        SqlAlchemyBehaviorReviewRepository(engine), clock, ids
    )
    action = BehaviorActionInputDTO(
        action_text="Keep the next entry bound to an exact pre-fill Decision.",
        cycle_ids=(cycle.cycle_id,),
        decision_ids=(DECISION_ID,),
    )
    first_review = behavior_reviews.run(
        BehaviorReviewRunInput(
            period_kind=BehaviorReviewPeriodKind.WEEKLY,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            strategy_code="strategy_v1",
            instrument_ids=(INSTRUMENT_ID,),
            cycle_ids=(cycle.cycle_id,),
            decision_ids=(DECISION_ID,),
            subject_ids=(SUBJECT_ID,),
            action_items=(action,),
            idempotency_key="phase4-review-week-1",
        )
    )
    clock.value = PERIOD_END + timedelta(days=7)
    second_review = behavior_reviews.run(
        BehaviorReviewRunInput(
            period_kind=BehaviorReviewPeriodKind.WEEKLY,
            period_start=PERIOD_END,
            period_end=PERIOD_END + timedelta(days=7),
            strategy_code="strategy_v1",
            instrument_ids=(INSTRUMENT_ID,),
            action_items=(action,),
            idempotency_key="phase4-review-week-2",
        )
    )
    assert first_review.action_observations[0].status is BehaviorActionStatus.NEW
    assert second_review.action_observations[0].status is BehaviorActionStatus.PERSISTENT

    research_timeline = SimpleNamespace(
        get_timeline=lambda **_kwargs: SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                items=(
                    SimpleNamespace(
                        entity_type=SimpleNamespace(value="decision"),
                        entity_id=DECISION_ID,
                        subject_id=SUBJECT_ID,
                        title=decision.title,
                        summary=decision.rationale,
                        occurred_at=decision.decided_at,
                        visible_at=decision.recorded_at,
                        instrument_ids=(INSTRUMENT_ID,),
                        source_name="user",
                    ),
                )
            ),
        )
    )
    container = SimpleNamespace(
        services=SimpleNamespace(
            research_timeline=research_timeline,
            account_transactions=coordinator,
            activity_annotations=annotation_service,
            broker_orders=broker_service,
        ),
        context=SimpleNamespace(clock=clock, id_generator=ids, secret_redactor=redactor),
    )
    timeline = build_portfolio_adapters(container).portfolio_get_journal_timeline(
        case_id=SUBJECT_ID,
        instrument_id=INSTRUMENT_ID,
        start=PERIOD_START,
        end=PERIOD_END + timedelta(days=7),
        limit=100,
    )
    assert timeline["ok"] is True
    items: list[dict[str, Any]] = timeline["data"]["items"]
    assert any(item["source_type"] == "RESEARCH" for item in items)
    assert any(item["source_type"] == "ORDER_INTENT" for item in items)
    assert any(
        item["source_type"] == "BROKER_ACTIVITY"
        and item["source_id"] == "fill-buy"
        and item["quality_status"] == "FULL_CHAIN"
        for item in items
    )
