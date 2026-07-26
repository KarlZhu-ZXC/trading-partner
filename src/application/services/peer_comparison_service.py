"""Cross-market orchestration for caller-specified peer comparison facts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from application.dto.a_share import (
    AShareFinancialStatementsDTO,
    AShareGetCompanyOperatingMetricsInput,
    AShareGetFinancialStatementsInput,
    AShareGetSnapshotInput,
    CompanyOperatingMetricsSnapshotDTO,
)
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.peer_comparison import (
    PeerComparisonFactPackageDTO,
    PeerComparisonRunInput,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.dto.us_research import (
    FundamentalGetSnapshotInput,
    FundamentalGetStatementsInput,
    USFinancialStatementsDTO,
    USFundamentalSnapshotDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from domain.a_share.enums import AShareSnapshotDetail
from domain.common.enums import Freshness, Market
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.company_comparison.calculator import PeerComparisonCalculator
from domain.company_comparison.enums import PeerComparisonPeriodMode, PeerComparisonStatus
from domain.company_comparison.models import (
    PeerCompanyFacts,
    PeerCompanyPeriod,
    PeerCompanyValuation,
    PeerOperatingFact,
)
from domain.us_research.enums import USStatementFrequency, USStatementView

_FRESHNESS_ORDER = {
    Freshness.FRESH: 0,
    Freshness.DELAYED: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 3,
}


class PeerComparisonService:
    """Fetch existing normalized facts serially and align them deterministically."""

    def __init__(
        self,
        *,
        a_share: AShareToolCoordinator,
        us_research: USResearchToolCoordinator,
        calculator: PeerComparisonCalculator,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._a_share = a_share
        self._us_research = us_research
        self._calculator = calculator
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def compare(
        self, request: PeerComparisonRunInput
    ) -> ToolEnvelope[PeerComparisonFactPackageDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        as_of = request.as_of or now
        _, market, _ = parse_instrument_id(request.primary_instrument_id)
        instruments = (request.primary_instrument_id, *request.peer_instrument_ids)
        envelopes: list[ToolEnvelope[object]] = []
        companies: list[PeerCompanyFacts] = []
        unavailable: list[str] = []
        warnings: list[WarningInfo] = []
        try:
            for index, instrument_id in enumerate(instruments):
                company, component_envelopes = await self._fetch_company(
                    instrument_id=instrument_id,
                    market=market,
                    request=request,
                    as_of=as_of,
                    allow_valuation=self._allow_valuation(request.as_of, now),
                )
                envelopes.extend(component_envelopes)
                if company is None:
                    if index == 0:
                        failure = next(
                            (item for item in component_envelopes if not item.ok),
                            None,
                        )
                        if failure is not None:
                            return ToolEnvelope.failure(
                                request_id=request_id,
                                market=market,
                                as_of=as_of,
                                fetched_at=now,
                                freshness=failure.freshness,
                                sources=failure.sources,
                                warnings=failure.warnings,
                                errors=failure.errors,
                            )
                        raise RuntimeError("primary peer-comparison facts unavailable")
                    unavailable.append(instrument_id)
                    continue
                companies.append(company)
            if request.include_valuation and not self._allow_valuation(request.as_of, now):
                warnings.append(
                    WarningInfo(
                        code="PEER_VALUATION_AS_OF_UNAVAILABLE",
                        message=(
                            "Historical cutoff-safe valuation facts are unavailable; "
                            "current values were not backfilled."
                        ),
                    )
                )
            if unavailable:
                warnings.append(
                    WarningInfo(
                        code="PEER_PROVIDER_PARTIAL",
                        message="One or more requested peer companies were unavailable.",
                        details={"instrument_ids": unavailable},
                    )
                )
            package = self._calculator.compare(
                primary_instrument_id=request.primary_instrument_id,
                peer_instrument_ids=request.peer_instrument_ids,
                market=market,
                as_of=as_of,
                period_mode=request.period_mode,
                periods=request.periods,
                companies=tuple(companies),
                unavailable_instrument_ids=tuple(unavailable),
            )
            warnings.extend(item for env in envelopes for item in env.warnings)
            warnings.extend(self._comparison_warnings(package))
            deduped_warnings = self._dedupe_warnings(warnings)
            sources = self._dedupe_sources(
                tuple(source for env in envelopes for source in env.sources)
            )
            freshness = max(
                (env.freshness for env in envelopes),
                key=lambda item: _FRESHNESS_ORDER[item],
                default=Freshness.UNKNOWN,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=market,
                as_of=as_of,
                fetched_at=max((env.fetched_at for env in envelopes), default=now),
                freshness=freshness,
                sources=sources,
                data=PeerComparisonFactPackageDTO.from_domain(package),
                degraded=bool(deduped_warnings),
                warnings=deduped_warnings,
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=market,
                as_of=as_of,
                fetched_at=self._clock.now(),
                errors=(error,),
            )

    async def _fetch_company(
        self,
        *,
        instrument_id: str,
        market: Market,
        request: PeerComparisonRunInput,
        as_of: datetime,
        allow_valuation: bool,
    ) -> tuple[PeerCompanyFacts | None, list[ToolEnvelope[object]]]:
        if market is Market.A_SHARE:
            return await self._fetch_a_share(
                instrument_id, request, as_of, allow_valuation
            )
        return await self._fetch_us(instrument_id, request, as_of, allow_valuation)

    async def _fetch_a_share(
        self,
        instrument_id: str,
        request: PeerComparisonRunInput,
        as_of: datetime,
        allow_valuation: bool,
    ) -> tuple[PeerCompanyFacts | None, list[ToolEnvelope[object]]]:
        statements = await self._a_share.get_financial_statements(
            AShareGetFinancialStatementsInput(
                instrument_id=instrument_id,
                periods=min(20, request.periods + 2),
                as_of=as_of,
            )
        )
        envelopes: list[ToolEnvelope[object]] = [cast(ToolEnvelope[object], statements)]
        if not statements.ok or statements.data is None:
            return None, envelopes
        valuation: PeerCompanyValuation | None = None
        if request.include_valuation and allow_valuation:
            snapshot = await self._a_share.get_snapshot(
                AShareGetSnapshotInput(
                    instrument_id=instrument_id,
                    detail=AShareSnapshotDetail.SUMMARY,
                    as_of=as_of,
                )
            )
            envelopes.append(cast(ToolEnvelope[object], snapshot))
            if snapshot.ok and snapshot.data is not None and snapshot.data.quote is not None:
                quote = snapshot.data.quote
                valuation = PeerCompanyValuation(
                    instrument_id=instrument_id,
                    observed_at=quote.quote_at,
                    values=(
                        ("market_cap", quote.total_market_cap_cny),
                        ("trailing_pe", quote.pe_ttm),
                        ("price_to_book", quote.pb),
                        ("price_to_sales", None),
                    ),
                    currency="CNY",
                    source_names=self._source_names(snapshot),
                )
        operating: tuple[PeerOperatingFact, ...] = ()
        if request.include_operating_metrics:
            operating_envelope = await self._a_share.get_company_operating_metrics(
                AShareGetCompanyOperatingMetricsInput(
                    instrument_id=instrument_id,
                    lookback_months=36,
                    document_limit=20,
                    as_of=as_of,
                )
            )
            envelopes.append(cast(ToolEnvelope[object], operating_envelope))
            if operating_envelope.ok and operating_envelope.data is not None:
                operating = self._a_share_operating_facts(
                    operating_envelope.data, self._source_names(operating_envelope)
                )
        periods = self._a_share_periods(statements.data, self._source_names(statements))
        return PeerCompanyFacts(instrument_id, periods, valuation, operating), envelopes

    async def _fetch_us(
        self,
        instrument_id: str,
        request: PeerComparisonRunInput,
        as_of: datetime,
        allow_valuation: bool,
    ) -> tuple[PeerCompanyFacts | None, list[ToolEnvelope[object]]]:
        frequency = (
            USStatementFrequency.ANNUAL
            if request.period_mode is PeerComparisonPeriodMode.ANNUAL
            else USStatementFrequency.QUARTERLY
        )
        statements = await self._us_research.get_fundamental_statements(
            FundamentalGetStatementsInput(
                instrument_id=instrument_id,
                frequency=frequency,
                limit=min(8, request.periods + 2),
                view=USStatementView.LATEST,
                as_of=as_of,
            )
        )
        envelopes: list[ToolEnvelope[object]] = [cast(ToolEnvelope[object], statements)]
        if not statements.ok or statements.data is None:
            return None, envelopes
        valuation: PeerCompanyValuation | None = None
        if request.include_valuation and allow_valuation:
            snapshot = await self._us_research.get_fundamental_snapshot(
                FundamentalGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
            )
            envelopes.append(cast(ToolEnvelope[object], snapshot))
            if snapshot.ok and snapshot.data is not None:
                valuation = self._us_valuation(snapshot.data, self._source_names(snapshot))
        periods = self._us_periods(statements.data, self._source_names(statements))
        return PeerCompanyFacts(instrument_id, periods, valuation), envelopes

    @staticmethod
    def _a_share_periods(
        data: AShareFinancialStatementsDTO, source_names: tuple[str, ...]
    ) -> tuple[PeerCompanyPeriod, ...]:
        return tuple(
            PeerCompanyPeriod(
                instrument_id=data.instrument_id,
                period_start=None,
                period_end=period.period_end,
                fiscal_year=period.period_end.year,
                basis=period.basis,
                currency="CNY",
                published_at=max(
                    (item.published_at for item in period.metrics if item.published_at),
                    default=None,
                ),
                line_items=tuple((item.metric_code, item.value) for item in period.metrics),
                source_names=source_names,
            )
            for period in data.periods
        )

    @staticmethod
    def _us_periods(
        data: USFinancialStatementsDTO, source_names: tuple[str, ...]
    ) -> tuple[PeerCompanyPeriod, ...]:
        PeriodKey = tuple[date, date | None, int, str | None, str | None]
        lines_by_key: dict[PeriodKey, dict[str, Decimal | None]] = {}
        filed_by_key: dict[PeriodKey, datetime | None] = {}
        for period in (*data.income, *data.balance_sheet, *data.cash_flow):
            key: PeriodKey = (
                period.period_end,
                period.period_start,
                period.fiscal_year,
                period.fiscal_period,
                period.currency,
            )
            lines_by_key.setdefault(key, {}).update(dict(period.line_items))
            if period.filed_at is not None:
                existing = filed_by_key.get(key)
                filed_by_key[key] = (
                    max(existing, period.filed_at) if existing else period.filed_at
                )
        periods: list[PeerCompanyPeriod] = []
        for key, line_items in lines_by_key.items():
            period_end, period_start, fiscal_year, fiscal_period, currency = key
            periods.append(
                PeerCompanyPeriod(
                    instrument_id=data.instrument_id,
                    period_start=period_start,
                    period_end=period_end,
                    fiscal_year=fiscal_year,
                    basis=(
                        "annual"
                        if data.frequency is USStatementFrequency.ANNUAL
                        else f"quarterly:{str(fiscal_period or 'unknown').lower()}"
                    ),
                    currency=str(currency or "currency_unknown"),
                    published_at=filed_by_key.get(key),
                    line_items=tuple(line_items.items()),
                    source_names=source_names,
                )
            )
        return tuple(sorted(periods, key=lambda item: item.period_end, reverse=True))

    @staticmethod
    def _us_valuation(
        data: USFundamentalSnapshotDTO, source_names: tuple[str, ...]
    ) -> PeerCompanyValuation | None:
        if data.metrics is None and data.profile is None:
            return None
        metrics = data.metrics
        return PeerCompanyValuation(
            instrument_id=data.instrument_id,
            observed_at=data.as_of,
            values=(
                ("market_cap", data.profile.market_cap if data.profile else None),
                ("trailing_pe", metrics.trailing_pe if metrics else None),
                ("price_to_book", metrics.price_to_book if metrics else None),
                ("price_to_sales", metrics.price_to_sales if metrics else None),
            ),
            currency="USD",
            source_names=source_names,
        )

    @staticmethod
    def _a_share_operating_facts(
        data: CompanyOperatingMetricsSnapshotDTO, source_names: tuple[str, ...]
    ) -> tuple[PeerOperatingFact, ...]:
        return tuple(
            PeerOperatingFact(
                instrument_id=item.instrument_id,
                metric_code=item.metric_code,
                value=item.value,
                unit=item.unit,
                period_start=item.period_start,
                period_end=item.period_end,
                frequency=item.frequency.value,
                measurement_basis=item.measurement_basis.value,
                published_at=item.published_at,
                source_names=source_names,
            )
            for item in data.observations
        )

    @staticmethod
    def _allow_valuation(requested_as_of: datetime | None, now: datetime) -> bool:
        if requested_as_of is None:
            return True
        age_seconds = (now - requested_as_of).total_seconds()
        return 0 <= age_seconds <= 86_400

    @staticmethod
    def _source_names(envelope: ToolEnvelope[Any]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.name for item in envelope.sources))

    @staticmethod
    def _dedupe_sources(sources: tuple[SourceReference, ...]) -> tuple[SourceReference, ...]:
        out: list[SourceReference] = []
        seen: set[tuple[object, ...]] = set()
        for item in sources:
            key = (item.name, item.role, item.url, item.retrieved_at, item.data_delay_seconds)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return tuple(out)

    @staticmethod
    def _dedupe_warnings(warnings: list[WarningInfo]) -> tuple[WarningInfo, ...]:
        out: list[WarningInfo] = []
        seen: set[str] = set()
        for item in warnings:
            if item.code not in seen:
                seen.add(item.code)
                out.append(item)
        return tuple(out)

    @staticmethod
    def _comparison_warnings(package: object) -> tuple[WarningInfo, ...]:
        from domain.company_comparison.models import PeerComparisonFactPackage

        value = cast(PeerComparisonFactPackage, package)
        rows = value.comparison_rows
        warnings: list[WarningInfo] = []
        if any(item.unit == "mixed" for item in rows):
            warnings.append(
                WarningInfo(
                    code="PEER_CURRENCY_MISMATCH",
                    message="Some absolute-value rows use different reporting currencies.",
                )
            )
        if any(
            cell.unavailable_reason == "period_unavailable"
            for row in rows
            for cell in row.values
        ):
            warnings.append(
                WarningInfo(
                    code="PEER_PERIOD_BASIS_MISMATCH",
                    message="Some companies lack a matching reporting period or basis.",
                )
            )
        if any(
            cell.unavailable_reason == "metric_unavailable"
            for row in rows
            for cell in row.values
        ):
            warnings.append(
                WarningInfo(
                    code="PEER_METRIC_UNAVAILABLE",
                    message="Some peer metrics are unavailable.",
                )
            )
        if any(
            row.comparability is not PeerComparisonStatus.COMPARABLE
            for row in value.operating_metric_appendix
        ):
            warnings.append(
                WarningInfo(
                    code="PEER_OPERATING_METRIC_NOT_COMPARABLE",
                    message="Some disclosed operating metrics lack an exact peer match.",
                )
            )
        return tuple(warnings)
