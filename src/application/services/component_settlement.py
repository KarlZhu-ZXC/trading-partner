"""Safe all-settle wrapper for concurrent application product components."""

from __future__ import annotations

from collections.abc import Awaitable

from application.dto.provider_routing import RouterExecutionResult
from domain.common.enums import DataCriticality
from domain.common.errors import ProviderUnavailableError, TradingPartnerError


async def settle_router_component[T](
    awaitable: Awaitable[RouterExecutionResult[T]],
) -> RouterExecutionResult[T]:
    """Turn a component exception into a typed result without cancelling siblings.

    Product services choose required/optional semantics and the deterministic
    component-order error after every scheduled operation has settled.
    """
    try:
        return await awaitable
    except TradingPartnerError as exc:
        return RouterExecutionResult(
            value=None,
            ok=False,
            criticality=DataCriticality.OPTIONAL,
            meta=None,
            attempts=(),
            warnings=(),
            error=exc,
        )
    except Exception:
        return RouterExecutionResult(
            value=None,
            ok=False,
            criticality=DataCriticality.OPTIONAL,
            meta=None,
            attempts=(),
            warnings=(),
            error=ProviderUnavailableError(
                "Unexpected A-share component failure",
                details={"error_type": "unexpected_component_failure"},
            ),
        )
