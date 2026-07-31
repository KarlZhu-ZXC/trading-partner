"""A-share composite snapshot product service (Phase 1E E3).

``AShareSnapshotService.get_snapshot`` accepts a resolved ``Instrument`` plus
aware ``as_of`` and ``AShareSnapshotDetail``. It is Router-backed and is
bootstrapped behind ``a_share_get_facts(operation="snapshot")``.

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
from datetime import datetime, timedelta

from application.dto.a_share import (
    AnnouncementItemDTO,
    AShareCompositeSnapshotDTO,
    AShareFinancialStatementsDTO,
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
from application.services.a_share_snapshot_validation import (
    AShareSnapshotValidationMixin,
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
from domain.common.enums import AssetType, DataCategory, Market, ReliabilityLevel
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


@dataclass(frozen=True, slots=True)
class AShareFinancialStatementsResult:
    ok: bool
    data: AShareFinancialStatementsDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(
            self.provenance,
            order=(AShareComponentType.STATEMENTS,),
        )
        if type(self.ok) is not bool:
            raise DataContractError("ok must be exact bool")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(item, WarningInfo) for item in self.warnings
        ):
            raise DataContractError("warnings must be a tuple of WarningInfo")
        if self.ok:
            if self.data is None or self.error is not None:
                raise DataContractError("successful financial statements require data only")
            validate_data_provenance(self.data, self.provenance)
            if {item.component for item in self.provenance} != {
                AShareComponentType.STATEMENTS
            }:
                raise DataContractError("financial statements provenance is required")
        elif self.data is not None or not isinstance(self.error, TradingPartnerError):
            raise DataContractError("failed financial statements require a typed error only")


class AShareSnapshotService(AShareSnapshotValidationMixin):
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

    async def get_financial_statements(
        self,
        instrument: Instrument,
        as_of: datetime,
        *,
        statement_types: tuple[FinancialStatementType, ...],
        periods: int,
        metric_codes: tuple[str, ...],
    ) -> AShareFinancialStatementsResult:
        """Return only bounded company statements; do not fetch snapshot extras."""

        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        self._require_a_share(instrument)
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "financial statements support A-share equities only",
                details={"field": "instrument", "rule": "asset_type"},
            )
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        routed = await self._fetch_statements(
            instrument,
            as_of,
            now=now,
            require_non_empty=True,
            statement_types=statement_types,
            periods=periods,
        )
        if not routed.ok or routed.value is None or routed.meta is None:
            return AShareFinancialStatementsResult(
                ok=False,
                data=None,
                warnings=routed.warnings,
                error=routed.error
                or DataContractError(
                    "required financial statements component failed",
                    details={"component": "statements", "rule": "required"},
                ),
                provenance=(),
            )
        provenance = (
            component_provenance(
                AShareComponentType.STATEMENTS,
                routed.meta,
                routed.value,
                empty_reliability=ReliabilityLevel.MEDIUM,
                empty_authoritative=False,
            ),
        )
        data = AShareFinancialStatementsDTO.from_lines(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            requested_periods=periods,
            metric_codes=metric_codes,
            lines=routed.value,
            provenance=provenance_dtos(provenance),
        )
        warnings = list(routed.warnings)
        if not any(period.metrics for period in data.periods):
            warnings.append(
                WarningInfo(
                    code="FINANCIAL_METRICS_UNAVAILABLE",
                    message="Requested normalized financial metrics were unavailable.",
                    details={},
                )
            )
        return AShareFinancialStatementsResult(
            ok=True,
            data=data,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

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
        statement_types: tuple[FinancialStatementType, ...] = _DEFAULT_STATEMENT_TYPES,
        periods: int = _DEFAULT_STATEMENT_PERIODS,
    ) -> RouterExecutionResult[tuple[FinancialStatementLine, ...]]:
        params = {
            "periods": str(periods),
            "types": ",".join(sorted(t.value for t in statement_types)),
        }
        fingerprint = build_a_share_fingerprint(
            OP_STATEMENTS, instrument.instrument_id, params, as_of
        )
        requested_types = statement_types

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
                periods=periods,
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
