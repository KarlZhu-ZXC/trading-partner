"""Focused Trade Cycle manual split/merge/relink override contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from application.dto.trade_cycle_overrides import TradeCycleOverrideAppendInput
from application.services.trade_cycle_override_service import TradeCycleOverrideService
from domain.common.enums import VendorId
from domain.common.errors import (
    IdempotencyConflict,
    ImmutableResearchRecord,
    InvalidTradeCycleOverride,
    TradeCycleOverrideVersionConflict,
)
from domain.common.ids import EntityIdPrefix
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    TradeCycleClassification,
    TradeCycleQuality,
    TradeCycleStatus,
)
from domain.portfolio.models import TradeCycle, TradeCycleProjection
from domain.portfolio.trade_cycle_overrides import (
    TradeCycleOverrideOperation,
    TradeCycleOverrideRevision,
    apply_trade_cycle_overrides,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm import TradeCycleOverrideRevisionRow
from infrastructure.persistence.trade_cycle_override_repository import (
    SqlAlchemyTradeCycleOverrideRepository,
)

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: EntityIdPrefix) -> str:
        self.value += 1
        return f"{prefix.value}_00000000-0000-7000-8000-{self.value:012d}"


def _cycle(cycle_id: str, activity_ids: tuple[str, ...]) -> TradeCycle:
    return TradeCycle(
        cycle_id=cycle_id,
        account_ref="acct-1",
        provider=VendorId.BROKER,
        instrument_id="equity:US:AAPL",
        currency="USD",
        activity_ids=activity_ids,
        opened_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        closed_at=datetime(2026, 8, 20, 15, tzinfo=UTC),
        status=TradeCycleStatus.CLOSED,
        classification=TradeCycleClassification.UNCLASSIFIED,
        opening_count=1,
        add_count=1,
        reduce_count=1,
        ending_quantity=Decimal("0"),
        gross_realized_pnl=Decimal("10"),
        net_realized_pnl=Decimal("9"),
        maximum_deployed_capital=Decimal("100"),
        holding_duration_seconds=18_000,
        reentry_of_cycle_id=None,
        quality=TradeCycleQuality.COMPLETE,
        warning_codes=(),
    )


def _projection(*cycles: TradeCycle) -> TradeCycleProjection:
    return TradeCycleProjection(
        cycles=tuple(cycles),
        status=TradeCycleQuality.COMPLETE,
        coverage_status=AccountActivityCoverageStatus.COMPLETE,
        warning_codes=(),
    )


def _revision(
    *,
    operation: TradeCycleOverrideOperation,
    root: str,
    version: int,
    cycle_ids: tuple[str, ...],
    activity_ids: tuple[str, ...] = (),
    split_groups: tuple[tuple[str, ...], ...] = (),
    target: str | None = None,
    key: str | None = None,
) -> TradeCycleOverrideRevision:
    return TradeCycleOverrideRevision(
        override_id=f"trade_cycle_override_{version:032x}",
        root_cycle_id=root,
        version=version,
        operation=operation,
        cycle_ids=cycle_ids,
        activity_ids=activity_ids,
        split_groups=split_groups,
        target_cycle_id=target,
        algorithm_version="trade_cycle_v1",
        note="manual review",
        actor="user",
        authorization_note="User explicitly corrected the deterministic grouping.",
        idempotency_key=key or f"override-{version}",
        created_at=NOW,
    )


def test_split_keeps_algorithm_cycle_and_invalidates_effective_metrics() -> None:
    original = _cycle("cycle-1", ("a", "b", "c", "d"))
    revision = _revision(
        operation=TradeCycleOverrideOperation.SPLIT,
        root="cycle-1",
        version=1,
        cycle_ids=("cycle-1",),
        split_groups=(("a", "b"), ("c", "d")),
    )

    result = apply_trade_cycle_overrides(_projection(original), (revision,))

    assert result.algorithm_projection.cycles == (original,)
    assert len(result.effective_projection.cycles) == 2
    assert {item.activity_ids for item in result.effective_projection.cycles} == {
        ("a", "b"),
        ("c", "d"),
    }
    assert all(item.net_realized_pnl is None for item in result.effective_projection.cycles)
    assert result.effective_projection.status is TradeCycleQuality.INCOMPLETE
    assert result.impacts[0].recompute_required is True


def test_merge_and_relink_are_deterministic_and_preserve_source_projection() -> None:
    first = _cycle("cycle-1", ("a", "b"))
    second = _cycle("cycle-2", ("c", "d"))
    merge = _revision(
        operation=TradeCycleOverrideOperation.MERGE,
        root="cycle-1",
        version=1,
        cycle_ids=("cycle-1", "cycle-2"),
    )
    merged = apply_trade_cycle_overrides(_projection(first, second), (merge,))
    assert _projection(first, second).cycles == merged.algorithm_projection.cycles
    assert len(merged.effective_projection.cycles) == 1
    assert merged.effective_projection.cycles[0].activity_ids == ("a", "b", "c", "d")

    relink = _revision(
        operation=TradeCycleOverrideOperation.RELINK,
        root="cycle-1",
        version=1,
        cycle_ids=("cycle-1", "cycle-2"),
        activity_ids=("b",),
        target="cycle-2",
    )
    relinked = apply_trade_cycle_overrides(_projection(first, second), (relink,))
    by_id = {item.cycle_id: item for item in relinked.effective_projection.cycles}
    assert by_id["cycle-1"].activity_ids == ("a",)
    assert by_id["cycle-2"].activity_ids == ("c", "d", "b")


def test_override_rejects_non_partitioned_split_and_missing_relink_activity() -> None:
    original = _cycle("cycle-1", ("a", "b"))
    bad_split = _revision(
        operation=TradeCycleOverrideOperation.SPLIT,
        root="cycle-1",
        version=1,
        cycle_ids=("cycle-1",),
        split_groups=(("a",), ("a",)),
    )
    with pytest.raises(InvalidTradeCycleOverride):
        apply_trade_cycle_overrides(_projection(original), (bad_split,))

    bad_relink = _revision(
        operation=TradeCycleOverrideOperation.RELINK,
        root="cycle-1",
        version=1,
        cycle_ids=("cycle-1",),
        activity_ids=("missing",),
        target="cycle-2",
    )
    with pytest.raises(InvalidTradeCycleOverride):
        apply_trade_cycle_overrides(_projection(original, _cycle("cycle-2", ("c",))), (bad_relink,))


def test_repository_and_service_are_versioned_idempotent_append_only() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyTradeCycleOverrideRepository(engine)
    service = TradeCycleOverrideService(repository, _Clock(), _Ids())
    request = TradeCycleOverrideAppendInput(
        cycle_id="cycle-1",
        operation="SPLIT",
        split_groups=(("a",), ("b",)),
        actor="user",
        authorization_note="User explicitly split the cycle.",
        idempotency_key="override-1",
        expected_version=0,
    )

    first = service.append_revision(request)
    duplicate = service.append_revision(request)

    assert first == duplicate
    assert first.version == 1
    with pytest.raises(IdempotencyConflict):
        service.append_revision(
            request.model_copy(
                update={"idempotency_key": "override-1", "note": "different"}
            )
        )
    with pytest.raises(TradeCycleOverrideVersionConflict):
        service.append_revision(
            request.model_copy(update={"idempotency_key": "override-2", "expected_version": 0})
        )
    assert len(service.list_revisions(root_cycle_id="cycle-1")) == 1
    preview = service.preview(_projection(_cycle("cycle-1", ("a", "b"))))
    assert len(preview.impacts) == 1
    assert preview.impacts[0].operation is TradeCycleOverrideOperation.SPLIT

    with pytest.raises(ImmutableResearchRecord):
        from sqlalchemy.orm import Session

        with Session(engine) as session, session.begin():
            row = session.get(TradeCycleOverrideRevisionRow, first.override_id)
            assert row is not None
            row.note = "mutated"
            session.flush()
