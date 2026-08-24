"""Durable Daily Equity projection and activation/shadow contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.performance.enums import (
    DailyEquityCoverageStatus,
    DailyEquityMaterializationMode,
)


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be a bounded non-blank string")
    return value


def _decimal(value: Decimal | None, field: str) -> None:
    if value is not None and (type(value) is not Decimal or not value.is_finite()):
        raise DataContractError(f"{field} must be a finite Decimal")


def _codes(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or len(values) != len(set(values)):
        raise DataContractError(f"{field} must be a unique tuple")
    for value in values:
        _text(value, field, 128)


def _ids(values: tuple[str, ...], field: str) -> None:
    _codes(values, field)


@dataclass(frozen=True, slots=True)
class JournalActivation:
    """The immutable epoch at which Journal capture became active."""

    activation_id: str
    journal_activation_at: datetime
    recorded_at: datetime
    actor: str
    idempotency_key: str
    algorithm_version: str = "journal_activation_v1"

    def __post_init__(self) -> None:
        _text(self.activation_id, "activation_id", 128)
        require_aware_datetime(
            self.journal_activation_at,
            field_name="journal_activation_at",
        )
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        if self.recorded_at < self.journal_activation_at:
            raise DataContractError("recorded_at must be >= journal_activation_at")
        _text(self.actor, "actor", 128)
        _text(self.idempotency_key, "idempotency_key", 200)
        _text(self.algorithm_version, "algorithm_version", 64)


@dataclass(frozen=True, slots=True)
class DailyEquitySnapshot:
    """One immutable, source-referenced Daily Equity projection.

    ``equity_value`` is copied only from broker ``AccountSnapshot.net_assets``.
    ``gross_position_value`` is retained as an optional source field for future
    explicit facts, but the materializer never derives or uses it as NAV.
    """

    daily_equity_snapshot_id: str
    account_ref: str
    currency: str
    valuation_at: datetime
    market_session_date: date
    equity_value: Decimal | None
    source_snapshot_id: str
    source_snapshot_as_of: datetime
    source_fetched_at: datetime
    valuation_basis: str
    coverage_status: DailyEquityCoverageStatus
    quality_status: DailyEquityCoverageStatus
    materialized_at: datetime
    journal_activation_at: datetime | None = None
    cash_value: Decimal | None = None
    gross_position_value: Decimal | None = None
    net_external_cash_flow_since_previous: Decimal | None = None
    warning_codes: tuple[str, ...] = ()
    algorithm_version: str = "daily_equity_v1"

    def __post_init__(self) -> None:
        _text(self.daily_equity_snapshot_id, "daily_equity_snapshot_id", 160)
        _text(self.account_ref, "account_ref", 128)
        _text(self.currency, "currency", 16)
        require_aware_datetime(self.valuation_at, field_name="valuation_at")
        if not isinstance(self.market_session_date, date):
            raise DataContractError("market_session_date must be a date")
        _decimal(self.equity_value, "equity_value")
        _text(self.source_snapshot_id, "source_snapshot_id", 128)
        require_aware_datetime(
            self.source_snapshot_as_of,
            field_name="source_snapshot_as_of",
        )
        require_aware_datetime(self.source_fetched_at, field_name="source_fetched_at")
        require_aware_datetime(self.materialized_at, field_name="materialized_at")
        if self.source_snapshot_as_of > self.source_fetched_at:
            raise DataContractError("source_snapshot_as_of must be <= source_fetched_at")
        if self.journal_activation_at is not None:
            require_aware_datetime(
                self.journal_activation_at,
                field_name="journal_activation_at",
            )
        _text(self.valuation_basis, "valuation_basis", 64)
        if self.valuation_basis != "BROKER_NET_ASSETS":
            raise DataContractError("daily equity valuation basis must be BROKER_NET_ASSETS")
        if not isinstance(self.coverage_status, DailyEquityCoverageStatus):
            raise DataContractError("coverage_status is invalid")
        if not isinstance(self.quality_status, DailyEquityCoverageStatus):
            raise DataContractError("quality_status is invalid")
        for field_name in (
            "cash_value",
            "gross_position_value",
            "net_external_cash_flow_since_previous",
        ):
            _decimal(getattr(self, field_name), field_name)
        _codes(self.warning_codes, "warning_codes")
        _text(self.algorithm_version, "algorithm_version", 64)
        if self.equity_value is None and self.quality_status is DailyEquityCoverageStatus.COMPLETE:
            raise DataContractError("missing equity_value cannot have COMPLETE quality")

    @property
    def snapshot_id(self) -> str:
        return self.source_snapshot_id

    @property
    def source_snapshot_ref(self) -> str:
        return self.source_snapshot_id


@dataclass(frozen=True, slots=True)
class DailyEquityMaterializationWriteResult:
    """Idempotent write counts returned by the projection repository."""

    snapshots: tuple[DailyEquitySnapshot, ...]
    inserted_count: int
    duplicate_count: int

    def __post_init__(self) -> None:
        if any(not isinstance(item, DailyEquitySnapshot) for item in self.snapshots):
            raise DataContractError("materialization snapshots are invalid")
        if self.inserted_count < 0 or self.duplicate_count < 0:
            raise DataContractError("materialization counts must be nonnegative")
        if self.inserted_count + self.duplicate_count != len(self.snapshots):
            raise DataContractError("materialization counts do not reconcile")


@dataclass(frozen=True, slots=True)
class DailyEquityMaterializationReceipt:
    """Bounded result contract for historical shadow/dry-run/persist runs."""

    receipt_id: str
    mode: DailyEquityMaterializationMode
    generated_at: datetime
    journal_activation_at: datetime | None
    account_refs: tuple[str, ...]
    source_snapshot_ids: tuple[str, ...]
    materialized_snapshot_ids: tuple[str, ...]
    candidate_count: int
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    coverage_status: DailyEquityCoverageStatus
    warning_codes: tuple[str, ...]
    algorithm_version: str = "daily_equity_v1"
    persisted: bool = False
    wall_clock_ms: int | None = None
    would_insert_count: int = 0

    def __post_init__(self) -> None:
        _text(self.receipt_id, "receipt_id", 160)
        if not isinstance(self.mode, DailyEquityMaterializationMode):
            raise DataContractError("materialization mode is invalid")
        require_aware_datetime(self.generated_at, field_name="generated_at")
        if self.journal_activation_at is not None:
            require_aware_datetime(
                self.journal_activation_at,
                field_name="journal_activation_at",
            )
        _ids(self.account_refs, "account_refs")
        _ids(self.source_snapshot_ids, "source_snapshot_ids")
        _ids(self.materialized_snapshot_ids, "materialized_snapshot_ids")
        for field_name in (
            "candidate_count",
            "inserted_count",
            "duplicate_count",
            "skipped_count",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise DataContractError(f"{field_name} must be a nonnegative int")
        if self.inserted_count + self.duplicate_count > self.candidate_count:
            raise DataContractError("materialization write counts exceed candidates")
        if self.skipped_count > self.candidate_count:
            raise DataContractError("materialization skipped count exceeds candidates")
        if not isinstance(self.coverage_status, DailyEquityCoverageStatus):
            raise DataContractError("materialization coverage_status is invalid")
        _codes(self.warning_codes, "warning_codes")
        _text(self.algorithm_version, "algorithm_version", 64)
        if type(self.persisted) is not bool:
            raise DataContractError("persisted must be bool")
        if self.wall_clock_ms is not None and (
            type(self.wall_clock_ms) is not int or self.wall_clock_ms < 0
        ):
            raise DataContractError("wall_clock_ms must be a nonnegative int")
        if type(self.would_insert_count) is not int or self.would_insert_count < 0:
            raise DataContractError("would_insert_count must be a nonnegative int")
        if self.would_insert_count > self.candidate_count:
            raise DataContractError("would_insert_count exceeds candidates")
        if self.mode is not DailyEquityMaterializationMode.PERSIST and self.persisted:
            raise DataContractError("shadow/dry-run receipt cannot be persisted")


# Short names used by callers that treat the receipt as a materialization run.
DailyEquityMaterializationResult = DailyEquityMaterializationReceipt
