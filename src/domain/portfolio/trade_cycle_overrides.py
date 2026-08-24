"""Append-only manual overrides for rebuildable Trade Cycle projections.

The calculator's ``TradeCycleProjection`` remains the algorithmic source.  An
override only changes a derived view and carries an explicit recomputation
warning when the original aggregate P/L cannot be safely split or merged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from domain.common.errors import DataContractError, InvalidTradeCycleOverride
from domain.common.time import require_aware_datetime
from domain.portfolio.models import TradeCycle, TradeCycleProjection


class TradeCycleOverrideOperation(StrEnum):
    SPLIT = "SPLIT"
    MERGE = "MERGE"
    RELINK = "RELINK"

    @classmethod
    def _missing_(cls, value: object) -> TradeCycleOverrideOperation | None:
        if isinstance(value, str):
            normalized = value.strip().upper()
            for item in cls:
                if item.value == normalized:
                    return item
        return None


@dataclass(frozen=True, slots=True)
class TradeCycleOverrideRevision:
    """One immutable split/merge/relink intent for a cycle projection."""

    override_id: str
    root_cycle_id: str
    version: int
    operation: TradeCycleOverrideOperation
    cycle_ids: tuple[str, ...]
    activity_ids: tuple[str, ...]
    split_groups: tuple[tuple[str, ...], ...]
    target_cycle_id: str | None
    algorithm_version: str
    note: str | None
    actor: str
    authorization_note: str
    idempotency_key: str
    created_at: datetime
    expected_version: int | None = None

    def __post_init__(self) -> None:
        if not self.override_id.startswith("trade_cycle_override_"):
            raise DataContractError("override_id must use trade_cycle_override_ prefix")
        _text(self.root_cycle_id, "root_cycle_id", 160)
        if type(self.version) is not int or self.version < 1:
            raise DataContractError("override version must be positive")
        if not isinstance(self.operation, TradeCycleOverrideOperation):
            raise DataContractError("trade cycle override operation is invalid")
        _unique_texts(self.cycle_ids, "cycle_ids", 160, minimum=1)
        _unique_texts(self.activity_ids, "activity_ids", 256)
        for group in self.split_groups:
            _unique_texts(group, "split_group", 256, minimum=1)
        if self.target_cycle_id is not None:
            _text(self.target_cycle_id, "target_cycle_id", 160)
        _text(self.algorithm_version, "algorithm_version", 64)
        if self.note is not None:
            _text(self.note, "note", 2_000)
        if self.actor not in {"user", "external_agent"}:
            raise DataContractError("override actor must be user or external_agent")
        _text(self.authorization_note, "authorization_note", 4_000)
        _text(self.idempotency_key, "idempotency_key", 200)
        require_aware_datetime(self.created_at, field_name="created_at")
        if self.expected_version is not None and (
            type(self.expected_version) is not int or self.expected_version < 0
        ):
            raise DataContractError("expected_version must be a nonnegative integer")

        if self.root_cycle_id not in self.cycle_ids and self.operation is not (
            TradeCycleOverrideOperation.RELINK
        ):
            raise DataContractError("root_cycle_id must be one of cycle_ids")
        if self.operation is TradeCycleOverrideOperation.SPLIT:
            if len(self.cycle_ids) != 1 or len(self.split_groups) < 2:
                raise DataContractError("SPLIT requires one cycle and at least two split groups")
            if self.activity_ids or self.target_cycle_id is not None:
                raise DataContractError("SPLIT cannot set activity_ids or target_cycle_id")
        elif self.operation is TradeCycleOverrideOperation.MERGE:
            if len(self.cycle_ids) < 2:
                raise DataContractError("MERGE requires at least two cycles")
            if self.activity_ids or self.split_groups:
                raise DataContractError("MERGE cannot set activity_ids or split_groups")
        else:
            if not self.activity_ids or self.target_cycle_id is None:
                raise DataContractError("RELINK requires activities and target_cycle_id")
            if self.split_groups:
                raise DataContractError("RELINK cannot set split_groups")
            if len(set(self.cycle_ids) - {self.target_cycle_id}) < 1:
                raise DataContractError("RELINK requires at least one source besides target")

    @property
    def cycle_id(self) -> str:
        return self.root_cycle_id

    @property
    def source_cycle_ids(self) -> tuple[str, ...]:
        return self.cycle_ids

    @property
    def relink_activity_ids(self) -> tuple[str, ...]:
        return self.activity_ids

    @property
    def groups(self) -> tuple[tuple[str, ...], ...]:
        return self.split_groups


@dataclass(frozen=True, slots=True)
class TradeCycleOverrideImpact:
    operation: TradeCycleOverrideOperation
    source_cycle_ids: tuple[str, ...]
    result_cycle_ids: tuple[str, ...]
    moved_activity_ids: tuple[str, ...]
    before_activity_count: int
    after_activity_count: int
    warning_codes: tuple[str, ...]
    recompute_required: bool

    @property
    def affected_cycle_ids(self) -> tuple[str, ...]:
        return self.source_cycle_ids


@dataclass(frozen=True, slots=True)
class TradeCycleOverrideProjection:
    """Algorithm output plus the effective, manually overridden view."""

    algorithm_projection: TradeCycleProjection
    effective_projection: TradeCycleProjection
    applied_revisions: tuple[TradeCycleOverrideRevision, ...]
    impacts: tuple[TradeCycleOverrideImpact, ...]

    @property
    def original_projection(self) -> TradeCycleProjection:
        """Compatibility spelling emphasizing that algorithm facts are retained."""

        return self.algorithm_projection

    @property
    def preview(self) -> tuple[TradeCycleOverrideImpact, ...]:
        return self.impacts

    @property
    def algorithm_cycles(self) -> tuple[TradeCycle, ...]:
        return self.algorithm_projection.cycles

    @property
    def effective_cycles(self) -> tuple[TradeCycle, ...]:
        return self.effective_projection.cycles


def apply_trade_cycle_overrides(
    projection: TradeCycleProjection,
    revisions: tuple[TradeCycleOverrideRevision, ...] = (),
) -> TradeCycleOverrideProjection:
    """Apply revisions deterministically without mutating algorithm cycles."""

    current = tuple(projection.cycles)
    impacts: list[TradeCycleOverrideImpact] = []
    applied: list[TradeCycleOverrideRevision] = []
    ordered = tuple(
        sorted(revisions, key=lambda item: (item.created_at, item.version, item.override_id))
    )
    for revision in ordered:
        if revision.algorithm_version != projection.algorithm_version:
            raise InvalidTradeCycleOverride(
                "override algorithm_version does not match the supplied projection",
                details={
                    "override_algorithm_version": revision.algorithm_version,
                    "projection_algorithm_version": projection.algorithm_version,
                },
            )
        current, impact = _apply_one(current, revision)
        impacts.append(impact)
        applied.append(revision)

    warnings = set(projection.warning_codes)
    warnings.update(code for impact in impacts for code in impact.warning_codes)
    effective = replace(
        projection,
        cycles=tuple(current),
        status=(
            projection.status
            if not impacts
            else type(projection.status).INCOMPLETE
        ),
        warning_codes=tuple(sorted(warnings)),
    )
    return TradeCycleOverrideProjection(
        algorithm_projection=projection,
        effective_projection=effective,
        applied_revisions=tuple(applied),
        impacts=tuple(impacts),
    )


# Short aliases used by projection callers.
apply_overrides = apply_trade_cycle_overrides
TradeCycleOverrideResult = TradeCycleOverrideProjection


def _apply_one(
    cycles: tuple[TradeCycle, ...], revision: TradeCycleOverrideRevision
) -> tuple[tuple[TradeCycle, ...], TradeCycleOverrideImpact]:
    by_id = {item.cycle_id: item for item in cycles}
    missing = tuple(item for item in revision.cycle_ids if item not in by_id)
    if missing:
        raise InvalidTradeCycleOverride(
            "override references a cycle absent from the current projection",
            details={"missing_cycle_ids": missing},
        )
    if revision.operation is TradeCycleOverrideOperation.SPLIT:
        source = by_id[revision.cycle_ids[0]]
        source_ids = set(source.activity_ids)
        groups = tuple(tuple(group) for group in revision.split_groups)
        flattened = tuple(activity_id for group in groups for activity_id in group)
        if len(flattened) != len(set(flattened)) or set(flattened) != source_ids:
            raise InvalidTradeCycleOverride(
                "SPLIT groups must partition the source cycle activities exactly"
            )
        replacements = tuple(
            _invalidated_cycle(
                source,
                cycle_id=_derived_cycle_id(source.cycle_id, revision, index),
                activity_ids=group,
                warning_code="MANUAL_OVERRIDE_SPLIT",
                reentry_of_cycle_id=source.reentry_of_cycle_id if index == 0 else None,
            )
            for index, group in enumerate(groups, start=1)
        )
        result = tuple(
            item for item in cycles if item.cycle_id != source.cycle_id
        ) + replacements
        impact = _impact(
            revision,
            source_ids=(source.cycle_id,),
            result_ids=tuple(item.cycle_id for item in replacements),
            moved_ids=flattened,
            before=len(source.activity_ids),
            after=len(flattened),
            warning="MANUAL_OVERRIDE_SPLIT",
        )
        return result, impact

    if revision.operation is TradeCycleOverrideOperation.MERGE:
        selected = tuple(by_id[item] for item in revision.cycle_ids)
        _require_same_cycle_scope(selected)
        activity_ids = tuple(
            activity_id for cycle in selected for activity_id in cycle.activity_ids
        )
        if len(activity_ids) != len(set(activity_ids)):
            raise InvalidTradeCycleOverride("MERGE source cycles contain duplicate activities")
        target_id = revision.target_cycle_id or _derived_cycle_id(
            selected[0].cycle_id, revision, 1, operation="merge"
        )
        merged = _invalidated_cycle(
            selected[0],
            cycle_id=target_id,
            activity_ids=activity_ids,
            warning_code="MANUAL_OVERRIDE_MERGE",
        )
        source_set = set(revision.cycle_ids)
        result = tuple(item for item in cycles if item.cycle_id not in source_set) + (merged,)
        impact = _impact(
            revision,
            source_ids=revision.cycle_ids,
            result_ids=(target_id,),
            moved_ids=activity_ids,
            before=len(activity_ids),
            after=len(activity_ids),
            warning="MANUAL_OVERRIDE_MERGE",
        )
        return result, impact

    target = by_id.get(revision.target_cycle_id or "")
    if target is None:
        raise InvalidTradeCycleOverride("RELINK target cycle is absent from the projection")
    source_cycle_ids = tuple(
        item for item in revision.cycle_ids if item != revision.target_cycle_id
    )
    source_cycles = tuple(by_id[item] for item in source_cycle_ids)
    _require_same_cycle_scope((*source_cycles, target))
    memberships = {
        activity_id: cycle
        for cycle in source_cycles
        for activity_id in cycle.activity_ids
    }
    missing_activities = tuple(
        item for item in revision.activity_ids if item not in memberships
    )
    if missing_activities:
        raise InvalidTradeCycleOverride(
            "RELINK activity is absent from the source cycles",
            details={"missing_activity_ids": missing_activities},
        )
    moved = set(revision.activity_ids)
    updated: dict[str, TradeCycle] = {}
    for cycle in source_cycles:
        remaining = tuple(item for item in cycle.activity_ids if item not in moved)
        if remaining:
            updated[cycle.cycle_id] = _invalidated_cycle(
                cycle,
                cycle_id=cycle.cycle_id,
                activity_ids=remaining,
                warning_code="MANUAL_OVERRIDE_RELINK",
            )
    target_activity_ids = tuple(
        item for item in target.activity_ids if item not in moved
    ) + revision.activity_ids
    updated[target.cycle_id] = _invalidated_cycle(
        target,
        cycle_id=target.cycle_id,
        activity_ids=target_activity_ids,
        warning_code="MANUAL_OVERRIDE_RELINK",
    )
    source_set = set(revision.cycle_ids) | {target.cycle_id}
    result = tuple(
        updated.get(item.cycle_id, item)
        for item in cycles
        if item.cycle_id not in source_set or item.cycle_id in updated
    )
    impact = _impact(
        revision,
        source_ids=revision.cycle_ids,
        result_ids=tuple(updated),
        moved_ids=revision.activity_ids,
        before=sum(len(item.activity_ids) for item in source_cycles) + len(target.activity_ids),
        after=sum(len(item.activity_ids) for item in updated.values()),
        warning="MANUAL_OVERRIDE_RELINK",
    )
    return result, impact


def _invalidated_cycle(
    cycle: TradeCycle,
    *,
    cycle_id: str,
    activity_ids: tuple[str, ...],
    warning_code: str,
    reentry_of_cycle_id: str | None = None,
) -> TradeCycle:
    warnings = tuple(sorted({*cycle.warning_codes, warning_code, "MANUAL_RECOMPUTE_REQUIRED"}))
    return replace(
        cycle,
        cycle_id=cycle_id,
        activity_ids=tuple(activity_ids),
        status=type(cycle.status).UNRESOLVED,
        opening_count=0,
        add_count=0,
        reduce_count=0,
        ending_quantity=None,
        gross_realized_pnl=None,
        net_realized_pnl=None,
        maximum_deployed_capital=None,
        holding_duration_seconds=None,
        reentry_of_cycle_id=reentry_of_cycle_id,
        quality=type(cycle.quality).INCOMPLETE,
        warning_codes=warnings,
    )


def _derived_cycle_id(
    source_cycle_id: str,
    revision: TradeCycleOverrideRevision,
    index: int,
    *,
    operation: str = "split",
) -> str:
    digest = hashlib.sha256(
        f"{source_cycle_id}|{revision.override_id}|{operation}|{index}".encode()
    ).hexdigest()[:16]
    return f"{source_cycle_id}:{operation}:{digest}"


def _impact(
    revision: TradeCycleOverrideRevision,
    *,
    source_ids: tuple[str, ...],
    result_ids: tuple[str, ...],
    moved_ids: tuple[str, ...],
    before: int,
    after: int,
    warning: str,
) -> TradeCycleOverrideImpact:
    return TradeCycleOverrideImpact(
        operation=revision.operation,
        source_cycle_ids=source_ids,
        result_cycle_ids=result_ids,
        moved_activity_ids=moved_ids,
        before_activity_count=before,
        after_activity_count=after,
        warning_codes=(warning, "MANUAL_RECOMPUTE_REQUIRED"),
        recompute_required=True,
    )


def _require_same_cycle_scope(cycles: tuple[TradeCycle, ...]) -> None:
    if not cycles:
        raise InvalidTradeCycleOverride("override requires at least one cycle")
    first = cycles[0]
    scope = (first.account_ref, first.provider, first.instrument_id, first.currency)
    if any(
        (item.account_ref, item.provider, item.instrument_id, item.currency) != scope
        for item in cycles[1:]
    ):
        raise InvalidTradeCycleOverride(
            "override cycles must share account, provider, instrument, and currency"
        )


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise DataContractError(f"{field} must be bounded non-blank text")
    return value.strip()


def _unique_texts(
    values: tuple[str, ...], field: str, maximum: int, *, minimum: int = 0
) -> None:
    if not isinstance(values, tuple) or len(values) < minimum:
        raise DataContractError(f"{field} must contain at least {minimum} items")
    normalized = tuple(_text(value, field, maximum) for value in values)
    if len(normalized) != len(set(normalized)):
        raise DataContractError(f"{field} must contain unique values")


__all__ = [
    "TradeCycleOverrideImpact",
    "TradeCycleOverrideOperation",
    "TradeCycleOverrideProjection",
    "TradeCycleOverrideResult",
    "TradeCycleOverrideRevision",
    "apply_overrides",
    "apply_trade_cycle_overrides",
]
