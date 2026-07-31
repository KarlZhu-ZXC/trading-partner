"""Phase 2 Watchlist Hub MCP request and response DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.watchlist.enums import (
    WatchlistGroupType,
    WatchlistMutationAction,
    WatchlistMutationStatus,
    WatchlistSource,
)
from domain.watchlist.models import (
    WatchlistGroup,
    WatchlistMembership,
    WatchlistMutation,
)


class _FrozenDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


def _strip_required(value: object, *, field: str) -> object:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(f"{field} must not be blank")
    return value


class WatchlistGetGroupsInput(_FrozenDTO):
    refresh: bool = False
    include_inactive: bool = False


class WatchlistGetItemsInput(_FrozenDTO):
    group_name: str | None = None
    refresh: bool = False
    include_inactive: bool = False
    limit: int = Field(default=200, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @field_validator("group_name", mode="before")
    @classmethod
    def _normalize_group(cls, value: object) -> object:
        if value is None:
            return None
        return _strip_required(value, field="group_name")


class WatchlistAddInput(_FrozenDTO):
    group_name: str | None = None
    instrument_id: str
    display_name: str | None = None
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("group_name", "display_name", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object, info: object) -> object:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "value")
        return _strip_required(value, field=field_name)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> object:
        return _strip_required(value, field="idempotency_key")

    @field_validator("instrument_id", mode="before")
    @classmethod
    def _validate_instrument_id(cls, value: object) -> object:
        value = _strip_required(value, field="instrument_id")
        if isinstance(value, str):
            parse_instrument_id(value)
        return value


class WatchlistRemoveInput(_FrozenDTO):
    membership_id: str
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("membership_id", "idempotency_key", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object, info: object) -> object:
        return _strip_required(value, field=getattr(info, "field_name", "value"))


class WatchlistGroupDTO(_FrozenDTO):
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

    @model_validator(mode="after")
    def _aware_times(self) -> Self:
        for name in ("first_seen_at", "last_seen_at", "last_synced_at"):
            require_aware_datetime(getattr(self, name), field_name=name)
        if self.removed_at is not None:
            require_aware_datetime(self.removed_at, field_name="removed_at")
        return self

    @classmethod
    def from_domain(cls, value: WatchlistGroup) -> WatchlistGroupDTO:
        return cls(
            group_id=value.group_id,
            source=value.source,
            source_group_key=value.source_group_key,
            name=value.name,
            group_type=value.group_type,
            writable=value.writable,
            active=value.active,
            first_seen_at=value.first_seen_at,
            last_seen_at=value.last_seen_at,
            removed_at=value.removed_at,
            last_synced_at=value.last_synced_at,
        )


class WatchlistMembershipDTO(_FrozenDTO):
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
    research_watchlist_item_ids: tuple[str, ...] = ()
    investment_case_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _aware_times(self) -> Self:
        for name in ("first_seen_at", "last_seen_at", "last_synced_at"):
            require_aware_datetime(getattr(self, name), field_name=name)
        if self.removed_at is not None:
            require_aware_datetime(self.removed_at, field_name="removed_at")
        return self

    @classmethod
    def from_domain(
        cls,
        value: WatchlistMembership,
        *,
        research_watchlist_item_ids: tuple[str, ...] = (),
        investment_case_ids: tuple[str, ...] = (),
    ) -> WatchlistMembershipDTO:
        return cls(
            membership_id=value.membership_id,
            group_id=value.group_id,
            source=value.source,
            provider_code=value.provider_code,
            instrument_id=value.instrument_id,
            display_name=value.display_name,
            provider_asset_type=value.provider_asset_type,
            research_supported=value.research_supported,
            active=value.active,
            first_seen_at=value.first_seen_at,
            last_seen_at=value.last_seen_at,
            removed_at=value.removed_at,
            last_synced_at=value.last_synced_at,
            research_watchlist_item_ids=research_watchlist_item_ids,
            investment_case_ids=investment_case_ids,
        )


class WatchlistMutationDTO(_FrozenDTO):
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

    @model_validator(mode="after")
    def _aware_times(self) -> Self:
        require_aware_datetime(self.requested_at, field_name="requested_at")
        if self.completed_at is not None:
            require_aware_datetime(self.completed_at, field_name="completed_at")
        return self

    @classmethod
    def from_domain(cls, value: WatchlistMutation) -> WatchlistMutationDTO:
        return cls(
            mutation_id=value.mutation_id,
            idempotency_key=value.idempotency_key,
            action=value.action,
            source=value.source,
            group_name=value.group_name,
            provider_code=value.provider_code,
            requested_by=value.requested_by,
            status=value.status,
            requested_at=value.requested_at,
            completed_at=value.completed_at,
            error_code=value.error_code,
        )


class WatchlistGroupsDTO(_FrozenDTO):
    source: WatchlistSource
    groups: tuple[WatchlistGroupDTO, ...]


class WatchlistItemsDTO(_FrozenDTO):
    source: WatchlistSource
    group: WatchlistGroupDTO
    items: tuple[WatchlistMembershipDTO, ...]
    total_returned: int = Field(ge=0)
    total_count: int = Field(ge=0)
    has_more: bool
    group_was_defaulted: bool


class WatchlistSyncResultDTO(_FrozenDTO):
    """Machine-readable summary for a full upstream-to-database refresh."""

    source: WatchlistSource
    groups_synced: int = Field(ge=0)
    membership_relations_synced: int = Field(ge=0)
    unique_provider_codes: int = Field(ge=0)
    research_supported_unique: int = Field(ge=0)
    unsupported_unique: int = Field(ge=0)


class WatchlistMutationResultDTO(_FrozenDTO):
    mutation: WatchlistMutationDTO
    membership: WatchlistMembershipDTO
