"""Persistence boundary for publication-aware industry metric history."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.a_share.enums import IndustryCycleType
from domain.a_share.models import IndustryMetricObservation


class IndustryMetricRepository(Protocol):
    def upsert(
        self,
        *,
        cycle: IndustryCycleType,
        dataset_code: str,
        observations: tuple[IndustryMetricObservation, ...],
        ingested_at: datetime,
    ) -> int: ...

    def list_visible(
        self,
        *,
        cycle: IndustryCycleType,
        as_of: datetime,
        metric_codes: tuple[str, ...] = (),
    ) -> tuple[IndustryMetricObservation, ...]: ...
