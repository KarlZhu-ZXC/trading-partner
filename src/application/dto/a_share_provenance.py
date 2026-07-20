"""Per-component provenance retained by A-share product services."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    ValidationInfo,
    field_validator,
    model_validator,
)

from application.dto.provider_routing import ProviderResultMeta
from domain.a_share.enums import AShareComponentType
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    Freshness,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError

_RELIABILITY_ORDER = {
    ReliabilityLevel.HIGH: 0,
    ReliabilityLevel.MEDIUM: 1,
    ReliabilityLevel.LOW: 2,
    ReliabilityLevel.UNKNOWN: 3,
}
_SAFE_WARNING_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DERIVED_WARNING = "DERIVED_CHIP_DISTRIBUTION"
_LOW_WARNING = "LOW_RELIABILITY_MARKET_SIGNAL"


def _validate_semantics(
    *,
    component: AShareComponentType,
    meta: ProviderResultMeta,
    reliability: ReliabilityLevel | None,
    is_authoritative: bool | None,
    is_derived: bool,
) -> None:
    if reliability is not None and not isinstance(reliability, ReliabilityLevel):
        raise DataContractError("reliability must be ReliabilityLevel or None")
    if is_authoritative is not None and type(is_authoritative) is not bool:
        raise DataContractError("is_authoritative must be exact bool or None")
    if type(is_derived) is not bool:
        raise DataContractError("is_derived must be exact bool")
    chip = component is AShareComponentType.CHIP_DISTRIBUTION
    if is_derived is not chip:
        raise DataContractError("only chip_distribution is derived")
    if chip and _DERIVED_WARNING not in meta.warnings:
        raise DataContractError("derived component requires DERIVED_CHIP_DISTRIBUTION")
    if not chip and _DERIVED_WARNING in meta.warnings:
        raise DataContractError("non-derived component must not carry derived warning")
    if reliability is ReliabilityLevel.LOW and not (
        chip or _LOW_WARNING in meta.warnings
    ):
        raise DataContractError("low reliability requires LOW_RELIABILITY_MARKET_SIGNAL")


@dataclass(frozen=True, slots=True)
class AShareComponentProvenance:
    component: AShareComponentType
    meta: ProviderResultMeta
    reliability: ReliabilityLevel | None
    is_authoritative: bool | None
    is_derived: bool

    def __post_init__(self) -> None:
        if not isinstance(self.component, AShareComponentType):
            raise DataContractError("component must be AShareComponentType")
        if not isinstance(self.meta, ProviderResultMeta):
            raise DataContractError("meta must be ProviderResultMeta")
        _validate_semantics(
            component=self.component,
            meta=self.meta,
            reliability=self.reliability,
            is_authoritative=self.is_authoritative,
            is_derived=self.is_derived,
        )


class AShareComponentProvenanceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: AShareComponentType
    vendor: VendorId
    category: DataCategory
    role: SourceRole
    as_of: datetime
    fetched_at: datetime
    freshness: Freshness
    session: TradingSession
    cache_disposition: CacheDisposition
    adjustment: AdjustmentMethod | None
    data_delay_seconds: int | None
    warnings: tuple[str, ...]
    reliability: ReliabilityLevel | None
    is_authoritative: StrictBool | None
    is_derived: StrictBool

    @field_validator("as_of", "fetched_at")
    @classmethod
    def _aware_datetime(cls, value: datetime, info: ValidationInfo) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{info.field_name} must be timezone-aware ISO 8601 datetime"
            )
        return value

    @field_validator("data_delay_seconds", mode="before")
    @classmethod
    def _strict_delay(cls, value: object) -> object:
        if value is not None and (
            type(value) is not int or isinstance(value, bool)
        ):
            raise ValueError("data_delay_seconds must be an exact int or None")
        return value

    @field_validator("warnings")
    @classmethod
    def _safe_warnings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _SAFE_WARNING_CODE_RE.fullmatch(code) for code in value):
            raise ValueError("warning codes must use the safe warning grammar")
        return value

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if self.data_delay_seconds is not None and self.data_delay_seconds < 0:
            raise ValueError("data_delay_seconds must be nonnegative")
        # Reconstructing ProviderResultMeta is deliberately avoided: latency is not wire data.
        chip = self.component is AShareComponentType.CHIP_DISTRIBUTION
        if self.is_derived is not chip:
            raise ValueError("only chip_distribution is derived")
        if chip and _DERIVED_WARNING not in self.warnings:
            raise ValueError("derived component requires DERIVED_CHIP_DISTRIBUTION")
        if not chip and _DERIVED_WARNING in self.warnings:
            raise ValueError("non-derived component must not carry derived warning")
        if self.reliability is ReliabilityLevel.LOW and not (
            chip or _LOW_WARNING in self.warnings
        ):
            raise ValueError("low reliability requires LOW_RELIABILITY_MARKET_SIGNAL")
        return self

    @classmethod
    def from_result(
        cls, provenance: AShareComponentProvenance
    ) -> AShareComponentProvenanceDTO:
        meta = provenance.meta
        return cls(
            component=provenance.component,
            vendor=meta.vendor,
            category=meta.category,
            role=meta.role,
            as_of=meta.as_of,
            fetched_at=meta.fetched_at,
            freshness=meta.freshness,
            session=meta.session,
            cache_disposition=meta.cache_disposition,
            adjustment=meta.adjustment,
            data_delay_seconds=meta.data_delay_seconds,
            warnings=meta.warnings,
            reliability=provenance.reliability,
            is_authoritative=provenance.is_authoritative,
            is_derived=provenance.is_derived,
        )


def provenance_dtos(
    values: tuple[AShareComponentProvenance, ...],
) -> tuple[AShareComponentProvenanceDTO, ...]:
    return tuple(AShareComponentProvenanceDTO.from_result(value) for value in values)


def validate_data_provenance(
    data: object,
    provenance: tuple[AShareComponentProvenance, ...],
) -> None:
    """Require a successful product DTO to mirror retained component metadata."""
    if getattr(data, "provenance", None) != provenance_dtos(provenance):
        raise DataContractError(
            "successful product data provenance must match result provenance",
            details={"field": "provenance", "rule": "result_data_identity"},
        )


def component_provenance(
    component: AShareComponentType,
    meta: ProviderResultMeta,
    value: object,
    *,
    empty_reliability: ReliabilityLevel | None = None,
    empty_authoritative: bool | None = None,
    is_derived: bool = False,
) -> AShareComponentProvenance:
    """Build product semantics while the typed component value is still present."""
    items: tuple[object, ...] = value if isinstance(value, tuple) else (value,)
    reliabilities = [
        reliability
        for item in items
        if isinstance(
            reliability := getattr(item, "reliability", None), ReliabilityLevel
        )
    ]
    authorities = [
        authority
        for item in items
        if type(authority := getattr(item, "is_authoritative", None)) is bool
    ]
    reliability = (
        max(reliabilities, key=_RELIABILITY_ORDER.__getitem__)
        if reliabilities
        else empty_reliability
    )
    authority = (
        (False not in authorities)
        if authorities
        else empty_authoritative
    )
    return AShareComponentProvenance(
        component=component,
        meta=meta,
        reliability=reliability,
        is_authoritative=authority,
        is_derived=is_derived,
    )


def validate_provenance_tuple(
    values: object,
    *,
    order: tuple[AShareComponentType, ...] | None = None,
) -> tuple[AShareComponentProvenance, ...]:
    if not isinstance(values, tuple):
        raise DataContractError("provenance must be a tuple")
    if any(not isinstance(item, AShareComponentProvenance) for item in values):
        raise DataContractError("provenance elements must be AShareComponentProvenance")
    components = tuple(item.component for item in values)
    if len(set(components)) != len(components):
        raise DataContractError("provenance components must be unique")
    if order is not None:
        positions = {component: index for index, component in enumerate(order)}
        if any(component not in positions for component in components):
            raise DataContractError("unexpected provenance component")
        if tuple(sorted(components, key=positions.__getitem__)) != components:
            raise DataContractError("provenance component order is invalid")
    return values
