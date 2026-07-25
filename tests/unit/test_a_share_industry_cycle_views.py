"""Representative HOG-P0-004 compact/series view contracts for industry_cycle."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from application.dto.a_share import (
    AShareGetIndustryCycleInput,
    IndustryCycleSnapshotDTO,
)
from application.dto.provider_routing import ProviderResultMeta
from application.dto.tool_envelope import WarningInfo
from application.services.a_share_industry_cycle_service import AShareIndustryCycleService
from application.services.provider_router import RouterExecutionResult
from domain.a_share.enums import (
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from domain.a_share.models import IndustryCycleSnapshot, IndustryMetricObservation
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    SourceRole,
    TradingSession,
    VendorId,
)

NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _obs(
    *,
    metric_code: str,
    value: str,
    period_end: date,
    published_at: datetime | None = None,
    frequency: IndustryMetricFrequency = IndustryMetricFrequency.MONTHLY,
) -> IndustryMetricObservation:
    period_start = date(period_end.year, period_end.month, 1)
    return IndustryMetricObservation(
        metric_code=metric_code,
        value=Decimal(value),
        unit="CNY/kg" if metric_code.endswith("cny_per_kg") else "ratio",
        period_start=period_start,
        period_end=period_end,
        frequency=frequency,
        published_at=published_at or datetime(period_end.year, period_end.month, 15, tzinfo=UTC),
        source_url="https://www.nahs.org.cn/jcyj/scxs/example.htm",
        measurement_basis=IndustryMeasurementBasis.PERIOD_AVERAGE,
    )


def _snapshot(observations: tuple[IndustryMetricObservation, ...]) -> IndustryCycleSnapshot:
    ordered = tuple(sorted(observations, key=lambda item: (item.period_end, item.metric_code)))
    return IndustryCycleSnapshot(
        cycle=IndustryCycleType.HOG,
        dataset_code="nahs_national_hog_cycle",
        as_of=NOW,
        observations=ordered,
        missing_components=("company_operating_data", "live_hog_futures_curve"),
    )


def _meta(*, warnings: tuple[str, ...] = ()) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.NAHS,
        category=DataCategory.INDUSTRY_CYCLE,
        role=SourceRole.PRIMARY,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.DELAYED,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=warnings,
    )


def test_industry_cycle_input_defaults_and_bounds() -> None:
    default = AShareGetIndustryCycleInput()
    assert default.view == "compact"
    assert default.metric_codes == ()
    assert default.offset == 0
    assert default.limit == 50

    bounded = AShareGetIndustryCycleInput(
        view="series",
        metric_codes=("live_hog_cny_per_kg", "pig_grain_ratio"),
        offset=10,
        limit=200,
        lookback_months=240,
    )
    assert bounded.view == "series"
    assert bounded.limit == 200

    with pytest.raises(ValidationError):
        AShareGetIndustryCycleInput(view="full")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AShareGetIndustryCycleInput(offset=-1)
    with pytest.raises(ValidationError):
        AShareGetIndustryCycleInput(limit=201)
    with pytest.raises(ValidationError):
        AShareGetIndustryCycleInput(limit=0)
    with pytest.raises(ValidationError):
        AShareGetIndustryCycleInput(metric_codes=("LiveHog",))
    with pytest.raises(ValidationError):
        AShareGetIndustryCycleInput(metric_codes=("live_hog_cny_per_kg", "live_hog_cny_per_kg"))


def test_compact_view_returns_latest_per_metric_with_coverage() -> None:
    snapshot = _snapshot(
        (
            _obs(metric_code="live_hog_cny_per_kg", value="10.00", period_end=date(2026, 4, 30)),
            _obs(metric_code="live_hog_cny_per_kg", value="10.15", period_end=date(2026, 5, 31)),
            _obs(metric_code="live_hog_cny_per_kg", value="10.09", period_end=date(2026, 6, 30)),
            _obs(metric_code="pig_grain_ratio", value="4.03", period_end=date(2026, 4, 30)),
            _obs(metric_code="pig_grain_ratio", value="4.06", period_end=date(2026, 5, 31)),
            _obs(metric_code="pig_grain_ratio", value="4.05", period_end=date(2026, 6, 30)),
            _obs(
                metric_code="breeding_sow_inventory_10k_head",
                value="3780",
                period_end=date(2026, 6, 30),
                frequency=IndustryMetricFrequency.HALF_YEAR,
            ),
        )
    )

    dto = IndustryCycleSnapshotDTO.from_domain(snapshot, provenance=(), view="compact")

    assert dto.view == "compact"
    assert dto.total_observations == 7
    assert dto.has_more is False
    assert [item.metric_code for item in dto.observations] == [
        "breeding_sow_inventory_10k_head",
        "live_hog_cny_per_kg",
        "pig_grain_ratio",
    ]
    by_code = {item.metric_code: item for item in dto.observations}
    assert str(by_code["live_hog_cny_per_kg"].value) == "10.09"
    assert str(by_code["pig_grain_ratio"].value) == "4.05"
    coverage = {item.metric_code: item for item in dto.coverage}
    assert set(coverage) == {
        "breeding_sow_inventory_10k_head",
        "live_hog_cny_per_kg",
        "pig_grain_ratio",
    }
    assert coverage["live_hog_cny_per_kg"].count == 3
    assert coverage["live_hog_cny_per_kg"].first_period == date(2026, 4, 30)
    assert coverage["live_hog_cny_per_kg"].last_period == date(2026, 6, 30)
    assert coverage["pig_grain_ratio"].count == 3
    assert coverage["breeding_sow_inventory_10k_head"].count == 1
    assert dto.missing_components == (
        "company_operating_data",
        "live_hog_futures_curve",
    )


def test_series_view_pages_filtered_history_and_reports_has_more() -> None:
    observations = tuple(
        _obs(
            metric_code="live_hog_cny_per_kg",
            value=f"10.{month:02d}",
            period_end=date(
                2025,
                month,
                28 if month == 2 else 30 if month in {4, 6, 9, 11} else 31,
            ),
        )
        for month in range(1, 7)
    )
    snapshot = _snapshot(observations)

    page = IndustryCycleSnapshotDTO.from_domain(
        snapshot,
        provenance=(),
        view="series",
        metric_codes=("live_hog_cny_per_kg",),
        offset=2,
        limit=2,
    )

    assert page.view == "series"
    assert page.total_observations == 6
    assert page.offset == 2
    assert page.limit == 2
    assert page.has_more is True
    assert [str(item.period_end) for item in page.observations] == [
        "2025-03-31",
        "2025-04-30",
    ]
    assert len(page.coverage) == 1
    assert page.coverage[0].metric_code == "live_hog_cny_per_kg"
    assert page.coverage[0].count == 6
    assert page.coverage[0].first_period == date(2025, 1, 31)
    assert page.coverage[0].last_period == date(2025, 6, 30)

    tail = IndustryCycleSnapshotDTO.from_domain(
        snapshot,
        provenance=(),
        view="series",
        offset=4,
        limit=2,
    )
    assert tail.has_more is False
    assert len(tail.observations) == 2


@pytest.mark.asyncio
async def test_service_preserves_partial_history_warning_and_bounds_output() -> None:
    full = _snapshot(
        tuple(
            _obs(
                metric_code="live_hog_cny_per_kg",
                value=str(Decimal("10") + Decimal(i)),
                period_end=date(2024 + (i // 12), (i % 12) + 1, 28),
            )
            for i in range(24)
        )
    )
    router = MagicMock()
    router.execute = AsyncMock(
        return_value=RouterExecutionResult(
            ok=True,
            value=full,
            criticality=DataCriticality.CORE,
            meta=_meta(warnings=("HOG_CYCLE_HISTORY_PARTIAL", "HOG_CYCLE_NOT_COMPANY_COST")),
            attempts=(),
            warnings=(),
            error=None,
        )
    )
    service = AShareIndustryCycleService(router=router, repository=None)

    result = await service.get_hog_cycle(
        lookback_months=240,
        as_of=NOW,
        view="compact",
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.view == "compact"
    assert result.data.total_observations == 24
    assert len(result.data.observations) == 1
    assert "HOG_CYCLE_HISTORY_PARTIAL" in {item.code for item in result.warnings}
    assert any(isinstance(item, WarningInfo) for item in result.warnings)
    # Never imply continuous multi-year coverage from the request window alone.
    wire: dict[str, Any] = result.data.model_dump(mode="json")
    assert wire["total_observations"] == 24
    assert len(wire["observations"]) == 1
    assert wire["coverage"][0]["count"] == 24
