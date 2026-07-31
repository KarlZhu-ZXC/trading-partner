"""A-share quote, statement, and disclosure output DTOs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from application.dto.a_share_common import _FrozenForbid
from application.dto.a_share_inputs import A_SHARE_DEFAULT_FINANCIAL_METRICS
from application.dto.a_share_provenance import AShareComponentProvenanceDTO
from application.dto.financial_quality import (
    FinancialQualityMetricDTO,
    derive_financial_quality_metrics,
)
from application.dto.market import DecimalWire
from domain.a_share.enums import BarInterval, FinancialStatementType, TickDirection
from domain.a_share.models import (
    AnalystReportItem,
    AnnouncementItem,
    AShareBar,
    AShareQuote,
    ConsensusEstimate,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    InteractiveQAItem,
    NewsItem,
    OrderBookLevel,
    TradeTick,
)
from domain.common.enums import AdjustmentMethod, TradingSession

# ---------------------------------------------------------------------------
# Domain-mirroring output DTOs
# ---------------------------------------------------------------------------


class AShareQuoteDTO(_FrozenForbid):
    instrument_id: str
    quote_at: datetime
    session: TradingSession
    last: DecimalWire
    open: DecimalWire | None
    high: DecimalWire | None
    low: DecimalWire | None
    previous_close: DecimalWire | None
    change: DecimalWire | None
    change_percent: DecimalWire | None
    volume_shares: int | None
    turnover_amount_cny: DecimalWire | None
    turnover_rate: DecimalWire | None
    pe_ttm: DecimalWire | None
    pb: DecimalWire | None
    total_market_cap_cny: DecimalWire | None
    float_market_cap_cny: DecimalWire | None
    limit_up_price: DecimalWire | None
    limit_down_price: DecimalWire | None

    @classmethod
    def from_domain(cls, quote: AShareQuote) -> AShareQuoteDTO:
        return cls.model_validate(quote, from_attributes=True)


class OrderBookLevelDTO(_FrozenForbid):
    level: int
    bid_price: DecimalWire | None
    bid_volume_shares: int | None
    ask_price: DecimalWire | None
    ask_volume_shares: int | None

    @classmethod
    def from_domain(cls, level: OrderBookLevel) -> OrderBookLevelDTO:
        return cls.model_validate(level, from_attributes=True)


class TradeTickDTO(_FrozenForbid):
    occurred_at: datetime
    price: DecimalWire
    volume_shares: int
    direction: TickDirection

    @classmethod
    def from_domain(cls, tick: TradeTick) -> TradeTickDTO:
        return cls.model_validate(tick, from_attributes=True)


class AShareBarDTO(_FrozenForbid):
    start_at: datetime
    end_at: datetime
    interval: BarInterval
    open: DecimalWire
    high: DecimalWire
    low: DecimalWire
    close: DecimalWire
    volume_shares: int
    turnover_amount_cny: DecimalWire | None
    adjustment: AdjustmentMethod

    @classmethod
    def from_domain(cls, bar: AShareBar) -> AShareBarDTO:
        return cls.model_validate(bar, from_attributes=True)


class FundamentalMetricDTO(_FrozenForbid):
    name: str
    value: DecimalWire | str | int | None
    unit: str | None
    period_end: date | None
    published_at: datetime | None

    @classmethod
    def from_domain(cls, metric: FundamentalMetric) -> FundamentalMetricDTO:
        return cls.model_validate(metric, from_attributes=True)


class FinancialStatementLineDTO(_FrozenForbid):
    statement_type: FinancialStatementType
    period_end: date
    published_at: datetime | None
    item_code: str
    item_name: str
    value: DecimalWire | None
    unit: str

    @classmethod
    def from_domain(cls, line: FinancialStatementLine) -> FinancialStatementLineDTO:
        return cls.model_validate(line, from_attributes=True)


class AShareFinancialMetricDTO(_FrozenForbid):
    statement_type: FinancialStatementType
    metric_code: str
    item_name: str
    value: DecimalWire | None
    unit: str
    published_at: datetime | None


class AShareFinancialPeriodDTO(_FrozenForbid):
    period_end: date
    basis: Literal["q1_ytd", "h1_ytd", "nine_month_ytd", "annual", "reported"]
    metrics: tuple[AShareFinancialMetricDTO, ...]


class AShareFinancialStatementsDTO(_FrozenForbid):
    """Bounded, canonical A-share statement facts plus deterministic ratios."""

    instrument_id: str
    as_of: datetime
    requested_periods: int
    metric_codes: tuple[str, ...]
    periods: tuple[AShareFinancialPeriodDTO, ...]
    quality_metrics: tuple[FinancialQualityMetricDTO, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_lines(
        cls,
        *,
        instrument_id: str,
        as_of: datetime,
        requested_periods: int,
        metric_codes: tuple[str, ...],
        lines: tuple[FinancialStatementLine, ...],
        provenance: tuple[AShareComponentProvenanceDTO, ...],
    ) -> AShareFinancialStatementsDTO:
        selected_codes = metric_codes or A_SHARE_DEFAULT_FINANCIAL_METRICS
        allowed = frozenset(selected_codes)
        grouped: dict[date, list[FinancialStatementLine]] = {}
        all_by_period: dict[date, dict[str, Decimal | None]] = {}
        for line in lines:
            all_by_period.setdefault(line.period_end, {})[line.item_code] = line.value
            if line.item_code in allowed:
                grouped.setdefault(line.period_end, []).append(line)

        periods: list[AShareFinancialPeriodDTO] = []
        quality: list[FinancialQualityMetricDTO] = []
        for period_end in sorted(all_by_period, reverse=True)[:requested_periods]:
            selected = grouped.get(period_end, [])
            selected.sort(key=lambda line: (line.statement_type.value, line.item_code))
            periods.append(
                AShareFinancialPeriodDTO(
                    period_end=period_end,
                    basis=_a_share_statement_basis(period_end),
                    metrics=tuple(
                        AShareFinancialMetricDTO(
                            statement_type=line.statement_type,
                            metric_code=line.item_code,
                            item_name=line.item_name,
                            value=line.value,
                            unit=line.unit,
                            published_at=line.published_at,
                        )
                        for line in selected
                    ),
                )
            )
            quality.extend(
                derive_financial_quality_metrics(
                    period_end=period_end,
                    line_items=all_by_period[period_end],
                    currency="CNY",
                )
            )
        return cls(
            instrument_id=instrument_id,
            as_of=as_of,
            requested_periods=requested_periods,
            metric_codes=selected_codes,
            periods=tuple(periods),
            quality_metrics=tuple(quality),
            provenance=provenance,
        )


def _a_share_statement_basis(
    period_end: date,
) -> Literal["q1_ytd", "h1_ytd", "nine_month_ytd", "annual", "reported"]:
    if (period_end.month, period_end.day) == (3, 31):
        return "q1_ytd"
    if (period_end.month, period_end.day) == (6, 30):
        return "h1_ytd"
    if (period_end.month, period_end.day) == (9, 30):
        return "nine_month_ytd"
    if (period_end.month, period_end.day) == (12, 31):
        return "annual"
    return "reported"


class F10SectionDTO(_FrozenForbid):
    section: str
    title: str
    body: str
    as_of: datetime

    @classmethod
    def from_domain(cls, section: F10Section) -> F10SectionDTO:
        return cls.model_validate(section, from_attributes=True)


class ConsensusEstimateDTO(_FrozenForbid):
    fiscal_year: int
    metric: str
    mean: DecimalWire | None
    high: DecimalWire | None
    low: DecimalWire | None
    institution_count: int | None

    @classmethod
    def from_domain(cls, estimate: ConsensusEstimate) -> ConsensusEstimateDTO:
        return cls.model_validate(estimate, from_attributes=True)


class AnalystReportItemDTO(_FrozenForbid):
    report_key: str
    title: str
    institution: str | None
    analyst_names: tuple[str, ...]
    published_at: datetime
    rating: str | None
    target_price: DecimalWire | None
    eps_forecasts: tuple[ConsensusEstimateDTO, ...]
    source_url: str | None
    pdf_url: str | None

    @classmethod
    def from_domain(cls, item: AnalystReportItem) -> AnalystReportItemDTO:
        return cls(
            report_key=item.report_key,
            title=item.title,
            institution=item.institution,
            analyst_names=item.analyst_names,
            published_at=item.published_at,
            rating=item.rating,
            target_price=item.target_price,
            eps_forecasts=tuple(ConsensusEstimateDTO.from_domain(e) for e in item.eps_forecasts),
            source_url=item.source_url,
            pdf_url=item.pdf_url,
        )


class AnnouncementItemDTO(_FrozenForbid):
    announcement_key: str
    title: str
    published_at: datetime
    category: str | None
    source_url: str
    pdf_url: str | None

    @classmethod
    def from_domain(cls, item: AnnouncementItem) -> AnnouncementItemDTO:
        return cls.model_validate(item, from_attributes=True)


class NewsItemDTO(_FrozenForbid):
    news_key: str
    title: str
    summary: str | None
    published_at: datetime
    source_name: str
    source_url: str | None

    @classmethod
    def from_domain(cls, item: NewsItem) -> NewsItemDTO:
        return cls.model_validate(item, from_attributes=True)


class InteractiveQAItemDTO(_FrozenForbid):
    qa_key: str
    question: str
    asked_at: datetime | None
    answer: str
    answered_at: datetime
    source_url: str | None

    @classmethod
    def from_domain(cls, item: InteractiveQAItem) -> InteractiveQAItemDTO:
        return cls.model_validate(item, from_attributes=True)

