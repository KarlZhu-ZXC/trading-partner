"""Compact Watchlist Hub operation adapters."""

from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.watchlist_hub import (
    WatchlistAddInput,
    WatchlistGetGroupsInput,
    WatchlistGetItemsInput,
    WatchlistRemoveInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure


def build_watchlist_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact Watchlist operation adapters."""

    async def get_items(
        group_name: str | None,
        refresh: bool,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        try:
            request = WatchlistGetItemsInput.model_validate(
                {
                    "group_name": group_name,
                    "refresh": refresh,
                    "include_inactive": include_inactive,
                    "limit": limit,
                    "offset": offset,
                }
            )
            envelope = await container.services.watchlist.get_items(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def watchlist_get(
        operation: Literal["groups", "items"] = "items",
        group_name: str | None = None,
        refresh: bool = False,
        include_inactive: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List durable Watchlist groups or members from the active source."""
        if operation == "items":
            return await get_items(group_name, refresh, include_inactive, limit, offset)
        try:
            request = WatchlistGetGroupsInput.model_validate(
                {"refresh": refresh, "include_inactive": include_inactive}
            )
            envelope = await container.services.watchlist.get_groups(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def watchlist_sync_all() -> dict[str, Any]:
        """Fetch and persist every group and membership from the active source."""
        try:
            envelope = await container.services.watchlist.sync_all()
            return envelope.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def watchlist_add(
        instrument_id: str,
        confirmed_by: str,
        idempotency_key: str,
        group_name: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Add one instrument to the active source after explicit confirmation."""
        try:
            request = WatchlistAddInput.model_validate(
                {
                    "group_name": group_name,
                    "instrument_id": instrument_id,
                    "display_name": display_name,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = await container.services.watchlist.add(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def watchlist_remove(
        membership_id: str,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Remove one membership from the active source without deleting research."""
        try:
            request = WatchlistRemoveInput.model_validate(
                {
                    "membership_id": membership_id,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = await container.services.watchlist.remove(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    return SimpleNamespace(
        watchlist_get=watchlist_get,
        watchlist_sync_all=watchlist_sync_all,
        watchlist_add=watchlist_add,
        watchlist_remove=watchlist_remove,
    )
