from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from application.dto.a_share import (
    AShareFinancialMetricDTO,
    AShareFinancialPeriodDTO,
    AShareFinancialStatementsDTO,
    AShareGetFinancialStatementsInput,
)
from application.dto.peer_comparison import PeerComparisonRunInput
from application.dto.tool_envelope import SourceReference, ToolEnvelope
from application.dto.us_research import (
    FundamentalGetStatementsInput,
    USFinancialStatementsDTO,
    USStatementPeriodDTO,
)
from application.services.peer_comparison_service import PeerComparisonService
from domain.a_share.enums import FinancialStatementType
from domain.common.enums import Freshness, Market, SourceRole
from domain.common.ids import EntityIdPrefix
from domain.company_comparison.calculator import PeerComparisonCalculator
from domain.company_comparison.enums import PeerComparisonPeriodMode, PeerComparisonStatus
from domain.company_comparison.models import PeerCompanyFacts, PeerCompanyPeriod
from domain.us_research.enums import USStatementFrequency, USStatementType, USStatementView
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def new(self, prefix: EntityIdPrefix) -> str:
        return f"{prefix.value}_peer"


def _period(
    instrument_id: str,
    year: int,
    revenue: str,
    net_income: str,
    *,
    currency: str = "USD",
) -> PeerCompanyPeriod:
    return PeerCompanyPeriod(
        instrument_id=instrument_id,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        fiscal_year=year,
        basis="annual",
        currency=currency,
        published_at=datetime(year + 1, 2, 1, tzinfo=UTC),
        line_items=(
            ("revenue", Decimal(revenue)),
            ("net_income", Decimal(net_income)),
            ("operating_cash_flow", Decimal(net_income) * Decimal("1.2")),
            ("capital_expenditure", Decimal("-10")),
        ),
        source_names=("test-statements",),
    )


def test_peer_input_rejects_cross_market_and_us_operating_appendix() -> None:
    with pytest.raises(ValidationError, match="peer_market_mismatch"):
        PeerComparisonRunInput(
            idempotency_key="peer-1",
            primary_instrument_id="equity:US:NVDA",
            peer_instrument_ids=("equity:A_SHARE:600519.SH",),
        )

    with pytest.raises(ValidationError, match="A-share only"):
        PeerComparisonRunInput(
            idempotency_key="peer-2",
            primary_instrument_id="equity:US:NVDA",
            peer_instrument_ids=("equity:US:AMD",),
            include_operating_metrics=True,
        )


def test_calculator_aligns_fiscal_years_and_preserves_comparability() -> None:
    primary = PeerCompanyFacts(
        "equity:US:NVDA",
        (
            _period("equity:US:NVDA", 2025, "120", "24"),
            _period("equity:US:NVDA", 2024, "100", "20"),
        ),
    )
    peer = PeerCompanyFacts(
        "equity:US:AMD",
        (
            _period("equity:US:AMD", 2025, "90", "9"),
            _period("equity:US:AMD", 2024, "75", "6"),
        ),
    )

    result = PeerComparisonCalculator().compare(
        primary_instrument_id=primary.instrument_id,
        peer_instrument_ids=(peer.instrument_id,),
        market=Market.US,
        as_of=NOW,
        period_mode=PeerComparisonPeriodMode.ANNUAL,
        periods=1,
        companies=(primary, peer),
    )

    revenue = next(row for row in result.comparison_rows if row.metric_code == "revenue")
    growth = next(row for row in result.comparison_rows if row.metric_code == "revenue_yoy")
    margin = next(row for row in result.comparison_rows if row.metric_code == "net_margin")
    assert revenue.comparability is PeerComparisonStatus.COMPARABLE
    assert tuple(cell.value for cell in revenue.values) == (Decimal("120"), Decimal("90"))
    assert tuple(cell.value for cell in growth.values) == (
        Decimal("0.2"),
        Decimal("0.2"),
    )
    assert tuple(cell.value for cell in margin.values) == (
        Decimal("0.2"),
        Decimal("0.1"),
    )
    assert not hasattr(result, "ranking")


def test_amounts_fail_closed_when_currencies_differ_but_ratios_remain_comparable() -> None:
    primary = PeerCompanyFacts(
        "equity:A_SHARE:600519.SH",
        (_period("equity:A_SHARE:600519.SH", 2025, "100", "20", currency="CNY"),),
    )
    peer = PeerCompanyFacts(
        "equity:A_SHARE:000858.SZ",
        (_period("equity:A_SHARE:000858.SZ", 2025, "80", "8", currency="HKD"),),
    )

    result = PeerComparisonCalculator().compare(
        primary_instrument_id=primary.instrument_id,
        peer_instrument_ids=(peer.instrument_id,),
        market=Market.A_SHARE,
        as_of=NOW,
        period_mode=PeerComparisonPeriodMode.ANNUAL,
        periods=1,
        companies=(primary, peer),
    )

    revenue = next(row for row in result.comparison_rows if row.metric_code == "revenue")
    margin = next(row for row in result.comparison_rows if row.metric_code == "net_margin")
    assert revenue.unit == "mixed"
    assert revenue.comparability is PeerComparisonStatus.NOT_COMPARABLE
    assert margin.comparability is PeerComparisonStatus.COMPARABLE


@pytest.mark.asyncio
async def test_service_reuses_normalized_us_statements_without_peer_discovery() -> None:
    def statements(instrument_id: str) -> USFinancialStatementsDTO:
        income = tuple(
            USStatementPeriodDTO(
                statement_type=USStatementType.INCOME,
                frequency=USStatementFrequency.ANNUAL,
                fiscal_year=year,
                fiscal_period="FY",
                period_start=date(year, 1, 1),
                period_end=date(year, 12, 31),
                filed_at=datetime(year + 1, 2, 1, tzinfo=UTC),
                currency="USD",
                line_items=(
                    ("revenue", Decimal(revenue)),
                    ("net_income", Decimal(net_income)),
                ),
                accession=f"{instrument_id}-{year}",
                filing_form="10-K",
                is_amendment=False,
            )
            for year, revenue, net_income in (
                (2025, "120", "24"),
                (2024, "100", "20"),
            )
        )
        return USFinancialStatementsDTO(
            instrument_id=instrument_id,
            as_of=NOW,
            frequency=USStatementFrequency.ANNUAL,
            income=income,
            balance_sheet=(),
            cash_flow=(),
            view=USStatementView.LATEST,
            quality_metrics=(),
        )

    async def get_statements(
        request: FundamentalGetStatementsInput,
    ) -> ToolEnvelope[USFinancialStatementsDTO]:
        instrument_id = request.instrument_id
        return ToolEnvelope.success(
            request_id=f"req_{instrument_id}",
            market=Market.US,
            as_of=NOW,
            fetched_at=NOW,
            freshness=Freshness.FRESH,
            sources=(SourceReference(name="sec", role=SourceRole.PRIMARY),),
            data=statements(instrument_id),
        )

    us_research = MagicMock()
    us_research.get_fundamental_statements = AsyncMock(side_effect=get_statements)
    service = PeerComparisonService(
        a_share=MagicMock(),
        us_research=us_research,
        calculator=PeerComparisonCalculator(),
        clock=_Clock(),
        id_generator=_Ids(),
        secret_redactor=DefaultSecretRedactor(),
    )
    request = PeerComparisonRunInput(
        idempotency_key="peer-us-1",
        primary_instrument_id="equity:US:NVDA",
        peer_instrument_ids=("equity:US:AMD",),
        periods=1,
        include_valuation=False,
        as_of=NOW,
    )

    result = await service.compare(request)

    assert result.ok
    assert result.data is not None
    assert result.data.primary_instrument_id == "equity:US:NVDA"
    assert any(row.metric_code == "revenue_yoy" for row in result.data.comparison_rows)
    assert us_research.get_fundamental_statements.await_count == 2
    us_research.get_fundamental_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_service_reuses_normalized_a_share_statements_serially() -> None:
    def statements(instrument_id: str) -> AShareFinancialStatementsDTO:
        periods = tuple(
            AShareFinancialPeriodDTO(
                period_end=date(year, 12, 31),
                basis="annual",
                metrics=(
                    AShareFinancialMetricDTO(
                        statement_type=FinancialStatementType.INCOME_STATEMENT,
                        metric_code="revenue",
                        item_name="营业收入",
                        value=Decimal(revenue),
                        unit="CNY",
                        published_at=datetime(year + 1, 3, 1, tzinfo=UTC),
                    ),
                    AShareFinancialMetricDTO(
                        statement_type=FinancialStatementType.INCOME_STATEMENT,
                        metric_code="net_income",
                        item_name="净利润",
                        value=Decimal(net_income),
                        unit="CNY",
                        published_at=datetime(year + 1, 3, 1, tzinfo=UTC),
                    ),
                ),
            )
            for year, revenue, net_income in (
                (2025, "120", "24"),
                (2024, "100", "20"),
            )
        )
        return AShareFinancialStatementsDTO(
            instrument_id=instrument_id,
            as_of=NOW,
            requested_periods=3,
            metric_codes=("revenue", "net_income"),
            periods=periods,
            quality_metrics=(),
            provenance=(),
        )

    async def get_statements(
        request: AShareGetFinancialStatementsInput,
    ) -> ToolEnvelope[AShareFinancialStatementsDTO]:
        return ToolEnvelope.success(
            request_id=f"req_{request.instrument_id}",
            market=Market.A_SHARE,
            as_of=NOW,
            fetched_at=NOW,
            freshness=Freshness.FRESH,
            sources=(SourceReference(name="sina", role=SourceRole.PRIMARY),),
            data=statements(request.instrument_id),
        )

    a_share = MagicMock()
    a_share.get_financial_statements = AsyncMock(side_effect=get_statements)
    service = PeerComparisonService(
        a_share=a_share,
        us_research=MagicMock(),
        calculator=PeerComparisonCalculator(),
        clock=_Clock(),
        id_generator=_Ids(),
        secret_redactor=DefaultSecretRedactor(),
    )
    request = PeerComparisonRunInput(
        idempotency_key="peer-cn-1",
        primary_instrument_id="equity:A_SHARE:600519.SH",
        peer_instrument_ids=("equity:A_SHARE:000858.SZ",),
        periods=1,
        include_valuation=False,
        as_of=NOW,
    )

    result = await service.compare(request)

    assert result.ok
    assert result.data is not None
    assert result.data.market is Market.A_SHARE
    assert a_share.get_financial_statements.await_count == 2
    a_share.get_snapshot.assert_not_called()
