from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from application.dto.broker_execution import (
    BrokerOrderIntentPreviewInput,
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
        return BrokerQuoteObservation(
            instrument_id=instrument_id,
            symbol="AAPL",
            quote_at=as_of,
            bid=Decimal("312.90"),
            ask=Decimal("313.10"),
            last=Decimal("313.00"),
            source="schwab",
        )


class _Provider:
    def __init__(self, *, uncertain: bool = False, cash: str = "10000") -> None:
        self.uncertain = uncertain
        self.cash = Decimal(cash)
        self.place_calls = 0
        self.payloads: list[Mapping[str, object]] = []

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
            status="WORKING",
            filled_quantity=Decimal(0),
            remaining_quantity=Decimal(1),
            average_fill_price=None,
        )

    async def cancel_order(self, *, account_ref: str, broker_order_id: str) -> None:
        del account_ref, broker_order_id


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


@pytest.mark.asyncio
async def test_buy_preview_uses_cash_not_buying_power(tmp_path) -> None:
    provider = _Provider(cash="50")
    service = _service(tmp_path, provider)

    result = await service.preview(_preview_request())

    assert result.ok is False
    assert result.errors[0].code == "BROKER_CASH_GUARD_FAILED"
    assert provider.place_calls == 0
