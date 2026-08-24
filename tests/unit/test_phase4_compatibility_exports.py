"""Keep the documented Phase 4 compatibility import paths executable."""

from application.dto import (
    activity_annotation,
    performance_daily_equity,
    performance_returns,
    trade_cycle_override,
)
from application.ports import (
    daily_equity_snapshot_repository as daily_equity_snapshot_port,
)
from application.ports import journal_activation_repository
from application.services import (
    daily_equity_service,
    performance_returns_calculator,
    returns_calculator,
    trade_cycle_overrides,
    trade_cycle_service,
)
from domain import activity_annotation as activity_annotation_domain
from domain import trade_cycle as trade_cycle_domain
from domain.activity_annotation import enums as activity_annotation_enums
from domain.activity_annotation import models as activity_annotation_models
from domain.journal import trade_cycle as journal_trade_cycle
from domain.returns import daily_equity as returns_daily_equity
from domain.trade_cycle import enums as trade_cycle_enums
from domain.trade_cycle import models as trade_cycle_models
from infrastructure.persistence import (
    daily_equity_snapshot_repository as daily_equity_snapshot_persistence,
)
from infrastructure.persistence import trade_cycle_overrides as persistence_trade_cycle_overrides
from infrastructure.persistence import trade_cycle_repository


def test_phase4_compatibility_modules_export_live_canonical_types() -> None:
    modules = (
        activity_annotation,
        performance_daily_equity,
        performance_returns,
        trade_cycle_override,
        daily_equity_snapshot_port,
        journal_activation_repository,
        daily_equity_service,
        performance_returns_calculator,
        returns_calculator,
        trade_cycle_overrides,
        trade_cycle_service,
        activity_annotation_domain,
        activity_annotation_enums,
        activity_annotation_models,
        journal_trade_cycle,
        returns_daily_equity,
        trade_cycle_domain,
        trade_cycle_enums,
        trade_cycle_models,
        daily_equity_snapshot_persistence,
        persistence_trade_cycle_overrides,
        trade_cycle_repository,
    )

    assert all(module.__all__ for module in modules if hasattr(module, "__all__"))
    assert trade_cycle_service.TradeCycleService is trade_cycle_service.TradeCycleOverrideService
    assert (
        trade_cycle_repository.SqlAlchemyTradeCycleRepository
        is trade_cycle_repository.SqlAlchemyTradeCycleOverrideRepository
    )
