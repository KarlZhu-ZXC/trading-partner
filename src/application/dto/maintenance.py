"""Read-only maintenance status and explicit operational receipts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetentionRuleDTO(_DTO):
    area: str
    policy: str
    days: int | None = None


class TableCountDTO(_DTO):
    table: str
    rows: int = Field(ge=0)


class MaintenanceStatusDTO(_DTO):
    generated_at: datetime
    database_filename: str
    database_bytes: int = Field(ge=0)
    table_counts: tuple[TableCountDTO, ...]
    provider_cache_total: int = Field(ge=0)
    provider_cache_expired: int = Field(ge=0)
    validation_artifact_files: int = Field(ge=0)
    validation_artifact_bytes: int = Field(ge=0)
    backup_files: int = Field(ge=0)
    latest_backup_at: datetime | None
    retention_rules: tuple[RetentionRuleDTO, ...]
    monitor_scheduler_plist_present: bool = False
    monitor_scheduler_loaded: bool | None = None
    monitor_scheduler_last_exit_code: int | None = None


class DatabaseBackupReceiptDTO(_DTO):
    created_at: datetime
    filename: str
    bytes: int = Field(ge=0)
    alembic_revision: str
    schema_versions: tuple[str, ...]


class CachePruneReceiptDTO(_DTO):
    checked_at: datetime
    cutoff: datetime
    dry_run: bool
    provider_cache_rows: int = Field(ge=0)
    reddit_cache_rows: int = Field(ge=0)
