from datetime import UTC, date, datetime
from decimal import Decimal

from domain.a_share.enums import (
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from domain.a_share.models import IndustryMetricObservation
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.industry_metric_repository import (
    SqlAlchemyIndustryMetricRepository,
)
from infrastructure.persistence.metadata import Base


def _observation(*, value: str, published: datetime) -> IndustryMetricObservation:
    return IndustryMetricObservation(
        metric_code="breeding_sow_inventory_10k_head",
        value=Decimal(value),
        unit="10k_head",
        period_start=date(2025, 6, 1),
        period_end=date(2025, 6, 30),
        frequency=IndustryMetricFrequency.MONTHLY,
        measurement_basis=IndustryMeasurementBasis.PERIOD_END,
        published_at=published,
        source_url="https://www.nahs.org.cn/jcyj/jcgz/example.htm",
    )


def test_repository_preserves_vintages_and_applies_publication_cutoff() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyIndustryMetricRepository(engine)
    first = _observation(value="4043", published=datetime(2025, 7, 10, tzinfo=UTC))
    revision = _observation(value="4042", published=datetime(2025, 8, 10, tzinfo=UTC))

    assert (
        repository.upsert(
            cycle=IndustryCycleType.HOG,
            dataset_code="nahs_national_hog_cycle",
            observations=(first, revision),
            ingested_at=datetime(2025, 8, 11, tzinfo=UTC),
        )
        == 2
    )
    assert repository.list_visible(
        cycle=IndustryCycleType.HOG,
        as_of=datetime(2025, 7, 31, tzinfo=UTC),
    ) == (first,)
    assert repository.list_visible(
        cycle=IndustryCycleType.HOG,
        as_of=datetime(2025, 8, 31, tzinfo=UTC),
    ) == (revision,)
