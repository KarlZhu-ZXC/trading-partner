"""Health status DTO for the system_health tool."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from domain.common.enums import HealthState


class HealthStatusDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    status: HealthState
    app_name: str
    version: str
    environment: str
    database: HealthState
    # C5: bootstrap/runtime always includes research_search; direct unit
    # construction without a probe omits components (no false ok).
    components: dict[str, HealthState] = Field(default_factory=dict)
