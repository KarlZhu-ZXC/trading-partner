"""Strict read-only CSV account snapshot adapter."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError, ProviderNotConfigured
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot
from infrastructure.system.clock import SystemClock

_HEADERS = (
    "schema_version",
    "account_ref",
    "account_as_of",
    "base_currency",
    "instrument_id",
    "side",
    "currency",
    "quantity",
    "sellable_quantity",
    "average_cost",
    "diluted_cost",
    "market_price",
    "market_price_at",
    "market_value",
    "unrealized_pnl",
    "realized_pnl",
    "cash",
    "buying_power",
    "net_assets",
    "margin_used",
)


def _contract(message: str, *, field: str) -> DataContractError:
    return DataContractError(
        message,
        details={"vendor": VendorId.MANUAL_CSV.value, "operation": "account", "field": field},
    )


def _text(row: Mapping[str, str], field: str) -> str:
    value = row[field].strip()
    if not value:
        raise _contract("CSV required value is blank", field=field)
    if value.startswith(("=", "+", "@")):
        raise _contract("CSV formulas are not allowed", field=field)
    return value


def _optional_decimal(row: Mapping[str, str], field: str) -> Decimal | None:
    value = row[field].strip()
    if not value:
        return None
    if value.startswith(("=", "+", "@")):
        raise _contract("CSV formulas are not allowed", field=field)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _contract("CSV numeric value is invalid", field=field) from None
    if not parsed.is_finite():
        raise _contract("CSV numeric value must be finite", field=field)
    return parsed


def _required_decimal(row: Mapping[str, str], field: str) -> Decimal:
    value = _optional_decimal(row, field)
    if value is None:
        raise _contract("CSV required numeric value is blank", field=field)
    return value


def _datetime(row: Mapping[str, str], field: str, *, optional: bool = False) -> datetime | None:
    value = row[field].strip()
    if optional and not value:
        return None
    if value.startswith(("=", "+", "@")):
        raise _contract("CSV formulas are not allowed", field=field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _contract("CSV datetime is invalid", field=field) from None
    require_aware_datetime(parsed, field_name=field)
    return parsed


class ManualCsvAccountAdapter:
    """Load the latest version-1 CSV snapshot per account at a cutoff."""

    def __init__(
        self,
        path: Path | None,
        id_generator: IdGenerator,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._path = path
        self._ids = id_generator
        self._clock = clock or SystemClock()

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.MANUAL_CSV

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market in {Market.A_SHARE, Market.US} and category is DataCategory.ACCOUNT

    def is_configured(self) -> bool:
        return self._path is not None

    async def get_account_snapshots(
        self, *, as_of: datetime
    ) -> ProviderSuccess[tuple[AccountSnapshot, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        if self._path is None:
            raise ProviderNotConfigured("Manual holdings CSV path is not configured")
        rows = self._read_rows(self._path)
        selected: dict[str, tuple[datetime, list[Mapping[str, str]]]] = {}
        grouped: dict[tuple[str, datetime], list[Mapping[str, str]]] = {}
        for row in rows:
            account_ref = _text(row, "account_ref")
            row_as_of = _datetime(row, "account_as_of")
            assert row_as_of is not None
            if row_as_of <= as_of:
                grouped.setdefault((account_ref, row_as_of), []).append(row)
        for (account_ref, row_as_of), account_rows in grouped.items():
            current = selected.get(account_ref)
            if current is None or row_as_of > current[0]:
                selected[account_ref] = (row_as_of, account_rows)

        fetched_at = self._clock.now()
        snapshots = tuple(
            self._snapshot(account_ref, row_as_of, account_rows, fetched_at)
            for account_ref, (row_as_of, account_rows) in sorted(selected.items())
        )
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.ACCOUNT,
            SourceRole.SUPPLEMENTAL,
            max((item.account_as_of for item in snapshots), default=as_of),
            fetched_at,
            Freshness.UNKNOWN,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            None,
            ("MANUAL_SOURCE",),
        )
        return ProviderSuccess(snapshots, meta)

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, str]]:
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != _HEADERS:
                    raise _contract("CSV header does not match schema version 1", field="header")
                rows = [dict(row) for row in reader]
        except OSError:
            raise ProviderNotConfigured("Manual holdings CSV cannot be read") from None
        for row in rows:
            if _text(row, "schema_version") != "1":
                raise _contract("CSV schema_version must be 1", field="schema_version")
        return rows

    def _snapshot(
        self,
        account_ref: str,
        account_as_of: datetime,
        rows: list[Mapping[str, str]],
        fetched_at: datetime,
    ) -> AccountSnapshot:
        first = rows[0]
        scalar_fields = ("base_currency", "cash", "buying_power", "net_assets", "margin_used")
        if any(
            row[field].strip() != first[field].strip()
            for row in rows
            for field in scalar_fields
        ):
            raise _contract("CSV account-level values disagree within a snapshot", field="account")
        positions = tuple(self._position(row) for row in rows)
        return AccountSnapshot(
            snapshot_id=self._ids.new(EntityIdPrefix.SNAPSHOT),
            account_ref=account_ref,
            provider=self.vendor_id,
            environment=AccountEnvironment.MANUAL,
            base_currency=_text(first, "base_currency").upper(),
            account_as_of=account_as_of,
            fetched_at=fetched_at,
            cash=_optional_decimal(first, "cash"),
            buying_power=_optional_decimal(first, "buying_power"),
            net_assets=_optional_decimal(first, "net_assets"),
            margin_used=_optional_decimal(first, "margin_used"),
            positions=positions,
            open_orders=(),
            degraded=True,
            warning_codes=("MANUAL_SOURCE",),
        )

    @staticmethod
    def _position(row: Mapping[str, str]) -> AccountPosition:
        market_price = _optional_decimal(row, "market_price")
        market_price_at = _datetime(row, "market_price_at", optional=True)
        if (market_price is None) != (market_price_at is None):
            raise _contract(
                "CSV market_price and market_price_at must appear together",
                field="market_price_at",
            )
        try:
            side = AccountPositionSide(_text(row, "side").lower())
        except ValueError:
            raise _contract("CSV side must be long or short", field="side") from None
        return AccountPosition(
            instrument_id=_text(row, "instrument_id"),
            side=side,
            quantity=_required_decimal(row, "quantity"),
            sellable_quantity=_optional_decimal(row, "sellable_quantity"),
            average_cost=_optional_decimal(row, "average_cost"),
            diluted_cost=_optional_decimal(row, "diluted_cost"),
            market_price=market_price,
            market_price_at=market_price_at,
            market_value=_optional_decimal(row, "market_value"),
            unrealized_pnl=_optional_decimal(row, "unrealized_pnl"),
            realized_pnl=_optional_decimal(row, "realized_pnl"),
            currency=_text(row, "currency").upper(),
        )
