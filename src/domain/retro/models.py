"""Immutable Trade Retro records and pre-trade plan snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.retro.enums import (
    TradeRetroFindingReviewStatus,
    TradeRetroReviewStatus,
    TradeRetroSeverity,
    TradeRetroStatus,
)

TRADE_RETRO_SCHEMA_VERSION = 1
TRADE_RETRO_ALGORITHM_VERSION = "trade-retro-v1"
TRADE_RETRO_LEGACY_MARKDOWN_IMPORT_VERSION = "trade-retro-legacy-markdown-import-v1"
_TRADE_RETRO_ALGORITHM_VERSIONS = {
    TRADE_RETRO_ALGORITHM_VERSION,
    TRADE_RETRO_LEGACY_MARKDOWN_IMPORT_VERSION,
}


def _text(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized


def _id(value: str, field: str, prefix: str) -> str:
    normalized = _text(value, field, maximum=128)
    if not normalized.startswith(f"{prefix}_"):
        raise DataContractError(f"{field} must start with {prefix}_")
    return normalized


def _optional_text(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized or None


@dataclass(frozen=True, slots=True)
class TradeRetroPlanEntry:
    subject_id: str
    subject_title: str
    plan_id: str
    plan_version: int
    thesis_id: str
    instrument_id: str
    status: str
    stop_price: str | None
    max_position_percent: str
    condition_codes: tuple[str, ...]
    decision_records: tuple[tuple[str, str, str, str | None], ...]

    def __post_init__(self) -> None:
        _id(self.subject_id, "subject_id", "case")
        _text(self.subject_title, "subject_title", maximum=500)
        _id(self.plan_id, "plan_id", "trade_plan")
        if self.plan_version < 1:
            raise DataContractError("plan_version must be positive")
        _id(self.thesis_id, "thesis_id", "thesis")
        _text(self.instrument_id, "instrument_id", maximum=200)
        _text(self.status, "status", maximum=32)
        _text(self.max_position_percent, "max_position_percent", maximum=64)
        for code in self.condition_codes:
            _text(code, "condition_code", maximum=64)
        for decision_id, decision_type, decided_at, primary_instrument_id in self.decision_records:
            _id(decision_id, "decision_id", "decision")
            _text(decision_type, "decision_type", maximum=64)
            _text(decided_at, "decided_at", maximum=64)
            if primary_instrument_id is not None:
                _text(primary_instrument_id, "primary_instrument_id", maximum=200)


@dataclass(frozen=True, slots=True)
class TradeRetroPlanSnapshot:
    snapshot_id: str
    period_start: datetime
    period_end: datetime
    captured_at: datetime
    entries: tuple[TradeRetroPlanEntry, ...]
    idempotency_key: str
    schema_version: int = TRADE_RETRO_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _id(self.snapshot_id, "snapshot_id", "retro_plan")
        for field in ("period_start", "period_end", "captured_at"):
            require_aware_datetime(getattr(self, field), field_name=field)
        if self.period_start >= self.period_end:
            raise DataContractError("Trade Retro period must be non-empty")
        _text(self.idempotency_key, "idempotency_key", maximum=200)
        if self.schema_version != TRADE_RETRO_SCHEMA_VERSION:
            raise DataContractError("Trade Retro plan snapshot schema version is invalid")


@dataclass(frozen=True, slots=True)
class TradeRetroFinding:
    code: str
    severity: TradeRetroSeverity
    title: str
    detail: str
    instrument_id: str | None
    transaction_ids: tuple[str, ...]
    plan_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.code, "finding code", maximum=64)
        if not isinstance(self.severity, TradeRetroSeverity):
            raise DataContractError("finding severity is invalid")
        _text(self.title, "finding title", maximum=300)
        _text(self.detail, "finding detail", maximum=2000)
        if self.instrument_id is not None:
            _text(self.instrument_id, "instrument_id", maximum=200)
        if self.plan_id is not None:
            _id(self.plan_id, "plan_id", "trade_plan")
        for transaction_id in self.transaction_ids:
            _text(transaction_id, "transaction_id", maximum=256)


def trade_retro_finding_key(value: TradeRetroFinding) -> str:
    """Return a stable review target without mutating the immutable finding."""

    payload = {
        "code": value.code,
        "severity": value.severity.value,
        "title": value.title,
        "detail": value.detail,
        "instrument_id": value.instrument_id,
        "transaction_ids": value.transaction_ids,
        "plan_id": value.plan_id,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return f"finding_{digest}"


@dataclass(frozen=True, slots=True)
class TradeRetroRun:
    run_id: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    status: TradeRetroStatus
    plan_snapshot_id: str | None
    transaction_ids: tuple[str, ...]
    findings: tuple[TradeRetroFinding, ...]
    warning_codes: tuple[str, ...]
    summary_markdown: str
    llm_provider: str | None
    llm_model: str | None
    idempotency_key: str
    algorithm_version: str = TRADE_RETRO_ALGORITHM_VERSION
    schema_version: int = TRADE_RETRO_SCHEMA_VERSION
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _id(self.run_id, "run_id", "retro")
        for field in ("period_start", "period_end", "generated_at"):
            require_aware_datetime(getattr(self, field), field_name=field)
        if self.period_start >= self.period_end:
            raise DataContractError("Trade Retro period must be non-empty")
        if not isinstance(self.status, TradeRetroStatus):
            raise DataContractError("Trade Retro status is invalid")
        if self.plan_snapshot_id is not None:
            _id(self.plan_snapshot_id, "plan_snapshot_id", "retro_plan")
        _text(self.summary_markdown, "summary_markdown", maximum=50_000)
        _text(self.idempotency_key, "idempotency_key", maximum=200)
        if self.algorithm_version not in _TRADE_RETRO_ALGORITHM_VERSIONS:
            raise DataContractError("Trade Retro algorithm version is invalid")
        if self.schema_version != TRADE_RETRO_SCHEMA_VERSION:
            raise DataContractError("Trade Retro schema version is invalid")
        if self.execution_effect:
            raise DataContractError("Trade Retro cannot have execution effect")


@dataclass(frozen=True, slots=True)
class TradeRetroFindingReview:
    finding_key: str
    status: TradeRetroFindingReviewStatus
    note: str | None = None

    def __post_init__(self) -> None:
        _id(self.finding_key, "finding_key", "finding")
        if not isinstance(self.status, TradeRetroFindingReviewStatus):
            raise DataContractError("finding review status is invalid")
        normalized_note = _optional_text(self.note, "finding review note", maximum=2_000)
        object.__setattr__(self, "note", normalized_note)
        if self.status is TradeRetroFindingReviewStatus.DISPUTED and normalized_note is None:
            raise DataContractError("a disputed finding requires a note")


@dataclass(frozen=True, slots=True)
class TradeRetroReviewRevision:
    review_id: str
    run_id: str
    version: int
    status: TradeRetroReviewStatus
    note_markdown: str
    action_items: tuple[str, ...]
    finding_reviews: tuple[TradeRetroFindingReview, ...]
    reviewed_by: str
    authorization_note: str
    created_at: datetime
    idempotency_key: str
    schema_version: int = TRADE_RETRO_SCHEMA_VERSION
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _id(self.review_id, "review_id", "retro_review")
        _id(self.run_id, "run_id", "retro")
        if self.version < 1:
            raise DataContractError("review version must be positive")
        if not isinstance(self.status, TradeRetroReviewStatus):
            raise DataContractError("Trade Retro review status is invalid")
        normalized_note = _optional_text(
            self.note_markdown,
            "review note_markdown",
            maximum=20_000,
        )
        object.__setattr__(self, "note_markdown", normalized_note or "")
        if len(self.action_items) > 20:
            raise DataContractError("Trade Retro review supports at most 20 action items")
        normalized_actions = tuple(
            _text(item, "action item", maximum=500) for item in self.action_items
        )
        object.__setattr__(self, "action_items", normalized_actions)
        finding_keys = tuple(item.finding_key for item in self.finding_reviews)
        if len(finding_keys) != len(set(finding_keys)):
            raise DataContractError("finding reviews must have unique finding_key values")
        if self.reviewed_by not in {"user", "external_agent"}:
            raise DataContractError("reviewed_by must be user or external_agent")
        object.__setattr__(
            self,
            "authorization_note",
            _text(self.authorization_note, "authorization_note", maximum=4_000),
        )
        require_aware_datetime(self.created_at, field_name="created_at")
        _text(self.idempotency_key, "idempotency_key", maximum=200)
        if self.schema_version != TRADE_RETRO_SCHEMA_VERSION:
            raise DataContractError("Trade Retro review schema version is invalid")
        if self.execution_effect:
            raise DataContractError("Trade Retro review cannot have execution effect")


@dataclass(frozen=True, slots=True)
class TradeRetroExportReceipt:
    receipt_id: str
    run_id: str
    target_path: str
    content_sha256: str
    exported_at: datetime
    idempotency_key: str
    review_version: int | None = None

    def __post_init__(self) -> None:
        _id(self.receipt_id, "receipt_id", "retro_export")
        _id(self.run_id, "run_id", "retro")
        _text(self.target_path, "target_path", maximum=2000)
        digest = _text(self.content_sha256, "content_sha256", maximum=64)
        if len(digest) != 64:
            raise DataContractError("content_sha256 must be a SHA-256 hex digest")
        require_aware_datetime(self.exported_at, field_name="exported_at")
        _text(self.idempotency_key, "idempotency_key", maximum=200)
        if self.review_version is not None and self.review_version < 1:
            raise DataContractError("review_version must be positive")
