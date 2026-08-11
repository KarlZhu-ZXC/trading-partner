"""Deterministic SGOV shadow calculation; no order-write dependency exists here."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from application.dto.broker_execution import (
    BrokerOrderPreviewAccountDTO,
    BrokerOrderPreviewDTO,
    BrokerOrderPreviewInput,
    BrokerQuotePreviewDTO,
)
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.broker_quote_provider import BrokerQuoteProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import Freshness, Market, SourceRole, VendorId
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.portfolio.enums import AccountOpenOrderSide
from domain.portfolio.models import AccountSnapshot


class CashSweepShadowService:
    def __init__(
        self,
        accounts: AccountSnapshotRepository,
        quote_provider: BrokerQuoteProvider,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._accounts = accounts
        self._quotes = quote_provider
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def preview(
        self, request: BrokerOrderPreviewInput
    ) -> ToolEnvelope[BrokerOrderPreviewDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            snapshots = self._select_accounts(request.account_refs)
            quote = await self._quotes.get_quote(
                instrument_id=request.instrument_id,
                as_of=as_of,
            )
            price_basis: Literal["ask", "last"] = (
                "ask" if quote.ask is not None else "last"
            )
            price = quote.ask or quote.last
            if price is None:
                raise DataContractError("broker quote has no usable ask or last price")
            quote_age = max(0, int((as_of - quote.quote_at).total_seconds()))
            spread = (
                quote.ask - quote.bid
                if quote.ask is not None and quote.bid is not None
                else None
            )
            quote_blockers: list[str] = []
            if quote_age > request.max_quote_age_seconds:
                quote_blockers.append("BROKER_QUOTE_STALE")
            if spread is not None and spread > request.max_spread:
                quote_blockers.append("BROKER_QUOTE_SPREAD_TOO_WIDE")
            calculations = tuple(
                self._calculate_account(
                    snapshot,
                    request=request,
                    price=price,
                    quote_blockers=tuple(quote_blockers),
                )
                for snapshot in snapshots
            )
            warnings = self._warnings(calculations)
            data = BrokerOrderPreviewDTO(
                mode=request.mode,
                policy={
                    "instrument_id": request.instrument_id,
                    "cash_source": "currentBalances.cashBalance",
                    "hard_cash_floor": str(request.hard_cash_floor),
                    "operational_buffer": str(request.operational_buffer),
                    "minimum_order_notional": str(request.minimum_order_notional),
                    "max_quote_age_seconds": request.max_quote_age_seconds,
                    "max_spread": str(request.max_spread),
                    "whole_shares_only": True,
                    "order_type": "LIMIT",
                    "duration": "DAY",
                    "session": "NORMAL",
                },
                quote=BrokerQuotePreviewDTO(
                    instrument_id=quote.instrument_id,
                    symbol=quote.symbol,
                    source=quote.source,
                    quote_at=quote.quote_at,
                    bid=quote.bid,
                    ask=quote.ask,
                    last=quote.last,
                    price_basis=price_basis,
                    reference_limit_price=price,
                    spread=spread,
                    age_seconds=quote_age,
                ),
                accounts=calculations,
                total_quantity=sum(item.quantity for item in calculations),
                total_estimated_notional=sum(
                    (item.estimated_notional for item in calculations), Decimal(0)
                ),
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=as_of,
                fetched_at=as_of,
                freshness=(Freshness.FRESH if not quote_blockers else Freshness.STALE),
                sources=(
                    SourceReference(
                        name=VendorId.SCHWAB.value,
                        role=SourceRole.PRIMARY,
                        retrieved_at=max(item.fetched_at for item in snapshots),
                    ),
                    SourceReference(
                        name=f"{VendorId.SCHWAB.value}_quote",
                        role=SourceRole.PRIMARY,
                        retrieved_at=quote.quote_at,
                        data_delay_seconds=quote_age,
                    ),
                ),
                data=data,
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
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
            )

    def _select_accounts(self, account_refs: tuple[str, ...]) -> tuple[AccountSnapshot, ...]:
        snapshots = tuple(
            item
            for item in self._accounts.latest_accounts()
            if item.provider is VendorId.SCHWAB
            and (not account_refs or item.account_ref in account_refs)
        )
        if not snapshots:
            raise DataContractError("no durable Schwab account snapshots matched the request")
        missing = set(account_refs) - {item.account_ref for item in snapshots}
        if missing:
            raise DataContractError(
                "requested Schwab account snapshot is unavailable",
                details={"missing_account_count": len(missing)},
            )
        return tuple(sorted(snapshots, key=lambda item: item.account_ref))

    @staticmethod
    def _calculate_account(
        snapshot: AccountSnapshot,
        *,
        request: BrokerOrderPreviewInput,
        price: Decimal,
        quote_blockers: tuple[str, ...],
    ) -> BrokerOrderPreviewAccountDTO:
        blockers = list(quote_blockers)
        if snapshot.base_currency != "USD":
            blockers.append("ACCOUNT_BASE_CURRENCY_NOT_USD")
        if snapshot.cash is None:
            blockers.append("CASH_BALANCE_UNAVAILABLE")
        if snapshot.margin_used is None:
            blockers.append("MARGIN_BALANCE_UNAVAILABLE")
        elif snapshot.margin_used != 0:
            blockers.append("MARGIN_BALANCE_NONZERO")
        if "SCHWAB_OPEN_ORDERS_UNAVAILABLE" in snapshot.warning_codes:
            blockers.append("OPEN_ORDERS_UNAVAILABLE")

        open_buy_reserve = Decimal(0)
        for order in snapshot.open_orders:
            if order.side is not AccountOpenOrderSide.BUY:
                continue
            if order.limit_price is None:
                blockers.append("OPEN_BUY_ORDER_RESERVE_UNAVAILABLE")
                continue
            open_buy_reserve += (order.quantity - order.filled_quantity) * order.limit_price

        cash = snapshot.cash
        reserved = (
            request.hard_cash_floor + request.operational_buffer + open_buy_reserve
            if cash is not None
            else None
        )
        surplus = (
            max(Decimal(0), cash - reserved)
            if cash is not None and reserved is not None
            else None
        )
        quantity = 0
        estimated = Decimal(0)
        status: Literal["INDICATIVE", "BELOW_THRESHOLD", "BLOCKED"] = "BLOCKED"
        soft_quote_blockers = {
            "BROKER_QUOTE_STALE",
            "BROKER_QUOTE_SPREAD_TOO_WIDE",
        }
        if surplus is not None and (
            not blockers or set(blockers) <= soft_quote_blockers
        ):
            quantity = int((surplus / price).to_integral_value(rounding=ROUND_FLOOR))
            estimated = price * quantity
            if quantity == 0 or estimated < request.minimum_order_notional:
                quantity = 0
                estimated = Decimal(0)
                status = "BELOW_THRESHOLD"
            else:
                status = "INDICATIVE"

        projected = (
            cash - open_buy_reserve - estimated if cash is not None else None
        )
        residual = (
            projected - request.hard_cash_floor - request.operational_buffer
            if projected is not None
            else None
        )
        payload = None
        if quantity > 0:
            payload = {
                "session": "NORMAL",
                "duration": "DAY",
                "orderType": "LIMIT",
                "price": str(price),
                "quantity": quantity,
                "orderStrategyType": "SINGLE",
                "orderLegCollection": [
                    {
                        "instruction": "BUY",
                        "quantity": quantity,
                        "instrument": {"symbol": "SGOV", "assetType": "EQUITY"},
                    }
                ],
            }
        return BrokerOrderPreviewAccountDTO(
            account_ref=snapshot.account_ref,
            snapshot_id=snapshot.snapshot_id,
            snapshot_as_of=snapshot.account_as_of,
            cash_balance=cash,
            hard_cash_floor=request.hard_cash_floor,
            operational_buffer=request.operational_buffer,
            open_buy_order_reserve=open_buy_reserve,
            reserved_cash=reserved,
            surplus_cash=surplus,
            minimum_order_notional=request.minimum_order_notional,
            quantity=quantity,
            estimated_notional=estimated,
            projected_cash_after_all_open_buys=projected,
            residual_above_reserve=residual,
            status=status,
            blocker_codes=tuple(dict.fromkeys(blockers)),
            schwab_order_payload=payload,
        )

    @staticmethod
    def _warnings(
        calculations: tuple[BrokerOrderPreviewAccountDTO, ...]
    ) -> tuple[WarningInfo, ...]:
        codes = tuple(
            dict.fromkeys(code for item in calculations for code in item.blocker_codes)
        )
        return tuple(
            WarningInfo(
                code=code,
                message="Shadow preview is not currently executable.",
                details={},
            )
            for code in codes
        )
