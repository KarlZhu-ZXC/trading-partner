"""Compatibility path for Trade Cycle override persistence."""

from infrastructure.persistence.trade_cycle_override_repository import (
    SqlAlchemyTradeCycleOverrideRepository,
)

SqlAlchemyTradeCycleRepository = SqlAlchemyTradeCycleOverrideRepository

__all__ = ["SqlAlchemyTradeCycleOverrideRepository", "SqlAlchemyTradeCycleRepository"]
