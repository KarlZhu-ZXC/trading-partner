"""Charles Schwab account facts plus an isolated live-order adapter.

Only the authenticated schwab-py session is used. The account adapter remains
GET-only; live writes use a separate closed adapter with no generic request escape.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol, cast

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
    BrokerOrderRejected,
    BrokerOrderSubmissionUncertain,
    DataContractError,
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id, parse_instrument_id
from domain.execution.models import (
    BrokerExecutionAccountState,
    BrokerOrderStatusObservation,
    BrokerOrderSubmission,
    BrokerQuoteObservation,
)
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
from infrastructure.system.clock import SystemClock

_BASE_URL = "https://api.schwabapi.com"
_MAX_BROKER_QUOTE_CLOCK_SKEW = timedelta(seconds=5)

_ACTIVE_ORDER_STATUSES = frozenset(
    {
        "ACCEPTED",
        "AWAITING_CONDITION",
        "AWAITING_MANUAL_REVIEW",
        "AWAITING_PARENT_ORDER",
        "AWAITING_STOP_CONDITION",
        "AWAITING_UR_OUT",
        "NEW",
        "PENDING_ACTIVATION",
        "PENDING_CANCEL",
        "PENDING_REPLACE",
        "QUEUED",
        "WORKING",
    }
)


class SchwabReadClient(Protocol):
    """Smallest authenticated Schwab surface allowed inside Trading Partner."""

    def account_numbers(self) -> object: ...
    def accounts_with_positions(self) -> object: ...
    def orders(self, account_hash: str, start: datetime, end: datetime) -> object: ...
    def quote(self, symbol: str) -> object: ...
    def transactions(self, account_hash: str, start: datetime, end: datetime) -> object: ...


class SchwabOrderClient(Protocol):
    """Exact authenticated calls required by the live-order adapter."""

    def account_numbers(self) -> object: ...
    def accounts_with_positions(self) -> object: ...
    def orders(self, account_hash: str, start: datetime, end: datetime) -> object: ...
    def place_order(self, account_hash: str, payload: Mapping[str, object]) -> str: ...
    def get_order(self, account_hash: str, broker_order_id: str) -> object: ...
    def cancel_order(self, account_hash: str, broker_order_id: str) -> None: ...


class SchwabPyReadClient:
    """schwab-py backed GET-only client."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_path: Path,
    ) -> None:
        # schwab-py logs the OAuth token file path at INFO on load/refresh.
        # Provider exceptions are translated below, so the SDK logger is not an
        # application observability channel and must not cross the adapter boundary.
        logging.getLogger("schwab.auth").disabled = True
        if not token_path.is_file():
            raise ProviderAuthenticationError(
                "Schwab OAuth token is missing; run the dedicated project OAuth setup"
            )
        try:
            from schwab.auth import client_from_token_file
        except ImportError:
            raise ProviderNotConfigured("schwab-py is unavailable") from None
        try:
            self._client = client_from_token_file(
                str(token_path),
                client_id,
                client_secret,
            )
        except Exception:
            raise ProviderAuthenticationError("Schwab OAuth client initialization failed") from None

    def account_numbers(self) -> object:
        return self._get("/trader/v1/accounts/accountNumbers", operation="account_numbers")

    def accounts_with_positions(self) -> object:
        return self._get(
            "/trader/v1/accounts",
            params={"fields": "positions"},
            operation="account_snapshot",
        )

    def orders(self, account_hash: str, start: datetime, end: datetime) -> object:
        return self._get(
            f"/trader/v1/accounts/{account_hash}/orders",
            params={
                "fromEnteredTime": start.isoformat(),
                "toEnteredTime": end.isoformat(),
                "maxResults": "3000",
            },
            operation="orders",
        )

    def quote(self, symbol: str) -> object:
        return self._get(
            "/marketdata/v1/quotes",
            params={"symbols": symbol, "fields": "quote,reference"},
            operation="quote",
        )

    def transactions(self, account_hash: str, start: datetime, end: datetime) -> object:
        return self._get(
            f"/trader/v1/accounts/{account_hash}/transactions",
            params={"startDate": start.isoformat(), "endDate": end.isoformat()},
            operation="transactions",
        )

    def _get(
        self,
        path: str,
        *,
        operation: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        base_details: dict[str, object] = {
            "vendor": VendorId.SCHWAB.value,
            "operation": operation,
        }
        try:
            response = self._client.session.request(
                "GET", f"{_BASE_URL}{path}", params=dict(params or {}) or None
            )
        except Exception:
            raise ProviderUnavailableError(
                "Schwab read request failed",
                details={**base_details, "error_type": "transport_failure"},
            ) from None
        status = int(getattr(response, "status_code", 0))
        status_details = {
            **base_details,
            "error_type": "http_failure",
            "status_code": status,
            "status_class": f"{status // 100}xx" if 100 <= status <= 599 else "invalid",
        }
        if status in {401, 403}:
            raise ProviderAuthenticationError(
                "Schwab authentication or authorization failed", details=status_details
            )
        if status == 429:
            raise ProviderRateLimitError("Schwab rate limit exceeded", details=status_details)
        if status < 200 or status >= 300:
            raise ProviderUnavailableError(
                "Schwab read request returned an HTTP failure", details=status_details
            )
        try:
            return response.json()
        except Exception:
            raise DataContractError(
                "Schwab response is not valid JSON",
                details={**base_details, "error_type": "invalid_json"},
            ) from None


class SchwabPyOrderClient:
    """schwab-py authenticated session with only named order endpoints."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_path: Path,
    ) -> None:
        del redirect_uri
        logging.getLogger("schwab.auth").disabled = True
        if not token_path.is_file():
            raise ProviderAuthenticationError(
                "Schwab OAuth token is missing; run the dedicated project OAuth setup"
            )
        try:
            from schwab.auth import client_from_token_file
        except ImportError:
            raise ProviderNotConfigured("schwab-py is unavailable") from None
        try:
            self._client = client_from_token_file(str(token_path), client_id, client_secret)
        except Exception:
            raise ProviderAuthenticationError("Schwab OAuth client initialization failed") from None

    def account_numbers(self) -> object:
        return self._read("/trader/v1/accounts/accountNumbers")

    def accounts_with_positions(self) -> object:
        return self._read("/trader/v1/accounts", params={"fields": "positions"})

    def orders(self, account_hash: str, start: datetime, end: datetime) -> object:
        return self._read(
            f"/trader/v1/accounts/{account_hash}/orders",
            params={
                "fromEnteredTime": start.isoformat(),
                "toEnteredTime": end.isoformat(),
                "maxResults": "3000",
            },
        )

    def place_order(self, account_hash: str, payload: Mapping[str, object]) -> str:
        response = self._write(
            "POST", f"/trader/v1/accounts/{account_hash}/orders", json_body=payload
        )
        headers = getattr(response, "headers", {})
        location = headers.get("Location") or headers.get("location")
        order_id = location.rstrip("/").rsplit("/", 1)[-1] if isinstance(location, str) else ""
        if not order_id:
            raise BrokerOrderSubmissionUncertain(
                "Schwab accepted an order response without a usable order identifier"
            )
        return order_id

    def get_order(self, account_hash: str, broker_order_id: str) -> object:
        return self._read(f"/trader/v1/accounts/{account_hash}/orders/{broker_order_id}")

    def cancel_order(self, account_hash: str, broker_order_id: str) -> None:
        self._write("DELETE", f"/trader/v1/accounts/{account_hash}/orders/{broker_order_id}")

    def _read(self, path: str, *, params: Mapping[str, str] | None = None) -> object:
        try:
            response = self._client.session.request(
                "GET", f"{_BASE_URL}{path}", params=dict(params or {}) or None
            )
        except Exception:
            raise ProviderUnavailableError("Schwab read request failed") from None
        status = int(getattr(response, "status_code", 0))
        if status in {401, 403}:
            raise ProviderAuthenticationError("Schwab authentication or authorization failed")
        if status == 429:
            raise ProviderRateLimitError("Schwab rate limit exceeded")
        if status < 200 or status >= 300:
            raise ProviderUnavailableError("Schwab read request returned an HTTP failure")
        try:
            return response.json()
        except Exception:
            raise DataContractError("Schwab response is not valid JSON") from None

    def _write(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None = None,
    ) -> object:
        try:
            response = self._client.session.request(
                method,
                f"{_BASE_URL}{path}",
                json=dict(json_body) if json_body is not None else None,
            )
        except Exception:
            raise BrokerOrderSubmissionUncertain(
                "Schwab order write response was not received"
            ) from None
        status = int(getattr(response, "status_code", 0))
        if 200 <= status < 300:
            return response
        if status in {401, 403}:
            raise ProviderAuthenticationError(
                "Schwab authentication or trading authorization failed"
            )
        if status == 429:
            raise BrokerOrderRejected(
                "Schwab rejected the order write due to rate limiting",
                details={"http_status": status},
                code="SCHWAB_ORDER_RATE_LIMITED",
            )
        if 400 <= status < 500:
            raise BrokerOrderRejected(
                "Schwab rejected the order request",
                details={"http_status": status},
                code="SCHWAB_ORDER_REJECTED",
            )
        raise BrokerOrderSubmissionUncertain(
            "Schwab order write returned an uncertain server response",
            details={"http_status": status},
        )


ReadClientFactory = Callable[[], SchwabReadClient]
OrderClientFactory = Callable[[], SchwabOrderClient]


class SchwabBrokerQuoteAdapter:
    """Read one Schwab bid/ask snapshot for an auditable order preview."""

    def __init__(
        self,
        *,
        enabled: bool,
        client_id: str | None,
        client_secret: str | None,
        redirect_uri: str,
        token_path: Path,
        clock: Clock | None = None,
        client_factory: ReadClientFactory | None = None,
    ) -> None:
        self._enabled = enabled
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_path = token_path
        self._clock = clock or SystemClock()
        self._client_factory = client_factory

    def is_configured(self) -> bool:
        return bool(
            self._enabled
            and self._client_id
            and self._client_secret
            and (self._client_factory is not None or self._token_path.is_file())
        )

    def _client(self) -> SchwabReadClient:
        if self._client_factory is not None:
            return self._client_factory()
        if not self.is_configured():
            raise ProviderNotConfigured("Schwab broker quote is not configured")
        assert self._client_id is not None and self._client_secret is not None
        return SchwabPyReadClient(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            token_path=self._token_path,
        )

    async def get_quote(self, *, instrument_id: str, as_of: datetime) -> BrokerQuoteObservation:
        require_aware_datetime(as_of, field_name="as_of")
        asset_type, market, symbol = parse_instrument_id(instrument_id)
        if market is not Market.US or asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            raise DataContractError("Schwab broker quote requires a US equity or ETF")
        try:
            payload = await asyncio.to_thread(self._client().quote, symbol)
            retrieved_at = self._clock.now()
            root = _mapping(payload, "quote response")
            row = _mapping(root.get(symbol) or root.get(symbol.upper()), "quote instrument")
            quote = _mapping(row.get("quote"), "quote")
            quote_millis = quote.get("quoteTime") or quote.get("tradeTime")
            if not isinstance(quote_millis, int | float):
                raise DataContractError(
                    "Schwab quote timestamp is missing",
                    code="SCHWAB_QUOTE_TIMESTAMP_MISSING",
                )
            quote_at = datetime.fromtimestamp(float(quote_millis) / 1000, tz=UTC)
            source = VendorId.SCHWAB.value
            if quote_at > retrieved_at:
                if quote_at - retrieved_at > _MAX_BROKER_QUOTE_CLOCK_SKEW:
                    raise DataContractError(
                        "Schwab quote timestamp exceeds retrieval time",
                        code="SCHWAB_QUOTE_TIMESTAMP_FUTURE",
                    )
                # A bounded Provider/server clock skew is not a future market
                # fact. Preserve a truthful retrieval-time observation instead
                # of comparing against the pre-request ``as_of`` instant.
                quote_at = retrieved_at
                source = "schwab_retrieval_time"
            return BrokerQuoteObservation(
                instrument_id=instrument_id,
                symbol=symbol,
                quote_at=quote_at,
                bid=_decimal(quote.get("bidPrice")),
                ask=_decimal(quote.get("askPrice")),
                last=_decimal(quote.get("lastPrice")),
                source=source,
            )
        except TradingPartnerError:
            raise
        except Exception:
            raise ProviderUnavailableError(
                "Schwab broker quote read failed",
                details={"vendor": VendorId.SCHWAB.value, "operation": "broker_quote"},
                code="SCHWAB_BROKER_QUOTE_UNAVAILABLE",
            ) from None


class SchwabOrderExecutionAdapter:
    """Closed live-order adapter for configured REAL Schwab accounts."""

    def __init__(
        self,
        *,
        enabled: bool,
        client_id: str | None,
        client_secret: str | None,
        redirect_uri: str,
        token_path: Path,
        account_hashes: Sequence[str],
        clock: Clock | None = None,
        client_factory: OrderClientFactory | None = None,
    ) -> None:
        self._enabled = enabled
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_path = token_path
        self._account_hashes = frozenset(item.strip() for item in account_hashes if item.strip())
        self._clock = clock or SystemClock()
        self._client_factory = client_factory

    def is_configured(self) -> bool:
        return bool(
            self._enabled
            and self._client_id
            and self._client_secret
            and self._account_hashes
            and (self._client_factory is not None or self._token_path.is_file())
        )

    def _client(self) -> SchwabOrderClient:
        if self._client_factory is not None:
            return self._client_factory()
        if not self.is_configured():
            raise ProviderNotConfigured("Schwab live order execution is not configured")
        assert self._client_id is not None and self._client_secret is not None
        return SchwabPyOrderClient(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            token_path=self._token_path,
        )

    async def get_account_state(
        self, *, account_ref: str, observed_at: datetime
    ) -> BrokerExecutionAccountState:
        require_aware_datetime(observed_at, field_name="observed_at")
        return await asyncio.to_thread(self._read_account_state, account_ref, observed_at)

    async def place_order(
        self, *, account_ref: str, order_payload: Mapping[str, object]
    ) -> BrokerOrderSubmission:
        client = self._client()
        _, account_hash = self._resolve_account(client, account_ref)
        broker_order_id = await asyncio.to_thread(client.place_order, account_hash, order_payload)
        return BrokerOrderSubmission(
            broker_order_id=broker_order_id,
            submitted_at=self._clock.now(),
            http_status=201,
        )

    async def get_order(
        self, *, account_ref: str, broker_order_id: str, observed_at: datetime
    ) -> BrokerOrderStatusObservation:
        require_aware_datetime(observed_at, field_name="observed_at")
        client = self._client()
        _, account_hash = self._resolve_account(client, account_ref)
        payload = await asyncio.to_thread(client.get_order, account_hash, broker_order_id)
        row = _mapping(payload, "order")
        quantity = _decimal(row.get("quantity")) or Decimal(0)
        filled = _decimal(row.get("filledQuantity")) or Decimal(0)
        return BrokerOrderStatusObservation(
            broker_order_id=broker_order_id,
            observed_at=observed_at,
            status=(_text(row.get("status")) or "UNKNOWN").upper(),
            filled_quantity=filled,
            remaining_quantity=max(Decimal(0), quantity - filled),
            average_fill_price=self._average_fill_price(row),
        )

    async def cancel_order(self, *, account_ref: str, broker_order_id: str) -> None:
        client = self._client()
        _, account_hash = self._resolve_account(client, account_ref)
        await asyncio.to_thread(client.cancel_order, account_hash, broker_order_id)

    def _read_account_state(
        self, account_ref: str, observed_at: datetime
    ) -> BrokerExecutionAccountState:
        client = self._client()
        account_number, account_hash = self._resolve_account(client, account_ref)
        account_row: Mapping[str, object] | None = None
        for raw in _sequence(client.accounts_with_positions(), "accounts"):
            candidate = _mapping(
                _mapping(raw, "account").get("securitiesAccount"),
                "securitiesAccount",
            )
            if _text(candidate.get("accountNumber")) == account_number:
                account_row = candidate
                break
        if account_row is None:
            raise DataContractError("Schwab execution account was not returned")
        balances = _mapping(account_row.get("currentBalances") or {}, "current balances")
        positions: dict[str, Decimal] = {}
        for raw in _sequence(account_row.get("positions") or [], "positions"):
            position = _mapping(raw, "position")
            instrument = _mapping(position.get("instrument"), "position instrument")
            symbol = _text(instrument.get("symbol"))
            if symbol is None:
                continue
            long_quantity = _decimal(position.get("longQuantity")) or Decimal(0)
            short_quantity = _decimal(position.get("shortQuantity")) or Decimal(0)
            positions[symbol.upper()] = long_quantity - short_quantity

        reserve = Decimal(0)
        reserve_complete = True
        rows = _sequence(
            client.orders(account_hash, observed_at - timedelta(days=60), observed_at),
            "orders",
        )
        for raw in rows:
            order = _mapping(raw, "order")
            if (_text(order.get("status")) or "").upper() not in _ACTIVE_ORDER_STATUSES:
                continue
            legs = _sequence(order.get("orderLegCollection") or [], "order legs")
            if not any(
                (_text(_mapping(leg, "order leg").get("instruction")) or "").upper()
                in {"BUY", "BUY_TO_COVER"}
                for leg in legs
            ):
                continue
            limit_price = _decimal(order.get("price"))
            quantity = _decimal(order.get("quantity")) or Decimal(0)
            filled = _decimal(order.get("filledQuantity")) or Decimal(0)
            if limit_price is None:
                reserve_complete = False
                continue
            reserve += max(Decimal(0), quantity - filled) * limit_price
        return BrokerExecutionAccountState(
            account_ref=account_ref,
            observed_at=observed_at,
            cash_balance=_decimal(balances.get("cashBalance")),
            margin_balance=_decimal(balances.get("marginBalance")),
            open_buy_order_reserve=reserve if reserve_complete else None,
            positions=positions,
        )

    def _resolve_account(self, client: SchwabOrderClient, account_ref: str) -> tuple[str, str]:
        for raw in _sequence(client.account_numbers(), "account numbers"):
            row = _mapping(raw, "account number")
            number = _text(row.get("accountNumber"))
            account_hash = _text(row.get("hashValue"))
            if (
                number is not None
                and account_hash is not None
                and account_hash in self._account_hashes
                and _stable_ref("account", account_hash, prefix="schwab_") == account_ref
            ):
                return number, account_hash
        raise DataContractError("Schwab account_ref is not configured for live orders")

    @staticmethod
    def _average_fill_price(order: Mapping[str, object]) -> Decimal | None:
        total_quantity = Decimal(0)
        total_notional = Decimal(0)
        for raw_activity in _sequence(
            order.get("orderActivityCollection") or [], "order activities"
        ):
            activity = _mapping(raw_activity, "order activity")
            for raw_leg in _sequence(activity.get("executionLegs") or [], "execution legs"):
                leg = _mapping(raw_leg, "execution leg")
                quantity = _decimal(leg.get("quantity"))
                price = _decimal(leg.get("price"))
                if quantity is None or price is None:
                    continue
                total_quantity += quantity
                total_notional += quantity * price
        return total_notional / total_quantity if total_quantity > 0 else None


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DataContractError(f"Schwab {field} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list | tuple):
        raise DataContractError(f"Schwab {field} must be an array")
    return value


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"Schwab {field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DataContractError(f"Schwab {field} is invalid") from None
    require_aware_datetime(parsed, field_name=field)
    return parsed


def _stable_ref(namespace: str, raw: str, *, prefix: str = "") -> str:
    digest = hashlib.sha256(f"schwab:{namespace}:{raw}".encode()).hexdigest()[:32]
    return f"{prefix}{digest}"


def _instrument_id(instrument: Mapping[str, object]) -> str | None:
    symbol = _text(instrument.get("symbol"))
    asset_type = (_text(instrument.get("assetType")) or "").upper()
    instrument_type = (_text(instrument.get("type")) or "").upper()
    if symbol is None:
        return None
    if asset_type in {"EQUITY", "ETF"} or (
        asset_type == "COLLECTIVE_INVESTMENT" and instrument_type == "EXCHANGE_TRADED_FUND"
    ):
        kind = (
            AssetType.ETF
            if asset_type == "ETF" or instrument_type == "EXCHANGE_TRADED_FUND"
            else AssetType.EQUITY
        )
    elif asset_type == "OPTION":
        kind = AssetType.OPTION
    else:
        return None
    return build_instrument_id(kind, Market.US, symbol.upper())


class SchwabAccountAdapter:
    """Normalize selected Schwab REAL accounts without exposing raw identifiers."""

    def __init__(
        self,
        id_generator: IdGenerator,
        *,
        enabled: bool,
        client_id: str | None,
        client_secret: str | None,
        redirect_uri: str,
        token_path: Path,
        account_hashes: Sequence[str],
        clock: Clock | None = None,
        client_factory: ReadClientFactory | None = None,
    ) -> None:
        self._ids = id_generator
        self._enabled = enabled
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_path = token_path
        self._account_hashes = frozenset(item.strip() for item in account_hashes if item.strip())
        self._clock = clock or SystemClock()
        self._client_factory = client_factory

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SCHWAB

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.ACCOUNT

    def is_configured(self) -> bool:
        return bool(
            self._enabled
            and self._client_id
            and self._client_secret
            and self._account_hashes
            and (self._client_factory is not None or self._token_path.is_file())
        )

    async def get_account_snapshots(
        self, *, as_of: datetime
    ) -> ProviderSuccess[tuple[AccountSnapshot, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        self._require_configured()
        try:
            return await asyncio.to_thread(self._read_snapshots)
        except TradingPartnerError:
            raise
        except Exception:
            raise ProviderUnavailableError(
                "Schwab account snapshot read failed",
                details={"vendor": VendorId.SCHWAB.value, "operation": "account_snapshot"},
                code="SCHWAB_ACCOUNT_SNAPSHOT_UNAVAILABLE",
            ) from None

    async def get_account_transactions(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> ProviderSuccess[AccountActivityBatch]:
        for name, value in (("start", start), ("end", end)):
            if value is not None:
                require_aware_datetime(value, field_name=name)
        if start is not None and end is not None and start > end:
            raise DataContractError("transaction start must be <= end")
        if not 1 <= limit <= 1_000:
            raise DataContractError("transaction limit must be in [1,1000]")
        self._require_configured()
        effective_end = end or self._clock.now()
        effective_start = start or effective_end - timedelta(days=60)
        return await asyncio.to_thread(
            self._read_transactions,
            effective_start,
            effective_end,
            limit,
            start is None,
        )

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Schwab requires enabled provider, credentials, token, and account hashes"
            )

    def _client(self) -> SchwabReadClient:
        if self._client_factory is not None:
            return self._client_factory()
        assert self._client_id is not None and self._client_secret is not None
        return SchwabPyReadClient(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            token_path=self._token_path,
        )

    def _account_hash_by_number(self, client: SchwabReadClient) -> dict[str, str]:
        rows = _sequence(client.account_numbers(), "account numbers")
        resolved: dict[str, str] = {}
        for raw in rows:
            row = _mapping(raw, "account number")
            number = _text(row.get("accountNumber"))
            account_hash = _text(row.get("hashValue"))
            if number is None or account_hash is None:
                raise DataContractError("Schwab account identity is incomplete")
            if account_hash in self._account_hashes:
                resolved[number] = account_hash
        return resolved

    def _read_snapshots(self) -> ProviderSuccess[tuple[AccountSnapshot, ...]]:
        client = self._client()
        hashes = self._account_hash_by_number(client)
        rows = _sequence(client.accounts_with_positions(), "accounts")
        fetched_at = self._clock.now()
        snapshots: list[AccountSnapshot] = []
        base_warnings: set[str] = {
            "ACCOUNT_AS_OF_FETCH_TIME",
        }
        all_warnings = set(base_warnings)
        for raw in rows:
            account = _mapping(
                _mapping(raw, "account").get("securitiesAccount"),
                "securitiesAccount",
            )
            number = _text(account.get("accountNumber"))
            if number is None or number not in hashes:
                continue
            warnings = set(base_warnings)
            positions: list[AccountPosition] = []
            instrument_ids_by_symbol: dict[str, str] = {}
            for raw_position in _sequence(account.get("positions") or [], "positions"):
                position = _mapping(raw_position, "position")
                instrument = _mapping(position.get("instrument"), "position instrument")
                instrument_id = _instrument_id(instrument)
                if instrument_id is None:
                    warnings.add("SCHWAB_UNSUPPORTED_ASSET_TYPE")
                    all_warnings.add("SCHWAB_UNSUPPORTED_ASSET_TYPE")
                    continue
                symbol = _text(instrument.get("symbol"))
                if symbol is not None:
                    instrument_ids_by_symbol[symbol.upper()] = instrument_id
                long_quantity = _decimal(position.get("longQuantity")) or Decimal(0)
                short_quantity = _decimal(position.get("shortQuantity")) or Decimal(0)
                if long_quantity > 0:
                    positions.append(self._position(position, instrument_id, long_quantity, False))
                if short_quantity > 0:
                    positions.append(self._position(position, instrument_id, short_quantity, True))
            if any(item.market_value is not None for item in positions):
                warnings.add("BROKER_VALUATION_PRICE_DERIVED")
                all_warnings.add("BROKER_VALUATION_PRICE_DERIVED")
            balances = _mapping(account.get("currentBalances") or {}, "current balances")
            cash_balance = _decimal(balances.get("cashBalance"))
            if cash_balance is None:
                warnings.add("SCHWAB_CASH_BALANCE_UNAVAILABLE")
                all_warnings.add("SCHWAB_CASH_BALANCE_UNAVAILABLE")
            account_hash = hashes[number]
            open_orders: tuple[AccountOpenOrder, ...]
            try:
                order_rows = _sequence(
                    client.orders(account_hash, fetched_at - timedelta(days=60), fetched_at),
                    "orders",
                )
                open_orders = self._open_orders(
                    order_rows,
                    instrument_ids_by_symbol=instrument_ids_by_symbol,
                    warnings=warnings,
                )
            except TradingPartnerError:
                warnings.add("SCHWAB_OPEN_ORDERS_UNAVAILABLE")
                all_warnings.add("SCHWAB_OPEN_ORDERS_UNAVAILABLE")
                open_orders = ()
            all_warnings.update(warnings)
            warning_codes = tuple(sorted(warnings))
            snapshots.append(
                AccountSnapshot(
                    snapshot_id=self._ids.new(EntityIdPrefix.SNAPSHOT),
                    account_ref=_stable_ref("account", account_hash, prefix="schwab_"),
                    provider=VendorId.SCHWAB,
                    environment=AccountEnvironment.REAL,
                    base_currency="USD",
                    account_as_of=fetched_at,
                    fetched_at=fetched_at,
                    cash=cash_balance,
                    buying_power=_decimal(balances.get("buyingPower")),
                    net_assets=_decimal(balances.get("liquidationValue")),
                    margin_used=abs(value)
                    if (value := _decimal(balances.get("marginBalance"))) is not None
                    else None,
                    positions=tuple(positions),
                    open_orders=open_orders,
                    degraded=True,
                    warning_codes=warning_codes,
                )
            )
        return ProviderSuccess(
            tuple(snapshots), self._meta(fetched_at, tuple(sorted(all_warnings)))
        )

    @staticmethod
    def _open_orders(
        rows: Sequence[object],
        *,
        instrument_ids_by_symbol: Mapping[str, str],
        warnings: set[str],
    ) -> tuple[AccountOpenOrder, ...]:
        values: list[AccountOpenOrder] = []
        for raw in rows:
            row = _mapping(raw, "order")
            upstream_status = (_text(row.get("status")) or "").upper()
            if upstream_status not in _ACTIVE_ORDER_STATUSES:
                continue
            raw_order_id = row.get("orderId")
            provider_order_id = (
                str(raw_order_id)
                if isinstance(raw_order_id, int | str) and str(raw_order_id).strip()
                else None
            )
            if provider_order_id is None:
                warnings.add("SCHWAB_OPEN_ORDER_ID_UNAVAILABLE")
                continue
            legs = _sequence(row.get("orderLegCollection") or [], "order legs")
            if len(legs) != 1:
                warnings.add("SCHWAB_COMPLEX_OPEN_ORDER_NOT_INGESTED")
                continue
            leg = _mapping(legs[0], "order leg")
            instrument = _mapping(leg.get("instrument"), "order instrument")
            symbol = _text(instrument.get("symbol"))
            instrument_id = (
                instrument_ids_by_symbol.get(symbol.upper()) if symbol is not None else None
            ) or _instrument_id(instrument)
            if instrument_id is None:
                warnings.add("SCHWAB_UNSUPPORTED_OPEN_ORDER_ASSET_TYPE")
                continue
            instruction = (_text(leg.get("instruction")) or "").upper()
            if instruction.startswith("BUY"):
                side = AccountOpenOrderSide.BUY
            elif instruction.startswith("SELL"):
                side = AccountOpenOrderSide.SELL
            else:
                warnings.add("SCHWAB_UNSUPPORTED_OPEN_ORDER_INSTRUCTION")
                continue
            quantity = _decimal(row.get("quantity") or leg.get("quantity"))
            filled_quantity = _decimal(row.get("filledQuantity")) or Decimal(0)
            if quantity is None or quantity <= 0 or filled_quantity > quantity:
                warnings.add("SCHWAB_INVALID_OPEN_ORDER_QUANTITY")
                continue
            submitted_at = None
            entered_time = row.get("enteredTime")
            if entered_time is not None:
                try:
                    submitted_at = _aware_datetime(entered_time, "order enteredTime")
                except DataContractError:
                    warnings.add("SCHWAB_OPEN_ORDER_TIME_UNAVAILABLE")
            values.append(
                AccountOpenOrder(
                    provider_order_id=provider_order_id,
                    instrument_id=instrument_id,
                    side=side,
                    status=(
                        AccountOpenOrderStatus.PARTIAL
                        if filled_quantity > 0
                        else AccountOpenOrderStatus.PENDING
                    ),
                    quantity=quantity,
                    filled_quantity=filled_quantity,
                    limit_price=_decimal(row.get("price")),
                    submitted_at=submitted_at,
                )
            )
        return tuple(values)

    @staticmethod
    def _position(
        row: Mapping[str, object], instrument_id: str, quantity: Decimal, short: bool
    ) -> AccountPosition:
        average = _decimal(
            row.get("averageShortPrice" if short else "averageLongPrice") or row.get("averagePrice")
        )
        market_value = _decimal(row.get("marketValue"))
        if short and market_value is not None and market_value > 0:
            market_value = -market_value
        return AccountPosition(
            instrument_id=instrument_id,
            side=AccountPositionSide.SHORT if short else AccountPositionSide.LONG,
            quantity=quantity,
            sellable_quantity=None,
            average_cost=average,
            diluted_cost=None,
            market_price=None,
            market_price_at=None,
            market_value=market_value,
            unrealized_pnl=_decimal(
                row.get("shortOpenProfitLoss" if short else "longOpenProfitLoss")
            ),
            realized_pnl=None,
            currency="USD",
        )

    def _read_transactions(
        self,
        start: datetime,
        end: datetime,
        limit: int,
        defaulted_window: bool,
    ) -> ProviderSuccess[AccountActivityBatch]:
        client = self._client()
        warnings: set[str] = set()
        if defaulted_window:
            warnings.add("SCHWAB_TRANSACTION_WINDOW_DEFAULTED")
        windows = self._transaction_windows(start, end)
        if len(windows) > 1:
            warnings.add("SCHWAB_TRANSACTION_WINDOW_PAGED")
        values: list[AccountTransaction] = []
        account_refs: list[str] = []
        for account_hash in sorted(self._account_hashes):
            account_ref = _stable_ref("account", account_hash, prefix="schwab_")
            account_refs.append(account_ref)
            for window_start, window_end in windows:
                rows = _sequence(
                    client.transactions(account_hash, window_start, window_end),
                    "transactions",
                )
                for raw in rows:
                    transaction = _mapping(raw, "transaction")
                    occurred_at = _aware_datetime(transaction.get("time"), "transaction time")
                    if occurred_at < window_start or occurred_at > window_end:
                        continue
                    raw_kind = (_text(transaction.get("type")) or "").upper()
                    description = (_text(transaction.get("description")) or "").upper()
                    if raw_kind in {"RECEIVE_AND_DELIVER", "CORPORATE_ACTION"}:
                        warnings.add("SCHWAB_CORPORATE_ACTION_DETAILS_PARTIAL")
                    if "REVERS" in description or "CANCEL" in description:
                        warnings.add("SCHWAB_REVERSAL_LINK_UNAVAILABLE")
                    items = _sequence(transaction.get("transferItems") or [], "transfer items")
                    transaction_value_count = len(values)
                    for index, raw_item in enumerate(items):
                        item = _mapping(raw_item, "transfer item")
                        instrument = _mapping(item.get("instrument"), "transaction instrument")
                        instrument_id = _instrument_id(instrument)
                        if instrument_id is None:
                            asset_type = (_text(instrument.get("assetType")) or "").upper()
                            if asset_type == "CURRENCY":
                                normalized = self._cash_transaction(
                                    account_hash,
                                    transaction,
                                    item,
                                    index,
                                    occurred_at,
                                )
                                if normalized is not None:
                                    values.append(normalized)
                            else:
                                warnings.add("SCHWAB_TRANSACTION_ITEM_OMITTED")
                            continue
                        normalized, side_inferred = self._transaction(
                            account_hash,
                            transaction,
                            item,
                            index,
                            instrument_id,
                            occurred_at,
                        )
                        if side_inferred:
                            warnings.add("SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN")
                        if normalized is not None:
                            values.append(normalized)
                        else:
                            warnings.add("SCHWAB_TRANSACTION_ITEM_OMITTED")
                    if (
                        len(values) == transaction_value_count
                        and _decimal(transaction.get("netAmount")) is not None
                    ):
                        synthetic_cash_item: Mapping[str, object] = {
                            "amount": transaction.get("netAmount"),
                            "instrument": {
                                "assetType": "CURRENCY",
                                "symbol": "CURRENCY_USD",
                            },
                        }
                        cash = self._cash_transaction(
                            account_hash,
                            transaction,
                            synthetic_cash_item,
                            0,
                            occurred_at,
                        )
                        if cash is not None:
                            values.append(cash)
        values.sort(key=lambda item: (item.occurred_at, item.provider_transaction_id), reverse=True)
        truncated = len(values) > limit
        if truncated:
            warnings.add("PROVIDER_RESULT_TRUNCATED")
        fetched_at = self._clock.now()
        gap_codes = tuple(
            sorted(
                warnings
                - {
                    "SCHWAB_TRANSACTION_WINDOW_DEFAULTED",
                    "SCHWAB_TRANSACTION_WINDOW_PAGED",
                    # This is an explainable normalization detail, not a
                    # missing activity category or truncated coverage.
                    "SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN",
                }
            )
        )
        coverage = tuple(
            ProviderAccountActivityCoverage(
                account_ref=account_ref,
                requested_start=start,
                requested_end=end,
                effective_start=start,
                effective_end=end,
                mapping_version="schwab_activity_v1",
                supported_kinds=tuple(AccountTransactionKind),
                unavailable_kinds=(),
                gap_codes=gap_codes,
                truncated=truncated,
            )
            for account_ref in account_refs
        )
        return ProviderSuccess(
            AccountActivityBatch(tuple(values[:limit]), coverage),
            self._meta(fetched_at, tuple(sorted(warnings))),
        )

    @staticmethod
    def _transaction_windows(
        start: datetime, end: datetime
    ) -> tuple[tuple[datetime, datetime], ...]:
        windows: list[tuple[datetime, datetime]] = []
        cursor = start
        while cursor <= end:
            window_end = min(cursor + timedelta(days=60), end)
            windows.append((cursor, window_end))
            if window_end == end:
                break
            cursor = window_end + timedelta(microseconds=1)
        return tuple(windows)

    @staticmethod
    def _cash_transaction(
        account_hash: str,
        transaction: Mapping[str, object],
        item: Mapping[str, object],
        index: int,
        occurred_at: datetime,
    ) -> AccountTransaction | None:
        raw_id_value = transaction.get("activityId")
        raw_id = str(raw_id_value) if isinstance(raw_id_value, int) else _text(raw_id_value)
        if raw_id is None:
            raise DataContractError("Schwab transaction id is missing")
        raw_kind = (_text(transaction.get("type")) or "UNKNOWN").upper()
        # A trade's currency transfer item is a settlement leg of the same broker
        # activity, not an external cash flow. The security leg carries netAmount
        # when Schwab supplies it, so do not double-book the currency item.
        if raw_kind == "TRADE":
            return None
        description = (_text(transaction.get("description")) or "").upper()
        if raw_kind == "DIVIDEND_OR_INTEREST":
            kind = (
                AccountTransactionKind.DIVIDEND
                if "DIVIDEND" in description
                else AccountTransactionKind.INTEREST
            )
        elif raw_kind in {
            "ACH_RECEIPT",
            "ACH_DISBURSEMENT",
            "ELECTRONIC_FUND",
            "WIRE_IN",
            "WIRE_OUT",
            "JOURNAL",
        }:
            kind = AccountTransactionKind.TRANSFER
        elif "FEE" in raw_kind or "FEE" in description or "COMMISSION" in description:
            kind = AccountTransactionKind.FEE
        elif raw_kind in {"RECEIVE_AND_DELIVER", "CORPORATE_ACTION"}:
            kind = AccountTransactionKind.CORPORATE_ACTION
        else:
            kind = AccountTransactionKind.OTHER
        amount = _decimal(item.get("amount"))
        if amount is None:
            amount = _decimal(transaction.get("netAmount"))
        if amount is None:
            return None
        instrument = _mapping(item.get("instrument"), "transaction instrument")
        symbol = _text(instrument.get("symbol")) or ""
        currency = symbol.removeprefix("CURRENCY_").upper() or "USD"
        fees = SchwabAccountAdapter._fees(transaction)
        return AccountTransaction(
            provider_transaction_id=_stable_ref(
                "transaction", f"{account_hash}:{raw_id}:{index}:cash:{currency}"
            ),
            account_ref=_stable_ref("account", account_hash, prefix="schwab_"),
            provider=VendorId.SCHWAB,
            instrument_id=None,
            kind=kind,
            side=None,
            quantity=None,
            price=None,
            fees=fees,
            currency=currency,
            occurred_at=occurred_at,
            cash_amount=amount,
            source_type=raw_kind,
            mapping_version="schwab_activity_v1",
        )

    @staticmethod
    def _transaction(
        account_hash: str,
        transaction: Mapping[str, object],
        item: Mapping[str, object],
        index: int,
        instrument_id: str,
        occurred_at: datetime,
    ) -> tuple[AccountTransaction | None, bool]:
        raw_id_value = transaction.get("activityId")
        raw_id = str(raw_id_value) if isinstance(raw_id_value, int) else _text(raw_id_value)
        if raw_id is None:
            raise DataContractError("Schwab transaction id is missing")
        raw_kind = (_text(transaction.get("type")) or "").upper()
        amount = _decimal(item.get("amount")) or Decimal(0)
        if raw_kind == "TRADE":
            if amount == 0:
                return None, False
            kind = AccountTransactionKind.TRADE
            instruction = (_text(item.get("instruction")) or "").upper()
            if instruction in {
                "BUY",
                "BUY_TO_COVER",
                "BUY_TO_OPEN",
                "BUY_TO_CLOSE",
            }:
                side = AccountTransactionSide.BUY
                side_inferred = False
            elif instruction in {
                "SELL",
                "SELL_SHORT",
                "SELL_TO_OPEN",
                "SELL_TO_CLOSE",
            }:
                side = AccountTransactionSide.SELL
                side_inferred = False
            elif not instruction:
                # Transaction history can omit instruction while keeping the
                # transferred security quantity signed. Positive moves the
                # security into the account; negative moves it out.
                side = AccountTransactionSide.BUY if amount > 0 else AccountTransactionSide.SELL
                side_inferred = True
            else:
                return None, False
        elif raw_kind == "DIVIDEND_OR_INTEREST":
            description = (_text(transaction.get("description")) or "").upper()
            kind = (
                AccountTransactionKind.DIVIDEND
                if "DIVIDEND" in description
                else AccountTransactionKind.INTEREST
            )
            side = None
            side_inferred = False
        elif raw_kind in {
            "ACH_RECEIPT",
            "ACH_DISBURSEMENT",
            "ELECTRONIC_FUND",
            "WIRE_IN",
            "WIRE_OUT",
        }:
            kind, side = AccountTransactionKind.TRANSFER, None
            side_inferred = False
        elif raw_kind in {"RECEIVE_AND_DELIVER", "CORPORATE_ACTION"}:
            kind, side = AccountTransactionKind.CORPORATE_ACTION, None
            side_inferred = False
        elif "FEE" in raw_kind or "FEE" in (_text(transaction.get("description")) or "").upper():
            kind, side = AccountTransactionKind.FEE, None
            side_inferred = False
        else:
            kind, side = AccountTransactionKind.OTHER, None
            side_inferred = False
        fees = SchwabAccountAdapter._fees(transaction) if index == 0 else Decimal(0)
        return (
            AccountTransaction(
                provider_transaction_id=_stable_ref(
                    "transaction", f"{account_hash}:{raw_id}:{index}:{instrument_id}"
                ),
                account_ref=_stable_ref("account", account_hash, prefix="schwab_"),
                provider=VendorId.SCHWAB,
                instrument_id=instrument_id,
                kind=kind,
                side=side,
                quantity=abs(amount),
                price=_decimal(item.get("price")),
                fees=fees,
                currency="USD",
                occurred_at=occurred_at,
                cash_amount=_decimal(transaction.get("netAmount")) if index == 0 else None,
                source_type=raw_kind or "UNKNOWN",
                mapping_version="schwab_activity_v1",
            ),
            side_inferred,
        )

    @staticmethod
    def _fees(transaction: Mapping[str, object]) -> Decimal:
        fees = Decimal(0)
        raw_fees = transaction.get("fees")
        if isinstance(raw_fees, Mapping):
            for value in raw_fees.values():
                parsed = _decimal(value)
                if parsed is not None:
                    fees += abs(parsed)
        return fees

    @staticmethod
    def _meta(fetched_at: datetime, warnings: tuple[str, ...]) -> ProviderResultMeta:
        return ProviderResultMeta(
            VendorId.SCHWAB,
            DataCategory.ACCOUNT,
            SourceRole.PRIMARY,
            fetched_at,
            fetched_at,
            Freshness.UNKNOWN,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            0,
            warnings,
        )
