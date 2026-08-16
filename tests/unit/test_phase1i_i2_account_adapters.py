from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from domain.common.errors import DataContractError, ProviderUnavailableError
from infrastructure.providers.account.manual_csv import ManualCsvAccountAdapter
from infrastructure.providers.account.moomoo import MoomooAccountAdapter
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDOperation


class _Ids:
    def __init__(self) -> None:
        self._value = 0

    def new(self, prefix: object) -> str:
        self._value += 1
        return f"snapshot_test-{self._value}"


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 18, 12, tzinfo=UTC)


_HEADER = (
    "schema_version,account_ref,account_as_of,base_currency,instrument_id,side,currency,"
    "quantity,sellable_quantity,average_cost,diluted_cost,market_price,market_price_at,"
    "market_value,unrealized_pnl,realized_pnl,cash,buying_power,net_assets,margin_used\n"
)


@pytest.mark.asyncio
async def test_manual_csv_cutoff_and_formula_rejection(tmp_path: Path) -> None:
    path = tmp_path / "holdings.csv"
    path.write_text(
        _HEADER + "1,main,2026-07-16T12:00:00Z,USD,equity:US:NVDA,long,USD,2,2,100,,"
        "120,2026-07-16T11:59:00Z,240,40,,100,100,340,0\n"
        + "1,main,2026-07-17T12:00:00Z,USD,equity:US:NVDA,long,USD,3,3,100,,"
        "125,2026-07-17T11:59:00Z,375,75,,100,100,475,0\n",
        encoding="utf-8",
    )
    adapter = ManualCsvAccountAdapter(path, _Ids(), clock=_Clock())

    result = await adapter.get_account_snapshots(as_of=datetime(2026, 7, 16, 18, tzinfo=UTC))

    assert result.value[0].positions[0].quantity.as_tuple().digits == (2,)
    assert result.value[0].account_as_of == datetime(2026, 7, 16, 12, tzinfo=UTC)

    formula_row = "1,=cmd,2026-07-16T12:00:00Z,USD,equity:US:NVDA,long,USD,2,,,,,,,,,,,,\n"
    path.write_text(_HEADER + formula_row)
    with pytest.raises(DataContractError, match="formulas"):
        await adapter.get_account_snapshots(as_of=datetime(2026, 7, 16, 18, tzinfo=UTC))


class _Context:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_acc_list(self) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("accounts")
        return 0, [{"acc_id": 998877, "trd_env": "REAL"}]

    def accinfo_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("funds")
        assert kwargs == {
            "trd_env": "REAL",
            "acc_id": 998877,
            "refresh_cache": True,
            "currency": "USD",
        }
        return 0, [
            {
                "currency": "USD",
                "cash": 100,
                "power": 200,
                "total_assets": 350,
                "interest_charged_amount": 25,
                "initial_margin": 240,
            }
        ]

    def position_list_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("positions")
        assert kwargs == {
            "trd_env": "REAL",
            "acc_id": 998877,
            "refresh_cache": True,
            "currency": "USD",
        }
        return 0, [
            {
                "code": "US.NVDA",
                "position_side": "LONG",
                "qty": 2,
                "can_sell_qty": 2,
                "average_cost": 100,
                "nominal_price": 125,
                "market_val": 250,
                "unrealized_pl": 50,
                "currency": "USD",
            }
        ]

    def order_list_query(
        self,
        *,
        trd_env: str,
        acc_id: int,
        refresh_cache: bool,
        status_filter_list: tuple[str, ...],
    ) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("orders")
        assert trd_env == "REAL"
        assert acc_id == 998877
        assert refresh_cache is True
        assert status_filter_list == (
            "WAITING_SUBMIT",
            "SUBMITTING",
            "SUBMITTED",
            "FILLED_PART",
        )
        return 0, []

    def close(self) -> None:
        self.calls.append("close")


class _RecordingLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[MoomooOpenDOperation, str | None]] = []

    def wait(
        self,
        operation: MoomooOpenDOperation,
        *,
        scope: str | None = None,
    ) -> None:
        self.calls.append((operation, scope))


@pytest.mark.asyncio
async def test_moomoo_adapter_calls_only_read_surface_and_redacts_account_id() -> None:
    context = _Context()
    limiter = _RecordingLimiter()
    adapter = MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        opend_rate_limiter=limiter,
    )

    result = await adapter.get_account_snapshots(as_of=datetime(2026, 7, 18, 12, tzinfo=UTC))

    snapshot = result.value[0]
    assert context.calls == ["accounts", "funds", "positions", "orders", "close"]
    assert snapshot.positions[0].instrument_id == "equity:US:NVDA"
    assert snapshot.base_currency == "USD"
    assert snapshot.positions[0].market_price == Decimal("125")
    assert snapshot.positions[0].market_price_at == snapshot.fetched_at
    assert snapshot.margin_used == Decimal("25")
    assert "MOOMOO_MARGIN_USAGE_UNAVAILABLE" not in snapshot.warning_codes
    assert "PRICE_TIME_IS_FETCH_TIME" in snapshot.warning_codes
    assert "PRICE_TIME_UNAVAILABLE" not in snapshot.warning_codes
    assert "PRICE_TIME_IS_FETCH_TIME" in result.meta.warnings
    assert "PRICE_TIME_UNAVAILABLE" not in result.meta.warnings
    assert "998877" not in repr(result)
    assert [operation for operation, _scope in limiter.calls] == [
        MoomooOpenDOperation.ACCOUNT_FUNDS,
        MoomooOpenDOperation.ACCOUNT_POSITIONS,
        MoomooOpenDOperation.ACCOUNT_ORDERS,
    ]
    assert len({scope for _operation, scope in limiter.calls}) == 1
    assert "998877" not in repr(limiter.calls)


@pytest.mark.asyncio
async def test_moomoo_initial_margin_is_never_mapped_as_financing_usage() -> None:
    class _NoDebtContext(_Context):
        def accinfo_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
            return 0, [
                {
                    "currency": "USD",
                    "cash": 100,
                    "power": 200,
                    "total_assets": 350,
                    "interest_charged_amount": "N/A",
                    "initial_margin": 240,
                }
            ]

    adapter = MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: _NoDebtContext(),
    )

    result = await adapter.get_account_snapshots(as_of=datetime(2026, 7, 18, 12, tzinfo=UTC))

    assert result.value[0].margin_used is None
    assert "MOOMOO_MARGIN_USAGE_UNAVAILABLE" in result.value[0].warning_codes


def test_moomoo_production_adapter_has_no_trade_write_surface() -> None:
    source = Path("src/infrastructure/providers/account/moomoo.py").read_text(encoding="utf-8")
    forbidden = ("unlock_", "place_", "modify_order", "cancel_", "acctradinginfo")
    assert not any(name in source for name in forbidden)


@pytest.mark.asyncio
async def test_moomoo_adapter_maps_raw_sdk_snapshot_failure_to_typed_error() -> None:
    class _BrokenContext(_Context):
        def get_acc_list(self) -> tuple[int, list[dict[str, object]]]:
            raise TypeError("raw sdk mismatch")

    adapter = MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: _BrokenContext(),
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        await adapter.get_account_snapshots(as_of=datetime(2026, 7, 18, 12, tzinfo=UTC))

    assert caught.value.code == "MOOMOO_ACCOUNT_SNAPSHOT_UNAVAILABLE"
    assert caught.value.details == {
        "vendor": "moomoo",
        "operation": "account_snapshot",
    }
