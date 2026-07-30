"""Compact account and portfolio operation adapters."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.account_transactions import AccountGetTransactionsInput
from application.dto.portfolio import (
    AccountGetPositionsInput,
    AccountGetSnapshotInput,
    PortfolioAnalyzeInput,
    PortfolioSimulateAdditionInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_portfolio_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact account and portfolio operation adapters."""

    # --------------------------------------------------- Phase 1I account/portfolio
    async def account_get(
        operation: Literal["positions", "refresh", "transactions"] = "positions",
        providers: tuple[str, ...] = (),
        as_of: datetime | None = None,
        snapshot_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read durable positions, explicitly refresh accounts, or fetch transactions."""
        if operation == "positions":
            return account_get_positions(snapshot_id)
        if operation == "transactions":
            return await account_get_transactions(providers, start, end, limit)
        if operation != "refresh":
            raise ValueError("operation must be positions, refresh, or transactions")
        try:
            inp = AccountGetSnapshotInput.model_validate({"providers": providers, "as_of": as_of})
            envelope = await container.services.portfolio.get_account_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def account_get_positions(snapshot_id: str | None = None) -> dict[str, Any]:
        """Return positions from one snapshot or the latest durable accounts."""
        try:
            inp = AccountGetPositionsInput.model_validate({"snapshot_id": snapshot_id})
            envelope = container.services.portfolio.get_account_positions(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_analyze(
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> dict[str, Any]:
        """Compute deterministic gross exposure without implicit FX conversion."""
        try:
            inp = PortfolioAnalyzeInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "base_currency": base_currency,
                }
            )
            envelope = container.services.portfolio.analyze_portfolio(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_simulate_addition(
        instrument_id: str,
        quantity: Decimal,
        assumed_price: Decimal,
        currency: str,
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> dict[str, Any]:
        """Compare gross exposure before/after a hypothetical non-executing addition."""
        try:
            inp = PortfolioSimulateAdditionInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "instrument_id": instrument_id,
                    "quantity": quantity,
                    "assumed_price": assumed_price,
                    "currency": currency,
                    "base_currency": base_currency,
                }
            )
            envelope = container.services.portfolio.simulate_addition(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ------------------------------------------- Phase 1L transactions/workflows

    async def account_get_transactions(
        providers: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Refresh and return normalized read-only historical account transactions."""
        try:
            inp = AccountGetTransactionsInput.model_validate(
                {"providers": providers, "start": start, "end": end, "limit": limit}
            )
            return (
                await container.services.account_transactions.get_transactions(inp)
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        account_get=account_get,
        portfolio_analyze=portfolio_analyze,
        portfolio_simulate_addition=portfolio_simulate_addition,
    )
