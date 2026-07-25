"""SQLAlchemy persistence for durable industry metric vintages."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from domain.a_share.enums import (
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from domain.a_share.models import IndustryMetricObservation
from infrastructure.persistence.models import IndustryMetricObservationRow
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db


def _utc_db(value: datetime) -> str:
    return dt_to_db(value.astimezone(UTC))


def _observation_key(
    cycle: IndustryCycleType,
    dataset_code: str,
    observation: IndustryMetricObservation,
) -> str:
    natural_key = "|".join(
        (
            cycle.value,
            dataset_code,
            observation.metric_code,
            observation.period_end.isoformat(),
            observation.published_at.isoformat(),
        )
    )
    return "industry_obs_" + hashlib.sha256(natural_key.encode()).hexdigest()[:32]


class SqlAlchemyIndustryMetricRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(
        self,
        *,
        cycle: IndustryCycleType,
        dataset_code: str,
        observations: tuple[IndustryMetricObservation, ...],
        ingested_at: datetime,
    ) -> int:
        inserted = 0
        with Session(self._engine) as session, session.begin():
            for item in observations:
                key = _observation_key(cycle, dataset_code, item)
                row = session.get(IndustryMetricObservationRow, key)
                if row is not None:
                    continue
                session.add(
                    IndustryMetricObservationRow(
                        observation_key=key,
                        cycle=cycle.value,
                        dataset_code=dataset_code,
                        metric_code=item.metric_code,
                        value=str(item.value),
                        unit=item.unit,
                        geography=item.geography,
                        period_start=item.period_start.isoformat(),
                        period_end=item.period_end.isoformat(),
                        frequency=item.frequency.value,
                        measurement_basis=item.measurement_basis.value,
                        published_at=_utc_db(item.published_at),
                        source_url=item.source_url,
                        is_estimated=int(item.is_estimated),
                        methodology_version=item.methodology_version,
                        methodology_break=item.methodology_break,
                        ingested_at=_utc_db(ingested_at),
                    )
                )
                inserted += 1
        return inserted

    def list_visible(
        self,
        *,
        cycle: IndustryCycleType,
        as_of: datetime,
        metric_codes: tuple[str, ...] = (),
    ) -> tuple[IndustryMetricObservation, ...]:
        statement = select(IndustryMetricObservationRow).where(
            IndustryMetricObservationRow.cycle == cycle.value,
            IndustryMetricObservationRow.published_at <= _utc_db(as_of),
        )
        if metric_codes:
            statement = statement.where(
                IndustryMetricObservationRow.metric_code.in_(metric_codes)
            )
        statement = statement.order_by(
            IndustryMetricObservationRow.period_end,
            IndustryMetricObservationRow.metric_code,
            IndustryMetricObservationRow.published_at,
        )
        with Session(self._engine) as session:
            rows = tuple(session.scalars(statement))
        latest: dict[tuple[str, str], IndustryMetricObservationRow] = {}
        for row in rows:
            latest[(row.metric_code, row.period_end)] = row
        return tuple(
            self._domain(row)
            for row in sorted(
                latest.values(), key=lambda item: (item.period_end, item.metric_code)
            )
        )

    @staticmethod
    def _domain(row: IndustryMetricObservationRow) -> IndustryMetricObservation:
        return IndustryMetricObservation(
            metric_code=row.metric_code,
            value=Decimal(row.value),
            unit=row.unit,
            geography=row.geography,
            period_start=date.fromisoformat(row.period_start),
            period_end=date.fromisoformat(row.period_end),
            frequency=IndustryMetricFrequency(row.frequency),
            measurement_basis=IndustryMeasurementBasis(row.measurement_basis),
            published_at=dt_from_db(row.published_at, field_name="published_at"),
            source_url=row.source_url,
            is_estimated=bool(row.is_estimated),
            methodology_version=row.methodology_version,
            methodology_break=row.methodology_break,
        )
