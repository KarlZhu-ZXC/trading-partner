"""Broker order shadow and explicitly confirmed live-order adapters."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.broker_execution import (
    BrokerOrderCancelInput,
    BrokerOrderIntentPreviewInput,
    BrokerOrderPreviewInput,
    BrokerOrderStatusInput,
    BrokerOrderSubmitInput,
)
from bootstrap import ApplicationContainer
from domain.execution.models import (
    BrokerOrderDuration,
    BrokerOrderInstruction,
    BrokerOrderSession,
    BrokerOrderType,
    BrokerTrailType,
)
from interfaces.mcp.validation import unexpected_failure


def build_execution_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Expose shadow calculation plus the closed preview-confirm-submit protocol."""

    async def cash_sweep_preview(
        account_refs: tuple[str, ...] = (),
        instrument_id: str = "etf:US:SGOV",
        hard_cash_floor: Decimal = Decimal("3000"),
        operational_buffer: Decimal = Decimal("200"),
        minimum_order_notional: Decimal = Decimal("1000"),
        max_quote_age_seconds: int = 30,
        max_spread: Decimal = Decimal("0.02"),
    ) -> dict[str, Any]:
        """Preview a Schwab SGOV cash sweep; never submit an order."""
        try:
            request = BrokerOrderPreviewInput.model_validate(
                {
                    "account_refs": account_refs,
                    "instrument_id": instrument_id,
                    "hard_cash_floor": hard_cash_floor,
                    "operational_buffer": operational_buffer,
                    "minimum_order_notional": minimum_order_notional,
                    "max_quote_age_seconds": max_quote_age_seconds,
                    "max_spread": max_spread,
                }
            )
            return (await container.services.broker_order_preview.preview(request)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def order_preview(
        account_ref: str,
        instrument_id: str,
        instruction: BrokerOrderInstruction,
        quantity: int,
        order_type: BrokerOrderType,
        session: BrokerOrderSession = BrokerOrderSession.NORMAL,
        duration: BrokerOrderDuration = BrokerOrderDuration.DAY,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        trail_offset: Decimal | None = None,
        trail_type: BrokerTrailType | None = None,
        limit_offset: Decimal | None = None,
        idempotency_key: str = "",
        preview_ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        """Create a short-lived, single-use exact Schwab order preview."""
        try:
            request = BrokerOrderIntentPreviewInput.model_validate(
                {
                    "account_ref": account_ref,
                    "instrument_id": instrument_id,
                    "instruction": instruction,
                    "quantity": quantity,
                    "order_type": order_type,
                    "session": session,
                    "duration": duration,
                    "limit_price": limit_price,
                    "stop_price": stop_price,
                    "trail_offset": trail_offset,
                    "trail_type": trail_type,
                    "limit_offset": limit_offset,
                    "idempotency_key": idempotency_key,
                    "preview_ttl_seconds": preview_ttl_seconds,
                }
            )
            return (await container.services.broker_orders.preview(request)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def order_submit(
        order_intent_id: str,
        idempotency_key: str,
        confirmed_by: Literal["user"],
        submitted_via: Literal["codex_chat", "mcp_chat"],
        authorization_note: str,
    ) -> dict[str, Any]:
        """Submit exactly one unexpired preview after explicit user confirmation."""
        try:
            request = BrokerOrderSubmitInput.model_validate(
                {
                    "order_intent_id": order_intent_id,
                    "idempotency_key": idempotency_key,
                    "confirmed_by": confirmed_by,
                    "submitted_via": submitted_via,
                    "authorization_note": authorization_note,
                }
            )
            return (await container.services.broker_orders.submit(request)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def order_status(
        order_intent_id: str,
        refresh_provider: bool = False,
    ) -> dict[str, Any]:
        """Read one durable order receipt and optionally refresh its Schwab status."""
        try:
            request = BrokerOrderStatusInput.model_validate(
                {
                    "order_intent_id": order_intent_id,
                    "refresh_provider": refresh_provider,
                }
            )
            return (await container.services.broker_orders.status(request)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def order_cancel(
        order_intent_id: str,
        idempotency_key: str,
        confirmed_by: Literal["user"],
        submitted_via: Literal["codex_chat", "mcp_chat"],
        authorization_note: str,
    ) -> dict[str, Any]:
        """Request cancellation of one exact submitted order after user confirmation."""
        try:
            request = BrokerOrderCancelInput.model_validate(
                {
                    "order_intent_id": order_intent_id,
                    "idempotency_key": idempotency_key,
                    "confirmed_by": confirmed_by,
                    "submitted_via": submitted_via,
                    "authorization_note": authorization_note,
                }
            )
            return (await container.services.broker_orders.cancel(request)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    return SimpleNamespace(
        cash_sweep_preview=cash_sweep_preview,
        order_preview=order_preview,
        order_submit=order_submit,
        order_status=order_status,
        order_cancel=order_cancel,
    )
