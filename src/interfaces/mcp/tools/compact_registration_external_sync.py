"""Compact MCP registrations for explicit upstream synchronization."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from interfaces.capabilities.registry import CapabilityRegistrar


def _register_external_sync(
    registry: CapabilityRegistrar,
    portfolio: SimpleNamespace,
    watchlist: SimpleNamespace,
    view_review: SimpleNamespace,
) -> None:
    """Register the explicit account/transaction/Watchlist sync capability."""

    from interfaces.capabilities.policy import SYNC
    from interfaces.capabilities.registration import _register_dispatch_tool, _spec

    _register_dispatch_tool(
        registry,
        name="external_state_sync",
        description=(
            "Explicitly fetch and persist broker accounts, transactions, or the active "
            "Watchlist upstream, or Moomoo notes without model analysis or account refresh."
        ),
        variants=(
            _spec("notes", view_review.notes_sync),
            _spec(
                "accounts",
                portfolio.account_get,
                ("providers", "as_of"),
                adapter_operation="refresh",
            ),
            _spec(
                "transactions",
                portfolio.account_get,
                ("providers", "start", "end", "limit"),
                adapter_operation="transactions",
            ),
            _spec(
                "watchlist",
                watchlist.watchlist_sync_all,
            ),
        ),
        policy=SYNC,
    )
