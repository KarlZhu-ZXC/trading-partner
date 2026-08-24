"""Read-only broker facts used to prepare, but never submit, an order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id


def _price(value: Decimal | None, field: str) -> None:
    if value is None:
        return
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise DataContractError(f"{field} must be a positive finite Decimal")


@dataclass(frozen=True, slots=True)
class BrokerQuoteObservation:
    instrument_id: str
    symbol: str
    quote_at: datetime
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    source: str

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        if not self.symbol.strip() or len(self.symbol) > 32:
            raise DataContractError("symbol must be a bounded non-blank string")
        require_aware_datetime(self.quote_at, field_name="quote_at")
        _price(self.bid, "bid")
        _price(self.ask, "ask")
        _price(self.last, "last")
        if self.bid is None and self.ask is None and self.last is None:
            raise DataContractError("broker quote requires at least one price")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise DataContractError("ask must be >= bid")
        if not self.source.strip() or len(self.source) > 64:
            raise DataContractError("source must be a bounded non-blank string")


class BrokerOrderInstruction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class BrokerOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    TRAILING_STOP_LIMIT = "TRAILING_STOP_LIMIT"


class BrokerOrderSession(StrEnum):
    NORMAL = "NORMAL"
    AM = "AM"
    PM = "PM"
    SEAMLESS = "SEAMLESS"


class BrokerOrderDuration(StrEnum):
    DAY = "DAY"
    GOOD_TILL_CANCEL = "GOOD_TILL_CANCEL"


class BrokerTrailType(StrEnum):
    VALUE = "VALUE"
    PERCENT = "PERCENT"


class BrokerOrderIntentStatus(StrEnum):
    PREVIEWED = "PREVIEWED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class BrokerExecutionAccountState:
    account_ref: str
    observed_at: datetime
    cash_balance: Decimal | None
    margin_balance: Decimal | None
    open_buy_order_reserve: Decimal | None
    positions: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class BrokerOrderSubmission:
    broker_order_id: str
    submitted_at: datetime
    http_status: int


@dataclass(frozen=True, slots=True)
class BrokerOrderStatusObservation:
    broker_order_id: str
    observed_at: datetime
    status: str
    filled_quantity: Decimal
    remaining_quantity: Decimal | None
    average_fill_price: Decimal | None


@dataclass(frozen=True, slots=True)
class BrokerOrderIntent:
    order_intent_id: str
    account_ref: str
    instrument_id: str
    symbol: str
    instruction: BrokerOrderInstruction
    quantity: int
    order_type: BrokerOrderType
    session: BrokerOrderSession
    duration: BrokerOrderDuration
    limit_price: Decimal | None
    stop_price: Decimal | None
    trail_offset: Decimal | None
    trail_type: BrokerTrailType | None
    limit_offset: Decimal | None
    payload_sha256: str
    order_payload: Mapping[str, object]
    preview_idempotency_key: str
    created_at: datetime
    expires_at: datetime
    account_observed_at: datetime
    cash_balance: Decimal | None
    margin_balance: Decimal | None
    open_buy_order_reserve: Decimal | None
    position_quantity: Decimal
    quote_at: datetime | None
    quote_source: str | None
    quote_price: Decimal | None
    estimated_notional: Decimal | None
    status: BrokerOrderIntentStatus
    subject_id: str | None = None
    decision_id: str | None = None
    trade_plan_id: str | None = None
    trade_plan_version: int | None = None
    submit_idempotency_key: str | None = None
    confirmed_by: str | None = None
    submitted_via: str | None = None
    authorization_note: str | None = None
    broker_order_id: str | None = None
    submitted_at: datetime | None = None
    provider_status: str | None = None
    rejection_code: str | None = None
    updated_at: datetime | None = None
