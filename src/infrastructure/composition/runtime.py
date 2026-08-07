"""Infrastructure resource ownership and deterministic composition overrides."""

from __future__ import annotations

from dataclasses import dataclass, field

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.clock import Clock
from application.ports.http_transport import HttpTransport
from application.ports.notification_sender import NotificationSender
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from infrastructure.persistence.database import SqlAlchemyDatabase
from infrastructure.providers.a_share.eastmoney_gate import EastmoneyRequestGate
from infrastructure.system.process_file_lock import ProcessFileLock


@dataclass(frozen=True, slots=True)
class CompositionOverrides:
    """Deterministic composition-only overrides; never a production mode switch."""

    clock: Clock | None = None
    a_share_transport: HttpTransport | None = None
    eastmoney_gate: EastmoneyRequestGate | None = None
    a_share_calendar: AShareTradingCalendar | None = None
    watchlist_provider: WatchlistSourceProvider | None = None
    notification_sender: NotificationSender | None = None
    # Legacy override name remains accepted for old test fixtures and scripts.
    monitor_notification_sender: NotificationSender | None = None


@dataclass(slots=True)
class RuntimeResources:
    """Own infrastructure resources and their deterministic shutdown order."""

    database: SqlAlchemyDatabase
    monitor_run_lock: ProcessFileLock
    post_market_sync_lock: ProcessFileLock
    a_share_transport: HttpTransport | None = None
    cross_asset_transport: HttpTransport | None = None
    notification_sender: NotificationSender | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def monitor_notification_sender(self) -> NotificationSender | None:
        """Compatibility accessor for pre-0030 composition callers."""
        return self.notification_sender

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closed: set[int] = set()
            for transport in (
                self.a_share_transport,
                self.cross_asset_transport,
                self.notification_sender,
            ):
                if transport is None or id(transport) in closed:
                    continue
                closed.add(id(transport))
                aclose = getattr(transport, "aclose", None)
                if callable(aclose):
                    await aclose()
        finally:
            self.database.close()
