"""Charles Schwab read-only account and transaction adapter.

Only the authenticated schwab-py session is used. This module deliberately has
no order, replace, cancel, or generic request surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
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
    DataContractError,
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountPosition, AccountSnapshot, AccountTransaction
from infrastructure.system.clock import SystemClock

_BASE_URL = "https://api.schwabapi.com"


class SchwabReadClient(Protocol):
    """Smallest authenticated Schwab surface allowed inside Trading Partner."""

    def account_numbers(self) -> object: ...
    def accounts_with_positions(self) -> object: ...
    def transactions(self, account_hash: str, start: datetime, end: datetime) -> object: ...


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
        return self._get("/trader/v1/accounts/accountNumbers")

    def accounts_with_positions(self) -> object:
        return self._get("/trader/v1/accounts", params={"fields": "positions"})

    def transactions(self, account_hash: str, start: datetime, end: datetime) -> object:
        return self._get(
            f"/trader/v1/accounts/{account_hash}/transactions",
            params={"startDate": start.isoformat(), "endDate": end.isoformat()},
        )

    def _get(self, path: str, *, params: Mapping[str, str] | None = None) -> object:
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


ReadClientFactory = Callable[[], SchwabReadClient]


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
        asset_type == "COLLECTIVE_INVESTMENT"
        and instrument_type == "EXCHANGE_TRADED_FUND"
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
        return await asyncio.to_thread(self._read_snapshots)

    async def get_account_transactions(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> ProviderSuccess[tuple[AccountTransaction, ...]]:
        for name, value in (("start", start), ("end", end)):
            if value is not None:
                require_aware_datetime(value, field_name=name)
        if start is not None and end is not None and start > end:
            raise DataContractError("transaction start must be <= end")
        if not 1 <= limit <= 1_000:
            raise DataContractError("transaction limit must be in [1,1000]")
        self._require_configured()
        effective_end = end or self._clock.now()
        earliest = effective_end - timedelta(days=60)
        effective_start = max(start, earliest) if start is not None else earliest
        return await asyncio.to_thread(
            self._read_transactions,
            effective_start,
            effective_end,
            limit,
            start is None,
            start is not None and start < earliest,
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
            "PRICE_TIME_UNAVAILABLE",
            "SCHWAB_OPEN_ORDERS_NOT_INGESTED",
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
            for raw_position in _sequence(account.get("positions") or [], "positions"):
                position = _mapping(raw_position, "position")
                instrument = _mapping(position.get("instrument"), "position instrument")
                instrument_id = _instrument_id(instrument)
                if instrument_id is None:
                    warnings.add("SCHWAB_UNSUPPORTED_ASSET_TYPE")
                    all_warnings.add("SCHWAB_UNSUPPORTED_ASSET_TYPE")
                    continue
                long_quantity = _decimal(position.get("longQuantity")) or Decimal(0)
                short_quantity = _decimal(position.get("shortQuantity")) or Decimal(0)
                if long_quantity > 0:
                    positions.append(self._position(position, instrument_id, long_quantity, False))
                if short_quantity > 0:
                    positions.append(self._position(position, instrument_id, short_quantity, True))
            balances = _mapping(account.get("currentBalances") or {}, "current balances")
            account_hash = hashes[number]
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
                    cash=_decimal(balances.get("cashBalance") or balances.get("totalCash")),
                    buying_power=_decimal(balances.get("buyingPower")),
                    net_assets=_decimal(balances.get("liquidationValue")),
                    margin_used=abs(value)
                    if (value := _decimal(balances.get("marginBalance"))) is not None
                    else None,
                    positions=tuple(positions),
                    open_orders=(),
                    degraded=True,
                    warning_codes=warning_codes,
                )
            )
        return ProviderSuccess(
            tuple(snapshots), self._meta(fetched_at, tuple(sorted(all_warnings)))
        )

    @staticmethod
    def _position(
        row: Mapping[str, object], instrument_id: str, quantity: Decimal, short: bool
    ) -> AccountPosition:
        average = _decimal(
            row.get("averageShortPrice" if short else "averageLongPrice")
            or row.get("averagePrice")
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
        clamped_window: bool,
    ) -> ProviderSuccess[tuple[AccountTransaction, ...]]:
        client = self._client()
        warnings: set[str] = set()
        if defaulted_window:
            warnings.add("SCHWAB_TRANSACTION_WINDOW_DEFAULTED")
        if clamped_window:
            warnings.add("SCHWAB_TRANSACTION_WINDOW_CLAMPED")
        values: list[AccountTransaction] = []
        for account_hash in sorted(self._account_hashes):
            rows = _sequence(client.transactions(account_hash, start, end), "transactions")
            for raw in rows:
                transaction = _mapping(raw, "transaction")
                occurred_at = _aware_datetime(transaction.get("time"), "transaction time")
                if occurred_at < start or occurred_at > end:
                    continue
                items = _sequence(transaction.get("transferItems") or [], "transfer items")
                for index, raw_item in enumerate(items):
                    item = _mapping(raw_item, "transfer item")
                    instrument = _mapping(item.get("instrument"), "transaction instrument")
                    instrument_id = _instrument_id(instrument)
                    if instrument_id is None:
                        warnings.add("SCHWAB_TRANSACTION_ITEM_OMITTED")
                        continue
                    normalized = self._transaction(
                        account_hash, transaction, item, index, instrument_id, occurred_at
                    )
                    if normalized is not None:
                        values.append(normalized)
                    else:
                        warnings.add("SCHWAB_TRANSACTION_ITEM_OMITTED")
        values.sort(key=lambda item: (item.occurred_at, item.provider_transaction_id), reverse=True)
        fetched_at = self._clock.now()
        return ProviderSuccess(
            tuple(values[:limit]), self._meta(fetched_at, tuple(sorted(warnings)))
        )

    @staticmethod
    def _transaction(
        account_hash: str,
        transaction: Mapping[str, object],
        item: Mapping[str, object],
        index: int,
        instrument_id: str,
        occurred_at: datetime,
    ) -> AccountTransaction | None:
        raw_id_value = transaction.get("activityId")
        raw_id = (
            str(raw_id_value)
            if isinstance(raw_id_value, int)
            else _text(raw_id_value)
        )
        if raw_id is None:
            raise DataContractError("Schwab transaction id is missing")
        raw_kind = (_text(transaction.get("type")) or "").upper()
        amount = _decimal(item.get("amount")) or Decimal(0)
        if raw_kind == "TRADE":
            if amount == 0:
                return None
            kind = AccountTransactionKind.TRADE
            instruction = (_text(item.get("instruction")) or "").upper()
            if instruction in {"BUY", "BUY_TO_COVER"}:
                side = AccountTransactionSide.BUY
            elif instruction in {"SELL", "SELL_SHORT"}:
                side = AccountTransactionSide.SELL
            else:
                return None
        elif raw_kind == "DIVIDEND_OR_INTEREST":
            description = (_text(transaction.get("description")) or "").upper()
            kind = (
                AccountTransactionKind.DIVIDEND
                if "DIVIDEND" in description
                else AccountTransactionKind.INTEREST
            )
            side = None
        elif raw_kind in {
            "ACH_RECEIPT",
            "ACH_DISBURSEMENT",
            "ELECTRONIC_FUND",
            "WIRE_IN",
            "WIRE_OUT",
        }:
            kind, side = AccountTransactionKind.TRANSFER, None
        else:
            kind, side = AccountTransactionKind.OTHER, None
        fees = Decimal(0)
        raw_fees = transaction.get("fees")
        if isinstance(raw_fees, Mapping):
            for value in raw_fees.values():
                parsed = _decimal(value)
                if parsed is not None:
                    fees += abs(parsed)
        return AccountTransaction(
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
        )

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
