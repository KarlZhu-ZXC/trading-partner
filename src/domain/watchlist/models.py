"""Watchlist hub domain models and invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from domain.common.enums import Market
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.watchlist.enums import (
    WatchlistGroupType,
    WatchlistMutationAction,
    WatchlistMutationStatus,
    WatchlistSource,
)

# Stable format ``<prefix>_<uuid7>``.
_UUID7_TOKEN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


def _require_entity_id(value: str, *, field: str, prefix: EntityIdPrefix) -> None:
    if not re.fullmatch(rf"^{re.escape(prefix.value)}_{_UUID7_TOKEN}$", value):
        raise DataContractError(
            f"{field} must match {prefix.value}_<uuid7>",
            details={
                "field": field,
                "value": value,
            },
        )


def _require_non_blank(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(
            f"{field} must be a non-empty string",
            details={"field": field, "value": value},
        )
    return value.strip()


def _require_optional_str(
    value: str | None,
    *,
    field: str,
    allow_blank: bool = False,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string or None",
            details={"field": field, "type": type(value).__name__},
        )
    if not allow_blank and not value.strip():
        raise DataContractError(
            f"{field} must be a non-empty string",
            details={"field": field},
        )
    return value


def _require_active_consistent(
    *, active: bool, removed_at: datetime | None, context: str
) -> None:
    if active and removed_at is not None:
        raise DataContractError(
            f"{context}: active=True requires removed_at is None",
            details={"context": context},
        )
    if not active and removed_at is None:
        raise DataContractError(
            f"{context}: active=False requires removed_at is not None",
            details={"context": context},
        )


WATCHLIST_CONFIRMER_ROLES = frozenset({"user", "external_agent"})


@dataclass(frozen=True, slots=True)
class WatchlistGroup:
    group_id: str
    source: WatchlistSource
    source_group_key: str
    name: str
    group_type: WatchlistGroupType
    writable: bool
    active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None
    last_synced_at: datetime

    def __post_init__(self) -> None:
        _require_entity_id(self.group_id, field="group_id", prefix=EntityIdPrefix.WATCH_GROUP)
        if not isinstance(self.source, WatchlistSource):
            raise DataContractError("source must be WatchlistSource")
        if not isinstance(self.group_type, WatchlistGroupType):
            raise DataContractError("group_type must be WatchlistGroupType")

        if not isinstance(self.writable, bool):
            raise DataContractError("writable must be bool")
        if not isinstance(self.active, bool):
            raise DataContractError("active must be bool")
        _require_active_consistent(
            active=self.active,
            removed_at=self.removed_at,
            context="WatchlistGroup",
        )

        source_group_key = _require_non_blank(self.source_group_key, field="source_group_key")
        name = _require_non_blank(self.name, field="name")

        object.__setattr__(self, "source_group_key", source_group_key)
        object.__setattr__(self, "name", name)

        require_aware_datetime(self.first_seen_at, field_name="first_seen_at")
        require_aware_datetime(self.last_seen_at, field_name="last_seen_at")
        require_aware_datetime(self.last_synced_at, field_name="last_synced_at")
        if self.removed_at is not None:
            require_aware_datetime(self.removed_at, field_name="removed_at")

        if self.last_seen_at < self.first_seen_at:
            raise DataContractError("last_seen_at must be >= first_seen_at")
        if self.last_synced_at < self.last_seen_at:
            raise DataContractError("last_synced_at must be >= last_seen_at")
        if self.removed_at is not None and self.removed_at < self.last_seen_at:
            raise DataContractError("removed_at must be >= last_seen_at")


@dataclass(frozen=True, slots=True)
class WatchlistMembership:
    membership_id: str
    group_id: str
    source: WatchlistSource
    provider_code: str
    instrument_id: str | None
    display_name: str
    provider_asset_type: str | None
    research_supported: bool
    active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None
    last_synced_at: datetime

    def __post_init__(self) -> None:
        _require_entity_id(
            self.membership_id,
            field="membership_id",
            prefix=EntityIdPrefix.WATCH_MEMBERSHIP,
        )
        _require_entity_id(
            self.group_id,
            field="group_id",
            prefix=EntityIdPrefix.WATCH_GROUP,
        )
        if not isinstance(self.source, WatchlistSource):
            raise DataContractError("source must be WatchlistSource")
        if not isinstance(self.research_supported, bool):
            raise DataContractError("research_supported must be bool")
        if not isinstance(self.active, bool):
            raise DataContractError("active must be bool")

        _require_active_consistent(
            active=self.active,
            removed_at=self.removed_at,
            context="WatchlistMembership",
        )

        provider_code = _require_non_blank(self.provider_code, field="provider_code")
        display_name = _require_non_blank(self.display_name, field="display_name")
        provider_asset_type = _require_optional_str(
            self.provider_asset_type,
            field="provider_asset_type",
        )
        instrument_id = _require_optional_str(
            self.instrument_id,
            field="instrument_id",
            allow_blank=False,
        )

        object.__setattr__(self, "provider_code", provider_code)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "provider_asset_type", provider_asset_type)
        object.__setattr__(self, "instrument_id", instrument_id)

        require_aware_datetime(self.first_seen_at, field_name="first_seen_at")
        require_aware_datetime(self.last_seen_at, field_name="last_seen_at")
        require_aware_datetime(self.last_synced_at, field_name="last_synced_at")
        if self.removed_at is not None:
            require_aware_datetime(self.removed_at, field_name="removed_at")

        if self.last_seen_at < self.first_seen_at:
            raise DataContractError("last_seen_at must be >= first_seen_at")
        if self.last_synced_at < self.last_seen_at:
            raise DataContractError("last_synced_at must be >= last_seen_at")
        if self.removed_at is not None and self.removed_at < self.last_seen_at:
            raise DataContractError("removed_at must be >= last_seen_at")

        if self.research_supported:
            if instrument_id is None:
                raise DataContractError(
                    "research_supported membership requires instrument_id",
                    details={"provider_code": self.provider_code},
                )
            _, market, _ = parse_instrument_id(instrument_id)
            if market not in {Market.A_SHARE, Market.US}:
                raise DataContractError(
                    "research_supported membership requires A_SHARE or US instrument_id",
                    details={
                        "provider_code": self.provider_code,
                        "instrument_id": instrument_id,
                    },
                )


@dataclass(frozen=True, slots=True)
class WatchlistMutation:
    mutation_id: str
    idempotency_key: str
    action: WatchlistMutationAction
    source: WatchlistSource
    group_name: str
    provider_code: str
    requested_by: str
    status: WatchlistMutationStatus
    requested_at: datetime
    completed_at: datetime | None
    error_code: str | None

    def __post_init__(self) -> None:
        _require_entity_id(
            self.mutation_id,
            field="mutation_id",
            prefix=EntityIdPrefix.WATCH_MUTATION,
        )
        if not isinstance(self.action, WatchlistMutationAction):
            raise DataContractError("action must be WatchlistMutationAction")
        if not isinstance(self.source, WatchlistSource):
            raise DataContractError("source must be WatchlistSource")
        if self.requested_by not in WATCHLIST_CONFIRMER_ROLES:
            raise DataContractError(
                "requested_by must be user or external_agent",
                details={"requested_by": self.requested_by},
            )
        if not isinstance(self.status, WatchlistMutationStatus):
            raise DataContractError("status must be WatchlistMutationStatus")

        _require_non_blank(self.group_name, field="group_name")
        _require_non_blank(self.provider_code, field="provider_code")
        idempotency_key = _require_non_blank(
            self.idempotency_key,
            field="idempotency_key",
        )
        error_code = _require_optional_str(self.error_code, field="error_code")

        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "error_code", error_code)

        require_aware_datetime(self.requested_at, field_name="requested_at")
        if self.completed_at is not None:
            require_aware_datetime(self.completed_at, field_name="completed_at")
            if self.completed_at < self.requested_at:
                raise DataContractError(
                    "completed_at must be >= requested_at",
                    details={
                        "requested_at": self.requested_at,
                        "completed_at": self.completed_at,
                    },
                )

        if self.status is WatchlistMutationStatus.PENDING:
            if self.completed_at is not None:
                raise DataContractError("PENDING mutation must not set completed_at")
            if self.error_code is not None:
                raise DataContractError("PENDING mutation must not set error_code")
            return

        if self.completed_at is None:
            raise DataContractError(
                "non-PENDING mutation must set completed_at",
                details={"mutation_id": self.mutation_id},
            )
        if self.status in {
            WatchlistMutationStatus.PARTIAL,
            WatchlistMutationStatus.FAILED,
        }:
            if self.error_code is None:
                raise DataContractError(
                    f"{self.status.value} mutation requires error_code",
                    details={"mutation_id": self.mutation_id},
                )
            return
        if self.error_code is not None:
            raise DataContractError(
                "only PARTIAL or FAILED mutations may set error_code",
                details={"mutation_id": self.mutation_id},
            )
