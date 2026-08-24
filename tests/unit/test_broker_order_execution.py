from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from application.dto.broker_execution import (
    BrokerOrderCancelInput,
    BrokerOrderIntentPreviewInput,
    BrokerOrderStatusInput,
    BrokerOrderSubmitInput,
)
from application.services.broker_order_service import BrokerOrderService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.errors import BrokerOrderSubmissionUncertain
from domain.execution.models import (
    BrokerExecutionAccountState,
    BrokerOrderStatusObservation,
    BrokerOrderSubmission,
    BrokerQuoteObservation,
)
from infrastructure.persistence.broker_order_repository import (
    SqlAlchemyBrokerOrderRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, Mapping[str, object]]] = []

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object],
        request_id: str | None = None,
    ) -> str:
        del request_id
        self.events.append((event_type, payload))
        return f"audit_{len(self.events)}"


class _Quote:
    async def get_quote(self, *, instrument_id: str, as_of: datetime) -> BrokerQuoteObservation:
        symbol = instrument_id.rsplit(":", 1)[-1]
        if symbol == "SGOV":
            bid, ask, last = Decimal("99.99"), Decimal("100"), Decimal("100")
        else:
            bid, ask, last = Decimal("312.90"), Decimal("313.10"), Decimal("313.00")
        return BrokerQuoteObservation(
            instrument_id=instrument_id,
            symbol=symbol,
            quote_at=as_of,
            bid=bid,
            ask=ask,
            last=last,
            source="schwab",
        )


class _MovingSgovQuote(_Quote):
    def __init__(self) -> None:
        self.calls = 0

    async def get_quote(self, *, instrument_id: str, as_of: datetime) -> BrokerQuoteObservation:
        self.calls += 1
        value = await super().get_quote(instrument_id=instrument_id, as_of=as_of)
        if instrument_id == "etf:US:SGOV" and self.calls > 1:
            return BrokerQuoteObservation(
                instrument_id=instrument_id,
                symbol="SGOV",
                quote_at=as_of,
                bid=Decimal("100.02"),
                ask=Decimal("100.03"),
                last=Decimal("100.02"),
                source="schwab",
            )
        return value


class _Provider:
    def __init__(self, *, uncertain: bool = False, cash: str = "10000") -> None:
        self.uncertain = uncertain
        self.cash = Decimal(cash)
        self.place_calls = 0
        self.cancel_calls = 0
        self.payloads: list[Mapping[str, object]] = []
        self.order_status = "WORKING"

    async def get_account_state(
        self, *, account_ref: str, observed_at: datetime
    ) -> BrokerExecutionAccountState:
        return BrokerExecutionAccountState(
            account_ref=account_ref,
            observed_at=observed_at,
            cash_balance=self.cash,
            margin_balance=Decimal(0),
            open_buy_order_reserve=Decimal(0),
            positions={"AAPL": Decimal(10)},
        )

    async def place_order(
        self, *, account_ref: str, order_payload: Mapping[str, object]
    ) -> BrokerOrderSubmission:
        del account_ref
        self.place_calls += 1
        self.payloads.append(order_payload)
        if self.uncertain:
            raise BrokerOrderSubmissionUncertain("response unavailable")
        return BrokerOrderSubmission("12345", NOW, 201)

    async def get_order(
        self, *, account_ref: str, broker_order_id: str, observed_at: datetime
    ) -> BrokerOrderStatusObservation:
        del account_ref
        return BrokerOrderStatusObservation(
            broker_order_id=broker_order_id,
            observed_at=observed_at,
            status=self.order_status,
            filled_quantity=Decimal(0),
            remaining_quantity=Decimal(1),
            average_fill_price=None,
        )

    async def cancel_order(self, *, account_ref: str, broker_order_id: str) -> None:
        del account_ref, broker_order_id
        self.cancel_calls += 1


def _service(tmp_path, provider: _Provider) -> BrokerOrderService:
    engine = create_engine(f"sqlite:///{tmp_path / 'orders.db'}")
    Base.metadata.create_all(engine)
    return BrokerOrderService(
        SqlAlchemyBrokerOrderRepository(engine),
        provider,
        _Quote(),
        _Audit(),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )


def _preview_request(**overrides: object) -> BrokerOrderIntentPreviewInput:
    values: dict[str, object] = {
        "account_ref": "schwab_account_a",
        "instrument_id": "equity:US:AAPL",
        "instruction": "BUY",
        "quantity": 1,
        "order_type": "LIMIT",
        "limit_price": "100",
        "session": "NORMAL",
        "duration": "DAY",
        "idempotency_key": "aapl-limit-100-preview",
    }
    values.update(overrides)
    return BrokerOrderIntentPreviewInput.model_validate(values)


def test_order_contract_supports_stop_limit_and_trailing_but_not_extended_stops() -> None:
    stop_limit = _preview_request(
        instruction="SELL",
        order_type="STOP_LIMIT",
        limit_price="290",
        stop_price="295",
    )
    trailing = _preview_request(
        instruction="SELL",
        order_type="TRAILING_STOP",
        limit_price=None,
        trail_offset="5",
        trail_type="PERCENT",
    )

    assert stop_limit.order_type.value == "STOP_LIMIT"
    assert trailing.order_type.value == "TRAILING_STOP"
    with pytest.raises(ValidationError, match="extended-hours sessions accept LIMIT"):
        _preview_request(
            instruction="SELL",
            order_type="STOP",
            limit_price=None,
            stop_price="295",
            session="SEAMLESS",
        )


def test_order_research_context_requires_subject_and_exact_plan_pair() -> None:
    with pytest.raises(ValidationError, match="requires case_id"):
        _preview_request(decision_id="decision_1")
    with pytest.raises(ValidationError, match="must be provided together"):
        _preview_request(case_id="case_1", trade_plan_id="trade_plan_1")


@pytest.mark.asyncio
async def test_aapl_limit_preview_submits_exactly_once(tmp_path) -> None:
    provider = _Provider()
    service = _service(tmp_path, provider)
    preview = await service.preview(_preview_request())
    assert preview.ok is True
    assert preview.data is not None
    assert preview.data.exact_order_payload == {
        "session": "NORMAL",
        "duration": "DAY",
        "orderType": "LIMIT",
        "price": "100",
        "quantity": 1,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 1,
                "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            }
        ],
    }
    submit = BrokerOrderSubmitInput(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="aapl-limit-100-submit",
        confirmed_by="user",
        submitted_via="codex_chat",
        authorization_note="Buy 1 AAPL at a $100 limit in the selected Schwab account.",
    )
    first = await service.submit(submit)
    replay = await service.submit(submit)

    assert first.ok is True
    assert first.data is not None and first.data.status == "SUBMITTED"
    assert replay.ok is True
    assert provider.place_calls == 1


@pytest.mark.asyncio
async def test_order_preview_rejects_cross_subject_research_context_before_provider_use(
    tmp_path,
) -> None:
    class ResearchUow:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        subjects = SimpleNamespace(get=lambda _case_id: object())
        decisions = SimpleNamespace(
            get=lambda _decision_id: SimpleNamespace(
                subject_id="case_other", primary_instrument_id="equity:US:AAPL"
            )
        )
        trade_plans = SimpleNamespace(get_version=lambda *_args: None)

    engine = create_engine(f"sqlite:///{tmp_path / 'linked-orders.db'}")
    Base.metadata.create_all(engine)
    service = BrokerOrderService(
        SqlAlchemyBrokerOrderRepository(engine),
        _Provider(),
        _Quote(),
        _Audit(),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
        ResearchUow,
    )

    result = await service.preview(
        _preview_request(case_id="case_expected", decision_id="decision_1")
    )

    assert result.ok is False
    assert result.errors[0].code == "INVALID_RESEARCH_LINK"


@pytest.mark.asyncio
async def test_uncertain_submit_is_persisted_and_never_retried(tmp_path) -> None:
    provider = _Provider(uncertain=True)
    service = _service(tmp_path, provider)
    preview = await service.preview(_preview_request())
    assert preview.data is not None
    submit = BrokerOrderSubmitInput(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="uncertain-submit",
        confirmed_by="user",
        submitted_via="codex_chat",
        authorization_note="Submit the exact preview once.",
    )

    first = await service.submit(submit)
    replay = await service.submit(submit)

    assert first.ok is False
    assert first.data is not None and first.data.status == "UNKNOWN"
    assert replay.ok is False
    assert provider.place_calls == 1
    assert replay.errors[0].code == "BROKER_ORDER_STATE_CONFLICT"
    assert [item.order_intent_id for item in service.list_unresolved()] == [
        preview.data.order_intent_id
    ]
    assert [item.order_intent_id for item in service.list_recent()] == [
        preview.data.order_intent_id
    ]


@pytest.mark.asyncio
async def test_replayed_submitting_claim_never_calls_provider_again(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'claimed.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyBrokerOrderRepository(engine)
    provider = _Provider()
    service = BrokerOrderService(
        repository,
        provider,
        _Quote(),
        _Audit(),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )
    preview = await service.preview(_preview_request())
    assert preview.data is not None
    repository.claim_submit(
        order_intent_id=preview.data.order_intent_id,
        now=NOW,
        submit_idempotency_key="claimed-submit",
        confirmed_by="user",
        submitted_via="codex_chat",
        authorization_note="Submit once.",
    )

    replay = await service.submit(
        BrokerOrderSubmitInput(
            order_intent_id=preview.data.order_intent_id,
            idempotency_key="claimed-submit",
            confirmed_by="user",
            submitted_via="codex_chat",
            authorization_note="Submit once.",
        )
    )

    assert replay.ok is False
    assert replay.data is not None and replay.data.status == "SUBMITTING"
    assert provider.place_calls == 0
    assert [item.status for item in service.list_unresolved()] == ["SUBMITTING"]


@pytest.mark.asyncio
async def test_sgov_scheduler_enforces_symbol_and_cash_reserve(tmp_path) -> None:
    provider = _Provider(cash="2250")
    service = _service(tmp_path, provider)
    preview = await service.preview(
        _preview_request(
            instrument_id="etf:US:SGOV",
            quantity=1,
            limit_price="100",
            idempotency_key="sgov-auto-preview",
        )
    )
    assert preview.data is not None

    result = await service.submit_sgov_cash_sweep(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="sgov-auto-submit",
        minimum_cash_reserve=Decimal("2200"),
    )

    assert result.ok is False
    assert result.errors[0].code == "BROKER_CASH_GUARD_FAILED"
    assert provider.place_calls == 0


@pytest.mark.asyncio
async def test_sgov_scheduler_submits_exact_normal_session_buy_once(tmp_path) -> None:
    provider = _Provider(cash="10000")
    service = _service(tmp_path, provider)
    preview = await service.preview(
        _preview_request(
            instrument_id="etf:US:SGOV",
            quantity=50,
            limit_price="100",
            idempotency_key="sgov-success-preview",
        )
    )
    assert preview.data is not None

    first = await service.submit_sgov_cash_sweep(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="sgov-success-submit",
        minimum_cash_reserve=Decimal("2200"),
    )
    replay = await service.submit_sgov_cash_sweep(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="sgov-success-submit",
        minimum_cash_reserve=Decimal("2200"),
    )

    assert first.ok is replay.ok is True
    assert first.data is not None and first.data.status == "SUBMITTED"
    assert provider.place_calls == 1
    assert provider.payloads == [
        {
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "LIMIT",
            "price": "100",
            "quantity": 50,
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": "BUY",
                    "quantity": 50,
                    "instrument": {"symbol": "SGOV", "assetType": "EQUITY"},
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_sgov_scheduler_blocks_when_ask_moves_before_submit(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'moved.db'}")
    Base.metadata.create_all(engine)
    provider = _Provider(cash="10000")
    service = BrokerOrderService(
        SqlAlchemyBrokerOrderRepository(engine),
        provider,
        _MovingSgovQuote(),
        _Audit(),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )
    preview = await service.preview(
        _preview_request(
            instrument_id="etf:US:SGOV",
            quantity=50,
            limit_price="100",
            idempotency_key="sgov-moved-preview",
        )
    )
    assert preview.data is not None

    result = await service.submit_sgov_cash_sweep(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="sgov-moved-submit",
        minimum_cash_reserve=Decimal("2200"),
    )

    assert result.ok is False
    assert result.errors[0].code == "SGOV_AUTO_LIMIT_PRICE_MOVED"
    assert provider.place_calls == 0


@pytest.mark.asyncio
async def test_successful_provider_write_with_failed_persistence_becomes_unknown(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'persist-failure.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyBrokerOrderRepository(engine)
    provider = _Provider(cash="10000")
    service = BrokerOrderService(
        repository,
        provider,
        _Quote(),
        _Audit(),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )
    preview = await service.preview(
        _preview_request(
            instrument_id="etf:US:SGOV",
            quantity=50,
            limit_price="100",
            idempotency_key="sgov-persistence-preview",
        )
    )
    assert preview.data is not None

    def fail_mark_submitted(**_: object) -> None:
        raise RuntimeError("simulated durable receipt failure")

    monkeypatch.setattr(repository, "mark_submitted", fail_mark_submitted)
    result = await service.submit_sgov_cash_sweep(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="sgov-persistence-submit",
        minimum_cash_reserve=Decimal("2200"),
    )

    assert result.ok is False
    assert result.data is not None and result.data.status == "UNKNOWN"
    assert result.data.execution_effect is True
    assert provider.place_calls == 1
    assert [item.status for item in service.list_unresolved()] == ["UNKNOWN"]


@pytest.mark.asyncio
async def test_sgov_scheduler_rejects_non_sgov_intent(tmp_path) -> None:
    provider = _Provider()
    service = _service(tmp_path, provider)
    preview = await service.preview(_preview_request())
    assert preview.data is not None

    result = await service.submit_sgov_cash_sweep(
        order_intent_id=preview.data.order_intent_id,
        idempotency_key="invalid-auto-submit",
        minimum_cash_reserve=Decimal("2200"),
    )

    assert result.ok is False
    assert result.errors[0].code == "SGOV_AUTO_EXECUTION_BOUNDARY_VIOLATION"
    assert provider.place_calls == 0


@pytest.mark.asyncio
async def test_buy_preview_uses_cash_not_buying_power(tmp_path) -> None:
    provider = _Provider(cash="50")
    service = _service(tmp_path, provider)

    result = await service.preview(_preview_request())

    assert result.ok is False
    assert result.errors[0].code == "BROKER_CASH_GUARD_FAILED"
    assert provider.place_calls == 0


async def _submitted_order(tmp_path, provider: _Provider) -> tuple[BrokerOrderService, str]:
    service = _service(tmp_path, provider)
    preview = await service.preview(_preview_request())
    assert preview.data is not None
    submit = await service.submit(
        BrokerOrderSubmitInput(
            order_intent_id=preview.data.order_intent_id,
            idempotency_key="aapl-lifecycle-submit",
            confirmed_by="user",
            submitted_via="codex_chat",
            authorization_note="Submit the exact preview once.",
        )
    )
    assert submit.data is not None and submit.data.status == "SUBMITTED"
    return service, preview.data.order_intent_id


@pytest.mark.asyncio
async def test_cancel_request_remains_cancel_requested_until_provider_confirmation(
    tmp_path,
) -> None:
    provider = _Provider()
    service, order_intent_id = await _submitted_order(tmp_path, provider)

    cancelled = await service.cancel(
        BrokerOrderCancelInput(
            order_intent_id=order_intent_id,
            idempotency_key="aapl-lifecycle-cancel",
            confirmed_by="user",
            submitted_via="codex_chat",
            authorization_note="Cancel the exact submitted order.",
        )
    )

    assert cancelled.ok is True
    assert cancelled.data is not None
    assert cancelled.data.status == "CANCEL_REQUESTED"
    assert cancelled.data.provider_status == "CANCEL_REQUEST_ACCEPTED"
    assert provider.cancel_calls == 1
    assert [item.status for item in service.list_unresolved()] == ["CANCEL_REQUESTED"]

    durable = await service.status(BrokerOrderStatusInput(order_intent_id=order_intent_id))
    assert durable.ok is True
    assert durable.data is not None
    assert durable.data.intent.status == "CANCEL_REQUESTED"
    assert durable.data.intent.provider_status == "CANCEL_REQUEST_ACCEPTED"


@pytest.mark.asyncio
async def test_status_refresh_persists_provider_observation_and_later_cancel_confirmation(
    tmp_path,
) -> None:
    provider = _Provider()
    service, order_intent_id = await _submitted_order(tmp_path, provider)

    working = await service.status(
        BrokerOrderStatusInput(order_intent_id=order_intent_id, refresh_provider=True)
    )
    assert working.ok is True
    assert working.data is not None
    assert working.data.intent.status == "SUBMITTED"
    assert working.data.intent.provider_status == "WORKING"

    durable_working = await service.status(BrokerOrderStatusInput(order_intent_id=order_intent_id))
    assert durable_working.ok is True
    assert durable_working.data is not None
    assert durable_working.data.intent.provider_status == "WORKING"

    requested = await service.cancel(
        BrokerOrderCancelInput(
            order_intent_id=order_intent_id,
            idempotency_key="aapl-lifecycle-cancel",
            confirmed_by="user",
            submitted_via="codex_chat",
            authorization_note="Cancel the exact submitted order.",
        )
    )
    assert requested.ok is True
    assert requested.data is not None and requested.data.status == "CANCEL_REQUESTED"

    provider.order_status = "FILLED"
    terminal_non_cancel = await service.status(
        BrokerOrderStatusInput(order_intent_id=order_intent_id, refresh_provider=True)
    )
    assert terminal_non_cancel.ok is True
    assert terminal_non_cancel.data is not None
    assert terminal_non_cancel.data.intent.status == "CANCEL_REQUESTED"
    assert terminal_non_cancel.data.intent.provider_status == "FILLED"

    provider.order_status = "CANCELED"
    confirmed = await service.status(
        BrokerOrderStatusInput(order_intent_id=order_intent_id, refresh_provider=True)
    )

    assert confirmed.ok is True
    assert confirmed.data is not None
    assert confirmed.data.intent.status == "CANCELLED"
    assert confirmed.data.intent.provider_status == "CANCELED"

    durable_confirmed = await service.status(
        BrokerOrderStatusInput(order_intent_id=order_intent_id)
    )
    assert durable_confirmed.ok is True
    assert durable_confirmed.data is not None
    assert durable_confirmed.data.intent.status == "CANCELLED"
    assert durable_confirmed.data.intent.provider_status == "CANCELED"
