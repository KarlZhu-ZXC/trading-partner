"""Closed application contracts for Trade Cycle manual overrides."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from application.dto.account_transactions import TradeCycleProjectionDTO
from domain.portfolio.trade_cycle_overrides import (
    TradeCycleOverrideImpact,
    TradeCycleOverrideOperation,
    TradeCycleOverrideProjection,
    TradeCycleOverrideRevision,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class TradeCycleOverrideAppendInput(_DTO):
    root_cycle_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        validation_alias=AliasChoices("root_cycle_id", "cycle_id"),
    )
    operation: TradeCycleOverrideOperation
    cycle_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("cycle_ids", "source_cycle_ids"),
    )
    activity_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("activity_ids", "relink_activity_ids"),
    )
    split_groups: tuple[tuple[str, ...], ...] = Field(
        default=(), validation_alias=AliasChoices("split_groups", "groups")
    )
    target_cycle_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        validation_alias=AliasChoices("target_cycle_id", "target_cycle"),
    )
    algorithm_version: str = Field(default="trade_cycle_v1", min_length=1, max_length=64)
    note: str | None = Field(default=None, min_length=1, max_length=2_000)
    actor: str = Field(min_length=1, max_length=32)
    authorization_note: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def normalize_root(self) -> TradeCycleOverrideAppendInput:
        root = self.root_cycle_id
        if root is None and self.cycle_ids:
            root = self.cycle_ids[0]
            object.__setattr__(self, "root_cycle_id", root)
        if root is None and self.target_cycle_id is not None:
            object.__setattr__(self, "root_cycle_id", self.target_cycle_id)
        if self.root_cycle_id is None:
            raise ValueError("root_cycle_id or cycle_ids is required")
        return self


class TradeCycleOverrideRevisionDTO(_DTO):
    override_id: str
    root_cycle_id: str
    version: int
    operation: TradeCycleOverrideOperation
    cycle_ids: tuple[str, ...]
    activity_ids: tuple[str, ...]
    split_groups: tuple[tuple[str, ...], ...]
    target_cycle_id: str | None
    algorithm_version: str
    note: str | None
    actor: str
    authorization_note: str
    idempotency_key: str
    created_at: datetime
    expected_version: int | None = None

    @classmethod
    def from_domain(cls, value: TradeCycleOverrideRevision) -> TradeCycleOverrideRevisionDTO:
        return cls.model_validate(value)

    @property
    def cycle_id(self) -> str:
        return self.root_cycle_id


class TradeCycleOverrideImpactDTO(_DTO):
    operation: TradeCycleOverrideOperation
    source_cycle_ids: tuple[str, ...]
    result_cycle_ids: tuple[str, ...]
    moved_activity_ids: tuple[str, ...]
    before_activity_count: int
    after_activity_count: int
    warning_codes: tuple[str, ...]
    recompute_required: bool

    @classmethod
    def from_domain(cls, value: TradeCycleOverrideImpact) -> TradeCycleOverrideImpactDTO:
        return cls.model_validate(value)

    @property
    def affected_cycle_ids(self) -> tuple[str, ...]:
        return self.source_cycle_ids


class TradeCycleOverrideProjectionDTO(_DTO):
    algorithm_projection: TradeCycleProjectionDTO
    effective_projection: TradeCycleProjectionDTO
    applied_revisions: tuple[TradeCycleOverrideRevisionDTO, ...]
    impacts: tuple[TradeCycleOverrideImpactDTO, ...]

    @classmethod
    def from_domain(
        cls, value: TradeCycleOverrideProjection
    ) -> TradeCycleOverrideProjectionDTO:
        return cls(
            algorithm_projection=TradeCycleProjectionDTO.from_domain(value.algorithm_projection),
            effective_projection=TradeCycleProjectionDTO.from_domain(value.effective_projection),
            applied_revisions=tuple(
                TradeCycleOverrideRevisionDTO.from_domain(item)
                for item in value.applied_revisions
            ),
            impacts=tuple(TradeCycleOverrideImpactDTO.from_domain(item) for item in value.impacts),
        )


TradeCycleOverridePreviewDTO = TradeCycleOverrideImpactDTO

__all__ = [
    "TradeCycleOverrideAppendInput",
    "TradeCycleOverrideImpactDTO",
    "TradeCycleOverridePreviewDTO",
    "TradeCycleOverrideProjectionDTO",
    "TradeCycleOverrideRevisionDTO",
]
