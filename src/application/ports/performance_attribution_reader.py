"""Read-only application port for durable performance attribution."""

from __future__ import annotations

from typing import Protocol

from application.dto.performance_attribution import (
    PerformanceAttributionDTO,
    PerformanceAttributionInput,
)
from application.dto.tool_envelope import ToolEnvelope


class PerformanceAttributionReader(Protocol):
    def get_performance_attribution(
        self, request: PerformanceAttributionInput
    ) -> ToolEnvelope[PerformanceAttributionDTO]:
        """Calculate attribution from durable facts without contacting a broker."""
        ...
