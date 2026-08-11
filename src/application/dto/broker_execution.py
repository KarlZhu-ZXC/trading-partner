"""Closed shadow-mode order preview DTOs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.common.enums import AssetType, Market
from domain.common.values import parse_instrument_id
from domain.execution.models import (
    BrokerOrderDuration,
    BrokerOrderInstruction,
    BrokerOrderIntent,
    BrokerOrderSession,
    BrokerOrderType,
    BrokerTrailType,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrokerOrderPreviewInput(_DTO):
    mode: Literal["cash_sweep_shadow"] = "cash_sweep_shadow"
    account_refs: tuple[str, ...] = ()
    instrument_id: str = "etf:US:SGOV"
    hard_cash_floor: Decimal = Field(default=Decimal("2000"), ge=0)
    operational_buffer: Decimal = Field(default=Decimal("200"), ge=0)
    minimum_order_notional: Decimal = Field(default=Decimal("1000"), gt=0)
    max_quote_age_seconds: int = Field(default=30, ge=1, le=3600)
    max_spread: Decimal = Field(default=Decimal("0.02"), ge=0)

    @field_validator("instrument_id")
    @classmethod
    def _sgov_only(cls, value: str) -> str:
        parse_instrument_id(value)
        if value != "etf:US:SGOV":
            raise ValueError("cash_sweep_shadow currently supports only etf:US:SGOV")
        return value

    @field_validator("account_refs")
    @classmethod
    def _unique_accounts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("account_refs must be unique non-blank values")
        return value


class BrokerQuotePreviewDTO(_DTO):
    instrument_id: str
    symbol: str
    source: str
    quote_at: datetime
    bid: DecimalWire | None
    ask: DecimalWire | None
    last: DecimalWire | None
    price_basis: Literal["ask", "last"]
    reference_limit_price: DecimalWire
    spread: DecimalWire | None
    age_seconds: int


class BrokerOrderPreviewAccountDTO(_DTO):
    account_ref: str
    snapshot_id: str
    snapshot_as_of: datetime
    cash_balance: DecimalWire | None
    hard_cash_floor: DecimalWire
    operational_buffer: DecimalWire
    open_buy_order_reserve: DecimalWire | None
    reserved_cash: DecimalWire | None
    surplus_cash: DecimalWire | None
    minimum_order_notional: DecimalWire
    quantity: int
    estimated_notional: DecimalWire
    projected_cash_after_all_open_buys: DecimalWire | None
    residual_above_reserve: DecimalWire | None
    status: Literal["INDICATIVE", "BELOW_THRESHOLD", "BLOCKED"]
    blocker_codes: tuple[str, ...]
    schwab_order_payload: dict[str, object] | None
    execution_effect: Literal[False] = False


class BrokerOrderPreviewDTO(_DTO):
    mode: Literal["cash_sweep_shadow"]
    policy: dict[str, object]
    quote: BrokerQuotePreviewDTO
    accounts: tuple[BrokerOrderPreviewAccountDTO, ...]
    total_quantity: int
    total_estimated_notional: DecimalWire
    execution_effect: Literal[False] = False
    shadow_only: Literal[True] = True


class SgovShadowPlanDisposition(StrEnum):
    EXECUTED = "EXECUTED"
    SKIPPED_NON_TRADING_DAY = "SKIPPED_NON_TRADING_DAY"
    SKIPPED_NOT_DUE = "SKIPPED_NOT_DUE"
    SKIPPED_ALREADY_COMPLETED = "SKIPPED_ALREADY_COMPLETED"
    SKIPPED_WINDOW_CLOSED = "SKIPPED_WINDOW_CLOSED"


class SgovShadowPlanDTO(_DTO):
    """One scheduled or foreground SGOV Shadow calculation receipt."""

    disposition: SgovShadowPlanDisposition
    market_session_date: date | None = None
    scheduled_for: datetime | None = None
    generated_at: datetime | None = None
    account_refresh_ok: bool | None = None
    refreshed_snapshot_ids: tuple[str, ...] = ()
    preview: BrokerOrderPreviewDTO | None = None
    notification_id: str | None = None
    notification_status: str | None = None
    notification_flush_disposition: str | None = None
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    execution_effect: Literal[False] = False
    shadow_only: Literal[True] = True


class BrokerOrderIntentPreviewInput(_DTO):
    account_ref: str = Field(min_length=1, max_length=128)
    instrument_id: str
    instruction: BrokerOrderInstruction
    quantity: int = Field(gt=0, le=1_000_000)
    order_type: BrokerOrderType
    session: BrokerOrderSession = BrokerOrderSession.NORMAL
    duration: BrokerOrderDuration = BrokerOrderDuration.DAY
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    trail_offset: Decimal | None = Field(default=None, gt=0)
    trail_type: BrokerTrailType | None = None
    limit_offset: Decimal | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    preview_ttl_seconds: int = Field(default=120, ge=30, le=300)

    @field_validator("instrument_id")
    @classmethod
    def _us_equity_or_etf(cls, value: str) -> str:
        asset_type, market, _ = parse_instrument_id(value)
        if market is not Market.US or asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            raise ValueError("live Schwab orders support only US equities and ETFs")
        return value

    @model_validator(mode="after")
    def _closed_order_contract(self) -> BrokerOrderIntentPreviewInput:
        if self.session is not BrokerOrderSession.NORMAL:
            if self.order_type is not BrokerOrderType.LIMIT:
                raise ValueError("extended-hours sessions accept LIMIT orders only")
            if self.stop_price is not None or self.trail_offset is not None:
                raise ValueError("extended-hours LIMIT orders cannot carry stop fields")
        if self.order_type is BrokerOrderType.MARKET:
            if self.duration is not BrokerOrderDuration.DAY:
                raise ValueError("MARKET orders require DAY duration")
            if any(
                value is not None
                for value in (
                    self.limit_price,
                    self.stop_price,
                    self.trail_offset,
                    self.trail_type,
                    self.limit_offset,
                )
            ):
                raise ValueError("MARKET orders cannot carry price or trail fields")
        elif self.order_type is BrokerOrderType.LIMIT:
            self._require_exact("LIMIT", required=("limit_price",))
        elif self.order_type is BrokerOrderType.STOP:
            self._require_exact("STOP", required=("stop_price",))
        elif self.order_type is BrokerOrderType.STOP_LIMIT:
            self._require_exact("STOP_LIMIT", required=("stop_price", "limit_price"))
        elif self.order_type is BrokerOrderType.TRAILING_STOP:
            self._require_exact("TRAILING_STOP", required=("trail_offset", "trail_type"))
        elif self.order_type is BrokerOrderType.TRAILING_STOP_LIMIT:
            self._require_exact(
                "TRAILING_STOP_LIMIT",
                required=("trail_offset", "trail_type", "limit_offset"),
            )
        return self

    def _require_exact(self, label: str, *, required: tuple[str, ...]) -> None:
        price_fields = {
            "limit_price",
            "stop_price",
            "trail_offset",
            "trail_type",
            "limit_offset",
        }
        missing = [name for name in required if getattr(self, name) is None]
        unexpected = [
            name for name in price_fields - set(required) if getattr(self, name) is not None
        ]
        if missing or unexpected:
            raise ValueError(
                f"{label} fields invalid; missing={sorted(missing)}, "
                f"unexpected={sorted(unexpected)}"
            )


class BrokerOrderSubmitInput(_DTO):
    order_intent_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=200)
    confirmed_by: Literal["user"]
    submitted_via: Literal["codex_chat"]
    authorization_note: str = Field(min_length=1, max_length=1_000)


class BrokerOrderStatusInput(_DTO):
    order_intent_id: str = Field(min_length=1, max_length=128)
    refresh_provider: bool = False


class BrokerOrderCancelInput(_DTO):
    order_intent_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=200)
    confirmed_by: Literal["user"]
    submitted_via: Literal["codex_chat"]
    authorization_note: str = Field(min_length=1, max_length=1_000)


class BrokerOrderIntentDTO(_DTO):
    order_intent_id: str
    account_ref: str
    instrument_id: str
    symbol: str
    instruction: str
    quantity: int
    order_type: str
    session: str
    duration: str
    limit_price: DecimalWire | None
    stop_price: DecimalWire | None
    trail_offset: DecimalWire | None
    trail_type: str | None
    limit_offset: DecimalWire | None
    created_at: datetime
    expires_at: datetime
    account_observed_at: datetime
    cash_balance: DecimalWire | None
    margin_balance: DecimalWire | None
    open_buy_order_reserve: DecimalWire | None
    position_quantity: DecimalWire
    cash_after_open_buy_reserve: DecimalWire | None
    quote_at: datetime | None
    quote_source: str | None
    quote_price: DecimalWire | None
    estimated_notional: DecimalWire | None
    status: str
    provider_status: str | None
    submitted_at: datetime | None
    blocker_codes: tuple[str, ...] = ()
    execution_effect: bool
    exact_order_payload: dict[str, object]
    confirmation_required: bool
    safety_notes: tuple[str, ...]

    @classmethod
    def from_domain(
        cls,
        value: BrokerOrderIntent,
        *,
        blocker_codes: tuple[str, ...] = (),
        execution_effect: bool = False,
    ) -> BrokerOrderIntentDTO:
        return cls(
            order_intent_id=value.order_intent_id,
            account_ref=value.account_ref,
            instrument_id=value.instrument_id,
            symbol=value.symbol,
            instruction=value.instruction.value,
            quantity=value.quantity,
            order_type=value.order_type.value,
            session=value.session.value,
            duration=value.duration.value,
            limit_price=value.limit_price,
            stop_price=value.stop_price,
            trail_offset=value.trail_offset,
            trail_type=value.trail_type.value if value.trail_type else None,
            limit_offset=value.limit_offset,
            created_at=value.created_at,
            expires_at=value.expires_at,
            account_observed_at=value.account_observed_at,
            cash_balance=value.cash_balance,
            margin_balance=value.margin_balance,
            open_buy_order_reserve=value.open_buy_order_reserve,
            position_quantity=value.position_quantity,
            cash_after_open_buy_reserve=(
                value.cash_balance - value.open_buy_order_reserve
                if value.cash_balance is not None and value.open_buy_order_reserve is not None
                else None
            ),
            quote_at=value.quote_at,
            quote_source=value.quote_source,
            quote_price=value.quote_price,
            estimated_notional=value.estimated_notional,
            status=value.status.value,
            provider_status=value.provider_status,
            submitted_at=value.submitted_at,
            blocker_codes=blocker_codes,
            execution_effect=execution_effect,
            exact_order_payload=dict(value.order_payload),
            confirmation_required=value.status.value == "PREVIEWED",
            safety_notes=(
                "Preview expires quickly and is single-use.",
                "A submit with an uncertain response is never retried automatically.",
                "Extended-hours support ends at 20:00 ET; Schwab API overnight is unavailable.",
            ),
        )


class BrokerOrderStatusDTO(_DTO):
    intent: BrokerOrderIntentDTO
    provider_checked: bool
    provider_status: str | None = None
    filled_quantity: DecimalWire | None = None
    remaining_quantity: DecimalWire | None = None
    average_fill_price: DecimalWire | None = None
    observed_at: datetime | None = None
