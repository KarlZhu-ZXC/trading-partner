"""Strict Provider-result validators for the A-share snapshot service."""

from __future__ import annotations

from datetime import date, datetime

from application.dto.provider_routing import ProviderSuccess
from domain.a_share.enums import FinancialStatementType
from domain.a_share.models import (
    AnnouncementItem,
    AShareQuote,
    DividendRecord,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    NewsItem,
    UnlockRecord,
)
from domain.common.enums import DataCategory
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument


def _require_provider_success(
    success: object, *, expected_category: DataCategory
) -> ProviderSuccess[object]:
    if not isinstance(success, ProviderSuccess):
        raise DataContractError(
            "provider call must return ProviderSuccess",
            details={"field": "result", "rule": "type"},
        )
    if success.meta.category is not expected_category:
        raise DataContractError(
            f"meta.category must be {expected_category.name}",
            details={
                "field": "meta.category",
                "rule": "category",
                "expected": expected_category.value,
                "actual": (
                    success.meta.category.value
                    if isinstance(success.meta.category, DataCategory)
                    else type(success.meta.category).__name__
                ),
            },
        )
    return success


def _require_value_tuple(value: object, *, field: str = "value") -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(
            f"{field} must be a tuple",
            details={
                "field": field,
                "rule": "type",
                "type": type(value).__name__,
            },
        )
    return value


def _is_current_window(
    as_of: datetime, now: datetime, *, window_seconds: int
) -> bool:
    return as_of <= now and (now - as_of).total_seconds() <= window_seconds


def _pub_sort_key(published_at: datetime | None) -> tuple[int, float]:
    """Published descending; None last (stable for current-window unknown pub)."""
    if published_at is None:
        return (1, 0.0)
    return (0, -published_at.timestamp())


class AShareSnapshotValidationMixin:
    """Validation-only behavior shared by the snapshot aggregation service."""

    _current_window_seconds: int

    # --- strict result validators (E2-parity; never rely on DTO conversion) ---

    def _validate_quote(
        self,
        success: ProviderSuccess[AShareQuote],
        *,
        instrument: Instrument,
        as_of: datetime,
    ) -> None:
        _require_provider_success(success, expected_category=DataCategory.MARKET_QUOTE)
        if not isinstance(success.value, AShareQuote):
            raise DataContractError(
                "success.value must be AShareQuote",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        if success.value.instrument_id != instrument.instrument_id:
            raise DataContractError(
                "quote instrument_id must match request",
                details={"field": "instrument_id", "rule": "identity"},
            )
        if success.value.quote_at > as_of:
            raise DataContractError(
                "quote_at must be <= as_of",
                details={"field": "quote_at", "rule": "as_of_cutoff"},
            )

    def _check_publication(
        self,
        published_at: datetime | None,
        *,
        as_of: datetime,
        now: datetime,
        allow_none_when_current: bool,
        field_prefix: str,
        index: int,
    ) -> None:
        if published_at is None:
            if allow_none_when_current and _is_current_window(
                as_of, now, window_seconds=self._current_window_seconds
            ):
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

    def _validate_fundamentals(
        self,
        success: ProviderSuccess[tuple[FundamentalMetric, ...]],
        *,
        as_of: datetime,
        now: datetime,
        require_non_empty: bool,
    ) -> None:
        _require_provider_success(success, expected_category=DataCategory.FUNDAMENTALS)
        values = _require_value_tuple(success.value)
        if require_non_empty and not values:
            raise DataContractError(
                "fundamentals required non-empty for full equity snapshot",
                details={"field": "value", "rule": "required_non_empty"},
            )
        seen: set[tuple[str, date | None, datetime | None]] = set()
        for idx, item in enumerate(values):
            if not isinstance(item, FundamentalMetric):
                raise DataContractError(
                    "fundamentals elements must be FundamentalMetric",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            identity = (item.name, item.period_end, item.published_at)
            if identity in seen:
                raise DataContractError(
                    "fundamental identity must be unique "
                    "(name+period_end+published_at)",
                    details={
                        "field": "identity",
                        "index": idx,
                        "rule": "unique",
                    },
                )
            seen.add(identity)
            self._check_publication(
                item.published_at,
                as_of=as_of,
                now=now,
                allow_none_when_current=True,
                field_prefix="fundamental",
                index=idx,
            )

    def _validate_f10(
        self,
        success: ProviderSuccess[tuple[F10Section, ...]],
        *,
        as_of: datetime,
        requested_sections: tuple[str, ...],
    ) -> None:
        _require_provider_success(success, expected_category=DataCategory.FUNDAMENTALS)
        values = _require_value_tuple(success.value)
        allowed = frozenset(requested_sections)
        seen: set[str] = set()
        for idx, item in enumerate(values):
            if not isinstance(item, F10Section):
                raise DataContractError(
                    "f10 elements must be F10Section",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.section not in allowed:
                raise DataContractError(
                    "f10 section not in requested set",
                    details={
                        "field": "section",
                        "index": idx,
                        "rule": "requested_only",
                        "section": item.section,
                    },
                )
            if item.section in seen:
                raise DataContractError(
                    "f10 section must be unique",
                    details={"field": "section", "index": idx, "rule": "unique"},
                )
            seen.add(item.section)
            if item.as_of > as_of:
                raise DataContractError(
                    "f10 section as_of must be <= requested as_of",
                    details={
                        "field": "as_of",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )

    def _validate_statements(
        self,
        success: ProviderSuccess[tuple[FinancialStatementLine, ...]],
        *,
        as_of: datetime,
        now: datetime,
        require_non_empty: bool,
        requested_types: tuple[FinancialStatementType, ...],
    ) -> None:
        _require_provider_success(
            success, expected_category=DataCategory.FINANCIAL_STATEMENTS
        )
        values = _require_value_tuple(success.value)
        if require_non_empty and not values:
            raise DataContractError(
                "financial statements required non-empty for full equity snapshot",
                details={"field": "value", "rule": "required_non_empty"},
            )
        allowed = frozenset(requested_types)
        seen: set[tuple[FinancialStatementType, date, str]] = set()
        for idx, item in enumerate(values):
            if not isinstance(item, FinancialStatementLine):
                raise DataContractError(
                    "statements elements must be FinancialStatementLine",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.statement_type not in allowed:
                raise DataContractError(
                    "statement_type not in requested set",
                    details={
                        "field": "statement_type",
                        "index": idx,
                        "rule": "requested_only",
                    },
                )
            identity = (item.statement_type, item.period_end, item.item_code)
            if identity in seen:
                raise DataContractError(
                    "statement identity must be unique "
                    "(statement_type+period_end+item_code)",
                    details={
                        "field": "identity",
                        "index": idx,
                        "rule": "unique",
                    },
                )
            seen.add(identity)
            self._check_publication(
                item.published_at,
                as_of=as_of,
                now=now,
                allow_none_when_current=True,
                field_prefix="statement",
                index=idx,
            )

    def _validate_announcements(
        self,
        success: ProviderSuccess[tuple[AnnouncementItem, ...]],
        *,
        as_of: datetime,
    ) -> None:
        _require_provider_success(success, expected_category=DataCategory.ANNOUNCEMENTS)
        values = _require_value_tuple(success.value)
        seen: set[str] = set()
        prev_sort: tuple[float, str] | None = None
        for idx, item in enumerate(values):
            if not isinstance(item, AnnouncementItem):
                raise DataContractError(
                    "announcements elements must be AnnouncementItem",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.announcement_key in seen:
                raise DataContractError(
                    "announcement_key must be unique",
                    details={
                        "field": "announcement_key",
                        "index": idx,
                        "rule": "unique",
                    },
                )
            seen.add(item.announcement_key)
            if item.published_at > as_of:
                raise DataContractError(
                    "announcement published_at must be <= as_of",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            sort_key = (-item.published_at.timestamp(), item.announcement_key)
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "announcements must be sorted published_at desc, key asc",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "sorted",
                    },
                )
            prev_sort = sort_key

    def _validate_news(
        self,
        success: ProviderSuccess[tuple[NewsItem, ...]],
        *,
        as_of: datetime,
        start: datetime,
    ) -> None:
        _require_provider_success(success, expected_category=DataCategory.NEWS)
        values = _require_value_tuple(success.value)
        seen: set[str] = set()
        prev_sort: tuple[float, str] | None = None
        for idx, item in enumerate(values):
            if not isinstance(item, NewsItem):
                raise DataContractError(
                    "news elements must be NewsItem",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.news_key in seen:
                raise DataContractError(
                    "news_key must be unique",
                    details={"field": "news_key", "index": idx, "rule": "unique"},
                )
            seen.add(item.news_key)
            if item.published_at < start or item.published_at > as_of:
                raise DataContractError(
                    "news published_at outside inclusive window",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "window",
                    },
                )
            sort_key = (-item.published_at.timestamp(), item.news_key)
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "news must be sorted published_at desc, key asc",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "sorted",
                    },
                )
            prev_sort = sort_key

    def _validate_corporate_actions(
        self,
        success: ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]],
        *,
        as_of: datetime,
        now: datetime,
    ) -> None:
        _require_provider_success(
            success, expected_category=DataCategory.CORPORATE_ACTIONS
        )
        values = _require_value_tuple(success.value)
        seen_unlock: set[
            tuple[date, str | None, int | None, datetime | None]
        ] = set()
        seen_div: set[
            tuple[
                int,
                str,
                date | None,
                object,
                datetime | None,
            ]
        ] = set()
        prev_sort: tuple[object, ...] | None = None
        for idx, item in enumerate(values):
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
                kind_rank = 0
                sort_key: tuple[object, ...] = (
                    *_pub_sort_key(item.published_at),
                    kind_rank,
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
                kind_rank = 1
                sort_key = (
                    *_pub_sort_key(item.published_at),
                    kind_rank,
                    -item.fiscal_year,
                    item.plan_status,
                    item.ex_date.toordinal() if item.ex_date is not None else -1,
                )
            else:
                raise DataContractError(
                    "corporate actions elements must be UnlockRecord or DividendRecord",
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
                allow_none_when_current=True,
                field_prefix="corporate action",
                index=idx,
            )
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "corporate actions must be sorted by stable published/kind keys",
                    details={
                        "field": "order",
                        "index": idx,
                        "rule": "sorted",
                    },
                )
            prev_sort = sort_key

