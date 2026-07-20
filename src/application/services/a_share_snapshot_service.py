"""A-share composite snapshot product service (Phase 1E E3).

``AShareSnapshotService.get_snapshot`` accepts a resolved ``Instrument`` plus
aware ``as_of`` and ``AShareSnapshotDetail``. E5 will wire InstrumentMaster /
MCP / bootstrap; this module is Router-backed only.

Parallel component fetches use ``asyncio.TaskGroup`` (structured concurrency).
All started tasks complete or cancel before ``get_snapshot`` returns.

Publication / current-window decisions use the single ``now`` sampled at
``get_snapshot`` entry so concurrent validators never drift across a window
boundary (including FixedClock stepping). Adapter clocks remain independent
provider defenses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from application.dto.a_share import (
    AnnouncementItemDTO,
    AShareCompositeSnapshotDTO,
    AShareQuoteDTO,
    DividendRecordDTO,
    F10SectionDTO,
    FinancialStatementLineDTO,
    FundamentalMetricDTO,
    NewsItemDTO,
    UnlockRecordDTO,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import (
    AShareDisclosureProvider,
    AShareFinancialStatementsProvider,
    AShareFundamentalsProvider,
    AShareNewsProvider,
    AShareQuoteProvider,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import (
    A_SHARE_TOOL_ASSET_SUPPORT,
    SNAPSHOT_FULL_POLICY,
    SNAPSHOT_NEWS_LOOKBACK_DAYS,
    SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY,
    SNAPSHOT_SUMMARY_POLICY,
)
from application.services.component_settlement import settle_router_component
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import (
    AShareComponentType,
    AShareSnapshotDetail,
    FinancialStatementType,
)
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
from domain.common.enums import AssetType, DataCategory, Market
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument

OP_QUOTE = "a_share.quote.v1"
OP_FUNDAMENTALS = "a_share.fundamentals.v1"
OP_F10 = "a_share.f10.v1"
OP_STATEMENTS = "a_share.statements.v1"
OP_ANNOUNCEMENTS = "a_share.announcements.v1"
OP_NEWS = "a_share.news.v1"
OP_CORPORATE_ACTIONS = "a_share.corporate_actions.v1"

_DEFAULT_F10_SECTIONS = ("company", "business")
_DEFAULT_STATEMENT_TYPES = (
    FinancialStatementType.BALANCE_SHEET,
    FinancialStatementType.INCOME_STATEMENT,
    FinancialStatementType.CASH_FLOW,
)
_DEFAULT_STATEMENT_PERIODS = 4
_DEFAULT_ANNOUNCEMENT_LIMIT = 20
_DEFAULT_NEWS_LIMIT = 20
_DEFAULT_CURRENT_WINDOW_SECONDS = 300


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


@dataclass(frozen=True, slots=True)
class AShareSnapshotResult:
    """Service outcome wrapping the frozen E1 composite DTO."""

    ok: bool
    data: AShareCompositeSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(
            self.provenance,
            order=(
                AShareComponentType.QUOTE,
                AShareComponentType.FUNDAMENTALS,
                AShareComponentType.STATEMENTS,
                AShareComponentType.F10,
                AShareComponentType.ANNOUNCEMENTS,
                AShareComponentType.NEWS,
                AShareComponentType.CORPORATE_ACTIONS,
            ),
        )
        if type(self.ok) is not bool:
            raise DataContractError(
                "ok must be exact bool",
                details={"field": "ok", "rule": "type", "type": type(self.ok).__name__},
            )
        if not isinstance(self.warnings, tuple):
            raise DataContractError(
                "warnings must be a tuple of WarningInfo",
                details={
                    "field": "warnings",
                    "rule": "type",
                    "type": type(self.warnings).__name__,
                },
            )
        for idx, warning in enumerate(self.warnings):
            if not isinstance(warning, WarningInfo):
                raise DataContractError(
                    "warnings elements must be WarningInfo",
                    details={"field": "warnings", "index": idx, "rule": "type"},
                )
        if self.ok:
            if self.data is None:
                raise DataContractError(
                    "AShareSnapshotResult ok=True requires data non-None",
                    details={"field": "data", "rule": "ok_true_data_required"},
                )
            if self.error is not None:
                raise DataContractError(
                    "AShareSnapshotResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            validate_data_provenance(self.data, self.provenance)
            components = {item.component for item in self.provenance}
            required = {AShareComponentType.QUOTE}
            if not required.issubset(components):
                raise DataContractError("successful snapshot omits required provenance")
        else:
            if self.data is not None:
                raise DataContractError(
                    "AShareSnapshotResult ok=False requires data is None",
                    details={"field": "data", "rule": "ok_false_data_none"},
                )
            if self.error is None or not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "AShareSnapshotResult ok=False requires typed TradingPartnerError",
                    details={
                        "field": "error",
                        "rule": "ok_false_error_required",
                        "type": type(self.error).__name__,
                    },
                )


class AShareSnapshotService:
    """E3 product service: summary/full snapshot via ProviderRouter components."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        quote_codec: ProviderCacheCodec[AShareQuote],
        fundamentals_codec: ProviderCacheCodec[tuple[FundamentalMetric, ...]],
        f10_codec: ProviderCacheCodec[tuple[F10Section, ...]],
        statements_codec: ProviderCacheCodec[tuple[FinancialStatementLine, ...]],
        announcements_codec: ProviderCacheCodec[tuple[AnnouncementItem, ...]],
        news_codec: ProviderCacheCodec[tuple[NewsItem, ...]],
        corporate_actions_codec: ProviderCacheCodec[
            tuple[UnlockRecord | DividendRecord, ...]
        ],
        current_window_seconds: int = _DEFAULT_CURRENT_WINDOW_SECONDS,
    ) -> None:
        if router is None or clock is None:
            raise DataContractError(
                "router and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        if (
            not isinstance(current_window_seconds, int)
            or isinstance(current_window_seconds, bool)
            or current_window_seconds < 0
        ):
            raise DataContractError(
                "current_window_seconds must be a nonnegative exact int",
                details={"field": "current_window_seconds", "rule": "nonnegative"},
            )
        self._current_window_seconds = current_window_seconds
        for name, codec in (
            ("quote_codec", quote_codec),
            ("fundamentals_codec", fundamentals_codec),
            ("f10_codec", f10_codec),
            ("statements_codec", statements_codec),
            ("announcements_codec", announcements_codec),
            ("news_codec", news_codec),
            ("corporate_actions_codec", corporate_actions_codec),
        ):
            if codec is None or not hasattr(codec, "codec_id"):
                raise DataContractError(
                    f"{name} must be a ProviderCacheCodec",
                    details={"field": name, "rule": "required"},
                )
        self._router = router
        self._clock = clock
        self._quote_codec = quote_codec
        self._fundamentals_codec = fundamentals_codec
        self._f10_codec = f10_codec
        self._statements_codec = statements_codec
        self._announcements_codec = announcements_codec
        self._news_codec = news_codec
        self._corporate_actions_codec = corporate_actions_codec

    def _require_a_share(self, instrument: Instrument) -> None:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.A_SHARE:
            raise DataContractError(
                "instrument market must be A_SHARE",
                details={"field": "instrument", "rule": "market"},
            )
        support = A_SHARE_TOOL_ASSET_SUPPORT["snapshot"].get(instrument.asset_type)
        if support == "reject" or support is None:
            raise DataContractError(
                "asset type not supported for snapshot",
                details={
                    "field": "instrument",
                    "rule": "asset_support",
                    "asset_type": instrument.asset_type.value,
                },
            )

    def _is_equity_full_matrix(self, instrument: Instrument) -> bool:
        return instrument.asset_type is AssetType.EQUITY

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
        detail: AShareSnapshotDetail,
    ) -> AShareSnapshotResult:
        require_aware_datetime(as_of, field_name="as_of")
        # Single sampled now for the whole snapshot composition (atomic clock).
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        if not isinstance(detail, AShareSnapshotDetail):
            raise DataContractError(
                "detail must be AShareSnapshotDetail",
                details={"field": "detail", "rule": "type"},
            )
        self._require_a_share(instrument)

        equity_full = (
            detail is AShareSnapshotDetail.FULL and self._is_equity_full_matrix(instrument)
        )
        # ETF/INDEX: quote required only; company F10/statements/actions not required.
        # FULL equity snapshots request all components, but only quote is required.
        require_fundamentals = False
        require_statements = False
        want_statements = equity_full
        want_optional_fundamentals = detail is AShareSnapshotDetail.SUMMARY or equity_full
        want_f10 = equity_full
        want_actions = equity_full
        want_announcements = True
        want_news = True

        warnings: list[WarningInfo] = []

        # Structured concurrency: every started task is awaited/cancelled before
        # return. Result processing order is fixed (independent of completion).
        quote_task: asyncio.Task[RouterExecutionResult[AShareQuote]] | None = None
        fund_task: (
            asyncio.Task[RouterExecutionResult[tuple[FundamentalMetric, ...]]] | None
        ) = None
        stmt_task: (
            asyncio.Task[RouterExecutionResult[tuple[FinancialStatementLine, ...]]]
            | None
        ) = None
        f10_task: (
            asyncio.Task[RouterExecutionResult[tuple[F10Section, ...]]] | None
        ) = None
        ann_task: (
            asyncio.Task[RouterExecutionResult[tuple[AnnouncementItem, ...]]] | None
        ) = None
        news_task: (
            asyncio.Task[RouterExecutionResult[tuple[NewsItem, ...]]] | None
        ) = None
        actions_task: (
            asyncio.Task[
                RouterExecutionResult[tuple[UnlockRecord | DividendRecord, ...]]
            ]
            | None
        ) = None

        async with asyncio.TaskGroup() as tg:
            quote_task = tg.create_task(
                settle_router_component(self._fetch_quote(instrument, as_of, now=now))
            )
            if want_optional_fundamentals or require_fundamentals:
                fund_task = tg.create_task(
                    settle_router_component(self._fetch_fundamentals(
                        instrument,
                        as_of,
                        now=now,
                        require_non_empty=require_fundamentals,
                        tool_policy=(
                            SNAPSHOT_FULL_POLICY
                            if detail is AShareSnapshotDetail.FULL
                            else SNAPSHOT_SUMMARY_POLICY
                        ),
                    ))
                )
            if want_statements:
                stmt_task = tg.create_task(
                    settle_router_component(self._fetch_statements(
                        instrument, as_of, now=now, require_non_empty=require_statements
                    ))
                )
            if want_f10:
                f10_task = tg.create_task(
                    settle_router_component(self._fetch_f10(instrument, as_of, now=now))
                )
            if want_announcements:
                ann_task = tg.create_task(
                    settle_router_component(self._fetch_announcements(
                        instrument, as_of, now=now, detail=detail
                    ))
                )
            if want_news:
                news_task = tg.create_task(
                    settle_router_component(
                        self._fetch_news(instrument, as_of, now=now, detail=detail)
                    )
                )
            if want_actions:
                actions_task = tg.create_task(
                    settle_router_component(self._fetch_actions(instrument, as_of, now=now))
                )

        assert quote_task is not None
        quote_res = quote_task.result()
        component_results = (
            (AShareComponentType.QUOTE, quote_res),
            (AShareComponentType.FUNDAMENTALS, fund_task.result() if fund_task else None),
            (AShareComponentType.STATEMENTS, stmt_task.result() if stmt_task else None),
            (AShareComponentType.F10, f10_task.result() if f10_task else None),
            (AShareComponentType.ANNOUNCEMENTS, ann_task.result() if ann_task else None),
            (AShareComponentType.NEWS, news_task.result() if news_task else None),
            (
                AShareComponentType.CORPORATE_ACTIONS,
                actions_task.result() if actions_task else None,
            ),
        )
        provenance = tuple(
            component_provenance(component, result.meta, result.value)
            for component, result in component_results
            if result is not None
            and result.ok
            and result.value is not None
            and result.meta is not None
        )
        # All scheduled components settled; merge their warnings in frozen product
        # order before selecting any required failure.
        for _component, result in component_results:
            if result is not None:
                self._merge_warnings(warnings, result)
        if not quote_res.ok or quote_res.value is None:
            return AShareSnapshotResult(
                ok=False,
                data=None,
                warnings=tuple(warnings),
                error=quote_res.error
                or DataContractError(
                    "required quote component failed",
                    details={"component": "quote", "rule": "required"},
                ),
                provenance=provenance,
            )

        fundamentals: tuple[FundamentalMetric, ...] = ()
        if fund_task is not None:
            fund_res = fund_task.result()
            if fund_res.ok and fund_res.value is not None:
                fundamentals = fund_res.value
                self._merge_warnings(warnings, fund_res)
                if detail is AShareSnapshotDetail.FULL and not fund_res.value:
                    self._partial(warnings, "fundamentals")
            elif require_fundamentals:
                self._merge_warnings(warnings, fund_res)
                return AShareSnapshotResult(
                    ok=False,
                    data=None,
                    warnings=tuple(warnings),
                    error=fund_res.error
                    or DataContractError(
                        "required fundamentals component failed",
                        details={"component": "fundamentals", "rule": "required"},
                    ),
                    provenance=provenance,
                )
            else:
                self._partial(warnings, "fundamentals")
                self._merge_warnings(warnings, fund_res)

        statements: tuple[FinancialStatementLine, ...] = ()
        if stmt_task is not None:
            stmt_res = stmt_task.result()
            if stmt_res.ok and stmt_res.value is not None:
                statements = stmt_res.value
                self._merge_warnings(warnings, stmt_res)
                if detail is AShareSnapshotDetail.FULL and not stmt_res.value:
                    self._partial(warnings, "statements")
            elif require_statements:
                self._merge_warnings(warnings, stmt_res)
                return AShareSnapshotResult(
                    ok=False,
                    data=None,
                    warnings=tuple(warnings),
                    error=stmt_res.error
                    or DataContractError(
                        "required financial statements component failed",
                        details={"component": "statements", "rule": "required"},
                    ),
                    provenance=provenance,
                )
            else:
                self._partial(warnings, "statements")
                self._merge_warnings(warnings, stmt_res)

        f10_sections: tuple[F10Section, ...] = ()
        if f10_task is not None:
            f10_res = f10_task.result()
            if f10_res.ok and f10_res.value is not None:
                f10_sections = f10_res.value
                self._merge_warnings(warnings, f10_res)
            else:
                self._partial(warnings, "f10")
                self._merge_warnings(warnings, f10_res)

        announcements: tuple[AnnouncementItem, ...] = ()
        if ann_task is not None:
            ann_res = ann_task.result()
            if ann_res.ok and ann_res.value is not None:
                announcements = ann_res.value
                self._merge_warnings(warnings, ann_res)
            else:
                self._partial(warnings, "announcements")
                self._merge_warnings(warnings, ann_res)

        news: tuple[NewsItem, ...] = ()
        if news_task is not None:
            news_res = news_task.result()
            if news_res.ok and news_res.value is not None:
                news = news_res.value
                self._merge_warnings(warnings, news_res)
            else:
                self._partial(warnings, "news")
                self._merge_warnings(warnings, news_res)

        unlocks: list[UnlockRecord] = []
        dividends: list[DividendRecord] = []
        if actions_task is not None:
            act_res = actions_task.result()
            if act_res.ok and act_res.value is not None:
                for item in act_res.value:
                    if isinstance(item, UnlockRecord):
                        unlocks.append(item)
                    elif isinstance(item, DividendRecord):
                        dividends.append(item)
                self._merge_warnings(warnings, act_res)
            else:
                self._partial(warnings, "corporate_actions")
                self._merge_warnings(warnings, act_res)

        dto = AShareCompositeSnapshotDTO(
            instrument_id=instrument.instrument_id,
            detail=detail,
            as_of=as_of,
            quote=AShareQuoteDTO.from_domain(quote_res.value),
            fundamentals=tuple(
                FundamentalMetricDTO.from_domain(m) for m in fundamentals
            ),
            statements=tuple(
                FinancialStatementLineDTO.from_domain(s) for s in statements
            ),
            f10_sections=tuple(F10SectionDTO.from_domain(s) for s in f10_sections),
            announcements=tuple(
                AnnouncementItemDTO.from_domain(a) for a in announcements
            ),
            news=tuple(NewsItemDTO.from_domain(n) for n in news),
            unlocks=tuple(UnlockRecordDTO.from_domain(u) for u in unlocks),
            dividends=tuple(DividendRecordDTO.from_domain(d) for d in dividends),
            provenance=provenance_dtos(provenance),
        )
        return AShareSnapshotResult(
            ok=True,
            data=dto,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

    @staticmethod
    def _partial(warnings: list[WarningInfo], component: str) -> None:
        if not any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in warnings):
            warnings.append(
                WarningInfo(
                    code="PARTIAL_A_SHARE_SNAPSHOT",
                    message="One or more optional snapshot components failed",
                    details={"component": component},
                )
            )

    @staticmethod
    def _merge_warnings(
        warnings: list[WarningInfo], result: RouterExecutionResult[object]
    ) -> None:
        for w in result.warnings:
            if w not in warnings:
                warnings.append(w)
        if result.meta is not None:
            for code in result.meta.warnings:
                if code == "PUBLICATION_TIME_UNKNOWN_EXCLUDED" and not any(
                    x.code == code for x in warnings
                ):
                    warnings.append(
                        WarningInfo(
                            code=code,
                            message="Records with unknown publication time excluded",
                            details={},
                        )
                    )

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

    # --- component fetches ----------------------------------------------------

    async def _fetch_quote(
        self, instrument: Instrument, as_of: datetime, *, now: datetime
    ) -> RouterExecutionResult[AShareQuote]:
        del now  # quote cutoff uses as_of only; now reserved for composition atomics
        fingerprint = build_a_share_fingerprint(
            OP_QUOTE, instrument.instrument_id, {}, as_of
        )

        async def _call(adapter: CategoryProvider) -> ProviderSuccess[AShareQuote]:
            if not isinstance(adapter, AShareQuoteProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.MARKET_QUOTE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_quote(instrument, as_of)

        def _validator(success: ProviderSuccess[AShareQuote]) -> None:
            self._validate_quote(success, instrument=instrument, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.MARKET_QUOTE,
            call=_call,
            operation_name=OP_QUOTE,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=SNAPSHOT_SUMMARY_POLICY,
            bypass_cache=False,
            cache_codec=self._quote_codec,
            result_validator=_validator,
        )

    async def _fetch_fundamentals(
        self,
        instrument: Instrument,
        as_of: datetime,
        *,
        now: datetime,
        require_non_empty: bool,
        tool_policy: ToolDataPolicy,
    ) -> RouterExecutionResult[tuple[FundamentalMetric, ...]]:
        fingerprint = build_a_share_fingerprint(
            OP_FUNDAMENTALS, instrument.instrument_id, {}, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[FundamentalMetric, ...]]:
            if not isinstance(adapter, AShareFundamentalsProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.FUNDAMENTALS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_fundamentals(instrument, as_of)

        def _validator(
            success: ProviderSuccess[tuple[FundamentalMetric, ...]],
        ) -> None:
            self._validate_fundamentals(
                success,
                as_of=as_of,
                now=now,
                require_non_empty=require_non_empty,
            )

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.FUNDAMENTALS,
            call=_call,
            operation_name=OP_FUNDAMENTALS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._fundamentals_codec,
            result_validator=_validator,
        )

    async def _fetch_f10(
        self, instrument: Instrument, as_of: datetime, *, now: datetime
    ) -> RouterExecutionResult[tuple[F10Section, ...]]:
        del now
        params = {"sections": ",".join(sorted(_DEFAULT_F10_SECTIONS))}
        fingerprint = build_a_share_fingerprint(
            OP_F10, instrument.instrument_id, params, as_of
        )
        requested = _DEFAULT_F10_SECTIONS

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[F10Section, ...]]:
            if not isinstance(adapter, AShareFundamentalsProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.FUNDAMENTALS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_f10_sections(
                instrument, sections=requested, as_of=as_of
            )

        def _validator(success: ProviderSuccess[tuple[F10Section, ...]]) -> None:
            self._validate_f10(
                success, as_of=as_of, requested_sections=requested
            )

        # F10 is optional even on full snapshot; do not elevate FUNDAMENTALS to CORE.
        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.FUNDAMENTALS,
            call=_call,
            operation_name=OP_F10,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY,
            bypass_cache=False,
            cache_codec=self._f10_codec,
            result_validator=_validator,
        )

    async def _fetch_statements(
        self,
        instrument: Instrument,
        as_of: datetime,
        *,
        now: datetime,
        require_non_empty: bool,
    ) -> RouterExecutionResult[tuple[FinancialStatementLine, ...]]:
        params = {
            "periods": str(_DEFAULT_STATEMENT_PERIODS),
            "types": ",".join(sorted(t.value for t in _DEFAULT_STATEMENT_TYPES)),
        }
        fingerprint = build_a_share_fingerprint(
            OP_STATEMENTS, instrument.instrument_id, params, as_of
        )
        requested_types = _DEFAULT_STATEMENT_TYPES

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]:
            if not isinstance(adapter, AShareFinancialStatementsProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.FINANCIAL_STATEMENTS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_financial_statements(
                instrument,
                statement_types=requested_types,
                periods=_DEFAULT_STATEMENT_PERIODS,
                as_of=as_of,
            )

        def _validator(
            success: ProviderSuccess[tuple[FinancialStatementLine, ...]],
        ) -> None:
            self._validate_statements(
                success,
                as_of=as_of,
                now=now,
                require_non_empty=require_non_empty,
                requested_types=requested_types,
            )

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.FINANCIAL_STATEMENTS,
            call=_call,
            operation_name=OP_STATEMENTS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=SNAPSHOT_FULL_POLICY,
            bypass_cache=False,
            cache_codec=self._statements_codec,
            result_validator=_validator,
        )

    async def _fetch_announcements(
        self,
        instrument: Instrument,
        as_of: datetime,
        *,
        now: datetime,
        detail: AShareSnapshotDetail,
    ) -> RouterExecutionResult[tuple[AnnouncementItem, ...]]:
        del now
        params = {"limit": str(_DEFAULT_ANNOUNCEMENT_LIMIT)}
        fingerprint = build_a_share_fingerprint(
            OP_ANNOUNCEMENTS, instrument.instrument_id, params, as_of
        )
        policy = (
            SNAPSHOT_FULL_POLICY
            if detail is AShareSnapshotDetail.FULL
            else SNAPSHOT_SUMMARY_POLICY
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[AnnouncementItem, ...]]:
            if not isinstance(adapter, AShareDisclosureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.ANNOUNCEMENTS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_announcements(
                instrument, limit=_DEFAULT_ANNOUNCEMENT_LIMIT, as_of=as_of
            )

        def _validator(
            success: ProviderSuccess[tuple[AnnouncementItem, ...]],
        ) -> None:
            self._validate_announcements(success, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.ANNOUNCEMENTS,
            call=_call,
            operation_name=OP_ANNOUNCEMENTS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=policy,
            bypass_cache=False,
            cache_codec=self._announcements_codec,
            result_validator=_validator,
        )

    async def _fetch_news(
        self,
        instrument: Instrument,
        as_of: datetime,
        *,
        now: datetime,
        detail: AShareSnapshotDetail,
    ) -> RouterExecutionResult[tuple[NewsItem, ...]]:
        del now
        start = as_of - timedelta(days=SNAPSHOT_NEWS_LOOKBACK_DAYS)
        params = {
            "end": as_of.isoformat(),
            "limit": str(_DEFAULT_NEWS_LIMIT),
            "start": start.isoformat(),
        }
        fingerprint = build_a_share_fingerprint(
            OP_NEWS, instrument.instrument_id, params, as_of
        )
        policy = (
            SNAPSHOT_FULL_POLICY
            if detail is AShareSnapshotDetail.FULL
            else SNAPSHOT_SUMMARY_POLICY
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[NewsItem, ...]]:
            if not isinstance(adapter, AShareNewsProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={"category": DataCategory.NEWS.value, "rule": "protocol"},
                )
            return await adapter.get_news(
                instrument,
                start=start,
                end=as_of,
                limit=_DEFAULT_NEWS_LIMIT,
                as_of=as_of,
            )

        def _validator(success: ProviderSuccess[tuple[NewsItem, ...]]) -> None:
            self._validate_news(success, as_of=as_of, start=start)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.NEWS,
            call=_call,
            operation_name=OP_NEWS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=policy,
            bypass_cache=False,
            cache_codec=self._news_codec,
            result_validator=_validator,
        )

    async def _fetch_actions(
        self, instrument: Instrument, as_of: datetime, *, now: datetime
    ) -> RouterExecutionResult[tuple[UnlockRecord | DividendRecord, ...]]:
        fingerprint = build_a_share_fingerprint(
            OP_CORPORATE_ACTIONS, instrument.instrument_id, {}, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
            if not isinstance(adapter, AShareDisclosureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CORPORATE_ACTIONS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_corporate_actions(
                instrument, start=None, end=None, as_of=as_of
            )

        def _validator(
            success: ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]],
        ) -> None:
            self._validate_corporate_actions(success, as_of=as_of, now=now)

        # Optional category under FULL policy (CORPORATE_ACTIONS in optional_categories).
        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CORPORATE_ACTIONS,
            call=_call,
            operation_name=OP_CORPORATE_ACTIONS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=SNAPSHOT_FULL_POLICY,
            bypass_cache=False,
            cache_codec=self._corporate_actions_codec,
            result_validator=_validator,
        )
