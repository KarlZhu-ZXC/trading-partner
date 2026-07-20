"""Provider routing DTOs (Phase 1D D6a).

Frozen slotted dataclasses for provider call metadata, success wrappers,
attempt records, router results, and tool-level data policy.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from application.dto.tool_envelope import WarningInfo
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    ProviderAttemptOutcome,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime

# Shared safe grammar for warning codes and attempt error codes (no free text).
_SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _require_optional_nonnegative_int(value: int | None, *, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataContractError(
            f"{field_name} must be None or a non-negative int",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value < 0:
        raise DataContractError(
            f"{field_name} must be nonnegative",
            details={"field": field_name},
        )


def _require_nonnegative_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataContractError(
            f"{field_name} must be an int",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value < 0:
        raise DataContractError(
            f"{field_name} must be nonnegative",
            details={"field": field_name},
        )


def _require_error_code(error_code: str | None) -> None:
    if error_code is None:
        return
    if not isinstance(error_code, str) or not _SAFE_CODE_RE.fullmatch(error_code):
        # Never echo the rejected value (may contain secrets or garbage).
        raise DataContractError(
            "error_code must match ^[A-Z][A-Z0-9_]{0,127}$",
            details={"field": "error_code"},
        )


def _require_warning_codes(warnings: object) -> None:
    if not isinstance(warnings, tuple):
        raise DataContractError(
            "warnings must be a tuple of strings",
            details={"field": "warnings", "type": type(warnings).__name__},
        )
    for idx, code in enumerate(warnings):
        if not isinstance(code, str) or not _SAFE_CODE_RE.fullmatch(code):
            # Never echo the rejected value (may contain secrets or free text).
            raise DataContractError(
                "warning codes must match ^[A-Z][A-Z0-9_]{0,127}$",
                details={"field": "warnings", "index": idx, "rule": "safe_code"},
            )


@dataclass(frozen=True, slots=True)
class ProviderResultMeta:
    """Metadata attached to a successful provider call at the app/domain edge."""

    vendor: VendorId
    category: DataCategory
    role: SourceRole
    as_of: datetime
    fetched_at: datetime
    freshness: Freshness
    session: TradingSession
    latency_ms: int | None
    cache_disposition: CacheDisposition
    adjustment: AdjustmentMethod | None
    data_delay_seconds: int | None
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.vendor, VendorId):
            raise DataContractError(
                "vendor must be a VendorId",
                details={"field": "vendor", "type": type(self.vendor).__name__},
            )
        if not isinstance(self.category, DataCategory):
            raise DataContractError(
                "category must be a DataCategory",
                details={"field": "category", "type": type(self.category).__name__},
            )
        if not isinstance(self.role, SourceRole):
            raise DataContractError(
                "role must be a SourceRole",
                details={"field": "role", "type": type(self.role).__name__},
            )
        if not isinstance(self.freshness, Freshness):
            raise DataContractError(
                "freshness must be a Freshness",
                details={"field": "freshness", "type": type(self.freshness).__name__},
            )
        if not isinstance(self.session, TradingSession):
            raise DataContractError(
                "session must be a TradingSession",
                details={"field": "session", "type": type(self.session).__name__},
            )
        if not isinstance(self.cache_disposition, CacheDisposition):
            raise DataContractError(
                "cache_disposition must be a CacheDisposition",
                details={
                    "field": "cache_disposition",
                    "type": type(self.cache_disposition).__name__,
                },
            )
        if self.adjustment is not None and not isinstance(
            self.adjustment, AdjustmentMethod
        ):
            raise DataContractError(
                "adjustment must be AdjustmentMethod or None",
                details={
                    "field": "adjustment",
                    "type": type(self.adjustment).__name__,
                },
            )
        require_aware_datetime(self.as_of, field_name="as_of")
        require_aware_datetime(self.fetched_at, field_name="fetched_at")
        _require_optional_nonnegative_int(self.latency_ms, field_name="latency_ms")
        _require_optional_nonnegative_int(
            self.data_delay_seconds, field_name="data_delay_seconds"
        )
        _require_warning_codes(self.warnings)


@dataclass(frozen=True, slots=True)
class ProviderSuccess[T]:
    """Successful provider result: non-null value + metadata."""

    value: T
    meta: ProviderResultMeta

    def __post_init__(self) -> None:
        if self.value is None:
            raise DataContractError(
                "ProviderSuccess.value must not be None; use NoMarketData for empty",
                details={"field": "value"},
            )
        if not isinstance(self.meta, ProviderResultMeta):
            raise DataContractError(
                "meta must be a ProviderResultMeta",
                details={"field": "meta", "type": type(self.meta).__name__},
            )


@dataclass(frozen=True, slots=True)
class ProviderAttemptRecord:
    """One vendor attempt within a router execution (success, skip, or failure)."""

    vendor: VendorId
    outcome: ProviderAttemptOutcome
    error_code: str | None
    duration_ms: int
    message: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.vendor, VendorId):
            raise DataContractError(
                "vendor must be a VendorId",
                details={"field": "vendor", "type": type(self.vendor).__name__},
            )
        if not isinstance(self.outcome, ProviderAttemptOutcome):
            raise DataContractError(
                "outcome must be a ProviderAttemptOutcome",
                details={"field": "outcome", "type": type(self.outcome).__name__},
            )
        _require_error_code(self.error_code)
        _require_nonnegative_int(self.duration_ms, field_name="duration_ms")
        if self.message is not None and not isinstance(self.message, str):
            raise DataContractError(
                "message must be str or None",
                details={"field": "message", "type": type(self.message).__name__},
            )


@dataclass(frozen=True, slots=True)
class RouterExecutionResult[T]:
    """Outcome of a ProviderRouter.execute call (success or typed failure)."""

    value: T | None
    ok: bool
    criticality: DataCriticality
    meta: ProviderResultMeta | None
    attempts: tuple[ProviderAttemptRecord, ...]
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise DataContractError(
                "ok must be a bool",
                details={"field": "ok", "type": type(self.ok).__name__},
            )
        if not isinstance(self.criticality, DataCriticality):
            raise DataContractError(
                "criticality must be a DataCriticality",
                details={
                    "field": "criticality",
                    "type": type(self.criticality).__name__,
                },
            )
        if not isinstance(self.attempts, tuple):
            raise DataContractError(
                "attempts must be a tuple",
                details={"field": "attempts", "type": type(self.attempts).__name__},
            )
        for idx, attempt in enumerate(self.attempts):
            if not isinstance(attempt, ProviderAttemptRecord):
                raise DataContractError(
                    "attempts elements must be ProviderAttemptRecord",
                    details={"field": "attempts", "index": idx},
                )
        if not isinstance(self.warnings, tuple):
            raise DataContractError(
                "warnings must be a tuple",
                details={"field": "warnings", "type": type(self.warnings).__name__},
            )
        for idx, warning in enumerate(self.warnings):
            if not isinstance(warning, WarningInfo):
                raise DataContractError(
                    "warnings elements must be WarningInfo",
                    details={"field": "warnings", "index": idx},
                )
        if self.ok:
            if self.value is None:
                raise DataContractError(
                    "RouterExecutionResult ok=True requires non-null value",
                    details={"field": "value", "rule": "ok_true_value_required"},
                )
            if self.meta is None:
                raise DataContractError(
                    "RouterExecutionResult ok=True requires non-null meta",
                    details={"field": "meta", "rule": "ok_true_meta_required"},
                )
            if self.error is not None:
                raise DataContractError(
                    "RouterExecutionResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            if not isinstance(self.meta, ProviderResultMeta):
                raise DataContractError(
                    "meta must be a ProviderResultMeta",
                    details={"field": "meta", "type": type(self.meta).__name__},
                )
        else:
            if self.value is not None:
                raise DataContractError(
                    "RouterExecutionResult ok=False requires value is None",
                    details={"field": "value", "rule": "ok_false_value_none"},
                )
            if self.meta is not None:
                raise DataContractError(
                    "RouterExecutionResult ok=False requires meta is None",
                    details={"field": "meta", "rule": "ok_false_meta_none"},
                )
            if self.error is None:
                raise DataContractError(
                    "RouterExecutionResult ok=False requires non-null error",
                    details={"field": "error", "rule": "ok_false_error_required"},
                )
            if not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "error must be a TradingPartnerError",
                    details={"field": "error", "type": type(self.error).__name__},
                )


@dataclass(frozen=True, slots=True)
class ToolDataPolicy:
    """Per-tool required/optional categories and optional vendor-chain overrides."""

    tool_name: str
    required_categories: tuple[DataCategory, ...]
    optional_categories: tuple[DataCategory, ...]
    category_chain_overrides: Mapping[DataCategory, tuple[VendorId, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise DataContractError(
                "tool_name must be a non-empty string",
                details={"field": "tool_name"},
            )
        required = self._normalize_category_tuple(
            self.required_categories, field_name="required_categories"
        )
        optional = self._normalize_category_tuple(
            self.optional_categories, field_name="optional_categories"
        )
        required_set = set(required)
        optional_set = set(optional)
        overlap = required_set & optional_set
        if overlap:
            raise DataContractError(
                "required_categories and optional_categories must not overlap",
                details={
                    "field": "required_categories|optional_categories",
                    "rule": "no_overlap",
                    "overlap_count": len(overlap),
                },
            )
        declared = required_set | optional_set
        frozen_overrides = self._normalize_overrides(
            self.category_chain_overrides, declared=declared
        )
        object.__setattr__(self, "required_categories", required)
        object.__setattr__(self, "optional_categories", optional)
        object.__setattr__(
            self, "category_chain_overrides", MappingProxyType(frozen_overrides)
        )

    @staticmethod
    def _normalize_category_tuple(
        value: object, *, field_name: str
    ) -> tuple[DataCategory, ...]:
        if not isinstance(value, tuple):
            raise DataContractError(
                f"{field_name} must be a tuple of DataCategory",
                details={"field": field_name, "type": type(value).__name__},
            )
        normalized: list[DataCategory] = []
        seen: set[DataCategory] = set()
        for idx, item in enumerate(value):
            if not isinstance(item, DataCategory):
                raise DataContractError(
                    f"{field_name} elements must be DataCategory",
                    details={
                        "field": field_name,
                        "index": idx,
                        "type": type(item).__name__,
                    },
                )
            if item in seen:
                raise DataContractError(
                    f"{field_name} must not contain duplicate categories",
                    details={"field": field_name, "rule": "no_duplicate_categories"},
                )
            seen.add(item)
            normalized.append(item)
        return tuple(normalized)

    @staticmethod
    def _normalize_overrides(
        value: object,
        *,
        declared: set[DataCategory],
    ) -> dict[DataCategory, tuple[VendorId, ...]]:
        if not isinstance(value, Mapping):
            raise DataContractError(
                "category_chain_overrides must be a Mapping",
                details={
                    "field": "category_chain_overrides",
                    "type": type(value).__name__,
                },
            )
        frozen: dict[DataCategory, tuple[VendorId, ...]] = {}
        for key, chain in value.items():
            if not isinstance(key, DataCategory):
                raise DataContractError(
                    "category_chain_overrides keys must be DataCategory",
                    details={
                        "field": "category_chain_overrides",
                        "rule": "key_type",
                        "type": type(key).__name__,
                    },
                )
            if key not in declared:
                raise DataContractError(
                    "category_chain_overrides may only reference "
                    "required or optional categories",
                    details={
                        "field": "category_chain_overrides",
                        "rule": "override_category_not_declared",
                        "category": key.value,
                    },
                )
            if not isinstance(chain, (tuple, list)):
                raise DataContractError(
                    "category_chain_overrides values must be sequences of VendorId",
                    details={
                        "field": "category_chain_overrides",
                        "rule": "chain_type",
                        "category": key.value,
                        "type": type(chain).__name__,
                    },
                )
            vendors: list[VendorId] = []
            seen_vendors: set[VendorId] = set()
            for idx, vendor in enumerate(chain):
                if not isinstance(vendor, VendorId):
                    raise DataContractError(
                        "category_chain_overrides chain elements must be VendorId",
                        details={
                            "field": "category_chain_overrides",
                            "rule": "vendor_type",
                            "category": key.value,
                            "index": idx,
                            "type": type(vendor).__name__,
                        },
                    )
                if vendor in seen_vendors:
                    raise DataContractError(
                        "category_chain_overrides chain must not duplicate vendors",
                        details={
                            "field": "category_chain_overrides",
                            "rule": "no_duplicate_vendors",
                            "category": key.value,
                        },
                    )
                seen_vendors.add(vendor)
                vendors.append(vendor)
            frozen[key] = tuple(vendors)
        return frozen
