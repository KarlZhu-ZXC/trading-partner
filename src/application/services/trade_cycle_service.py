"""Compatibility path for Trade Cycle override application service."""

from application.services.trade_cycle_override_service import TradeCycleOverrideService

TradeCycleService = TradeCycleOverrideService

__all__ = ["TradeCycleOverrideService", "TradeCycleService"]
