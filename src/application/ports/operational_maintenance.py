"""Operational maintenance port for local status, backup, and cache retention."""

from __future__ import annotations

from typing import Protocol

from application.dto.maintenance import (
    CachePruneReceiptDTO,
    DatabaseBackupReceiptDTO,
    MaintenanceStatusDTO,
)


class OperationalMaintenancePort(Protocol):
    def status(self) -> MaintenanceStatusDTO: ...

    def backup(self) -> DatabaseBackupReceiptDTO: ...

    def prune_expired_cache(
        self, *, retention_days: int, dry_run: bool
    ) -> CachePruneReceiptDTO: ...
