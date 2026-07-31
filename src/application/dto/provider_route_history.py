"""Secret-safe durable Provider routing receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from application.dto.provider_routing import ProviderAttemptRecord
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Market,
    SourceRole,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

_SAFE_OPERATION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


@dataclass(frozen=True, slots=True)
class ProviderRouteReceipt:
    """One bounded Router execution receipt without request or payload content."""

    route_id: str
    recorded_at: datetime
    market: Market
    category: DataCategory
    operation_name: str
    instrument_id: str | None
    criticality: DataCriticality
    requested_chain: tuple[VendorId, ...]
    ok: bool
    selected_vendor: VendorId | None
    selected_role: SourceRole | None
    cache_disposition: CacheDisposition | None
    attempts: tuple[ProviderAttemptRecord, ...]
    warning_codes: tuple[str, ...]
    final_error_code: str | None

    def __post_init__(self) -> None:
        if not self.route_id.startswith("provider_route_"):
            raise DataContractError(
                "route_id must use provider_route prefix",
                details={"field": "route_id", "rule": "prefix"},
            )
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        for field, value, expected in (
            ("market", self.market, Market),
            ("category", self.category, DataCategory),
            ("criticality", self.criticality, DataCriticality),
        ):
            if not isinstance(value, expected):
                raise DataContractError(
                    f"{field} has invalid type",
                    details={"field": field, "type": type(value).__name__},
                )
        if _SAFE_OPERATION_RE.fullmatch(self.operation_name) is None:
            raise DataContractError(
                "operation_name has invalid format",
                details={"field": "operation_name", "rule": "safe_operation"},
            )
        if self.instrument_id is not None and not self.instrument_id:
            raise DataContractError(
                "instrument_id must be non-empty when set",
                details={"field": "instrument_id"},
            )
        if not isinstance(self.requested_chain, tuple) or any(
            not isinstance(item, VendorId) for item in self.requested_chain
        ):
            raise DataContractError(
                "requested_chain must contain VendorId values",
                details={"field": "requested_chain"},
            )
        if not isinstance(self.ok, bool):
            raise DataContractError("ok must be bool", details={"field": "ok"})
        if self.selected_vendor is not None and not isinstance(
            self.selected_vendor, VendorId
        ):
            raise DataContractError(
                "selected_vendor must be VendorId or None",
                details={"field": "selected_vendor"},
            )
        if self.selected_role is not None and not isinstance(self.selected_role, SourceRole):
            raise DataContractError(
                "selected_role must be SourceRole or None",
                details={"field": "selected_role"},
            )
        if self.cache_disposition is not None and not isinstance(
            self.cache_disposition, CacheDisposition
        ):
            raise DataContractError(
                "cache_disposition must be CacheDisposition or None",
                details={"field": "cache_disposition"},
            )
        if not isinstance(self.attempts, tuple) or any(
            not isinstance(item, ProviderAttemptRecord) for item in self.attempts
        ):
            raise DataContractError(
                "attempts must contain ProviderAttemptRecord values",
                details={"field": "attempts"},
            )
        if any(item.message is not None for item in self.attempts):
            raise DataContractError(
                "durable route attempts must not store messages",
                details={"field": "attempts", "rule": "no_free_text"},
            )
        if not isinstance(self.warning_codes, tuple) or any(
            not isinstance(code, str) or _SAFE_CODE_RE.fullmatch(code) is None
            for code in self.warning_codes
        ):
            raise DataContractError(
                "warning_codes must contain safe codes",
                details={"field": "warning_codes"},
            )
        if self.final_error_code is not None and (
            _SAFE_CODE_RE.fullmatch(self.final_error_code) is None
        ):
            raise DataContractError(
                "final_error_code must be a safe code",
                details={"field": "final_error_code"},
            )
        if self.ok:
            if self.selected_vendor is None or self.selected_role is None:
                raise DataContractError(
                    "successful route requires selected provider metadata",
                    details={"field": "selected_vendor", "rule": "success_metadata"},
                )
            if self.final_error_code is not None:
                raise DataContractError(
                    "successful route cannot have final_error_code",
                    details={"field": "final_error_code"},
                )
        else:
            if self.selected_vendor is not None or self.selected_role is not None:
                raise DataContractError(
                    "failed route cannot have selected provider metadata",
                    details={"field": "selected_vendor", "rule": "failure_metadata"},
                )
            if self.final_error_code is None:
                raise DataContractError(
                    "failed route requires final_error_code",
                    details={"field": "final_error_code", "rule": "failure_metadata"},
                )

    @property
    def used_fallback(self) -> bool:
        return self.selected_role is SourceRole.FALLBACK

    @property
    def used_cache(self) -> bool:
        return self.cache_disposition in {
            CacheDisposition.HIT,
            CacheDisposition.STALE_HIT,
        }
