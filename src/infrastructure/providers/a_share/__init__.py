"""A-share market data providers."""

from infrastructure.providers.a_share.mock_market import (
    MockAShareMarketSnapshotProvider,
)

# E2 adapters are constructed explicitly in tests / E5 bootstrap — not auto-wired.

__all__ = ["MockAShareMarketSnapshotProvider"]
