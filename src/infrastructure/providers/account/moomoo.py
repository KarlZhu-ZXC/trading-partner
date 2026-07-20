"""Moomoo OpenD read-only account adapter with raw SDK isolation."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError, ProviderNotConfigured, ProviderUnavailableError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import (
    AccountOpenOrder,
    AccountPosition,
    AccountSnapshot,
    AccountTransaction,
)
from infrastructure.providers.moomoo_rate_limiter import (
    MoomooOpenDOperation,
    OpenDRequestLimiter,
)
from infrastructure.system.clock import SystemClock


class _ReadContext(Protocol):
    def get_acc_list(self) -> tuple[object, object]: ...
    def accinfo_query(self, **kwargs: object) -> tuple[object, object]: ...
    def position_list_query(self, **kwargs: object) -> tuple[object, object]: ...
    def order_list_query(self, **kwargs: object) -> tuple[object, object]: ...
    def history_deal_list_query(self, **kwargs: object) -> tuple[object, object]: ...
    def close(self) -> object: ...


ContextFactory = Callable[[str, int], _ReadContext]


def _default_factory(host: str, port: int) -> _ReadContext:
    try:
        import moomoo
    except ImportError:
        raise ProviderNotConfigured("Moomoo SDK is unavailable") from None
    # The SDK otherwise writes connection ids and context object identities to
    # stderr. Adapter failures remain typed; raw SDK diagnostics stay internal.
    moomoo.SysConfig.enable_console_log(False)
    return cast(_ReadContext, moomoo.OpenSecTradeContext(host=host, port=port))


def _records(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return list(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        rows = to_dict(orient="records")
        if isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows):
            return list(rows)
    raise DataContractError(
        "Moomoo response table is invalid",
        details={"vendor": VendorId.MOOMOO.value, "operation": "account"},
    )


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "" or value == "N/A":
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _account_ref(raw: object) -> str:
    digest = hashlib.sha256(f"moomoo:{raw}".encode()).hexdigest()[:24]
    return f"moomoo_{digest}"


def _provider_order_ref(raw: object) -> str:
    return hashlib.sha256(f"moomoo-order:{raw}".encode()).hexdigest()[:32]


def _provider_transaction_ref(raw: object) -> str:
    return hashlib.sha256(f"moomoo-transaction:{raw}".encode()).hexdigest()[:32]


def _instrument_id(code: object) -> str:
    if not isinstance(code, str) or "." not in code:
        raise DataContractError("Moomoo instrument code is invalid")
    prefix, symbol = code.split(".", 1)
    if prefix == "US":
        market, canonical = Market.US, symbol.upper()
    elif prefix in {"SH", "SZ"}:
        market, canonical = Market.A_SHARE, f"{symbol}.{prefix}"
    else:
        raise DataContractError("Moomoo instrument market is unsupported")
    return build_instrument_id(AssetType.EQUITY, market, canonical)


class MoomooAccountAdapter:
    """Read current REAL account facts; no SDK object escapes this adapter."""

    def __init__(
        self,
        id_generator: IdGenerator,
        *,
        enabled: bool,
        host: str,
        port: int,
        account_ids: Sequence[str] = (),
        clock: Clock | None = None,
        context_factory: ContextFactory | None = None,
        opend_rate_limiter: OpenDRequestLimiter | None = None,
    ) -> None:
        self._ids = id_generator
        self._enabled = enabled
        self._host = host
        self._port = port
        self._account_ids = frozenset(item.strip() for item in account_ids if item.strip())
        self._clock = clock or SystemClock()
        self._factory = context_factory or _default_factory
        self._opend_rate_limiter = opend_rate_limiter

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.MOOMOO

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market in {Market.A_SHARE, Market.US} and category is DataCategory.ACCOUNT

    def is_configured(self) -> bool:
        return self._enabled

    async def get_account_snapshots(
        self, *, as_of: datetime
    ) -> ProviderSuccess[tuple[AccountSnapshot, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo account provider is disabled")
        return await asyncio.to_thread(self._read_current)

    async def get_account_transactions(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> ProviderSuccess[tuple[AccountTransaction, ...]]:
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo account provider is disabled")
        for name, value in (("start", start), ("end", end)):
            if value is not None:
                require_aware_datetime(value, field_name=name)
        if start is not None and end is not None and start > end:
            raise DataContractError("transaction start must be <= end")
        if not 1 <= limit <= 1_000:
            raise DataContractError("transaction limit must be in [1,1000]")
        return await asyncio.to_thread(self._read_transactions, start, end, limit)

    def _read_transactions(
        self, start: datetime | None, end: datetime | None, limit: int
    ) -> ProviderSuccess[tuple[AccountTransaction, ...]]:
        try:
            context = self._factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError("Moomoo OpenD connection failed") from None
        try:
            accounts = self._query(context.get_acc_list())
            items: list[AccountTransaction] = []
            for account in accounts:
                raw_id = account.get("acc_id")
                if (
                    raw_id in {None, ""}
                    or str(account.get("trd_env", "")).upper() != "REAL"
                    or (self._account_ids and str(raw_id) not in self._account_ids)
                ):
                    continue
                kwargs: dict[str, object] = {"trd_env": "REAL", "acc_id": raw_id}
                if start is not None:
                    kwargs["start"] = start.date().isoformat()
                if end is not None:
                    kwargs["end"] = end.date().isoformat()
                self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_HISTORY_DEALS, raw_id)
                rows = self._query(context.history_deal_list_query(**kwargs))
                items.extend(self._transaction(raw_id, row) for row in rows)
        finally:
            context.close()
        items.sort(key=lambda item: (item.occurred_at, item.provider_transaction_id), reverse=True)
        fetched_at = self._clock.now()
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.ACCOUNT,
            SourceRole.PRIMARY,
            fetched_at,
            fetched_at,
            Freshness.FRESH,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            0,
            ("TRANSACTION_FEES_UNAVAILABLE",),
        )
        return ProviderSuccess(tuple(items[:limit]), meta)

    def _read_current(self) -> ProviderSuccess[tuple[AccountSnapshot, ...]]:
        try:
            context = self._factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError("Moomoo OpenD connection failed") from None
        try:
            accounts = self._query(context.get_acc_list())
            snapshots = tuple(
                self._read_account(context, row)
                for row in accounts
                if str(row.get("trd_env", "")).upper() == "REAL"
                and (
                    not self._account_ids
                    or str(row.get("acc_id", "")) in self._account_ids
                )
            )
        finally:
            context.close()
        fetched_at = self._clock.now()
        normalized = tuple(
            AccountSnapshot(
                item.snapshot_id,
                item.account_ref,
                item.provider,
                item.environment,
                item.base_currency,
                fetched_at,
                fetched_at,
                item.cash,
                item.buying_power,
                item.net_assets,
                item.margin_used,
                item.positions,
                item.open_orders,
                item.degraded,
                item.warning_codes,
            )
            for item in snapshots
        )
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.ACCOUNT,
            SourceRole.PRIMARY,
            fetched_at,
            fetched_at,
            Freshness.FRESH,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            0,
            ("PRICE_TIME_UNAVAILABLE", "ASSET_TYPE_ASSUMED_EQUITY"),
        )
        return ProviderSuccess(normalized, meta)

    @staticmethod
    def _query(result: tuple[object, object]) -> list[Mapping[str, object]]:
        code, value = result
        if code != 0:
            raise ProviderUnavailableError("Moomoo OpenD read failed")
        return _records(value)

    def _wait_for_quota(self, operation: MoomooOpenDOperation, raw_id: object) -> None:
        if self._opend_rate_limiter is not None:
            self._opend_rate_limiter.wait(operation, scope=_account_ref(raw_id))

    def _read_account(
        self, context: _ReadContext, account: Mapping[str, object]
    ) -> AccountSnapshot:
        raw_id = account.get("acc_id")
        if raw_id in {None, ""}:
            raise DataContractError("Moomoo account id is missing")
        kwargs: dict[str, object] = {
            "trd_env": "REAL",
            "acc_id": raw_id,
            "refresh_cache": True,
        }
        self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_FUNDS, raw_id)
        info_rows = self._query(context.accinfo_query(**kwargs))
        self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_POSITIONS, raw_id)
        position_rows = self._query(context.position_list_query(**kwargs))
        self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_ORDERS, raw_id)
        order_rows = self._query(
            context.order_list_query(
                **kwargs,
                status_filter_list=(
                    "WAITING_SUBMIT",
                    "SUBMITTING",
                    "SUBMITTED",
                    "FILLED_PART",
                ),
            )
        )
        info = info_rows[0] if info_rows else {}
        return AccountSnapshot(
            snapshot_id=self._ids.new(EntityIdPrefix.SNAPSHOT),
            account_ref=_account_ref(raw_id),
            provider=self.vendor_id,
            environment=AccountEnvironment.REAL,
            base_currency=str(info.get("currency") or "USD").upper(),
            account_as_of=self._clock.now(),
            fetched_at=self._clock.now(),
            cash=_decimal(info.get("cash")),
            buying_power=_decimal(info.get("power")),
            net_assets=_decimal(info.get("total_assets")),
            margin_used=_decimal(info.get("initial_margin")),
            positions=tuple(self._position(row) for row in position_rows),
            open_orders=tuple(self._order(row) for row in order_rows),
            degraded=True,
            warning_codes=("PRICE_TIME_UNAVAILABLE", "ASSET_TYPE_ASSUMED_EQUITY"),
        )

    @staticmethod
    def _position(row: Mapping[str, object]) -> AccountPosition:
        side = (
            AccountPositionSide.SHORT
            if str(row.get("position_side", "")).upper() == "SHORT"
            else AccountPositionSide.LONG
        )
        quantity = _decimal(row.get("qty"))
        if quantity is None:
            raise DataContractError("Moomoo position quantity is invalid")
        return AccountPosition(
            instrument_id=_instrument_id(row.get("code")),
            side=side,
            quantity=abs(quantity),
            sellable_quantity=_decimal(row.get("can_sell_qty")),
            average_cost=_decimal(row.get("average_cost")),
            diluted_cost=_decimal(row.get("diluted_cost")),
            market_price=None,
            market_price_at=None,
            market_value=_decimal(row.get("market_val")),
            unrealized_pnl=_decimal(row.get("unrealized_pl")),
            realized_pnl=_decimal(row.get("realized_pl")),
            currency=str(row.get("currency") or "USD").upper(),
        )

    @staticmethod
    def _order(row: Mapping[str, object]) -> AccountOpenOrder:
        raw_side = str(row.get("trd_side", "")).upper()
        side = (
            AccountOpenOrderSide.BUY
            if raw_side in {"BUY", "BUY_BACK"}
            else AccountOpenOrderSide.SELL
        )
        raw_status = str(row.get("order_status", "")).upper()
        status = (
            AccountOpenOrderStatus.PARTIAL
            if raw_status == "FILLED_PART"
            else AccountOpenOrderStatus.PENDING
            if raw_status in {"WAITING_SUBMIT", "SUBMITTING", "SUBMITTED"}
            else AccountOpenOrderStatus.UNKNOWN
        )
        quantity = _decimal(row.get("qty")) or Decimal(0)
        filled = _decimal(row.get("dealt_qty")) or Decimal(0)
        submitted_at: datetime | None = None
        raw_time = row.get("create_time")
        if isinstance(raw_time, str) and raw_time.strip():
            try:
                submitted_at = datetime.fromisoformat(raw_time)
                if submitted_at.tzinfo is None:
                    submitted_at = None
            except ValueError:
                pass
        return AccountOpenOrder(
            provider_order_id=_provider_order_ref(row.get("order_id")),
            instrument_id=_instrument_id(row.get("code")),
            side=side,
            status=status,
            quantity=quantity,
            filled_quantity=filled,
            limit_price=_decimal(row.get("price")),
            submitted_at=submitted_at,
        )

    @staticmethod
    def _transaction(raw_account_id: object, row: Mapping[str, object]) -> AccountTransaction:
        raw_side = str(row.get("trd_side", "")).upper()
        if raw_side in {"BUY", "BUY_BACK"}:
            side = AccountTransactionSide.BUY
        elif raw_side in {"SELL", "SELL_SHORT"}:
            side = AccountTransactionSide.SELL
        else:
            raise DataContractError("Moomoo transaction side is unsupported")
        instrument_id = _instrument_id(row.get("code"))
        market = instrument_id.split(":", 2)[1]
        timezone = ZoneInfo("America/New_York" if market == Market.US.value else "Asia/Shanghai")
        raw_time = row.get("create_time")
        if not isinstance(raw_time, str) or not raw_time.strip():
            raise DataContractError("Moomoo transaction time is missing")
        try:
            occurred_at = datetime.fromisoformat(raw_time)
        except ValueError:
            raise DataContractError("Moomoo transaction time is invalid") from None
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone)
        quantity = _decimal(row.get("qty"))
        price = _decimal(row.get("price"))
        if quantity is None or quantity <= 0 or price is None:
            raise DataContractError("Moomoo transaction quantity or price is invalid")
        transaction_id = row.get("deal_id")
        if transaction_id in {None, ""}:
            raise DataContractError("Moomoo transaction id is missing")
        return AccountTransaction(
            provider_transaction_id=_provider_transaction_ref(transaction_id),
            account_ref=_account_ref(raw_account_id),
            provider=VendorId.MOOMOO,
            instrument_id=instrument_id,
            kind=AccountTransactionKind.TRADE,
            side=side,
            quantity=quantity,
            price=price,
            fees=Decimal(0),
            currency="USD" if market == Market.US.value else "CNY",
            occurred_at=occurred_at,
        )
