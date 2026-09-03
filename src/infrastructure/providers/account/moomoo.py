"""Moomoo OpenD read-only account adapter with raw SDK isolation."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
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
from domain.common.errors import (
    DataContractError,
    ProviderNotConfigured,
    ProviderUnavailableError,
    TradingPartnerError,
)
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
    AccountActivityBatch,
    AccountOpenOrder,
    AccountPosition,
    AccountSnapshot,
    AccountTransaction,
    ProviderAccountActivityCoverage,
)
from infrastructure.providers.moomoo_opend import ensure_moomoo_opend_running
from infrastructure.providers.moomoo_rate_limiter import (
    MoomooOpenDOperation,
    OpenDRequestLimiter,
)
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)
from infrastructure.system.clock import SystemClock


class _ReadContext(Protocol):
    def get_acc_list(self) -> tuple[object, object]: ...
    def accinfo_query(self, **kwargs: object) -> tuple[object, object]: ...
    def position_list_query(self, **kwargs: object) -> tuple[object, object]: ...
    def order_list_query(self, **kwargs: object) -> tuple[object, object]: ...
    def history_deal_list_query(self, **kwargs: object) -> tuple[object, object]: ...
    def order_fee_query(self, **kwargs: object) -> tuple[object, object]: ...
    def close(self) -> object: ...


ContextFactory = Callable[[str, int], _ReadContext]

_FEE_ORDER_CHUNK_SIZE = 20
_US_OPTION_SYMBOL = re.compile(r"^[A-Z0-9.]{1,8}\d{6}[CP]\d+$")


def _default_factory(host: str, port: int) -> _ReadContext:
    try:
        import moomoo
    except ImportError:
        raise ProviderNotConfigured("Moomoo SDK is unavailable") from None
    # The SDK otherwise writes connection ids and context object identities to
    # stderr. Adapter failures remain typed; raw SDK diagnostics stay internal.
    moomoo.SysConfig.enable_console_log(False)
    ensure_moomoo_opend_running(host, port)
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


def _nonnegative_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _optional_identifier(value: object) -> str | None:
    """Return a bounded-enough textual provider identifier, or no identifier.

    OpenD normally returns order identifiers as strings, but some SDK versions
    expose numeric identifiers.  Fee enrichment only needs a stable lookup key;
    unsupported values are treated as missing and therefore remain explicitly
    unavailable instead of escaping raw SDK objects.
    """

    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float, Decimal)):
        return None
    identifier = str(value).strip()
    return identifier or None


def _account_ref(raw: object) -> str:
    digest = hashlib.sha256(f"moomoo:{raw}".encode()).hexdigest()[:24]
    return f"moomoo_{digest}"


def _provider_order_ref(raw: object) -> str:
    return hashlib.sha256(f"moomoo-order:{raw}".encode()).hexdigest()[:32]


def _provider_transaction_ref(raw: object) -> str:
    return hashlib.sha256(f"moomoo-transaction:{raw}".encode()).hexdigest()[:32]


def _instrument_id(code: object, corrections: MoomooSecurityCorrections) -> str:
    if not isinstance(code, str) or "." not in code:
        raise DataContractError("Moomoo instrument code is invalid")
    prefix, symbol = code.split(".", 1)
    symbol = symbol.replace(" ", "").upper()
    if prefix == "US":
        market, canonical = Market.US, symbol.upper()
    elif prefix in {"SH", "SZ"}:
        market, canonical = Market.A_SHARE, f"{symbol}.{prefix}"
    else:
        raise DataContractError("Moomoo instrument market is unsupported")
    correction = corrections.for_code(code.upper())
    asset_type = correction.asset_type if correction is not None else AssetType.EQUITY
    # OpenD account rows do not carry an asset-type discriminator.  US option
    # deal symbols use the stable OCC-style root/date/C|P/strike shape, though
    # the SDK may insert padding spaces between the root and expiry.
    if market is Market.US and _US_OPTION_SYMBOL.fullmatch(canonical):
        asset_type = AssetType.OPTION
    return build_instrument_id(asset_type, market, canonical)


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
        base_currency: str = "USD",
        clock: Clock | None = None,
        context_factory: ContextFactory | None = None,
        opend_rate_limiter: OpenDRequestLimiter | None = None,
        security_corrections: MoomooSecurityCorrections | None = None,
    ) -> None:
        self._ids = id_generator
        self._enabled = enabled
        self._host = host
        self._port = port
        self._account_ids = frozenset(item.strip() for item in account_ids if item.strip())
        self._base_currency = base_currency.strip().upper()
        if self._base_currency not in {"USD", "HKD", "CNH", "JPY", "SGD"}:
            raise DataContractError("Moomoo account base_currency is unsupported")
        self._clock = clock or SystemClock()
        self._factory = context_factory or _default_factory
        self._opend_rate_limiter = opend_rate_limiter
        self._security_corrections = security_corrections or MoomooSecurityCorrections.empty()

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
        try:
            return await asyncio.to_thread(self._read_current)
        except TradingPartnerError:
            raise
        except Exception:
            raise ProviderUnavailableError(
                "Moomoo account snapshot read failed",
                details={"vendor": VendorId.MOOMOO.value, "operation": "account_snapshot"},
                code="MOOMOO_ACCOUNT_SNAPSHOT_UNAVAILABLE",
            ) from None

    async def get_account_transactions(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> ProviderSuccess[AccountActivityBatch]:
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
    ) -> ProviderSuccess[AccountActivityBatch]:
        requested_end = end or self._clock.now()
        requested_start = start or requested_end - timedelta(days=60)
        try:
            context = self._factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError("Moomoo OpenD connection failed") from None
        try:
            accounts = self._query(context.get_acc_list())
            items: list[AccountTransaction] = []
            account_refs: list[str] = []
            account_fee_complete: dict[str, bool] = {}
            for account in accounts:
                raw_id = account.get("acc_id")
                if (
                    raw_id in {None, ""}
                    or str(account.get("trd_env", "")).upper() != "REAL"
                    or (self._account_ids and str(raw_id) not in self._account_ids)
                ):
                    continue
                account_refs.append(_account_ref(raw_id))
                kwargs: dict[str, object] = {"trd_env": "REAL", "acc_id": raw_id}
                kwargs["start"] = requested_start.date().isoformat()
                kwargs["end"] = requested_end.date().isoformat()
                self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_HISTORY_DEALS, raw_id)
                rows = self._query(context.history_deal_list_query(**kwargs))
                account_items = [self._transaction(raw_id, row) for row in rows]
                enriched_items, fees_complete = self._enrich_transaction_fees(
                    context, raw_id, rows, account_items
                )
                items.extend(enriched_items)
                account_fee_complete[_account_ref(raw_id)] = fees_complete
        finally:
            context.close()
        items.sort(key=lambda item: (item.occurred_at, item.provider_transaction_id), reverse=True)
        fetched_at = self._clock.now()
        truncated = len(items) > limit
        fees_complete = bool(items) and all(item.fees is not None for item in items)
        gap_codes = ["MOOMOO_ACTIVITY_TYPES_UNAVAILABLE"]
        if not fees_complete:
            gap_codes.insert(0, "TRANSACTION_FEES_UNAVAILABLE")
        if truncated:
            gap_codes.append("PROVIDER_RESULT_TRUNCATED")
        warnings = list(gap_codes)
        if start is None:
            warnings.append("TRANSACTION_WINDOW_DEFAULTED")
        unavailable_kinds = tuple(
            kind for kind in AccountTransactionKind if kind is not AccountTransactionKind.TRADE
        )
        coverage = tuple(
            ProviderAccountActivityCoverage(
                account_ref=account_ref,
                requested_start=requested_start,
                requested_end=requested_end,
                effective_start=requested_start,
                effective_end=requested_end,
                mapping_version="moomoo_deals_v2",
                supported_kinds=(AccountTransactionKind.TRADE,),
                unavailable_kinds=unavailable_kinds,
                gap_codes=tuple(
                    code
                    for code in gap_codes
                    if code != "TRANSACTION_FEES_UNAVAILABLE"
                    or not account_fee_complete.get(account_ref, False)
                ),
                truncated=truncated,
            )
            for account_ref in account_refs
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
            tuple(warnings),
        )
        return ProviderSuccess(AccountActivityBatch(tuple(items[:limit]), coverage), meta)

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
                and (not self._account_ids or str(row.get("acc_id", "")) in self._account_ids)
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
                tuple(
                    replace(position, market_price_at=fetched_at)
                    if position.market_price is not None
                    else position
                    for position in item.positions
                ),
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
            tuple(sorted({code for item in normalized for code in item.warning_codes})),
        )
        return ProviderSuccess(normalized, meta)

    @staticmethod
    def _query(result: tuple[object, object]) -> list[Mapping[str, object]]:
        code, value = result
        if code != 0:
            raise ProviderUnavailableError("Moomoo OpenD read failed")
        return _records(value)

    def _enrich_transaction_fees(
        self,
        context: _ReadContext,
        raw_account_id: object,
        rows: Sequence[Mapping[str, object]],
        transactions: Sequence[AccountTransaction],
    ) -> tuple[tuple[AccountTransaction, ...], bool]:
        """Best-effort fee enrichment for one account's history response.

        OpenD reports one fee total per order while history deals can contain
        several partial fills.  Query chunks are deliberately bounded and each
        failed or malformed chunk remains unresolved; transaction history itself
        is still returned to the caller.
        """

        order_deals: dict[str, list[int]] = {}
        order_ids: list[str] = []
        for index, row in enumerate(rows):
            order_id = _optional_identifier(row.get("order_id"))
            if order_id is None:
                continue
            if order_id not in order_deals:
                order_deals[order_id] = []
                order_ids.append(order_id)
            order_deals[order_id].append(index)

        if not order_ids:
            return tuple(transactions), False

        fee_by_order: dict[str, Decimal] = {}
        for offset in range(0, len(order_ids), _FEE_ORDER_CHUNK_SIZE):
            chunk = order_ids[offset : offset + _FEE_ORDER_CHUNK_SIZE]
            try:
                self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_ORDER_FEES, raw_account_id)
                fee_rows = self._query(
                    context.order_fee_query(
                        order_id_list=chunk,
                        trd_env="REAL",
                        acc_id=raw_account_id,
                    )
                )
                fee_by_order.update(self._parse_fee_rows(fee_rows, expected=set(chunk)))
            except Exception:
                # Fee enrichment is optional.  Do not let an SDK, admission, or
                # response-shape failure discard otherwise valid trade history.
                continue

        enriched = list(transactions)
        for order_id, indices in order_deals.items():
            total_fee = fee_by_order.get(order_id)
            if total_fee is None:
                continue
            allocations = self._allocate_order_fee(
                total_fee, tuple(transactions[index] for index in indices)
            )
            for index, allocation in zip(indices, allocations, strict=True):
                enriched[index] = replace(enriched[index], fees=allocation)
        return tuple(enriched), bool(enriched) and all(item.fees is not None for item in enriched)

    @staticmethod
    def _parse_fee_rows(
        rows: Sequence[Mapping[str, object]], *, expected: set[str]
    ) -> dict[str, Decimal]:
        parsed: dict[str, Decimal] = {}
        for row in rows:
            order_id = _optional_identifier(row.get("order_id"))
            # Current SDK responses call this field ``fee_amount`` while the
            # documented order-fee contract and older fixtures call it
            # ``total_fee``.  Prefer the SDK field when it is present and only
            # use the compatibility alias when it is absent.
            fee_value = row["fee_amount"] if "fee_amount" in row else row.get("total_fee")
            total_fee = _nonnegative_decimal(fee_value)
            if (
                order_id is None
                or order_id not in expected
                or total_fee is None
                or order_id in parsed
            ):
                raise DataContractError("Moomoo order fee response is invalid")
            parsed[order_id] = total_fee
        return parsed

    @staticmethod
    def _allocate_order_fee(
        total_fee: Decimal, deals: Sequence[AccountTransaction]
    ) -> tuple[Decimal, ...]:
        if not deals:
            return ()
        if len(deals) == 1:
            return (total_fee,)

        notionals = tuple(
            abs((deal.quantity or Decimal(0)) * (deal.price or Decimal(0))) for deal in deals
        )
        weights = (
            notionals
            if sum(notionals, Decimal(0)) > 0
            else tuple(abs(deal.quantity or Decimal(0)) for deal in deals)
        )
        total_weight = sum(weights, Decimal(0))
        if total_weight <= 0:
            # AccountTransaction validates positive trade quantities, so this is
            # defensive only; keeping it explicit avoids a division-by-zero path
            # if the model contract changes.
            return tuple(Decimal(0) for _ in deals[:-1]) + (total_fee,)

        allocations: list[Decimal] = []
        remaining_fee = total_fee
        remaining_weight = total_weight
        for weight in weights[:-1]:
            allocation = remaining_fee * weight / remaining_weight if weight > 0 else Decimal(0)
            allocation = min(max(allocation, Decimal(0)), remaining_fee)
            allocations.append(allocation)
            remaining_fee -= allocation
            remaining_weight -= weight
        allocations.append(remaining_fee)
        return tuple(allocations)

    def _wait_for_quota(self, operation: MoomooOpenDOperation, raw_id: object) -> None:
        if self._opend_rate_limiter is not None:
            self._opend_rate_limiter.wait(operation, scope=_account_ref(raw_id))

    def _read_account(
        self, context: _ReadContext, account: Mapping[str, object]
    ) -> AccountSnapshot:
        raw_id = account.get("acc_id")
        if raw_id in {None, ""}:
            raise DataContractError("Moomoo account id is missing")
        common_kwargs: dict[str, object] = {
            "trd_env": "REAL",
            "acc_id": raw_id,
            "refresh_cache": True,
        }
        currency_kwargs = {
            **common_kwargs,
            # OpenD otherwise defaults funds and positions to a provider-specific
            # currency. Keep account-level values in one explicit base currency.
            "currency": self._base_currency,
        }
        self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_FUNDS, raw_id)
        info_rows = self._query(context.accinfo_query(**currency_kwargs))
        self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_POSITIONS, raw_id)
        position_rows = self._query(context.position_list_query(**currency_kwargs))
        supplemental_warnings: set[str] = set()
        open_orders: tuple[AccountOpenOrder, ...]
        try:
            self._wait_for_quota(MoomooOpenDOperation.ACCOUNT_ORDERS, raw_id)
            order_rows = self._query(
                context.order_list_query(
                    **common_kwargs,
                    status_filter_list=(
                        "WAITING_SUBMIT",
                        "SUBMITTING",
                        "SUBMITTED",
                        "FILLED_PART",
                    ),
                )
            )
            open_orders = tuple(self._order(row) for row in order_rows)
        except TradingPartnerError:
            # Account balances and positions remain useful when OpenD cannot return
            # or safely normalize its optional open-order view. The warning makes
            # the empty tuple explicitly unknown rather than "no open orders".
            open_orders = ()
            supplemental_warnings.add("MOOMOO_OPEN_ORDERS_UNAVAILABLE")
        info = info_rows[0] if info_rows else {}
        fetched_at = self._clock.now()
        positions_list: list[AccountPosition] = []
        for row in position_rows:
            quantity = _decimal(row.get("qty"))
            if quantity is None:
                raise DataContractError("Moomoo position quantity is invalid")
            if quantity == 0:
                # OpenD can retain a same-day zero-quantity row after a position is
                # closed. It is not a current holding and must not invalidate the
                # rest of the account snapshot.
                supplemental_warnings.add("MOOMOO_ZERO_QUANTITY_POSITION_OMITTED")
                continue
            positions_list.append(self._position(row, market_price_at=fetched_at))
        positions = tuple(positions_list)
        # OpenD exposes the provider ``debtCash`` field under the misleading SDK
        # name ``interest_charged_amount``. It is the interest-bearing balance
        # suitable for account-level financing usage. ``initial_margin`` is a
        # collateral requirement (and documented as futures-only), not borrowed
        # cash, so it must never populate the canonical ``margin_used`` field.
        margin_used = _nonnegative_decimal(info.get("interest_charged_amount"))
        return AccountSnapshot(
            snapshot_id=self._ids.new(EntityIdPrefix.SNAPSHOT),
            account_ref=_account_ref(raw_id),
            provider=self.vendor_id,
            environment=AccountEnvironment.REAL,
            base_currency=str(info.get("currency") or self._base_currency).upper(),
            account_as_of=fetched_at,
            fetched_at=fetched_at,
            cash=_decimal(info.get("cash")),
            buying_power=_decimal(info.get("power")),
            net_assets=_decimal(info.get("total_assets")),
            margin_used=margin_used,
            positions=positions,
            open_orders=open_orders,
            degraded=True,
            warning_codes=self._warning_codes(
                positions,
                margin_usage_available=margin_used is not None,
                supplemental_codes=tuple(supplemental_warnings),
            ),
        )

    @staticmethod
    def _warning_codes(
        positions: Sequence[AccountPosition],
        *,
        margin_usage_available: bool,
        supplemental_codes: Sequence[str] = (),
    ) -> tuple[str, ...]:
        codes = {"ASSET_TYPE_ASSUMED_EQUITY", *supplemental_codes}
        if not margin_usage_available:
            codes.add("MOOMOO_MARGIN_USAGE_UNAVAILABLE")
        if any(position.market_price is not None for position in positions):
            codes.add("PRICE_TIME_IS_FETCH_TIME")
        if any(
            position.market_value is not None and position.market_price_at is None
            for position in positions
        ):
            codes.add("PRICE_TIME_UNAVAILABLE")
        return tuple(sorted(codes))

    def _position(self, row: Mapping[str, object], *, market_price_at: datetime) -> AccountPosition:
        side = (
            AccountPositionSide.SHORT
            if str(row.get("position_side", "")).upper() == "SHORT"
            else AccountPositionSide.LONG
        )
        quantity = _decimal(row.get("qty"))
        if quantity is None:
            raise DataContractError("Moomoo position quantity is invalid")
        market_price = _decimal(row.get("nominal_price"))
        return AccountPosition(
            instrument_id=_instrument_id(row.get("code"), self._security_corrections),
            side=side,
            quantity=abs(quantity),
            sellable_quantity=_decimal(row.get("can_sell_qty")),
            average_cost=_decimal(row.get("average_cost")),
            diluted_cost=_decimal(row.get("diluted_cost")),
            market_price=market_price,
            market_price_at=(market_price_at if market_price is not None else None),
            market_value=_decimal(row.get("market_val")),
            unrealized_pnl=_decimal(row.get("unrealized_pl")),
            realized_pnl=_decimal(row.get("realized_pl")),
            currency=str(row.get("currency") or "USD").upper(),
        )

    def _order(self, row: Mapping[str, object]) -> AccountOpenOrder:
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
            instrument_id=_instrument_id(row.get("code"), self._security_corrections),
            side=side,
            status=status,
            quantity=quantity,
            filled_quantity=filled,
            limit_price=_decimal(row.get("price")),
            submitted_at=submitted_at,
        )

    def _transaction(self, raw_account_id: object, row: Mapping[str, object]) -> AccountTransaction:
        raw_side = str(row.get("trd_side", "")).upper()
        if raw_side in {"BUY", "BUY_BACK"}:
            side = AccountTransactionSide.BUY
        elif raw_side in {"SELL", "SELL_SHORT"}:
            side = AccountTransactionSide.SELL
        else:
            raise DataContractError("Moomoo transaction side is unsupported")
        instrument_id = _instrument_id(row.get("code"), self._security_corrections)
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
            fees=None,
            currency="USD" if market == Market.US.value else "CNY",
            occurred_at=occurred_at,
            cash_amount=None,
            source_type="HISTORY_DEAL",
            mapping_version="moomoo_deals_v2",
        )
