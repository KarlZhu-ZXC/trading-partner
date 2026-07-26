"""Futures product, contract, continuous-series, and curve domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.cross_asset.enums import (
    ContinuousAdjustment,
    ContractLifecycleStatus,
    CurveCompleteness,
    CurveShape,
    PriceBasis,
    RollRule,
    SettlementMethod,
    SettlementStatus,
)

_UUID7_TOKEN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_PRODUCT_ID_RE = re.compile(
    rf"^{re.escape(EntityIdPrefix.FUTURES_PRODUCT.value)}_{_UUID7_TOKEN}$"
)
_PRODUCT_VERSION_ID_RE = re.compile(
    rf"^{re.escape(EntityIdPrefix.FUTURES_PRODUCT_VERSION.value)}_{_UUID7_TOKEN}$"
)
_CONTRACT_VERSION_ID_RE = re.compile(
    rf"^{re.escape(EntityIdPrefix.FUTURES_CONTRACT_VERSION.value)}_{_UUID7_TOKEN}$"
)
_PRODUCT_KEY_RE = re.compile(r"^[A-Z0-9_]+:[A-Z0-9_]+$")
_ROOT_RE = re.compile(r"^[A-Z0-9_]{1,16}$")
_CONTRACT_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_FUTURES_MARKETS = frozenset({Market.CME, Market.DCE, Market.US, Market.LME})


def _require_str(value: object, *, field: str, max_len: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    text = value.strip()
    if len(text) > max_len:
        raise DataContractError(
            f"{field} exceeds max length",
            details={"field": field, "max": max_len},
        )
    if text != value:
        raise DataContractError(
            f"{field} must not have leading/trailing whitespace",
            details={"field": field},
        )
    return text


def _require_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, float):
        raise DataContractError(
            f"{field} must not be float; use Decimal",
            details={"field": field, "rule": "no_float"},
        )
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field},
        )
    return value


def _require_positive_decimal(value: object, *, field: str) -> Decimal:
    number = _require_decimal(value, field=field)
    if number <= 0:
        raise DataContractError(
            f"{field} must be positive",
            details={"field": field},
        )
    return number


def _require_nonnegative_decimal(value: object, *, field: str) -> Decimal:
    number = _require_decimal(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field},
        )
    return number


def _require_optional_aware(value: datetime | None, *, field: str) -> None:
    if value is not None:
        require_aware_datetime(value, field_name=field)


def _require_optional_date(value: date | None, *, field: str) -> None:
    if value is not None and type(value) is not date:
        raise DataContractError(
            f"{field} must be a date",
            details={"field": field, "type": type(value).__name__},
        )


def _require_future_instrument_id(value: object, *, field: str) -> str:
    text = _require_str(value, field=field, max_len=128)
    try:
        asset_type, market, _symbol = parse_instrument_id(text)
    except DataContractError as exc:
        raise DataContractError(
            f"{field} must be a well-formed instrument_id",
            details={"field": field, "rule": "instrument_id_syntax"},
        ) from exc
    if asset_type is not AssetType.FUTURE:
        raise DataContractError(
            f"{field} must use AssetType.FUTURE",
            details={"field": field, "asset_type": asset_type.value},
        )
    if market not in _FUTURES_MARKETS:
        raise DataContractError(
            f"{field} market is not a futures market",
            details={
                "field": field,
                "market": market.value,
                "allowed": sorted(m.value for m in _FUTURES_MARKETS),
            },
        )
    return text


def _require_product_key(value: object, *, field: str = "product_key") -> str:
    text = _require_str(value, field=field, max_len=64)
    if _PRODUCT_KEY_RE.fullmatch(text) is None:
        raise DataContractError(
            f"{field} must match MARKET:ROOT (e.g. CME:GC)",
            details={"field": field, "value": text},
        )
    return text


@dataclass(frozen=True, slots=True)
class FuturesProductDefinition:
    """Versioned exchange product definition (root contract specs)."""

    product_id: str
    product_key: str
    root: str
    market: Market
    exchange: str
    commodity: str
    currency: str
    price_unit: str
    multiplier: Decimal
    tick_size: Decimal
    settlement_method: SettlementMethod
    session_calendar_id: str
    source: str
    valid_from: datetime
    definition_as_of: datetime
    version_id: str | None = None
    version: int = 1
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not _PRODUCT_ID_RE.fullmatch(self.product_id):
            raise DataContractError(
                "product_id must match futures_product_<uuid7>",
                details={"product_id": self.product_id},
            )
        if self.version_id is not None and not _PRODUCT_VERSION_ID_RE.fullmatch(
            self.version_id
        ):
            raise DataContractError(
                "version_id must match futures_product_version_<uuid7>",
                details={"version_id": self.version_id},
            )
        product_key = _require_product_key(self.product_key)
        root = _require_str(self.root, field="root", max_len=16)
        if _ROOT_RE.fullmatch(root) is None:
            raise DataContractError(
                "root must be uppercase alphanumeric",
                details={"root": root},
            )
        if not isinstance(self.market, Market) or self.market not in _FUTURES_MARKETS:
            raise DataContractError(
                "market must be a futures Market",
                details={"market": getattr(self.market, "value", self.market)},
            )
        market_prefix = f"{self.market.value}:"
        if not product_key.startswith(market_prefix):
            raise DataContractError(
                "product_key market segment must match market",
                details={"product_key": product_key, "market": self.market.value},
            )
        if product_key.removeprefix(market_prefix) != root:
            raise DataContractError(
                "product_key root segment must match root",
                details={"product_key": product_key, "root": root},
            )
        _require_str(self.exchange, field="exchange", max_len=32)
        _require_str(self.commodity, field="commodity", max_len=64)
        _require_str(self.currency, field="currency", max_len=16)
        _require_str(self.price_unit, field="price_unit", max_len=64)
        _require_positive_decimal(self.multiplier, field="multiplier")
        _require_positive_decimal(self.tick_size, field="tick_size")
        if not isinstance(self.settlement_method, SettlementMethod):
            raise DataContractError("settlement_method must be SettlementMethod")
        _require_str(self.session_calendar_id, field="session_calendar_id", max_len=64)
        _require_str(self.source, field="source", max_len=64)
        require_aware_datetime(self.valid_from, field_name="valid_from")
        require_aware_datetime(self.definition_as_of, field_name="definition_as_of")
        _require_optional_aware(self.valid_to, field="valid_to")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise DataContractError(
                "valid_to must be after valid_from",
                details={"field": "valid_to"},
            )
        if type(self.version) is not int or self.version < 1:
            raise DataContractError(
                "version must be a positive integer",
                details={"version": self.version},
            )


@dataclass(frozen=True, slots=True)
class FuturesContractDefinition:
    """Specific exchange-traded futures contract definition."""

    instrument_id: str
    product_id: str
    contract_month: str
    status: ContractLifecycleStatus
    definition_as_of: datetime
    version_id: str | None = None
    version: int = 1
    listed_at: datetime | None = None
    first_trade_at: datetime | None = None
    last_trade_at: datetime | None = None
    expiration_at: datetime | None = None
    first_notice_at: datetime | None = None
    delivery_start: date | None = None
    delivery_end: date | None = None
    settlement_at: datetime | None = None
    source: str = "unknown"

    def __post_init__(self) -> None:
        _require_future_instrument_id(self.instrument_id, field="instrument_id")
        if not _PRODUCT_ID_RE.fullmatch(self.product_id):
            raise DataContractError(
                "product_id must match futures_product_<uuid7>",
                details={"product_id": self.product_id},
            )
        if self.version_id is not None and not _CONTRACT_VERSION_ID_RE.fullmatch(
            self.version_id
        ):
            raise DataContractError(
                "version_id must match futures_contract_version_<uuid7>",
                details={"version_id": self.version_id},
            )
        month = _require_str(self.contract_month, field="contract_month", max_len=7)
        if _CONTRACT_MONTH_RE.fullmatch(month) is None:
            raise DataContractError(
                "contract_month must use YYYY-MM",
                details={"contract_month": month},
            )
        if not isinstance(self.status, ContractLifecycleStatus):
            raise DataContractError("status must be ContractLifecycleStatus")
        require_aware_datetime(self.definition_as_of, field_name="definition_as_of")
        if type(self.version) is not int or self.version < 1:
            raise DataContractError(
                "version must be a positive integer",
                details={"version": self.version},
            )
        for field_name in (
            "listed_at",
            "first_trade_at",
            "last_trade_at",
            "expiration_at",
            "first_notice_at",
            "settlement_at",
        ):
            _require_optional_aware(getattr(self, field_name), field=field_name)
        _require_optional_date(self.delivery_start, field="delivery_start")
        _require_optional_date(self.delivery_end, field="delivery_end")
        if (
            self.delivery_start is not None
            and self.delivery_end is not None
            and self.delivery_end < self.delivery_start
        ):
            raise DataContractError(
                "delivery_end must be >= delivery_start",
                details={"field": "delivery_end"},
            )
        if (
            self.first_trade_at is not None
            and self.last_trade_at is not None
            and self.last_trade_at < self.first_trade_at
        ):
            raise DataContractError(
                "last_trade_at must be >= first_trade_at",
                details={"field": "last_trade_at"},
            )
        _require_str(self.source, field="source", max_len=64)


@dataclass(frozen=True, slots=True)
class FuturesContractStatistics:
    """One trade-date settlement / volume / open-interest observation."""

    instrument_id: str
    trade_date: date
    settlement: Decimal | None
    settlement_status: SettlementStatus
    session_volume: Decimal | None
    open_interest: Decimal | None
    published_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_future_instrument_id(self.instrument_id, field="instrument_id")
        if type(self.trade_date) is not date:
            raise DataContractError(
                "trade_date must be a date",
                details={"field": "trade_date"},
            )
        if self.settlement is not None:
            _require_nonnegative_decimal(self.settlement, field="settlement")
        if not isinstance(self.settlement_status, SettlementStatus):
            raise DataContractError("settlement_status must be SettlementStatus")
        if self.session_volume is not None:
            _require_nonnegative_decimal(self.session_volume, field="session_volume")
        if self.open_interest is not None:
            _require_nonnegative_decimal(self.open_interest, field="open_interest")
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.source, field="source", max_len=64)


@dataclass(frozen=True, slots=True)
class ContinuousSeriesDefinition:
    """Ruled continuous futures series (unadjusted in Phase 3A)."""

    instrument_id: str
    product_id: str
    roll_rule: RollRule
    rank: int
    adjustment: ContinuousAdjustment
    provider_methodology_version: str
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        _require_future_instrument_id(self.instrument_id, field="instrument_id")
        if not _PRODUCT_ID_RE.fullmatch(self.product_id):
            raise DataContractError(
                "product_id must match futures_product_<uuid7>",
                details={"product_id": self.product_id},
            )
        if not isinstance(self.roll_rule, RollRule):
            raise DataContractError("roll_rule must be RollRule")
        if type(self.rank) is not int or self.rank < 0:
            raise DataContractError(
                "rank must be a nonnegative int",
                details={"rank": self.rank},
            )
        if not isinstance(self.adjustment, ContinuousAdjustment):
            raise DataContractError("adjustment must be ContinuousAdjustment")
        if self.adjustment is not ContinuousAdjustment.NONE:
            raise DataContractError(
                "Phase 3A only supports continuous adjustment=none",
                details={"adjustment": self.adjustment.value},
            )
        _require_str(
            self.provider_methodology_version,
            field="provider_methodology_version",
            max_len=64,
        )
        require_aware_datetime(self.valid_from, field_name="valid_from")
        _require_optional_aware(self.valid_to, field="valid_to")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise DataContractError(
                "valid_to must be after valid_from",
                details={"field": "valid_to"},
            )


@dataclass(frozen=True, slots=True)
class ContinuousContractMapping:
    """Maps one continuous series observation interval to a specific contract."""

    continuous_instrument_id: str
    contract_instrument_id: str
    effective_from: datetime
    mapping_source: str
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        continuous = _require_future_instrument_id(
            self.continuous_instrument_id,
            field="continuous_instrument_id",
        )
        contract = _require_future_instrument_id(
            self.contract_instrument_id,
            field="contract_instrument_id",
        )
        if continuous == contract:
            raise DataContractError(
                "continuous and contract instrument ids must differ",
                details={
                    "continuous_instrument_id": continuous,
                    "contract_instrument_id": contract,
                },
            )
        require_aware_datetime(self.effective_from, field_name="effective_from")
        _require_optional_aware(self.effective_to, field="effective_to")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise DataContractError(
                "effective_to must be after effective_from",
                details={"field": "effective_to"},
            )
        _require_str(self.mapping_source, field="mapping_source", max_len=64)


@dataclass(frozen=True, slots=True)
class FuturesCurveContractPoint:
    """One contract node on a same-basis futures curve."""

    instrument_id: str
    contract_month: str
    expiration_at: datetime | None
    price: Decimal
    open_interest: Decimal | None = None
    session_volume: Decimal | None = None

    def __post_init__(self) -> None:
        _require_future_instrument_id(self.instrument_id, field="instrument_id")
        month = _require_str(self.contract_month, field="contract_month", max_len=7)
        if _CONTRACT_MONTH_RE.fullmatch(month) is None:
            raise DataContractError(
                "contract_month must use YYYY-MM",
                details={"contract_month": month},
            )
        _require_optional_aware(self.expiration_at, field="expiration_at")
        _require_nonnegative_decimal(self.price, field="price")
        if self.open_interest is not None:
            _require_nonnegative_decimal(self.open_interest, field="open_interest")
        if self.session_volume is not None:
            _require_nonnegative_decimal(self.session_volume, field="session_volume")


@dataclass(frozen=True, slots=True)
class FuturesCurveSnapshot:
    """Same-provider, same-as_of, same-basis term structure snapshot."""

    product_id: str
    as_of: datetime
    price_basis: PriceBasis
    contracts: tuple[FuturesCurveContractPoint, ...]
    curve_shape: CurveShape
    completeness: CurveCompleteness
    front_next_spread: Decimal | None = None

    def __post_init__(self) -> None:
        if not _PRODUCT_ID_RE.fullmatch(self.product_id):
            raise DataContractError(
                "product_id must match futures_product_<uuid7>",
                details={"product_id": self.product_id},
            )
        require_aware_datetime(self.as_of, field_name="as_of")
        if not isinstance(self.price_basis, PriceBasis):
            raise DataContractError("price_basis must be PriceBasis")
        if not isinstance(self.contracts, tuple):
            raise DataContractError("contracts must be a tuple")
        if not isinstance(self.curve_shape, CurveShape):
            raise DataContractError("curve_shape must be CurveShape")
        if not isinstance(self.completeness, CurveCompleteness):
            raise DataContractError("completeness must be CurveCompleteness")
        if self.front_next_spread is not None:
            _require_decimal(self.front_next_spread, field="front_next_spread")

        seen_ids: set[str] = set()
        prev_sort: tuple[datetime | date, str] | None = None
        for idx, point in enumerate(self.contracts):
            if not isinstance(point, FuturesCurveContractPoint):
                raise DataContractError(
                    "contracts items must be FuturesCurveContractPoint",
                    details={"field": f"contracts[{idx}]"},
                )
            if point.instrument_id in seen_ids:
                raise DataContractError(
                    "contracts instrument_id values must be unique",
                    details={"instrument_id": point.instrument_id},
                )
            seen_ids.add(point.instrument_id)
            # Sort by effective expiration when present, else contract_month.
            sort_key: tuple[datetime | date, str]
            if point.expiration_at is not None:
                sort_key = (point.expiration_at, point.instrument_id)
            else:
                year, month = point.contract_month.split("-")
                sort_key = (date(int(year), int(month), 1), point.instrument_id)
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "contracts must be ordered by expiration then instrument_id",
                    details={"field": f"contracts[{idx}]"},
                )
            prev_sort = sort_key

        if not self.contracts:
            if self.completeness is not CurveCompleteness.EMPTY:
                raise DataContractError(
                    "empty contracts require completeness=empty",
                    details={"completeness": self.completeness.value},
                )
            if self.curve_shape is not CurveShape.NOT_EVALUATED:
                raise DataContractError(
                    "empty contracts require curve_shape=NOT_EVALUATED",
                    details={"curve_shape": self.curve_shape.value},
                )
            if self.front_next_spread is not None:
                raise DataContractError(
                    "empty contracts cannot set front_next_spread",
                    details={"field": "front_next_spread"},
                )
        elif len(self.contracts) == 1:
            if self.front_next_spread is not None:
                raise DataContractError(
                    "front_next_spread requires at least two contracts",
                    details={"field": "front_next_spread"},
                )
        else:
            expected = self.contracts[1].price - self.contracts[0].price
            if self.front_next_spread is None:
                raise DataContractError(
                    "front_next_spread is required when two+ contracts are present",
                    details={"field": "front_next_spread"},
                )
            if self.front_next_spread != expected:
                raise DataContractError(
                    "front_next_spread must equal far_price - near_price",
                    details={
                        "front_next_spread": str(self.front_next_spread),
                        "expected": str(expected),
                    },
                )
