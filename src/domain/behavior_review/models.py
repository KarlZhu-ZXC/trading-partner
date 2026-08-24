"""Append-only behavior-review periods and action recurrence facts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from domain.behavior_review.enums import (
    BehaviorActionStatus,
    BehaviorReviewPeriodKind,
    BehaviorReviewRunStatus,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.review_item.enums import ReviewItemSourceType
from domain.review_item.models import ReviewItem

BEHAVIOR_REVIEW_SCHEMA_VERSION = 1
BEHAVIOR_REVIEW_ALGORITHM_VERSION = "behavior_review_v1"
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized


def _ids(values: tuple[str, ...], field: str, maximum: int = 256) -> None:
    if not isinstance(values, tuple):
        raise DataContractError(f"{field} must be a tuple")
    if len(values) != len(set(values)):
        raise DataContractError(f"{field} must be unique")
    for value in values:
        _text(value, field, maximum)


def _period_key(
    kind: BehaviorReviewPeriodKind,
    start: datetime,
    end: datetime,
) -> str:
    return f"{kind.value.lower()}:{start.isoformat()}:{end.isoformat()}"


def _next_month(value: date) -> date:
    return date(value.year + (value.month // 12), (value.month % 12) + 1, 1)


def _validate_period_shape(
    kind: BehaviorReviewPeriodKind,
    start: datetime,
    end: datetime,
) -> None:
    start_date = start.date()
    end_date = end.date()
    if kind is BehaviorReviewPeriodKind.WEEKLY:
        if end_date - start_date != timedelta(days=7):
            raise DataContractError("WEEKLY cohort must cover exactly seven calendar days")
        return
    if kind is BehaviorReviewPeriodKind.MONTHLY:
        if start_date.day != 1 or end_date != _next_month(start_date):
            raise DataContractError("MONTHLY cohort must cover one calendar month")
        return
    quarter_month = ((start_date.month - 1) // 3) * 3 + 1
    if start_date.day != 1 or start_date.month != quarter_month:
        raise DataContractError("QUARTERLY cohort must start on a quarter boundary")
    next_quarter = start_date.month + 3
    next_year = start_date.year + (next_quarter // 13)
    next_month = ((next_quarter - 1) % 12) + 1
    if end_date != date(next_year, next_month, 1):
        raise DataContractError("QUARTERLY cohort must cover one calendar quarter")


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_action_text(value: str) -> str:
    """Normalize only whitespace/case for a stable action identity."""

    return " ".join(value.split()).casefold()


def stable_action_key(action_text: str, *, action_code: str | None = None) -> str:
    """Return a stable key shared by equivalent action items across periods."""

    normalized_text = normalize_action_text(_text(action_text, "action_text", 2_000))
    code = action_code.strip().upper() if action_code is not None else None
    if code is not None and _CODE_RE.fullmatch(code) is None:
        raise DataContractError("action_code must be uppercase machine code")
    payload = (
        {"action_code": code}
        if code is not None
        else {"action_text": normalized_text}
    )
    return f"behavior_action_{_stable_hash(payload)}"


@dataclass(frozen=True, slots=True)
class BehaviorReviewCohort:
    """An exact period and source-reference cohort used by one Review Run."""

    period_kind: BehaviorReviewPeriodKind
    period_start: datetime
    period_end: datetime
    strategy_code: str | None = None
    strategy_version: str | None = None
    horizon: str | None = None
    instrument_ids: tuple[str, ...] = ()
    currency: str | None = None
    cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    retro_run_ids: tuple[str, ...] = ()
    retro_review_ids: tuple[str, ...] = ()
    review_item_source_keys: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.period_kind, BehaviorReviewPeriodKind):
            raise DataContractError("period_kind is invalid")
        require_aware_datetime(self.period_start, field_name="period_start")
        require_aware_datetime(self.period_end, field_name="period_end")
        if self.period_start >= self.period_end:
            raise DataContractError("period_end must follow period_start")
        _validate_period_shape(self.period_kind, self.period_start, self.period_end)
        for field in ("strategy_code", "strategy_version", "horizon", "currency"):
            value = getattr(self, field)
            if value is not None:
                _text(value, field, 128 if field != "currency" else 32)
        _ids(self.instrument_ids, "instrument_ids")
        _ids(self.cycle_ids, "cycle_ids")
        _ids(self.decision_ids, "decision_ids")
        _ids(self.retro_run_ids, "retro_run_ids")
        _ids(self.retro_review_ids, "retro_review_ids")
        _ids(self.review_item_source_keys, "review_item_source_keys", 300)
        _ids(self.subject_ids, "subject_ids")

    @property
    def period_key(self) -> str:
        return _period_key(self.period_kind, self.period_start, self.period_end)

    @property
    def cohort_key(self) -> str:
        payload = {
            "period_kind": self.period_kind.value,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "strategy_code": self.strategy_code,
            "strategy_version": self.strategy_version,
            "horizon": self.horizon,
            "instrument_ids": self.instrument_ids,
            "currency": self.currency,
            "cycle_ids": self.cycle_ids,
            "decision_ids": self.decision_ids,
            "retro_run_ids": self.retro_run_ids,
            "retro_review_ids": self.retro_review_ids,
            "review_item_source_keys": self.review_item_source_keys,
            "subject_ids": self.subject_ids,
        }
        return f"behavior_cohort_{_stable_hash(payload)}"


@dataclass(frozen=True, slots=True)
class BehaviorActionInput:
    """One action item extracted from a durable Trade Retro Review."""

    action_text: str
    action_code: str | None = None
    review_item_source_keys: tuple[str, ...] = ()
    retro_review_ids: tuple[str, ...] = ()
    cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.action_text, "action_text", 2_000)
        if self.action_code is not None:
            code = _text(self.action_code, "action_code", 128).upper()
            if _CODE_RE.fullmatch(code) is None:
                raise DataContractError("action_code must be uppercase machine code")
            object.__setattr__(self, "action_code", code)
        _ids(self.review_item_source_keys, "review_item_source_keys", 300)
        _ids(self.retro_review_ids, "retro_review_ids")
        _ids(self.cycle_ids, "cycle_ids")
        _ids(self.decision_ids, "decision_ids")

    @property
    def stable_key(self) -> str:
        return stable_action_key(self.action_text, action_code=self.action_code)

    @classmethod
    def from_trade_retro_review_item(
        cls,
        item: ReviewItem,
        *,
        retro_review_ids: tuple[str, ...] = (),
        cycle_ids: tuple[str, ...] = (),
        decision_ids: tuple[str, ...] = (),
        action_code: str | None = None,
    ) -> BehaviorActionInput:
        """Reference an existing Trade Retro ReviewItem without copying state."""

        if item.source_type is not ReviewItemSourceType.TRADE_RETRO:
            raise DataContractError(
                "behavior actions must reference a Trade Retro ReviewItem"
            )
        return cls(
            action_text=item.detail,
            action_code=action_code,
            review_item_source_keys=(item.source_key,),
            retro_review_ids=retro_review_ids,
            cycle_ids=cycle_ids,
            decision_ids=decision_ids,
        )


@dataclass(frozen=True, slots=True)
class BehaviorActionObservation:
    """One immutable action observation in one BehaviorReviewRun."""

    observation_id: str
    run_id: str
    stable_key: str
    action_text: str
    action_code: str | None
    status: BehaviorActionStatus
    occurrence_count: int
    period_key: str
    cohort_key: str
    review_item_source_keys: tuple[str, ...]
    retro_review_ids: tuple[str, ...]
    cycle_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    observed_at: datetime
    previous_observation_id: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None

    def __post_init__(self) -> None:
        if not self.observation_id.startswith("behavior_action_observation_"):
            raise DataContractError("observation_id has an invalid prefix")
        if not self.run_id.startswith("behavior_review_"):
            raise DataContractError("run_id has an invalid prefix")
        if not self.stable_key.startswith("behavior_action_"):
            raise DataContractError("stable_key has an invalid prefix")
        _text(self.action_text, "action_text", 2_000)
        if self.action_code is not None and _CODE_RE.fullmatch(self.action_code) is None:
            raise DataContractError("action_code must be uppercase machine code")
        if not isinstance(self.status, BehaviorActionStatus):
            raise DataContractError("action status is invalid")
        if type(self.occurrence_count) is not int or self.occurrence_count < 1:
            raise DataContractError("occurrence_count must be positive")
        _text(self.period_key, "period_key", 300)
        _text(self.cohort_key, "cohort_key", 128)
        _ids(self.review_item_source_keys, "review_item_source_keys", 300)
        _ids(self.retro_review_ids, "retro_review_ids")
        _ids(self.cycle_ids, "cycle_ids")
        _ids(self.decision_ids, "decision_ids")
        require_aware_datetime(self.observed_at, field_name="observed_at")
        if self.previous_observation_id is not None:
            _text(self.previous_observation_id, "previous_observation_id", 160)
        if self.resolved_at is not None:
            require_aware_datetime(self.resolved_at, field_name="resolved_at")
        if self.resolution_note is not None:
            _text(self.resolution_note, "resolution_note", 2_000)
        if self.status is BehaviorActionStatus.RESOLVED and self.resolved_at is None:
            raise DataContractError("RESOLVED action requires resolved_at")
        if self.status is not BehaviorActionStatus.RESOLVED and self.resolved_at is not None:
            raise DataContractError("only RESOLVED action may set resolved_at")


@dataclass(frozen=True, slots=True)
class BehaviorReviewRun:
    """Append-only deterministic Review Run; no score and no execution effect."""

    run_id: str
    cohort: BehaviorReviewCohort
    generated_at: datetime
    status: BehaviorReviewRunStatus
    source_read_complete: bool
    action_observations: tuple[BehaviorActionObservation, ...]
    warning_codes: tuple[str, ...]
    idempotency_key: str
    source_error_code: str | None = None
    algorithm_version: str = BEHAVIOR_REVIEW_ALGORITHM_VERSION
    schema_version: int = BEHAVIOR_REVIEW_SCHEMA_VERSION
    execution_effect: bool = False

    def __post_init__(self) -> None:
        if not self.run_id.startswith("behavior_review_"):
            raise DataContractError("run_id has an invalid prefix")
        require_aware_datetime(self.generated_at, field_name="generated_at")
        if not isinstance(self.status, BehaviorReviewRunStatus):
            raise DataContractError("behavior review status is invalid")
        if type(self.source_read_complete) is not bool:
            raise DataContractError("source_read_complete must be bool")
        if self.status is BehaviorReviewRunStatus.COMPLETE and not self.source_read_complete:
            raise DataContractError("COMPLETE review requires complete source read")
        if self.status is BehaviorReviewRunStatus.UNAVAILABLE and self.source_read_complete:
            raise DataContractError("UNAVAILABLE review requires incomplete source read")
        keys = tuple(item.stable_key for item in self.action_observations)
        if len(keys) != len(set(keys)):
            raise DataContractError("action observations must have unique stable keys")
        for item in self.action_observations:
            if item.run_id != self.run_id or item.cohort_key != self.cohort.cohort_key:
                raise DataContractError("action observation does not belong to this run")
        _ids(self.warning_codes, "warning_codes", 160)
        _text(self.idempotency_key, "idempotency_key", 200)
        if self.source_error_code is not None:
            _text(self.source_error_code, "source_error_code", 160)
            if self.source_read_complete:
                raise DataContractError("source_error_code requires incomplete source read")
        _text(self.algorithm_version, "algorithm_version", 64)
        if self.schema_version != BEHAVIOR_REVIEW_SCHEMA_VERSION:
            raise DataContractError("unsupported behavior review schema version")
        if self.execution_effect is not False:
            raise DataContractError("behavior review cannot have execution effect")


# Compatibility-friendly names for callers using the shorter product language.
BehaviorReviewPeriod = BehaviorReviewCohort
BehaviorReviewAction = BehaviorActionObservation
BehaviorReview = BehaviorReviewRun


__all__ = [
    "BEHAVIOR_REVIEW_ALGORITHM_VERSION",
    "BEHAVIOR_REVIEW_SCHEMA_VERSION",
    "BehaviorActionInput",
    "BehaviorActionObservation",
    "BehaviorReview",
    "BehaviorReviewAction",
    "BehaviorReviewCohort",
    "BehaviorReviewPeriod",
    "BehaviorReviewRun",
    "normalize_action_text",
    "stable_action_key",
]
