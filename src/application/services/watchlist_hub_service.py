"""Phase 2 durable Watchlist Hub orchestration."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import TypeVar

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.dto.watchlist_hub import (
    WatchlistAddInput,
    WatchlistGetGroupsInput,
    WatchlistGetItemsInput,
    WatchlistGroupDTO,
    WatchlistGroupsDTO,
    WatchlistItemsDTO,
    WatchlistMembershipDTO,
    WatchlistMutationDTO,
    WatchlistMutationResultDTO,
    WatchlistRemoveInput,
    WatchlistSyncResultDTO,
)
from application.dto.watchlist_source import (
    WatchlistSourceGroup,
    WatchlistSourceMembership,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.ports.watchlist_hub_unit_of_work import WatchlistHubUnitOfWork
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from domain.common.enums import Freshness, Market, SourceRole
from domain.common.errors import (
    DataContractError,
    DuplicateIdempotencyKey,
    PartialDataError,
    PersistenceError,
    TradingPartnerError,
    WatchlistGroupNotFound,
)
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.watchlist.enums import (
    WatchlistMutationAction,
    WatchlistMutationStatus,
    WatchlistSource,
)
from domain.watchlist.models import (
    WatchlistGroup,
    WatchlistMembership,
    WatchlistMutation,
)

WatchlistUowFactory = Callable[[], WatchlistHubUnitOfWork]
ResearchUowFactory = Callable[[], ResearchUnitOfWork]
T = TypeVar("T")


class WatchlistHubService:
    """Synchronize one active upstream with durable lifecycle/history rows."""

    def __init__(
        self,
        *,
        provider: WatchlistSourceProvider,
        uow_factory: WatchlistUowFactory,
        research_uow_factory: ResearchUowFactory,
        default_group: str,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._provider = provider
        self._uow_factory = uow_factory
        self._research_uow_factory = research_uow_factory
        self._default_group = default_group
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    @property
    def source(self) -> WatchlistSource:
        return self._provider.source

    async def get_groups(
        self, request: WatchlistGetGroupsInput
    ) -> ToolEnvelope[WatchlistGroupsDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        refreshed = False
        warning: WarningInfo | None = None
        try:
            if request.refresh:
                try:
                    upstream = await self._provider.list_groups()
                    self._persist_group_snapshot(upstream, request_id=request_id)
                    refreshed = True
                except Exception as exc:  # noqa: BLE001
                    durable = self._list_groups(include_inactive=request.include_inactive)
                    if not durable:
                        return self._failure(request_id, exc)
                    warning = self._stale_warning(exc, scope="groups")
            groups = self._list_groups(include_inactive=request.include_inactive)
            now = self._clock.now()
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH if refreshed else Freshness.UNKNOWN,
                sources=(self._source_reference(now),),
                data=WatchlistGroupsDTO(
                    source=self.source,
                    groups=tuple(WatchlistGroupDTO.from_domain(item) for item in groups),
                ),
                degraded=warning is not None,
                warnings=() if warning is None else (warning,),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, exc)

    async def sync_all(self) -> ToolEnvelope[WatchlistSyncResultDTO]:
        """Refresh every upstream group and membership into durable storage.

        This operational entry point is intentionally not an MCP tool. It exists
        for explicit CLI/scheduler jobs and never performs an upstream mutation.
        """

        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            groups = await self._provider.list_groups()
            memberships = await self._provider.list_memberships(None)
            known_group_names = {group.name for group in groups}
            unknown_group_names = sorted(
                {item.group_name for item in memberships} - known_group_names
            )
            if unknown_group_names:
                raise DataContractError(
                    "watchlist source returned memberships for unknown groups",
                    details={"group_names": unknown_group_names},
                )

            self._persist_group_snapshot(groups, request_id=request_id)
            memberships_by_group: dict[str, list[WatchlistSourceMembership]] = {
                group.name: [] for group in groups
            }
            for membership in memberships:
                memberships_by_group[membership.group_name].append(membership)
            for group in groups:
                self._persist_membership_snapshot(
                    group.name,
                    tuple(memberships_by_group[group.name]),
                    request_id=request_id,
                )

            unique_codes = {item.provider_code for item in memberships}
            supported_codes = {
                item.provider_code for item in memberships if item.research_supported
            }
            now = self._clock.now()
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH,
                sources=(self._source_reference(now),),
                data=WatchlistSyncResultDTO(
                    source=self.source,
                    groups_synced=len(groups),
                    membership_relations_synced=len(memberships),
                    unique_provider_codes=len(unique_codes),
                    research_supported_unique=len(supported_codes),
                    unsupported_unique=len(unique_codes - supported_codes),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, exc)

    async def get_items(
        self, request: WatchlistGetItemsInput
    ) -> ToolEnvelope[WatchlistItemsDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        group_name = request.group_name or self._default_group
        refreshed = False
        warning: WarningInfo | None = None
        try:
            if request.refresh:
                try:
                    groups = await self._provider.list_groups()
                    self._persist_group_snapshot(groups, request_id=request_id)
                    upstream = await self._provider.list_memberships(group_name)
                    self._persist_membership_snapshot(
                        group_name,
                        upstream,
                        request_id=request_id,
                    )
                    refreshed = True
                except Exception as exc:  # noqa: BLE001
                    durable_group, _ = self._read_group_items(
                        group_name,
                        include_inactive=request.include_inactive,
                        limit=request.limit,
                        offset=request.offset,
                    )
                    if durable_group is None:
                        return self._failure(request_id, exc)
                    warning = self._stale_warning(exc, scope="memberships")
            group, memberships = self._read_group_items(
                group_name,
                include_inactive=request.include_inactive,
                limit=request.limit,
                offset=request.offset,
            )
            if group is None:
                raise WatchlistGroupNotFound(
                    f"Watchlist group not found: {group_name}",
                    details={"group_name": group_name},
                )
            items = self._membership_dtos(memberships)
            now = self._clock.now()
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH if refreshed else Freshness.UNKNOWN,
                sources=(self._source_reference(now),),
                data=WatchlistItemsDTO(
                    source=self.source,
                    group=WatchlistGroupDTO.from_domain(group),
                    items=items,
                    total_returned=len(items),
                ),
                degraded=warning is not None,
                warnings=() if warning is None else (warning,),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, exc)

    async def add(
        self, request: WatchlistAddInput
    ) -> ToolEnvelope[WatchlistMutationResultDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        group_name = request.group_name or self._default_group
        provider_code, display_name = self._source_identity(
            request.instrument_id,
            request.display_name,
        )
        return await self._mutate(
            request_id=request_id,
            action=WatchlistMutationAction.ADD,
            group_name=group_name,
            provider_code=provider_code,
            display_name=display_name,
            requested_by=request.confirmed_by,
            idempotency_key=request.idempotency_key,
        )

    async def remove(
        self, request: WatchlistRemoveInput
    ) -> ToolEnvelope[WatchlistMutationResultDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                membership = uow.memberships.get(request.membership_id)
                group = uow.groups.get(membership.group_id)
            if membership.source is not self.source:
                raise DataContractError(
                    "membership does not belong to the active watchlist source",
                    details={"membership_id": membership.membership_id},
                )
            return await self._mutate(
                request_id=request_id,
                action=WatchlistMutationAction.REMOVE,
                group_name=group.name,
                provider_code=membership.provider_code,
                display_name=membership.display_name,
                requested_by=request.confirmed_by,
                idempotency_key=request.idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, exc)

    async def _mutate(
        self,
        *,
        request_id: str,
        action: WatchlistMutationAction,
        group_name: str,
        provider_code: str,
        display_name: str,
        requested_by: str,
        idempotency_key: str,
    ) -> ToolEnvelope[WatchlistMutationResultDTO]:
        try:
            duplicate = self._existing_mutation(
                action=action,
                group_name=group_name,
                provider_code=provider_code,
                requested_by=requested_by,
                idempotency_key=idempotency_key,
            )
            if duplicate is not None:
                if duplicate.status is not WatchlistMutationStatus.SUCCEEDED:
                    raise DuplicateIdempotencyKey(
                        "Existing watchlist mutation is not safely repeatable",
                        details={
                            "mutation_id": duplicate.mutation_id,
                            "status": duplicate.status.value,
                            "error_code": duplicate.error_code,
                        },
                    )
                membership = self._membership_for_mutation(duplicate)
                return self._mutation_success(
                    request_id,
                    duplicate,
                    membership,
                    warning=WarningInfo(
                        code="DUPLICATE_IDEMPOTENCY_KEY",
                        message="Returning the existing watchlist mutation receipt.",
                        details={"mutation_id": duplicate.mutation_id},
                    ),
                )

            # Refresh group metadata before accepting a source write.
            upstream_groups = await self._provider.list_groups()
            self._persist_group_snapshot(upstream_groups, request_id=request_id)
            group = self._require_writable_group(group_name)
            mutation = WatchlistMutation(
                mutation_id=self._ids.new(EntityIdPrefix.WATCH_MUTATION),
                idempotency_key=idempotency_key,
                action=action,
                source=self.source,
                group_name=group.name,
                provider_code=provider_code,
                requested_by=requested_by,
                status=WatchlistMutationStatus.PENDING,
                requested_at=self._clock.now(),
                completed_at=None,
                error_code=None,
            )
            self._persist_pending(mutation, request_id=request_id)
            try:
                if action is WatchlistMutationAction.ADD:
                    source_membership = await self._provider.add_membership(
                        group_name=group.name,
                        provider_code=provider_code,
                        display_name=display_name,
                    )
                else:
                    source_membership = await self._provider.remove_membership(
                        group_name=group.name,
                        provider_code=provider_code,
                    )
            except Exception as exc:  # noqa: BLE001
                self._record_terminal_failure(
                    mutation,
                    status=WatchlistMutationStatus.FAILED,
                    error_code=self._error_code(exc),
                    request_id=request_id,
                )
                return self._failure(request_id, exc)

            try:
                completed, membership = self._persist_source_success(
                    mutation,
                    group,
                    source_membership,
                    request_id=request_id,
                )
            except Exception:  # noqa: BLE001
                persistence_error = PersistenceError(
                    "Upstream watchlist write succeeded but local persistence failed",
                    details={
                        "mutation_id": mutation.mutation_id,
                        "source": self.source.value,
                    },
                )
                with suppress(Exception):
                    self._record_terminal_failure(
                        mutation,
                        status=WatchlistMutationStatus.PARTIAL,
                        error_code=persistence_error.code,
                        request_id=request_id,
                    )
                return self._failure(
                    request_id,
                    PartialDataError(
                        persistence_error.message,
                        details=persistence_error.details,
                    ),
                )
            return self._mutation_success(request_id, completed, membership)
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, exc)

    def _persist_group_snapshot(
        self,
        values: tuple[WatchlistSourceGroup, ...],
        *,
        request_id: str,
    ) -> None:
        now = self._clock.now()
        seen = {item.name for item in values}
        with self._uow_factory() as uow:
            existing_groups = uow.groups.list(
                source=self.source,
                include_inactive=True,
                limit=500,
            )
            for item in values:
                existing = uow.groups.get_by_source_key(self.source, item.name)
                group = WatchlistGroup(
                    group_id=(
                        existing.group_id
                        if existing is not None
                        else self._ids.new(EntityIdPrefix.WATCH_GROUP)
                    ),
                    source=self.source,
                    source_group_key=item.name,
                    name=item.name,
                    group_type=item.group_type,
                    writable=item.writable,
                    active=True,
                    first_seen_at=existing.first_seen_at if existing else now,
                    last_seen_at=now,
                    removed_at=None,
                    last_synced_at=now,
                )
                uow.groups.upsert(group)
            for group in existing_groups:
                if group.source_group_key in seen or not group.active:
                    continue
                for membership in uow.memberships.list(
                    group_id=group.group_id,
                    include_inactive=False,
                    limit=500,
                ):
                    uow.memberships.mark_inactive(
                        membership.membership_id,
                        removed_at=now,
                    )
                uow.groups.mark_inactive(group.group_id, removed_at=now)
            uow.audit.append(
                "phase2.watchlist.groups.refreshed",
                {"source": self.source.value, "group_count": len(values)},
                request_id=request_id,
            )
            uow.commit()

    def _persist_membership_snapshot(
        self,
        group_name: str,
        values: tuple[WatchlistSourceMembership, ...],
        *,
        request_id: str,
    ) -> None:
        now = self._clock.now()
        with self._uow_factory() as uow:
            group = uow.groups.get_by_source_key(self.source, group_name)
            if group is None:
                raise WatchlistGroupNotFound(
                    f"Watchlist group not found: {group_name}",
                    details={"group_name": group_name},
                )
            seen: list[str] = []
            for item in values:
                if item.group_name != group.name or item.source is not self.source:
                    raise DataContractError(
                        "watchlist source returned membership for another group/source"
                    )
                existing = uow.memberships.get_by_code(group.group_id, item.provider_code)
                membership = self._membership_from_source(
                    item,
                    group_id=group.group_id,
                    existing=existing,
                    active=True,
                    now=now,
                )
                uow.memberships.upsert(membership)
                seen.append(item.provider_code)
            uow.memberships.mark_inactive_not_seen(
                group_id=group.group_id,
                seen_provider_codes=tuple(seen),
                removed_at=now,
            )
            uow.groups.upsert(
                WatchlistGroup(
                    group_id=group.group_id,
                    source=group.source,
                    source_group_key=group.source_group_key,
                    name=group.name,
                    group_type=group.group_type,
                    writable=group.writable,
                    active=True,
                    first_seen_at=group.first_seen_at,
                    last_seen_at=now,
                    removed_at=None,
                    last_synced_at=now,
                )
            )
            uow.audit.append(
                "phase2.watchlist.memberships.refreshed",
                {
                    "source": self.source.value,
                    "group_name": group.name,
                    "membership_count": len(values),
                },
                request_id=request_id,
            )
            uow.commit()

    def _persist_pending(self, mutation: WatchlistMutation, *, request_id: str) -> None:
        with self._uow_factory() as uow:
            uow.mutations.add(mutation)
            uow.audit.append(
                "phase2.watchlist.mutation.requested",
                {
                    "mutation_id": mutation.mutation_id,
                    "action": mutation.action.value,
                    "source": mutation.source.value,
                    "group_name": mutation.group_name,
                    "provider_code": mutation.provider_code,
                    "requested_by": mutation.requested_by,
                },
                request_id=request_id,
            )
            uow.commit()

    def _persist_source_success(
        self,
        mutation: WatchlistMutation,
        group: WatchlistGroup,
        source_membership: WatchlistSourceMembership,
        *,
        request_id: str,
    ) -> tuple[WatchlistMutation, WatchlistMembership]:
        now = self._clock.now()
        with self._uow_factory() as uow:
            current_group = uow.groups.get(group.group_id)
            existing = uow.memberships.get_by_code(
                current_group.group_id,
                source_membership.provider_code,
            )
            membership = self._membership_from_source(
                source_membership,
                group_id=current_group.group_id,
                existing=existing,
                active=mutation.action is WatchlistMutationAction.ADD,
                now=now,
            )
            membership = uow.memberships.upsert(membership)
            uow.mutations.update_status(
                mutation.mutation_id,
                status=WatchlistMutationStatus.SUCCEEDED,
                completed_at=now,
                error_code=None,
            )
            uow.audit.append(
                "phase2.watchlist.mutation.succeeded",
                {
                    "mutation_id": mutation.mutation_id,
                    "membership_id": membership.membership_id,
                    "action": mutation.action.value,
                    "source": mutation.source.value,
                },
                request_id=request_id,
            )
            uow.commit()
            completed = uow.mutations.get(mutation.mutation_id)
            return completed, membership

    def _record_terminal_failure(
        self,
        mutation: WatchlistMutation,
        *,
        status: WatchlistMutationStatus,
        error_code: str,
        request_id: str,
    ) -> None:
        now = self._clock.now()
        with self._uow_factory() as uow:
            uow.mutations.update_status(
                mutation.mutation_id,
                status=status,
                completed_at=now,
                error_code=error_code,
            )
            uow.audit.append(
                f"phase2.watchlist.mutation.{status.value.lower()}",
                {
                    "mutation_id": mutation.mutation_id,
                    "action": mutation.action.value,
                    "source": mutation.source.value,
                    "error_code": error_code,
                },
                request_id=request_id,
            )
            uow.commit()

    def _existing_mutation(
        self,
        *,
        action: WatchlistMutationAction,
        group_name: str,
        provider_code: str,
        requested_by: str,
        idempotency_key: str,
    ) -> WatchlistMutation | None:
        with self._uow_factory() as uow:
            existing = uow.mutations.get_by_idempotency_key(idempotency_key)
        if existing is None:
            return None
        expected = (action, self.source, group_name, provider_code, requested_by)
        actual = (
            existing.action,
            existing.source,
            existing.group_name,
            existing.provider_code,
            existing.requested_by,
        )
        if actual != expected:
            raise DuplicateIdempotencyKey(
                "idempotency_key already belongs to another watchlist mutation",
                details={"mutation_id": existing.mutation_id},
            )
        return existing

    def _membership_for_mutation(self, mutation: WatchlistMutation) -> WatchlistMembership:
        with self._uow_factory() as uow:
            group = uow.groups.get_by_source_key(mutation.source, mutation.group_name)
            if group is None:
                raise WatchlistGroupNotFound(
                    "Watchlist group for mutation no longer exists",
                    details={"mutation_id": mutation.mutation_id},
                )
            membership = uow.memberships.get_by_code(group.group_id, mutation.provider_code)
            if membership is None:
                raise DuplicateIdempotencyKey(
                    "Existing mutation has no durable membership result",
                    details={"mutation_id": mutation.mutation_id},
                )
            return membership

    def _require_writable_group(self, group_name: str) -> WatchlistGroup:
        with self._uow_factory() as uow:
            group = uow.groups.get_by_source_key(self.source, group_name)
        if group is None or not group.active:
            raise WatchlistGroupNotFound(
                f"Watchlist group not found: {group_name}",
                details={"group_name": group_name},
            )
        if not group.writable:
            raise DataContractError(
                "watchlist group is read-only",
                details={"group_name": group_name},
            )
        return group

    def _list_groups(self, *, include_inactive: bool) -> tuple[WatchlistGroup, ...]:
        with self._uow_factory() as uow:
            return uow.groups.list(
                source=self.source,
                include_inactive=include_inactive,
                limit=500,
            )

    def _read_group_items(
        self,
        group_name: str,
        *,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> tuple[WatchlistGroup | None, tuple[WatchlistMembership, ...]]:
        with self._uow_factory() as uow:
            group = uow.groups.get_by_source_key(self.source, group_name)
            if group is None:
                return None, ()
            return group, uow.memberships.list(
                group_id=group.group_id,
                include_inactive=include_inactive,
                limit=limit,
                offset=offset,
            )

    def _membership_from_source(
        self,
        value: WatchlistSourceMembership,
        *,
        group_id: str,
        existing: WatchlistMembership | None,
        active: bool,
        now: datetime,
    ) -> WatchlistMembership:
        return WatchlistMembership(
            membership_id=(
                existing.membership_id
                if existing is not None
                else self._ids.new(EntityIdPrefix.WATCH_MEMBERSHIP)
            ),
            group_id=group_id,
            source=self.source,
            provider_code=value.provider_code,
            instrument_id=value.instrument_id,
            display_name=value.display_name,
            provider_asset_type=value.provider_asset_type,
            research_supported=value.research_supported,
            active=active,
            first_seen_at=existing.first_seen_at if existing else now,
            last_seen_at=now,
            removed_at=None if active else now,
            last_synced_at=now,
        )

    def _membership_dtos(
        self, memberships: tuple[WatchlistMembership, ...]
    ) -> tuple[WatchlistMembershipDTO, ...]:
        research_links: dict[tuple[Market, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
        markets = {
            parse_instrument_id(item.instrument_id)[1]
            for item in memberships
            if item.instrument_id is not None
        }
        with self._research_uow_factory() as uow:
            for market in markets:
                items = uow.watchlist.list(market=market, limit=500)
                for item in items:
                    key = (item.market, item.symbol)
                    old_items, old_cases = research_links.get(key, ((), ()))
                    new_case_ids = tuple(
                        case_id
                        for case_id in (item.case_id, item.promoted_to_case_id)
                        if case_id is not None and case_id not in old_cases
                    )
                    research_links[key] = (
                        old_items + (item.item_id,),
                        old_cases + new_case_ids,
                    )
        result: list[WatchlistMembershipDTO] = []
        for membership in memberships:
            item_ids: tuple[str, ...] = ()
            case_ids: tuple[str, ...] = ()
            if membership.instrument_id is not None:
                _, market, symbol = parse_instrument_id(membership.instrument_id)
                item_ids, case_ids = research_links.get((market, symbol), ((), ()))
            result.append(
                WatchlistMembershipDTO.from_domain(
                    membership,
                    research_watchlist_item_ids=item_ids,
                    investment_case_ids=case_ids,
                )
            )
        return tuple(result)

    def _source_identity(
        self, instrument_id: str, display_name: str | None
    ) -> tuple[str, str]:
        _, market, symbol = parse_instrument_id(instrument_id)
        if market not in {Market.A_SHARE, Market.US}:
            raise DataContractError("watchlist supports A_SHARE and US instruments only")
        name = display_name or symbol
        if self.source is WatchlistSource.MANUAL_CSV:
            return instrument_id, name
        if market is Market.US:
            return f"US.{symbol}", name
        if symbol.endswith(".SH"):
            return f"SH.{symbol.removesuffix('.SH')}", name
        if symbol.endswith(".SZ"):
            return f"SZ.{symbol.removesuffix('.SZ')}", name
        raise DataContractError(
            "A-share watchlist symbol must end in .SH or .SZ",
            details={"instrument_id": instrument_id},
        )

    def _mutation_success(
        self,
        request_id: str,
        mutation: WatchlistMutation,
        membership: WatchlistMembership,
        *,
        warning: WarningInfo | None = None,
    ) -> ToolEnvelope[WatchlistMutationResultDTO]:
        now = self._clock.now()
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=now,
            fetched_at=now,
            freshness=Freshness.FRESH,
            sources=(self._source_reference(now),),
            data=WatchlistMutationResultDTO(
                mutation=WatchlistMutationDTO.from_domain(mutation),
                membership=self._membership_dtos((membership,))[0],
            ),
            degraded=warning is not None,
            warnings=() if warning is None else (warning,),
        )

    def _source_reference(self, now: datetime) -> SourceReference:
        return SourceReference(
            name=self.source.value,
            role=SourceRole.PRIMARY,
            retrieved_at=now,
        )

    def _stale_warning(self, exc: Exception, *, scope: str) -> WarningInfo:
        return WarningInfo(
            code="WATCHLIST_SOURCE_UNAVAILABLE_USING_DURABLE_STATE",
            message="Watchlist refresh failed; returning the latest durable state.",
            details={
                "source": self.source.value,
                "scope": scope,
                "error_code": self._error_code(exc),
            },
        )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        return exc.code if isinstance(exc, TradingPartnerError) else "UNEXPECTED_ERROR"

    def _failure(self, request_id: str, exc: Exception) -> ToolEnvelope[T]:
        now = self._clock.now()
        error = (
            to_error_info(exc, self._redactor)
            if isinstance(exc, TradingPartnerError)
            else to_error_info_from_exception(exc, self._redactor)
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=None,
            as_of=now,
            fetched_at=now,
            errors=(error,),
        )
