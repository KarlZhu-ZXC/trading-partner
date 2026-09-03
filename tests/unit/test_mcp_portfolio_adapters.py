from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from interfaces.mcp.tools.portfolio import build_portfolio_adapters


def test_behavior_summary_adapter_excludes_internal_container_from_request() -> None:
    envelope = MagicMock()
    envelope.model_dump.return_value = {
        "ok": True,
        "data": {"algorithm_version": "behavior_summary_v1"},
    }
    coordinator = MagicMock()
    coordinator.get_behavior_summary.return_value = envelope
    container = MagicMock()
    container.services = SimpleNamespace(account_transactions=coordinator)

    adapter = build_portfolio_adapters(container).portfolio_get_behavior_summary
    result = adapter(
        providers=("schwab",),
        account_refs=("account_1",),
        classifications=("ACTIVE_TRADE",),
        minimum_sample_size=5,
    )

    request = coordinator.get_behavior_summary.call_args.args[0]
    assert request.providers == ("schwab",)
    assert request.account_refs == ("account_1",)
    assert request.classifications == ("ACTIVE_TRADE",)
    assert request.minimum_sample_size == 5
    assert result["ok"] is True
