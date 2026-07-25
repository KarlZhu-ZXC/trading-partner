"""Tests for the operational durable account-transactions CLI."""

from __future__ import annotations

import json
from datetime import date

import pytest

from domain.common.enums import VendorId
from interfaces.cli import account_transactions as cli


class _Result:
    ok = True

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"ok": True, "data": {"transactions": []}}


class _Coordinator:
    def __init__(self) -> None:
        self.request = None

    async def get_transactions(self, request: object) -> _Result:
        self.request = request
        return _Result()


class _Container:
    def __init__(self) -> None:
        self.account_transaction_coordinator = _Coordinator()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_cli_uses_et_day_window_and_both_providers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    container = _Container()
    monkeypatch.setattr(cli, "build_default_application", lambda: container)

    code = await cli._run(["--date", "2026-07-21"])

    payload = json.loads(capsys.readouterr().out)
    request = container.account_transaction_coordinator.request
    assert code == 0
    assert payload == {"ok": True, "data": {"transactions": []}}
    assert request is not None
    assert request.providers == (VendorId.SCHWAB, VendorId.MOOMOO)
    assert request.start.date() == date(2026, 7, 21)
    assert request.start.tzinfo is not None
    assert request.end.date() == date(2026, 7, 21)
    assert request.limit == 1_000
    assert container.closed is True


async def test_cli_honors_provider_filter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    container = _Container()
    monkeypatch.setattr(cli, "build_default_application", lambda: container)

    code = await cli._run(
        ["--date", "2026-07-21", "--provider", "schwab", "--limit", "25"]
    )

    capsys.readouterr()
    request = container.account_transaction_coordinator.request
    assert code == 0
    assert request is not None
    assert request.providers == (VendorId.SCHWAB,)
    assert request.limit == 25
