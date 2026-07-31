"""Strict Provider-result validators for the A-share capital service."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderSuccess
from application.ports.a_share_trading_calendar import AShareTradingCalendar
from domain.a_share.enums import BarInterval
from domain.a_share.models import (
    BlockTradeRecord,
    ChipDistributionSnapshot,
    DividendRecord,
    DragonTigerRecord,
    FundFlowPoint,
    MarginRecord,
    NorthboundFlowPoint,
    ShareholderCountRecord,
    UnlockRecord,
)
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    DataCategory,
    ReliabilityLevel,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _is_current_window(as_of: datetime, now: datetime, *, window_seconds: int) -> bool:
    return as_of <= now and (now - as_of).total_seconds() <= window_seconds


def _pub_sort_key(published_at: datetime | None) -> tuple[int, float]:
    """Published descending; None last (stable for current-window unknown pub)."""
    if published_at is None:
        return (1, 0.0)
    return (0, -published_at.timestamp())


class AShareCapitalValidationMixin:
    """Validation-only behavior shared by the capital aggregation service."""

    _calendar: AShareTradingCalendar
    _current_window_seconds: int

    # --- strict validators ----------------------------------------------------

    def _require_success(
        self, success: object, *, expected_category: DataCategory
    ) -> ProviderSuccess[object]:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not expected_category:
            raise DataContractError(
                "meta.category must match expected category",
                details={
                    "field": "meta.category",
                    "rule": "category",
                    "expected": expected_category.value,
                },
            )
        return success

    def _check_publication(
        self,
        published_at: datetime | None,
        *,
        as_of: datetime,
        now: datetime,
        field_prefix: str,
        index: int,
    ) -> None:
        if published_at is None:
            if _is_current_window(as_of, now, window_seconds=self._current_window_seconds):
                return
            raise DataContractError(
                f"{field_prefix} published_at unknown rejected for historical as_of",
                details={
                    "field": "published_at",
                    "index": index,
                    "rule": "historical_requires_published_at",
                },
            )
        if published_at > as_of:
            raise DataContractError(
                f"{field_prefix} published_at must be <= as_of",
                details={
                    "field": "published_at",
                    "index": index,
                    "rule": "as_of_cutoff",
                },
            )

    def _validate_fund_flow(
        self,
        success: ProviderSuccess[tuple[FundFlowPoint, ...]],
        *,
        as_of: datetime,
        expected_interval: BarInterval,
        start: date | None = None,
        end: date | None = None,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of FundFlowPoint",
                details={"field": "value", "rule": "type"},
            )
        prev: datetime | None = None
        for idx, point in enumerate(success.value):
            if not isinstance(point, FundFlowPoint):
                raise DataContractError(
                    "fund flow elements must be FundFlowPoint",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if point.interval is not expected_interval:
                raise DataContractError(
                    "fund flow interval must match requested metric",
                    details={
                        "field": "interval",
                        "index": idx,
                        "rule": "interval",
                        "expected": expected_interval.value,
                    },
                )
            if point.occurred_at > as_of:
                raise DataContractError(
                    "fund flow occurred_at must be <= as_of",
                    details={
                        "field": "occurred_at",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            if prev is not None and point.occurred_at <= prev:
                raise DataContractError(
                    "fund flow must be sorted by unique occurred_at ascending",
                    details={
                        "field": "occurred_at",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev = point.occurred_at
            if start is not None or end is not None:
                local_day = point.occurred_at.astimezone(_SHANGHAI).date()
                if start is not None and local_day < start:
                    raise DataContractError(
                        "fund flow day before start",
                        details={"field": "occurred_at", "index": idx, "rule": "range"},
                    )
                if end is not None and local_day > end:
                    raise DataContractError(
                        "fund flow day after end",
                        details={"field": "occurred_at", "index": idx, "rule": "range"},
                    )

    def _validate_northbound(
        self,
        success: ProviderSuccess[tuple[NorthboundFlowPoint, ...]],
        *,
        as_of: datetime,
        start: date | None,
        end: date | None,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of NorthboundFlowPoint",
                details={"field": "value", "rule": "type"},
            )
        as_of_day = as_of.astimezone(_SHANGHAI).date()
        seen: set[tuple[date, str]] = set()
        prev_key: tuple[date, str] | None = None
        incomplete_present = False
        for idx, point in enumerate(success.value):
            if not isinstance(point, NorthboundFlowPoint):
                raise DataContractError(
                    "northbound elements must be NorthboundFlowPoint",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if point.trade_date > as_of_day:
                raise DataContractError(
                    "northbound trade_date must be <= as_of local day",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            if start is not None and point.trade_date < start:
                raise DataContractError(
                    "northbound trade_date before start",
                    details={"field": "trade_date", "index": idx, "rule": "range"},
                )
            if end is not None and point.trade_date > end:
                raise DataContractError(
                    "northbound trade_date after end",
                    details={"field": "trade_date", "index": idx, "rule": "range"},
                )
            key = (point.trade_date, point.channel)
            if key in seen:
                raise DataContractError(
                    "northbound (trade_date, channel) must be unique",
                    details={"field": "channel", "index": idx, "rule": "unique"},
                )
            seen.add(key)
            if prev_key is not None and key < prev_key:
                raise DataContractError(
                    "northbound must be sorted by trade_date then channel ascending",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev_key = key
            if point.is_authoritative and point.reliability is ReliabilityLevel.LOW:
                raise DataContractError(
                    "authoritative northbound cannot be low reliability",
                    details={
                        "field": "reliability",
                        "index": idx,
                        "rule": "auth_reliability",
                    },
                )
            all_none = (
                point.net_buy_cny is None and point.buy_cny is None and point.sell_cny is None
            )
            if all_none:
                incomplete_present = True
                note = point.disclosure_note
                if not isinstance(note, str) or not note.strip():
                    raise DataContractError(
                        "incomplete northbound requires nonblank disclosure_note",
                        details={
                            "field": "disclosure_note",
                            "index": idx,
                            "rule": "incomplete_disclosure",
                        },
                    )
        if incomplete_present:
            meta_warnings = success.meta.warnings
            if "NORTHBOUND_DISCLOSURE_INCOMPLETE" not in meta_warnings:
                raise DataContractError(
                    "incomplete northbound requires NORTHBOUND_DISCLOSURE_INCOMPLETE meta warning",
                    details={
                        "field": "meta.warnings",
                        "rule": "incomplete_disclosure",
                    },
                )

    def _validate_dragon_tiger(
        self,
        success: ProviderSuccess[tuple[DragonTigerRecord, ...]],
        *,
        trade_date: date,
        instrument: Instrument | None,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of DragonTigerRecord",
                details={"field": "value", "rule": "type"},
            )
        seen: set[tuple[str, str]] = set()
        prev_key: tuple[str, str] | None = None
        for idx, record in enumerate(success.value):
            if not isinstance(record, DragonTigerRecord):
                raise DataContractError(
                    "dragon tiger elements must be DragonTigerRecord",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if record.trade_date != trade_date:
                raise DataContractError(
                    "dragon tiger trade_date must match request",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "trade_date",
                    },
                )
            if instrument is not None and record.instrument_id != instrument.instrument_id:
                raise DataContractError(
                    "dragon tiger instrument_id must match request",
                    details={
                        "field": "instrument_id",
                        "index": idx,
                        "rule": "identity",
                    },
                )
            identity = (record.instrument_id, record.reason)
            if identity in seen:
                raise DataContractError(
                    "dragon tiger identity must be unique (instrument_id+reason)",
                    details={"field": "identity", "index": idx, "rule": "unique"},
                )
            seen.add(identity)
            if prev_key is not None and identity < prev_key:
                raise DataContractError(
                    "dragon tiger must be sorted by instrument_id then reason ascending",
                    details={
                        "field": "identity",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev_key = identity

    def _validate_margin(
        self,
        success: ProviderSuccess[tuple[MarginRecord, ...]],
        *,
        as_of: datetime,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of MarginRecord",
                details={"field": "value", "rule": "type"},
            )
        as_of_day = as_of.astimezone(_SHANGHAI).date()
        prev: date | None = None
        for idx, record in enumerate(success.value):
            if not isinstance(record, MarginRecord):
                raise DataContractError(
                    "margin elements must be MarginRecord",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if record.trade_date > as_of_day:
                raise DataContractError(
                    "margin trade_date must be <= as_of local day",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            if prev is not None and record.trade_date >= prev:
                raise DataContractError(
                    "margin must be sorted by trade_date descending unique",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev = record.trade_date

    def _validate_block_trades(
        self,
        success: ProviderSuccess[tuple[BlockTradeRecord, ...]],
        *,
        as_of: datetime,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of BlockTradeRecord",
                details={"field": "value", "rule": "type"},
            )
        as_of_day = as_of.astimezone(_SHANGHAI).date()
        seen: set[tuple[object, ...]] = set()
        prev_key: tuple[object, ...] | None = None
        for idx, record in enumerate(success.value):
            if not isinstance(record, BlockTradeRecord):
                raise DataContractError(
                    "block trade elements must be BlockTradeRecord",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if record.trade_date > as_of_day:
                raise DataContractError(
                    "block trade trade_date must be <= as_of local day",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            identity = (
                record.trade_date,
                record.price,
                record.volume_shares,
                record.amount_cny,
                record.buyer_branch or "",
                record.seller_branch or "",
            )
            if identity in seen:
                raise DataContractError(
                    "block trade identity must be unique",
                    details={"field": "identity", "index": idx, "rule": "unique"},
                )
            seen.add(identity)
            sort_key = (
                -record.trade_date.toordinal(),
                record.price,
                record.volume_shares,
                record.amount_cny,
                record.buyer_branch or "",
                record.seller_branch or "",
            )
            if prev_key is not None and sort_key < prev_key:
                raise DataContractError(
                    "block trades must be sorted by trade_date desc then stable keys",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev_key = sort_key

    def _validate_shareholder(
        self,
        success: ProviderSuccess[tuple[ShareholderCountRecord, ...]],
        *,
        as_of: datetime,
        now: datetime,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of ShareholderCountRecord",
                details={"field": "value", "rule": "type"},
            )
        seen: set[tuple[date, datetime | None]] = set()
        prev_key: tuple[object, ...] | None = None
        for idx, record in enumerate(success.value):
            if not isinstance(record, ShareholderCountRecord):
                raise DataContractError(
                    "shareholder elements must be ShareholderCountRecord",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            self._check_publication(
                record.published_at,
                as_of=as_of,
                now=now,
                field_prefix="shareholder",
                index=idx,
            )
            identity = (record.period_end, record.published_at)
            if identity in seen:
                raise DataContractError(
                    "shareholder identity must be unique (period_end+published_at)",
                    details={"field": "identity", "index": idx, "rule": "unique"},
                )
            seen.add(identity)
            sort_key = (
                -record.period_end.toordinal(),
                *_pub_sort_key(record.published_at),
            )
            if prev_key is not None and sort_key < prev_key:
                raise DataContractError(
                    "shareholder must be sorted by period_end desc then published_at",
                    details={
                        "field": "period_end",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev_key = sort_key

    def _validate_chip(
        self,
        success: ProviderSuccess[ChipDistributionSnapshot],
        *,
        instrument: Instrument,
        as_of: datetime,
    ) -> None:
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "chip distribution supports equity only",
                details={"field": "instrument", "rule": "asset_support"},
            )
        self._require_success(success, expected_category=DataCategory.CAPITAL)
        if not isinstance(success.value, ChipDistributionSnapshot):
            raise DataContractError(
                "success.value must be ChipDistributionSnapshot",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        if success.value.as_of > as_of:
            raise DataContractError(
                "chip as_of must be <= request as_of",
                details={"field": "as_of", "rule": "as_of_cutoff"},
            )
        value = success.value
        if (
            success.meta.vendor is not VendorId.EASTMONEY
            or success.meta.as_of != as_of
            or success.meta.adjustment is not AdjustmentMethod.FORWARD_ADJUSTED
        ):
            raise DataContractError(
                "chip provider meta provenance mismatch", details={"rule": "provenance"}
            )
        if set(success.meta.warnings) != {"DERIVED_CHIP_DISTRIBUTION"}:
            raise DataContractError(
                "chip warning must be exact derived marker", details={"rule": "warning"}
            )
        if (
            value.source_vendor is not VendorId.EASTMONEY
            or value.reliability is not ReliabilityLevel.LOW
            or value.is_authoritative is not False
        ):
            raise DataContractError(
                "chip source provenance mismatch", details={"rule": "provenance"}
            )
        if (
            value.calculation_method != "turnover_decay_uniform_range"
            or value.algorithm_version != "tp_chip_v1"
            or value.lookback_sessions != 120
            or value.input_adjustment is not AdjustmentMethod.FORWARD_ADJUSTED
        ):
            raise DataContractError(
                "chip algorithm invariant mismatch", details={"rule": "algorithm"}
            )
        sessions = self._calendar.sessions_for(value.bar_trade_date)
        if not sessions or value.as_of != sessions[-1].end_at:
            raise DataContractError(
                "chip snapshot time must be the bar session close",
                details={"field": "as_of", "rule": "session_close"},
            )

    def _validate_corporate_actions(
        self,
        success: ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]],
        *,
        as_of: datetime,
        now: datetime,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.CORPORATE_ACTIONS)
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of Unlock/Dividend records",
                details={"field": "value", "rule": "type"},
            )
        seen_unlock: set[tuple[date, str | None, int | None, datetime | None]] = set()
        seen_div: set[tuple[int, str, date | None, object, datetime | None]] = set()
        prev_sort: tuple[object, ...] | None = None
        for idx, item in enumerate(success.value):
            if isinstance(item, UnlockRecord):
                identity = (
                    item.unlock_date,
                    item.unlock_type,
                    item.unlock_shares,
                    item.published_at,
                )
                if identity in seen_unlock:
                    raise DataContractError(
                        "unlock identity must be unique "
                        "(unlock_date+unlock_type+unlock_shares+published_at)",
                        details={
                            "field": "identity",
                            "index": idx,
                            "rule": "unique",
                        },
                    )
                seen_unlock.add(identity)
                sort_key: tuple[object, ...] = (
                    *_pub_sort_key(item.published_at),
                    0,
                    -item.unlock_date.toordinal(),
                    item.unlock_type or "",
                    item.unlock_shares if item.unlock_shares is not None else -1,
                )
            elif isinstance(item, DividendRecord):
                identity_d = (
                    item.fiscal_year,
                    item.plan_status,
                    item.ex_date,
                    item.cash_per_share,
                    item.published_at,
                )
                if identity_d in seen_div:
                    raise DataContractError(
                        "dividend identity must be unique "
                        "(fiscal_year+plan_status+ex_date+cash_per_share+published_at)",
                        details={
                            "field": "identity",
                            "index": idx,
                            "rule": "unique",
                        },
                    )
                seen_div.add(identity_d)
                sort_key = (
                    *_pub_sort_key(item.published_at),
                    1,
                    -item.fiscal_year,
                    item.plan_status,
                    item.ex_date.toordinal() if item.ex_date is not None else -1,
                )
            else:
                raise DataContractError(
                    "corporate action elements must be UnlockRecord or DividendRecord",
                    details={
                        "field": "value",
                        "index": idx,
                        "rule": "type",
                        "type": type(item).__name__,
                    },
                )
            self._check_publication(
                item.published_at,
                as_of=as_of,
                now=now,
                field_prefix="corporate action",
                index=idx,
            )
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "corporate actions must be sorted by stable published/kind keys",
                    details={
                        "field": "value",
                        "index": idx,
                        "rule": "sorted_unique",
                    },
                )
            prev_sort = sort_key
