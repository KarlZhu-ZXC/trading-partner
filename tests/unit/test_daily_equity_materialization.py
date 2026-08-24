from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.dto.daily_equity import (
    DailyEquityMaterializationReceiptDTO,
    DailyEquitySnapshotDTO,
)
from application.services.daily_equity_materialization_service import (
    DailyEquityMaterializationService,
)
from domain.common.enums import VendorId
from domain.performance.enums import (
    DailyEquityCoverageStatus,
    DailyEquityMaterializationMode,
)
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.daily_equity_repository import (
    SqlAlchemyDailyEquityRepository,
)
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.orm import Base

ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


class _Clock:
    def __init__(self, value: datetime = ORIGIN) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _snapshot(snapshot_id: str, days: int, net_assets: str | None) -> AccountSnapshot:
    at = ORIGIN + timedelta(days=days)
    position = AccountPosition(
        instrument_id="equity:US:NVDA",
        side=AccountPositionSide.LONG,
        quantity=Decimal("1"),
        sellable_quantity=None,
        average_cost=Decimal("10"),
        diluted_cost=None,
        market_price=None,
        market_price_at=None,
        market_value=Decimal("999999"),
        unrealized_pnl=None,
        realized_pnl=None,
        currency="USD",
    )
    return AccountSnapshot(
        snapshot_id=snapshot_id,
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        environment=AccountEnvironment.REAL,
        base_currency="USD",
        account_as_of=at,
        fetched_at=at,
        cash=Decimal("50"),
        buying_power=None,
        net_assets=Decimal(net_assets) if net_assets is not None else None,
        margin_used=None,
        positions=(position,),
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )


def _service() -> tuple[
    SqlAlchemyDailyEquityRepository,
    DailyEquityMaterializationService,
    SqlAlchemyAccountSnapshotRepository,
]:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyDailyEquityRepository(engine)
    snapshots = SqlAlchemyAccountSnapshotRepository(engine)
    return repository, DailyEquityMaterializationService(repository, clock=_Clock()), snapshots


def test_materialization_uses_net_assets_and_never_position_value_as_nav() -> None:
    repository, service, snapshots = _service()
    source = _snapshot("source_1", 0, "1000")
    snapshots.append_account(source)
    service.activate(journal_activation_at=ORIGIN)

    receipt = service.materialize(
        snapshots=(source,),
        mode=DailyEquityMaterializationMode.PERSIST,
        transactions=(),
    )
    value = repository.list()[0]

    assert receipt.inserted_count == 1
    assert value.equity_value == Decimal("1000")
    assert value.cash_value == Decimal("50")
    assert value.gross_position_value is None
    assert value.valuation_basis == "BROKER_NET_ASSETS"
    assert value.coverage_status is DailyEquityCoverageStatus.COMPLETE


def test_missing_net_assets_is_retained_as_unavailable_not_reconstructed() -> None:
    repository, service, snapshots = _service()
    source = _snapshot("source_missing", 0, None)
    snapshots.append_account(source)
    service.activate(journal_activation_at=ORIGIN)

    receipt = service.materialize(
        snapshots=(source,),
        mode=DailyEquityMaterializationMode.PERSIST,
        transactions=(),
    )
    value = repository.list()[0]

    assert receipt.coverage_status is DailyEquityCoverageStatus.UNAVAILABLE
    assert value.equity_value is None
    assert value.gross_position_value is None
    assert "EQUITY_VALUE_UNAVAILABLE" in value.warning_codes


def test_persist_is_idempotent_by_source_snapshot_and_algorithm() -> None:
    repository, service, snapshots = _service()
    source = _snapshot("source_idempotent", 0, "100")
    snapshots.append_account(source)
    service.activate(journal_activation_at=ORIGIN)

    first = service.materialize(
        snapshots=(source,),
        mode="PERSIST",
        transactions=(),
    )
    service._clock.value = ORIGIN + timedelta(minutes=1)  # type: ignore[attr-defined]
    second = service.materialize(
        snapshots=(source,),
        mode="PERSIST",
        transactions=(),
    )

    assert first.inserted_count == 1
    assert first.duplicate_count == 0
    assert second.inserted_count == 0
    assert second.duplicate_count == 1
    assert len(repository.list()) == 1
    assert first.materialized_snapshot_ids == second.materialized_snapshot_ids


def test_shadow_and_dry_run_do_not_write_projection_rows() -> None:
    repository, service, snapshots = _service()
    source = _snapshot("source_shadow", 0, "100")
    snapshots.append_account(source)
    service.activate(journal_activation_at=ORIGIN)

    shadow = service.materialize(snapshots=(source,), mode="SHADOW")
    dry_run = service.materialize(snapshots=(source,), mode="DRY_RUN")

    assert shadow.persisted is False
    assert shadow.would_insert_count == 1
    assert dry_run.persisted is False
    assert dry_run.would_insert_count == 1
    assert repository.list() == ()


def test_activation_epoch_marks_pre_activation_rows_retrospective() -> None:
    repository, service, snapshots = _service()
    pre = _snapshot("source_pre", -2, "90")
    post = _snapshot("source_post", 1, "110")
    snapshots.append_account(pre)
    snapshots.append_account(post)
    service.activate(journal_activation_at=ORIGIN)

    receipt = service.materialize(
        snapshots=(pre, post),
        mode="PERSIST",
        transactions=(),
    )
    values = repository.list()

    assert receipt.coverage_status is DailyEquityCoverageStatus.PARTIAL
    assert "RETROSPECTIVE_ENTRY" in values[0].warning_codes
    assert "RETROSPECTIVE_ENTRY" not in values[1].warning_codes


def test_activation_is_singleton_and_conflicting_epoch_is_rejected() -> None:
    repository, service, _snapshots = _service()
    first = service.activate(journal_activation_at=ORIGIN, idempotency_key="activation-a")
    same_epoch = service.activate(journal_activation_at=ORIGIN, idempotency_key="activation-b")

    assert same_epoch.journal_activation_at == first.journal_activation_at
    try:
        service.activate(
            journal_activation_at=ORIGIN - timedelta(days=1),
            idempotency_key="activation-c",
        )
    except Exception as exc:  # noqa: BLE001 - exact typed code is asserted below
        assert getattr(exc, "code", None) == "JOURNAL_ACTIVATION_CONFLICT"
    else:
        raise AssertionError("conflicting activation epoch must fail closed")


def test_daily_equity_dto_preserves_source_ref_and_shadow_receipt_counts() -> None:
    repository, service, snapshots = _service()
    source = _snapshot("source_dto", 0, "100")
    snapshots.append_account(source)
    service.activate(journal_activation_at=ORIGIN)
    receipt = service.materialize(snapshots=(source,), mode="SHADOW")
    service.materialize(snapshots=(source,), mode="PERSIST", transactions=())
    value = DailyEquitySnapshotDTO.from_domain(repository.list()[0])
    receipt_dto = DailyEquityMaterializationReceiptDTO.from_domain(receipt)

    assert value.source_snapshot_id == "source_dto"
    assert value.equity_value == Decimal("100")
    assert receipt_dto.would_insert_count == 1
