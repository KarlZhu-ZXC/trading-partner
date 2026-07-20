"""Tool Envelope DTO — universal MCP response wrapper."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.common.enums import Freshness, Market, SourceRole
from domain.common.time import require_aware_datetime


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    name: str
    role: SourceRole
    url: str | None = None
    retrieved_at: datetime | None = None
    data_delay_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _aware_retrieved_at(self) -> Self:
        if self.retrieved_at is not None:
            require_aware_datetime(self.retrieved_at, field_name="retrieved_at")
        return self


class WarningInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


# Phase 1B: returned when propose_* hits an existing idempotency_key with same payload.
DUPLICATE_IDEMPOTENCY_KEY = WarningInfo(
    code="DUPLICATE_IDEMPOTENCY_KEY",
    message="Candidate already exists for this idempotency_key.",
    details={},
)

# Phase 1C: returned when Evidence/Report content hash or CaseEvidenceLink already exists.
DUPLICATE_CONTENT = WarningInfo(
    code="DUPLICATE_CONTENT",
    message="Content already exists; returning existing immutable record.",
    details={},
)


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool
    details: dict[str, object] = Field(default_factory=dict)


class ToolEnvelope[T](BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    ok: bool
    request_id: str
    market: Market | None
    as_of: datetime
    fetched_at: datetime
    freshness: Freshness
    sources: tuple[SourceReference, ...]
    degraded: bool
    data: T | None
    warnings: tuple[WarningInfo, ...]
    errors: tuple[ErrorInfo, ...]

    @model_validator(mode="after")
    def _enforce_invariants(self) -> Self:
        require_aware_datetime(self.as_of, field_name="as_of")
        require_aware_datetime(self.fetched_at, field_name="fetched_at")

        if self.ok:
            if self.data is None:
                raise ValueError("ok=True requires non-null data")
            if self.errors:
                raise ValueError("ok=True requires empty errors")
        else:
            if not self.errors:
                raise ValueError("ok=False requires at least one ErrorInfo")

        if self.degraded and not self.warnings and not self.errors:
            raise ValueError("degraded=True requires at least one warning or error")

        return self

    @classmethod
    def success(
        cls,
        *,
        request_id: str,
        market: Market | None,
        as_of: datetime,
        fetched_at: datetime,
        freshness: Freshness,
        sources: tuple[SourceReference, ...] | list[SourceReference],
        data: T,
        degraded: bool = False,
        warnings: tuple[WarningInfo, ...] | list[WarningInfo] | None = None,
        errors: tuple[ErrorInfo, ...] | list[ErrorInfo] | None = None,
    ) -> ToolEnvelope[T]:
        return cls(
            ok=True,
            request_id=request_id,
            market=market,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            sources=tuple(sources),
            degraded=degraded,
            data=data,
            warnings=tuple(warnings or ()),
            errors=tuple(errors or ()),
        )

    @classmethod
    def failure(
        cls,
        *,
        request_id: str,
        market: Market | None,
        as_of: datetime,
        fetched_at: datetime,
        freshness: Freshness = Freshness.UNKNOWN,
        sources: tuple[SourceReference, ...] | list[SourceReference] | None = None,
        errors: tuple[ErrorInfo, ...] | list[ErrorInfo],
        degraded: bool = True,
        warnings: tuple[WarningInfo, ...] | list[WarningInfo] | None = None,
        data: T | None = None,
    ) -> ToolEnvelope[T]:
        return cls(
            ok=False,
            request_id=request_id,
            market=market,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            sources=tuple(sources or ()),
            degraded=degraded,
            data=data,
            warnings=tuple(warnings or ()),
            errors=tuple(errors),
        )
