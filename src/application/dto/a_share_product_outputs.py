"""A-share bounded product-composite output DTOs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from application.dto.a_share_common import _FrozenForbid
from application.dto.a_share_core_outputs import (
    AnalystReportItemDTO,
    AnnouncementItemDTO,
    AShareBarDTO,
    AShareQuoteDTO,
    ConsensusEstimateDTO,
    F10SectionDTO,
    FinancialStatementLineDTO,
    FundamentalMetricDTO,
    InteractiveQAItemDTO,
    NewsItemDTO,
    OrderBookLevelDTO,
    TradeTickDTO,
)
from application.dto.a_share_market_outputs import (
    BlockTradeRecordDTO,
    ChipDistributionSnapshotDTO,
    DividendRecordDTO,
    DragonTigerRecordDTO,
    FundFlowPointDTO,
    IndustryPerformanceRowDTO,
    MarginRecordDTO,
    MarketBoardSnapshotDTO,
    NorthboundFlowPointDTO,
    ShareholderCountRecordDTO,
    UnlockRecordDTO,
)
from application.dto.a_share_provenance import AShareComponentProvenanceDTO
from application.dto.a_share_signal_outputs import LimitUpContextDTO, SentimentSignalDTO
from application.dto.market import DecimalWire
from domain.a_share.enums import (
    AShareComponentType,
    AShareMarketScope,
    AShareSnapshotDetail,
    CapitalMetricType,
    CompanyDocumentParseStatus,
    CompanyDocumentType,
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
    LimitPoolType,
    SentimentSourceType,
)
from domain.a_share.models import (
    CompanyOperatingMetricObservation,
    CompanyOperatingMetricsSnapshot,
    DocumentParseReceipt,
    IndustryCycleSnapshot,
    IndustryMetricObservation,
)


class AShareCompositeSnapshotDTO(_FrozenForbid):
    """Product composite for snapshot service (components optional when partial)."""

    instrument_id: str
    detail: AShareSnapshotDetail
    as_of: datetime
    quote: AShareQuoteDTO | None = None
    fundamentals: tuple[FundamentalMetricDTO, ...] = ()
    statements: tuple[FinancialStatementLineDTO, ...] = ()
    f10_sections: tuple[F10SectionDTO, ...] = ()
    announcements: tuple[AnnouncementItemDTO, ...] = ()
    news: tuple[NewsItemDTO, ...] = ()
    unlocks: tuple[UnlockRecordDTO, ...] = ()
    dividends: tuple[DividendRecordDTO, ...] = ()
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class AShareMarketStructureSnapshotDTO(_FrozenForbid):
    scope: AShareMarketScope
    instrument_id: str | None
    trade_date: date | None
    as_of: datetime
    included_components: tuple[AShareComponentType, ...]
    bars: tuple[AShareBarDTO, ...] = ()
    order_book: tuple[OrderBookLevelDTO, ...] = ()
    ticks: tuple[TradeTickDTO, ...] = ()
    industries: tuple[IndustryPerformanceRowDTO, ...] = ()
    market_board: MarketBoardSnapshotDTO | None = None
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class AShareCapitalSnapshotDTO(_FrozenForbid):
    """Product composite for capital service (metrics optional when partial)."""

    instrument_id: str | None
    as_of: datetime
    metrics: tuple[CapitalMetricType, ...]
    intraday_flow: tuple[FundFlowPointDTO, ...] = ()
    daily_flow: tuple[FundFlowPointDTO, ...] = ()
    northbound: tuple[NorthboundFlowPointDTO, ...] = ()
    dragon_tiger: tuple[DragonTigerRecordDTO, ...] = ()
    margin: tuple[MarginRecordDTO, ...] = ()
    block_trades: tuple[BlockTradeRecordDTO, ...] = ()
    shareholder_counts: tuple[ShareholderCountRecordDTO, ...] = ()
    chip_distribution: ChipDistributionSnapshotDTO | None = None
    unlocks: tuple[UnlockRecordDTO, ...] = ()
    dividends: tuple[DividendRecordDTO, ...] = ()
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class IndustryMetricObservationDTO(_FrozenForbid):
    metric_code: str
    value: DecimalWire
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    published_at: datetime
    source_url: str
    geography: str
    measurement_basis: IndustryMeasurementBasis
    is_estimated: bool
    methodology_version: str
    methodology_break: str | None

    @classmethod
    def from_domain(cls, value: IndustryMetricObservation) -> IndustryMetricObservationDTO:
        return cls.model_validate(value, from_attributes=True)


class IndustryMetricCoverageDTO(_FrozenForbid):
    """Deterministic per-metric coverage over the selected filtered history."""

    metric_code: str
    count: int = Field(ge=0)
    first_period: date
    last_period: date


class IndustryCycleSnapshotDTO(_FrozenForbid):
    """Bounded industry-cycle fact package.

    Provider/repository may retain the full requested lookback. MCP payloads use
    ``view=compact`` (default: latest visible observation per selected metric) or
    ``view=series`` (offset/limit page, max 200 rows). Coverage always describes
    the full selected filtered history, not only the returned page.
    """

    cycle: IndustryCycleType
    dataset_code: str
    as_of: datetime
    view: Literal["compact", "series"] = "compact"
    observations: tuple[IndustryMetricObservationDTO, ...]
    coverage: tuple[IndustryMetricCoverageDTO, ...]
    total_observations: int = Field(ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    has_more: bool = False
    interpretation_owner: Literal["external_host"] = "external_host"
    missing_components: tuple[str, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_domain(
        cls,
        value: IndustryCycleSnapshot,
        *,
        provenance: tuple[AShareComponentProvenanceDTO, ...],
        view: Literal["compact", "series"] = "compact",
        metric_codes: tuple[str, ...] = (),
        offset: int = 0,
        limit: int = 50,
    ) -> IndustryCycleSnapshotDTO:
        selected = _filter_industry_observations(value.observations, metric_codes)
        coverage = _industry_metric_coverage(selected)
        total = len(selected)
        if view == "compact":
            page = _compact_industry_observations(selected)
            page_offset = 0
            page_limit = limit
            has_more = False
        else:
            page_offset = offset
            page_limit = limit
            page = selected[page_offset : page_offset + page_limit]
            has_more = page_offset + len(page) < total
        return cls(
            cycle=value.cycle,
            dataset_code=value.dataset_code,
            as_of=value.as_of,
            view=view,
            observations=tuple(IndustryMetricObservationDTO.from_domain(item) for item in page),
            coverage=coverage,
            total_observations=total,
            offset=page_offset,
            limit=page_limit,
            has_more=has_more,
            missing_components=value.missing_components,
            provenance=provenance,
        )


def _filter_industry_observations(
    observations: tuple[IndustryMetricObservation, ...],
    metric_codes: tuple[str, ...],
) -> tuple[IndustryMetricObservation, ...]:
    if not metric_codes:
        return observations
    allowed = frozenset(metric_codes)
    return tuple(item for item in observations if item.metric_code in allowed)


def _industry_metric_coverage(
    observations: tuple[IndustryMetricObservation, ...],
) -> tuple[IndustryMetricCoverageDTO, ...]:
    by_code: dict[str, list[IndustryMetricObservation]] = {}
    for item in observations:
        by_code.setdefault(item.metric_code, []).append(item)
    return tuple(
        IndustryMetricCoverageDTO(
            metric_code=code,
            count=len(items),
            first_period=min(item.period_end for item in items),
            last_period=max(item.period_end for item in items),
        )
        for code, items in sorted(by_code.items(), key=lambda pair: pair[0])
    )


def _compact_industry_observations(
    observations: tuple[IndustryMetricObservation, ...],
) -> tuple[IndustryMetricObservation, ...]:
    latest: dict[str, IndustryMetricObservation] = {}
    for item in observations:
        current = latest.get(item.metric_code)
        if current is None or (item.period_end, item.published_at) > (
            current.period_end,
            current.published_at,
        ):
            latest[item.metric_code] = item
    return tuple(sorted(latest.values(), key=lambda item: (item.period_end, item.metric_code)))


class AShareLimitUpContextProductDTO(_FrozenForbid):
    """Product composite for limit-up service."""

    trade_date: date
    as_of: datetime
    pools: tuple[LimitPoolType, ...]
    context: LimitUpContextDTO
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class AShareSentimentSnapshotDTO(_FrozenForbid):
    """Product composite for sentiment service (all sources optional/partial)."""

    instrument_id: str | None
    trade_date: date
    as_of: datetime
    sources: tuple[SentimentSourceType, ...]
    signals: tuple[SentimentSignalDTO, ...] = ()
    interactive_qa: tuple[InteractiveQAItemDTO, ...] = ()
    company_news: tuple[NewsItemDTO, ...] = ()
    market_news: tuple[NewsItemDTO, ...] = ()
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class ResearchReportSearchDTO(_FrozenForbid):
    instrument_id: str | None
    industry_code: str | None
    published_from: date | None
    published_to: date | None
    include_consensus: bool
    limit: int
    offset: int
    reports: tuple[AnalystReportItemDTO, ...]
    consensus: tuple[ConsensusEstimateDTO, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class DocumentParseReceiptDTO(_FrozenForbid):
    announcement_key: str
    title: str
    document_type: CompanyDocumentType
    published_at: datetime
    source_url: str
    pdf_url: str | None
    parser_version: str
    page_count: int | None
    status: CompanyDocumentParseStatus
    extracted_metric_count: int
    warning_code: str | None = None

    @classmethod
    def from_domain(cls, value: DocumentParseReceipt) -> DocumentParseReceiptDTO:
        return cls.model_validate(value, from_attributes=True)


class CompanyOperatingMetricObservationDTO(_FrozenForbid):
    instrument_id: str
    metric_code: str
    value: DecimalWire
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    measurement_basis: IndustryMeasurementBasis
    published_at: datetime
    source_url: str
    parser_version: str
    pdf_url: str | None
    announcement_key: str | None
    is_audited: bool
    is_estimated: bool

    @classmethod
    def from_domain(
        cls, value: CompanyOperatingMetricObservation
    ) -> CompanyOperatingMetricObservationDTO:
        return cls.model_validate(value, from_attributes=True)


class CompanyOperatingMetricsSnapshotDTO(_FrozenForbid):
    """Bounded company operating-metric package (max 200 observations)."""

    instrument_id: str
    as_of: datetime
    lookback_months: int
    observations: tuple[CompanyOperatingMetricObservationDTO, ...]
    documents: tuple[DocumentParseReceiptDTO, ...]
    missing_metric_codes: tuple[str, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_domain(
        cls,
        value: CompanyOperatingMetricsSnapshot,
        *,
        provenance: tuple[AShareComponentProvenanceDTO, ...],
        metric_codes: tuple[str, ...] = (),
    ) -> CompanyOperatingMetricsSnapshotDTO:
        selected = value.observations
        if metric_codes:
            allowed = frozenset(metric_codes)
            selected = tuple(item for item in value.observations if item.metric_code in allowed)
        present = {item.metric_code for item in selected}
        missing = (
            tuple(code for code in metric_codes if code not in present)
            if metric_codes
            else value.missing_metric_codes
        )
        # Preserve newest-first order from the domain snapshot.
        return cls(
            instrument_id=value.instrument_id,
            as_of=value.as_of,
            lookback_months=value.lookback_months,
            observations=tuple(
                CompanyOperatingMetricObservationDTO.from_domain(item) for item in selected
            ),
            documents=tuple(DocumentParseReceiptDTO.from_domain(item) for item in value.documents),
            missing_metric_codes=missing,
            provenance=provenance,
        )

