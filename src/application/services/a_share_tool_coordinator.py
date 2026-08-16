"""A-share MCP-facing tool coordinator (Phase 1E E5c).

Resolves instruments, samples request_id / effective as_of once, delegates to
product services, and aggregates multi-component provenance into ToolEnvelope.
Does not select vendors or import MCP/interface layers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, TypeVar

from application.dto.a_share import (
    AShareCapitalSnapshotDTO,
    AShareCompositeSnapshotDTO,
    AShareFinancialStatementsDTO,
    AShareGetCapitalSnapshotInput,
    AShareGetCompanyOperatingMetricsInput,
    AShareGetEtfOptionSnapshotInput,
    AShareGetFinancialStatementsInput,
    AShareGetIndustryCycleInput,
    AShareGetLimitUpContextInput,
    AShareGetMarketStructureInput,
    AShareGetSentimentSnapshotInput,
    AShareGetSnapshotInput,
    AShareLimitUpContextProductDTO,
    AShareMarketStructureSnapshotDTO,
    AShareSentimentSnapshotDTO,
    CompanyOperatingMetricsSnapshotDTO,
    EtfOptionSnapshotDTO,
    IndustryCycleSnapshotDTO,
    ResearchReportSearchDTO,
    ResearchSearchReportsInput,
)
from application.dto.a_share_provenance import AShareComponentProvenance
from application.dto.error_mapper import to_error_info
from application.dto.tool_envelope import (
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._router_envelope_support import exception_envelope
from application.services.a_share_capital_service import AShareCapitalService
from application.services.a_share_company_operating_metrics_service import (
    AShareCompanyOperatingMetricsService,
)
from application.services.a_share_etf_option_service import AShareEtfOptionService
from application.services.a_share_industry_cycle_service import AShareIndustryCycleService
from application.services.a_share_limit_up_service import AShareLimitUpService
from application.services.a_share_market_structure_service import (
    AShareMarketStructureService,
)
from application.services.a_share_sentiment_service import AShareSentimentService
from application.services.a_share_snapshot_service import AShareSnapshotService
from application.services.instrument_access_service import InstrumentAccessService
from application.services.research_report_search_service import (
    ResearchReportSearchService,
)
from domain.common.enums import Freshness, Market, ReliabilityLevel, SourceRole
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.instruments.models import Instrument

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class _ProductResult(Protocol[T_co]):
    """Structural result shape shared by the seven A-share product services."""

    @property
    def ok(self) -> bool: ...

    @property
    def data(self) -> T_co | None: ...

    @property
    def warnings(self) -> tuple[WarningInfo, ...]: ...

    @property
    def error(self) -> TradingPartnerError | None: ...

    @property
    def provenance(self) -> tuple[AShareComponentProvenance, ...]: ...


_FRESHNESS_WORST_ORDER: dict[Freshness, int] = {
    Freshness.FRESH: 0,
    Freshness.DELAYED: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 3,
}

_FALLBACK_WARNING = WarningInfo(
    code="FALLBACK_A_SHARE_SOURCE",
    message="One or more A-share components used a configured fallback source.",
    details={},
)
_DELAYED_WARNING = WarningInfo(
    code="DELAYED_A_SHARE_DATA",
    message="One or more A-share components are delayed.",
    details={},
)
_STALE_WARNING = WarningInfo(
    code="STALE_A_SHARE_DATA",
    message="One or more A-share components are stale.",
    details={},
)
_UNKNOWN_FRESHNESS_WARNING = WarningInfo(
    code="UNKNOWN_A_SHARE_FRESHNESS",
    message="One or more A-share components have unknown freshness.",
    details={},
)
_NON_AUTHORITATIVE_WARNING = WarningInfo(
    code="NON_AUTHORITATIVE_A_SHARE_DATA",
    message="One or more A-share components are non-authoritative.",
    details={},
)
_UNKNOWN_RELIABILITY_WARNING = WarningInfo(
    code="UNKNOWN_A_SHARE_RELIABILITY",
    message="One or more A-share components have unknown reliability.",
    details={},
)
# Reuse exact product codes for LOW / derived / publication-time exclusion.
_LOW_RELIABILITY_WARNING = WarningInfo(
    code="LOW_RELIABILITY_MARKET_SIGNAL",
    message="One or more A-share components carry low reliability.",
    details={},
)
_DERIVED_CHIP_WARNING = WarningInfo(
    code="DERIVED_CHIP_DISTRIBUTION",
    message="Chip distribution is a derived turnover-decay estimate",
    details={},
)
_PUBLICATION_EXCLUDED_WARNING = WarningInfo(
    code="PUBLICATION_TIME_UNKNOWN_EXCLUDED",
    message="Records with unknown publication time excluded",
    details={},
)


class AShareToolCoordinator:
    """Application-layer coordinator for the seven A-share product tools."""

    def __init__(
        self,
        *,
        instrument_access: InstrumentAccessService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        snapshot_service: AShareSnapshotService,
        market_structure_service: AShareMarketStructureService,
        capital_service: AShareCapitalService,
        limit_up_service: AShareLimitUpService,
        sentiment_service: AShareSentimentService,
        etf_option_service: AShareEtfOptionService,
        industry_cycle_service: AShareIndustryCycleService,
        company_operating_metrics_service: AShareCompanyOperatingMetricsService,
        report_search_service: ResearchReportSearchService,
    ) -> None:
        self._instrument_access = instrument_access
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._snapshot_service = snapshot_service
        self._market_structure_service = market_structure_service
        self._capital_service = capital_service
        self._limit_up_service = limit_up_service
        self._sentiment_service = sentiment_service
        self._etf_option_service = etf_option_service
        self._industry_cycle_service = industry_cycle_service
        self._company_operating_metrics_service = company_operating_metrics_service
        self._report_search_service = report_search_service

    async def get_snapshot(
        self, request: AShareGetSnapshotInput
    ) -> ToolEnvelope[AShareCompositeSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_required(request.instrument_id, as_of=effective_as_of)
            result = await self._snapshot_service.get_snapshot(
                instrument, effective_as_of, request.detail
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_financial_statements(
        self, request: AShareGetFinancialStatementsInput
    ) -> ToolEnvelope[AShareFinancialStatementsDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_required(request.instrument_id, as_of=effective_as_of)
            result = await self._snapshot_service.get_financial_statements(
                instrument,
                effective_as_of,
                statement_types=request.statement_types,
                periods=request.periods,
                metric_codes=request.metric_codes,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_industry_cycle(
        self, request: AShareGetIndustryCycleInput
    ) -> ToolEnvelope[IndustryCycleSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            result = await self._industry_cycle_service.get_hog_cycle(
                lookback_months=request.lookback_months,
                as_of=effective_as_of,
                view=request.view,
                metric_codes=request.metric_codes,
                offset=request.offset,
                limit=request.limit,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_company_operating_metrics(
        self, request: AShareGetCompanyOperatingMetricsInput
    ) -> ToolEnvelope[CompanyOperatingMetricsSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_required(request.instrument_id, as_of=effective_as_of)
            result = await self._company_operating_metrics_service.get_company_operating_metrics(
                instrument,
                lookback_months=request.lookback_months,
                document_limit=request.document_limit,
                metric_codes=request.metric_codes,
                as_of=effective_as_of,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_market_structure(
        self, request: AShareGetMarketStructureInput
    ) -> ToolEnvelope[AShareMarketStructureSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_optional(request.instrument_id, as_of=effective_as_of)
            result = await self._market_structure_service.get(
                scope=request.scope,
                instrument=instrument,
                trade_date=request.trade_date,
                start=request.start,
                end=request.end,
                interval=request.interval,
                adjustment=request.adjustment,
                include_bars=bool(request.include_bars),
                include_order_book=bool(request.include_order_book),
                include_ticks=request.include_ticks,
                include_industries=bool(request.include_industries),
                include_market_board=bool(request.include_market_board),
                industry_limit=request.industry_limit,
                tick_limit=request.tick_limit,
                as_of=effective_as_of,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_capital_snapshot(
        self, request: AShareGetCapitalSnapshotInput
    ) -> ToolEnvelope[AShareCapitalSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_optional(request.instrument_id, as_of=effective_as_of)
            result = await self._capital_service.get(
                instrument=instrument,
                metrics=request.metrics,
                start=request.start,
                end=request.end,
                as_of=effective_as_of,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_limit_up_context(
        self, request: AShareGetLimitUpContextInput
    ) -> ToolEnvelope[AShareLimitUpContextProductDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            result = await self._limit_up_service.get(
                trade_date=request.trade_date,
                pools=request.pools,
                as_of=effective_as_of,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_sentiment_snapshot(
        self, request: AShareGetSentimentSnapshotInput
    ) -> ToolEnvelope[AShareSentimentSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_optional(request.instrument_id, as_of=effective_as_of)
            result = await self._sentiment_service.get(
                instrument=instrument,
                sources=request.sources,
                trade_date=request.trade_date,
                as_of=effective_as_of,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def get_etf_option_snapshot(
        self, request: AShareGetEtfOptionSnapshotInput
    ) -> ToolEnvelope[EtfOptionSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            underlying = await self._resolve_required(
                request.underlying_instrument_id, as_of=effective_as_of
            )
            result = await self._etf_option_service.get(
                underlying,
                expiry=request.expiry,
                strike_center=request.strike_center,
                strike_count_each_side=request.strike_count_each_side,
                as_of=effective_as_of,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    async def search_reports(
        self, request: ResearchSearchReportsInput
    ) -> ToolEnvelope[ResearchReportSearchDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_optional(request.instrument_id, as_of=effective_as_of)
            result = await self._report_search_service.search(
                text=request.text,
                instrument=instrument,
                industry_code=request.industry_code,
                published_from=request.published_from,
                published_to=request.published_to,
                include_consensus=request.include_consensus,
                as_of=effective_as_of,
                limit=request.limit,
                offset=request.offset,
            )
        except TradingPartnerError as exc:
            return self._exception_failure(request_id, effective_as_of, exc)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)
        return self._envelope_from_result(request_id, effective_as_of, result)

    def _begin(self, as_of: datetime | None) -> tuple[str, datetime]:
        """Sample request_id once; sample effective as_of only when caller omitted it."""
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        effective_as_of = self._clock.now() if as_of is None else as_of
        return request_id, effective_as_of

    async def _resolve_required(self, instrument_id: str, *, as_of: datetime) -> Instrument:
        return await self._instrument_access.get(instrument_id, as_of=as_of)

    async def _resolve_optional(
        self, instrument_id: str | None, *, as_of: datetime
    ) -> Instrument | None:
        return await self._instrument_access.get_optional(instrument_id, as_of=as_of)

    def _envelope_from_result(
        self,
        request_id: str,
        effective_as_of: datetime,
        result: _ProductResult[T],
    ) -> ToolEnvelope[T]:
        sources = _sources_from_provenance(result.provenance)
        warnings = _merge_warnings(result.warnings, result.provenance)
        freshness = _worst_freshness(result.provenance)
        fetched_at = _max_fetched_at(result.provenance)
        if fetched_at is None:
            # Single fallback clock sample when no successful component exists.
            fetched_at = self._clock.now()
        # Provenance degrade reasons always synthesize a warning (see merge helpers).
        degraded = (not result.ok) or bool(warnings)

        if result.ok:
            assert result.data is not None
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.A_SHARE,
                as_of=effective_as_of,
                fetched_at=fetched_at,
                freshness=freshness,
                sources=sources,
                data=result.data,
                degraded=degraded,
                warnings=warnings,
            )

        assert result.error is not None
        return ToolEnvelope.failure(
            request_id=request_id,
            market=Market.A_SHARE,
            as_of=effective_as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            sources=sources,
            errors=[to_error_info(result.error, self._secret_redactor)],
            degraded=True,
            warnings=warnings,
            data=None,
        )

    def _exception_failure(
        self,
        request_id: str,
        effective_as_of: datetime,
        exc: BaseException,
    ) -> ToolEnvelope[T]:
        return exception_envelope(
            request_id=request_id,
            market=Market.A_SHARE,
            as_of=effective_as_of,
            exc=exc,
            clock=self._clock,
            redactor=self._secret_redactor,
        )


def _sources_from_provenance(
    provenance: tuple[AShareComponentProvenance, ...],
) -> tuple[SourceReference, ...]:
    """Dedupe sources while retaining the newest source metadata."""
    order: list[tuple[str, SourceRole]] = []
    best_meta: dict[tuple[str, SourceRole], AShareComponentProvenance] = {}
    for item in provenance:
        key = (item.meta.vendor.value, item.meta.role)
        if key not in best_meta:
            order.append(key)
            best_meta[key] = item
        elif item.meta.fetched_at > best_meta[key].meta.fetched_at:
            best_meta[key] = item
    return tuple(
        SourceReference(
            name=vendor,
            role=role,
            url=None,
            retrieved_at=best_meta[(vendor, role)].meta.fetched_at,
            data_delay_seconds=best_meta[(vendor, role)].meta.data_delay_seconds,
        )
        for vendor, role in order
    )


def _worst_freshness(
    provenance: tuple[AShareComponentProvenance, ...],
) -> Freshness:
    if not provenance:
        return Freshness.UNKNOWN
    return max(
        (item.meta.freshness for item in provenance),
        key=lambda f: _FRESHNESS_WORST_ORDER[f],
    )


def _max_fetched_at(
    provenance: tuple[AShareComponentProvenance, ...],
) -> datetime | None:
    if not provenance:
        return None
    return max(item.meta.fetched_at for item in provenance)


def _synthesized_warnings_from_provenance(
    provenance: tuple[AShareComponentProvenance, ...],
) -> tuple[WarningInfo, ...]:
    """Append-order synthesized warnings; first occurrence of each code wins."""
    out: list[WarningInfo] = []
    seen: set[str] = set()

    def _add(warning: WarningInfo) -> None:
        if warning.code not in seen:
            seen.add(warning.code)
            out.append(warning)

    for item in provenance:
        meta = item.meta
        if meta.role is SourceRole.FALLBACK:
            _add(_FALLBACK_WARNING)
        if meta.freshness is Freshness.DELAYED:
            _add(_DELAYED_WARNING)
        elif meta.freshness is Freshness.STALE:
            _add(_STALE_WARNING)
        elif meta.freshness is Freshness.UNKNOWN:
            _add(_UNKNOWN_FRESHNESS_WARNING)
        if item.is_authoritative is False:
            _add(_NON_AUTHORITATIVE_WARNING)
        if item.reliability is ReliabilityLevel.UNKNOWN:
            _add(_UNKNOWN_RELIABILITY_WARNING)
        if (
            item.reliability is ReliabilityLevel.LOW
            or "LOW_RELIABILITY_MARKET_SIGNAL" in meta.warnings
        ):
            _add(_LOW_RELIABILITY_WARNING)
        if item.is_derived or "DERIVED_CHIP_DISTRIBUTION" in meta.warnings:
            _add(_DERIVED_CHIP_WARNING)
        if "PUBLICATION_TIME_UNKNOWN_EXCLUDED" in meta.warnings:
            _add(_PUBLICATION_EXCLUDED_WARNING)
    return tuple(out)


def _merge_warnings(
    product_warnings: tuple[WarningInfo, ...],
    provenance: tuple[AShareComponentProvenance, ...],
) -> tuple[WarningInfo, ...]:
    """Product warnings first (original order), then provenance-synthesized codes."""
    merged: list[WarningInfo] = []
    seen: set[str] = set()
    for warning in product_warnings:
        if warning.code not in seen:
            seen.add(warning.code)
            merged.append(warning)
    for warning in _synthesized_warnings_from_provenance(provenance):
        if warning.code not in seen:
            seen.add(warning.code)
            merged.append(warning)
    return tuple(merged)
