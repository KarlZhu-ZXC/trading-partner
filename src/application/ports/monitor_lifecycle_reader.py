"""Read-only Monitor dependencies used by research lifecycle guards."""

from typing import Protocol


class MonitorLifecycleReader(Protocol):
    """Expose current live Monitor identities without mutating Monitoring state."""

    def list_live_ids(
        self,
        *,
        subject_id: str | None = None,
        trade_plan_id: str | None = None,
    ) -> tuple[str, ...]: ...
