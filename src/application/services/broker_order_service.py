"""Preview-confirm-submit orchestration for narrow Schwab live orders."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TypeVar

from application.dto.broker_execution import (
    BrokerOrderCancelInput,
    BrokerOrderIntentDTO,
    BrokerOrderIntentPreviewInput,
    BrokerOrderStatusDTO,
    BrokerOrderStatusInput,
    BrokerOrderSubmitInput,
)
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.tool_envelope import (
    DUPLICATE_IDEMPOTENCY_KEY,
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from application.ports.audit_log_writer import AuditLogWriter
from application.ports.broker_order_provider import BrokerOrderProvider
from application.ports.broker_order_repository import BrokerOrderRepository
from application.ports.broker_quote_provider import BrokerQuoteProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import Freshness, Market, SourceRole, VendorId
from domain.common.errors import (
    BrokerOrderNotFound,
    BrokerOrderRejected,
    BrokerOrderStateConflict,
    BrokerOrderSubmissionUncertain,
    DataContractError,
    IdempotencyConflict,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.execution.models import (
    BrokerExecutionAccountState,
    BrokerOrderInstruction,
    BrokerOrderIntent,
    BrokerOrderIntentStatus,
    BrokerOrderType,
    BrokerQuoteObservation,
)

T = TypeVar("T")


class BrokerOrderService:
    """Keep every external write behind an expiring, single-use durable intent."""

    def __init__(
        self,
        repository: BrokerOrderRepository,
        provider: BrokerOrderProvider,
        quote_provider: BrokerQuoteProvider,
        audit: AuditLogWriter,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._quotes = quote_provider
        self._audit = audit
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def preview(
        self, request: BrokerOrderIntentPreviewInput
    ) -> ToolEnvelope[BrokerOrderIntentDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        request_payload = request.model_dump(mode="json", exclude={"idempotency_key"})
        request_sha256 = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        existing = self._repository.get_by_preview_idempotency_key(request.idempotency_key)
        if existing is not None:
            if existing.payload_sha256 != request_sha256:
                return self._failure(
                    request_id,
                    now,
                    IdempotencyConflict(
                        "Broker preview idempotency key was reused with another order"
                    ),
                )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH,
                sources=(),
                data=BrokerOrderIntentDTO.from_domain(existing),
                degraded=True,
                warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
            )
        try:
            account, quote = await self._read_current_facts(request, now=now)
            self._validate_account(request, account)
            _, _, symbol = parse_instrument_id(request.instrument_id)
            payload = self._build_payload(request, symbol=symbol)
            price = quote.ask or quote.last or quote.bid
            if price is None:
                raise DataContractError("Schwab quote has no usable price")
            estimated = self._estimated_notional(request, quote_price=price)
            intent = BrokerOrderIntent(
                order_intent_id=self._ids.new(EntityIdPrefix.BROKER_ORDER),
                account_ref=request.account_ref,
                instrument_id=request.instrument_id,
                symbol=symbol,
                instruction=request.instruction,
                quantity=request.quantity,
                order_type=request.order_type,
                session=request.session,
                duration=request.duration,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                trail_offset=request.trail_offset,
                trail_type=request.trail_type,
                limit_offset=request.limit_offset,
                payload_sha256=request_sha256,
                order_payload=payload,
                preview_idempotency_key=request.idempotency_key,
                created_at=now,
                expires_at=now + timedelta(seconds=request.preview_ttl_seconds),
                account_observed_at=account.observed_at,
                cash_balance=account.cash_balance,
                margin_balance=account.margin_balance,
                open_buy_order_reserve=account.open_buy_order_reserve,
                position_quantity=account.positions.get(symbol.upper(), Decimal(0)),
                quote_at=quote.quote_at,
                quote_source=quote.source,
                quote_price=price,
                estimated_notional=estimated,
                status=BrokerOrderIntentStatus.PREVIEWED,
                updated_at=now,
            )
            persisted = self._repository.create_preview(intent)
            self._audit.append(
                "BROKER_ORDER_PREVIEWED",
                {
                    "order_intent_id": persisted.order_intent_id,
                    "account_ref": persisted.account_ref,
                    "instrument_id": persisted.instrument_id,
                    "instruction": persisted.instruction.value,
                    "quantity": persisted.quantity,
                    "order_type": persisted.order_type.value,
                    "session": persisted.session.value,
                    "duration": persisted.duration.value,
                    "payload_sha256": persisted.payload_sha256,
                    "expires_at": persisted.expires_at.isoformat(),
                },
                request_id=request_id,
            )
            age = max(0, int((now - quote.quote_at).total_seconds()))
            warnings: tuple[WarningInfo, ...] = ()
            if age > 60:
                warnings = (
                    WarningInfo(
                        code="BROKER_QUOTE_STALE_FOR_CONTEXT",
                        message=(
                            "The quote is stale and is shown only as context; the exact order "
                            "price remains authoritative."
                        ),
                        details={"data_delay_seconds": age},
                    ),
                )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH if not warnings else Freshness.STALE,
                sources=(
                    SourceReference(
                        name=VendorId.SCHWAB.value,
                        role=SourceRole.PRIMARY,
                        retrieved_at=account.observed_at,
                    ),
                    SourceReference(
                        name=f"{VendorId.SCHWAB.value}_quote",
                        role=SourceRole.PRIMARY,
                        retrieved_at=quote.quote_at,
                        data_delay_seconds=age,
                    ),
                ),
                data=BrokerOrderIntentDTO.from_domain(persisted),
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, now, exc)

    async def submit(self, request: BrokerOrderSubmitInput) -> ToolEnvelope[BrokerOrderIntentDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            claimed, acquired = self._repository.claim_submit(
                order_intent_id=request.order_intent_id,
                now=now,
                submit_idempotency_key=request.idempotency_key,
                confirmed_by=request.confirmed_by,
                submitted_via=request.submitted_via,
                authorization_note=request.authorization_note.strip(),
            )
            if claimed.status is BrokerOrderIntentStatus.SUBMITTED:
                return ToolEnvelope.success(
                    request_id=request_id,
                    market=Market.US,
                    as_of=now,
                    fetched_at=now,
                    freshness=Freshness.FRESH,
                    sources=(),
                    data=BrokerOrderIntentDTO.from_domain(claimed, execution_effect=True),
                    degraded=True,
                    warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                )
            if claimed.status is not BrokerOrderIntentStatus.SUBMITTING or not acquired:
                # `acquired=False` marks an idempotent replay of a submit that
                # another call already claimed; sending again could duplicate
                # the broker order, so the replay stays a conflict.
                raise BrokerOrderStateConflict(
                    "Broker order submit was already claimed and will not be sent again",
                    details={"status": claimed.status.value},
                )
            account = await self._provider.get_account_state(
                account_ref=claimed.account_ref, observed_at=self._clock.now()
            )
            self._validate_intent_account(claimed, account)
            submission = await self._provider.place_order(
                account_ref=claimed.account_ref,
                order_payload=claimed.order_payload,
            )
            saved = self._repository.mark_submitted(
                order_intent_id=claimed.order_intent_id,
                broker_order_id=submission.broker_order_id,
                submitted_at=submission.submitted_at,
                provider_status="SUBMITTED",
            )
            self._audit.append(
                "BROKER_ORDER_SUBMITTED",
                {
                    "order_intent_id": saved.order_intent_id,
                    "account_ref": saved.account_ref,
                    "instrument_id": saved.instrument_id,
                    "instruction": saved.instruction.value,
                    "quantity": saved.quantity,
                    "order_type": saved.order_type.value,
                    "payload_sha256": saved.payload_sha256,
                    "confirmed_by": request.confirmed_by,
                    "submitted_via": request.submitted_via,
                },
                request_id=request_id,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH,
                sources=(
                    SourceReference(
                        name=VendorId.SCHWAB.value,
                        role=SourceRole.PRIMARY,
                        retrieved_at=submission.submitted_at,
                    ),
                ),
                data=BrokerOrderIntentDTO.from_domain(saved, execution_effect=True),
            )
        except BrokerOrderSubmissionUncertain as exc:
            saved = self._repository.mark_unknown(
                order_intent_id=request.order_intent_id,
                code=exc.code,
                now=self._clock.now(),
            )
            return self._failure(
                request_id,
                now,
                exc,
                data=BrokerOrderIntentDTO.from_domain(saved, execution_effect=True),
            )
        except Exception as exc:  # noqa: BLE001
            current = self._repository.get(request.order_intent_id)
            data = None
            if current is not None and current.status is BrokerOrderIntentStatus.SUBMITTING:
                current = self._repository.mark_rejected(
                    order_intent_id=current.order_intent_id,
                    code=getattr(exc, "code", type(exc).__name__),
                    now=self._clock.now(),
                )
                data = BrokerOrderIntentDTO.from_domain(current)
            return self._failure(request_id, now, exc, data=data)

    async def status(self, request: BrokerOrderStatusInput) -> ToolEnvelope[BrokerOrderStatusDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            intent = self._repository.get(request.order_intent_id)
            if intent is None:
                raise BrokerOrderNotFound("Broker order intent was not found")
            observation = None
            if request.refresh_provider:
                if intent.broker_order_id is None:
                    raise BrokerOrderStateConflict(
                        "Provider status cannot be refreshed without a broker order id",
                        details={"status": intent.status.value},
                    )
                observation = await self._provider.get_order(
                    account_ref=intent.account_ref,
                    broker_order_id=intent.broker_order_id,
                    observed_at=now,
                )
            data = BrokerOrderStatusDTO(
                intent=BrokerOrderIntentDTO.from_domain(intent),
                provider_checked=observation is not None,
                provider_status=observation.status if observation else intent.provider_status,
                filled_quantity=observation.filled_quantity if observation else None,
                remaining_quantity=observation.remaining_quantity if observation else None,
                average_fill_price=observation.average_fill_price if observation else None,
                observed_at=observation.observed_at if observation else None,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH,
                sources=(
                    (
                        SourceReference(
                            name=VendorId.SCHWAB.value,
                            role=SourceRole.PRIMARY,
                            retrieved_at=observation.observed_at,
                        ),
                    )
                    if observation
                    else ()
                ),
                data=data,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, now, exc)

    def list_unresolved(self, *, limit: int = 100) -> tuple[BrokerOrderIntentDTO, ...]:
        """Read durable order intents that require operator reconciliation."""

        return tuple(
            BrokerOrderIntentDTO.from_domain(value)
            for value in self._repository.list_unresolved(limit=limit)
        )

    async def cancel(self, request: BrokerOrderCancelInput) -> ToolEnvelope[BrokerOrderIntentDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            intent = self._repository.get(request.order_intent_id)
            if intent is None:
                raise BrokerOrderNotFound("Broker order intent was not found")
            if intent.status in {
                BrokerOrderIntentStatus.CANCEL_REQUESTED,
                BrokerOrderIntentStatus.CANCELLED,
            }:
                return ToolEnvelope.success(
                    request_id=request_id,
                    market=Market.US,
                    as_of=now,
                    fetched_at=now,
                    freshness=Freshness.FRESH,
                    sources=(),
                    data=BrokerOrderIntentDTO.from_domain(intent),
                    degraded=True,
                    warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                )
            if intent.status is not BrokerOrderIntentStatus.SUBMITTED or not intent.broker_order_id:
                raise BrokerOrderStateConflict(
                    "Only a submitted order with a broker id can be cancelled",
                    details={"status": intent.status.value},
                )
            await self._provider.cancel_order(
                account_ref=intent.account_ref, broker_order_id=intent.broker_order_id
            )
            saved = self._repository.mark_cancelled(
                order_intent_id=intent.order_intent_id, now=self._clock.now()
            )
            self._audit.append(
                "BROKER_ORDER_CANCEL_REQUESTED",
                {
                    "order_intent_id": saved.order_intent_id,
                    "account_ref": saved.account_ref,
                    "confirmed_by": request.confirmed_by,
                    "submitted_via": request.submitted_via,
                    "idempotency_key": request.idempotency_key,
                },
                request_id=request_id,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH,
                sources=(
                    SourceReference(
                        name=VendorId.SCHWAB.value,
                        role=SourceRole.PRIMARY,
                        retrieved_at=self._clock.now(),
                    ),
                ),
                data=BrokerOrderIntentDTO.from_domain(saved, execution_effect=True),
            )
        except BrokerOrderSubmissionUncertain as exc:
            saved = self._repository.mark_unknown(
                order_intent_id=request.order_intent_id,
                code=exc.code,
                now=self._clock.now(),
            )
            return self._failure(
                request_id,
                now,
                exc,
                data=BrokerOrderIntentDTO.from_domain(saved, execution_effect=True),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, now, exc)

    async def _read_current_facts(
        self, request: BrokerOrderIntentPreviewInput, *, now: datetime
    ) -> tuple[BrokerExecutionAccountState, BrokerQuoteObservation]:
        account = await self._provider.get_account_state(
            account_ref=request.account_ref, observed_at=now
        )
        quote = await self._quotes.get_quote(instrument_id=request.instrument_id, as_of=now)
        return account, quote

    @staticmethod
    def _validate_account(
        request: BrokerOrderIntentPreviewInput,
        account: BrokerExecutionAccountState,
    ) -> None:
        BrokerOrderService._validate_values(
            instruction=request.instruction,
            quantity=request.quantity,
            symbol=parse_instrument_id(request.instrument_id)[2],
            order_type=request.order_type,
            limit_price=request.limit_price,
            account=account,
        )

    @staticmethod
    def _validate_intent_account(
        intent: BrokerOrderIntent, account: BrokerExecutionAccountState
    ) -> None:
        BrokerOrderService._validate_values(
            instruction=intent.instruction,
            quantity=intent.quantity,
            symbol=intent.symbol,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
            account=account,
        )

    @staticmethod
    def _validate_values(
        *,
        instruction: BrokerOrderInstruction,
        quantity: int,
        symbol: str,
        order_type: BrokerOrderType,
        limit_price: Decimal | None,
        account: BrokerExecutionAccountState,
    ) -> None:
        if account.margin_balance is None:
            raise DataContractError(
                "Schwab margin balance is unavailable",
                code="BROKER_MARGIN_GUARD_UNAVAILABLE",
            )
        if account.margin_balance != 0:
            raise BrokerOrderRejected(
                "Live orders are blocked while the account has a non-zero margin balance",
                details={"margin_balance": str(account.margin_balance)},
                code="BROKER_MARGIN_GUARD_FAILED",
            )
        if instruction is BrokerOrderInstruction.BUY:
            if order_type in {
                BrokerOrderType.MARKET,
                BrokerOrderType.STOP,
                BrokerOrderType.TRAILING_STOP,
                BrokerOrderType.TRAILING_STOP_LIMIT,
            }:
                raise DataContractError(
                    "Unbounded or trailing BUY orders are disabled by the no-margin safety policy",
                    code="BROKER_BUY_ORDER_NOTIONAL_UNBOUNDED",
                )
            if account.cash_balance is None or account.open_buy_order_reserve is None:
                raise DataContractError(
                    "Cash or open BUY reserve is unavailable",
                    code="BROKER_CASH_GUARD_UNAVAILABLE",
                )
            assert limit_price is not None
            required = limit_price * quantity
            available = account.cash_balance - account.open_buy_order_reserve
            if required > available:
                raise BrokerOrderRejected(
                    "The BUY order exceeds cash after existing open BUY reserves",
                    details={"required_notional": str(required), "available_cash": str(available)},
                    code="BROKER_CASH_GUARD_FAILED",
                )
        else:
            held = account.positions.get(symbol.upper(), Decimal(0))
            if held < quantity:
                raise BrokerOrderRejected(
                    "The SELL order exceeds the current long position",
                    details={"held_quantity": str(held), "requested_quantity": quantity},
                    code="BROKER_POSITION_GUARD_FAILED",
                )

    @staticmethod
    def _build_payload(request: BrokerOrderIntentPreviewInput, *, symbol: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "session": request.session.value,
            "duration": request.duration.value,
            "orderType": request.order_type.value,
            "quantity": request.quantity,
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": request.instruction.value,
                    "quantity": request.quantity,
                    "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                }
            ],
        }
        if request.limit_price is not None:
            payload["price"] = str(request.limit_price)
        if request.stop_price is not None:
            payload["stopPrice"] = str(request.stop_price)
        if request.trail_offset is not None:
            assert request.trail_type is not None
            payload.update(
                {
                    "stopPriceLinkBasis": (
                        "BID" if request.instruction is BrokerOrderInstruction.SELL else "ASK"
                    ),
                    "stopPriceLinkType": request.trail_type.value,
                    "stopPriceOffset": str(request.trail_offset),
                    "stopType": "STANDARD",
                }
            )
        if request.limit_offset is not None:
            payload.update(
                {
                    "priceLinkBasis": (
                        "BID" if request.instruction is BrokerOrderInstruction.SELL else "ASK"
                    ),
                    "priceLinkType": "VALUE",
                    "priceOffset": str(request.limit_offset),
                }
            )
        return payload

    @staticmethod
    def _estimated_notional(
        request: BrokerOrderIntentPreviewInput, *, quote_price: Decimal
    ) -> Decimal:
        reference = request.limit_price or request.stop_price or quote_price
        return reference * request.quantity

    def _failure(
        self,
        request_id: str,
        as_of: datetime,
        exc: Exception,
        *,
        data: T | None = None,
    ) -> ToolEnvelope[T]:
        mapped = (
            to_error_info(exc, self._redactor)
            if isinstance(exc, TradingPartnerError)
            else to_error_info_from_exception(exc, self._redactor)
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=Market.US,
            as_of=as_of,
            fetched_at=self._clock.now(),
            errors=(mapped,),
            data=data,
        )
