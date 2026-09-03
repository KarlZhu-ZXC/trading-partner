from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from domain.common.errors import DataContractError, ProviderUnavailableError
from infrastructure.providers.account.manual_csv import ManualCsvAccountAdapter
from infrastructure.providers.account.moomoo import MoomooAccountAdapter
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDOperation
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)


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


@pytest.mark.asyncio
async def test_moomoo_zero_quantity_position_is_omitted_without_losing_snapshot() -> None:
    class _ClosedPositionContext(_Context):
        def position_list_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
            code, rows = super().position_list_query(**kwargs)
            return code, [
                {**rows[0], "code": "US.CLOSED", "qty": 0, "can_sell_qty": 0},
                rows[0],
            ]

    adapter = MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: _ClosedPositionContext(),
    )

    result = await adapter.get_account_snapshots(as_of=datetime(2026, 7, 18, 12, tzinfo=UTC))

    assert [item.instrument_id for item in result.value[0].positions] == ["equity:US:NVDA"]
    assert "MOOMOO_ZERO_QUANTITY_POSITION_OMITTED" in result.value[0].warning_codes
    assert "MOOMOO_ZERO_QUANTITY_POSITION_OMITTED" in result.meta.warnings


@pytest.mark.asyncio
async def test_moomoo_open_order_failure_degrades_instead_of_losing_snapshot() -> None:
    class _UnavailableOrdersContext(_Context):
        def order_list_query(self, **kwargs: object) -> tuple[int, str]:
            self.calls.append("orders")
            return -1, "unavailable"

    adapter = MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: _UnavailableOrdersContext(),
    )

    result = await adapter.get_account_snapshots(as_of=datetime(2026, 7, 18, 12, tzinfo=UTC))

    snapshot = result.value[0]
    assert snapshot.positions[0].instrument_id == "equity:US:NVDA"
    assert snapshot.open_orders == ()
    assert "MOOMOO_OPEN_ORDERS_UNAVAILABLE" in snapshot.warning_codes
    assert "MOOMOO_OPEN_ORDERS_UNAVAILABLE" in result.meta.warnings


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


def _history_deal(
    order_id: object,
    deal_id: str,
    *,
    quantity: str = "2",
    price: str = "100.25",
    code: str = "US.NVDA",
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "deal_id": deal_id,
        "code": code,
        "trd_side": "BUY",
        "qty": quantity,
        "price": price,
        "create_time": "2026-07-02 10:30:00",
    }


class _TransactionContext(_Context):
    def __init__(
        self,
        rows: list[dict[str, object]],
        fee_rows: list[dict[str, object]],
        *,
        fee_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.fee_rows = fee_rows
        self.fee_error = fee_error
        self.fee_calls: list[dict[str, object]] = []

    def history_deal_list_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("history")
        return 0, self.rows

    def order_fee_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("fees")
        self.fee_calls.append(kwargs)
        if self.fee_error is not None:
            raise self.fee_error
        requested = set(kwargs["order_id_list"])
        return 0, [row for row in self.fee_rows if row.get("order_id") in requested]


def _transaction_adapter(context: _TransactionContext) -> MoomooAccountAdapter:
    return MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
    )


@pytest.mark.asyncio
async def test_moomoo_order_fee_query_enriches_one_deal() -> None:
    context = _TransactionContext(
        [_history_deal("order-1", "deal-1")],
        [{"order_id": "order-1", "total_fee": "1.25"}],
    )

    result = await _transaction_adapter(context).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )

    transaction = result.value.transactions[0]
    assert transaction.fees == Decimal("1.25")
    assert context.fee_calls == [
        {"order_id_list": ["order-1"], "trd_env": "REAL", "acc_id": 998877}
    ]
    assert "TRANSACTION_FEES_UNAVAILABLE" not in result.meta.warnings
    assert "TRANSACTION_FEES_UNAVAILABLE" not in result.value.coverage[0].gap_codes


@pytest.mark.asyncio
async def test_moomoo_account_correction_uses_canonical_soxl_etf_identity() -> None:
    context = _TransactionContext(
        [_history_deal("order-soxl", "deal-soxl", code="US.SOXL")],
        [{"order_id": "order-soxl", "total_fee": "1.25"}],
    )
    adapter = MoomooAccountAdapter(
        _Ids(),
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        security_corrections=MoomooSecurityCorrections.load_default(),
    )

    result = await adapter.get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )

    transaction = result.value.transactions[0]
    assert transaction.instrument_id == "etf:US:SOXL"
    assert transaction.mapping_version == "moomoo_deals_v2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_code", "instrument_id"),
    [
        ("US.NIO260702C5000", "option:US:NIO260702C5000"),
        ("US.FCX   260821C00065000", "option:US:FCX260821C00065000"),
    ],
)
async def test_moomoo_account_detects_and_normalizes_us_option_identity(
    provider_code: str, instrument_id: str
) -> None:
    context = _TransactionContext(
        [_history_deal("order-option", "deal-option", code=provider_code)],
        [{"order_id": "order-option", "total_fee": "1.25"}],
    )

    result = await _transaction_adapter(context).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )

    assert result.value.transactions[0].instrument_id == instrument_id


@pytest.mark.asyncio
async def test_moomoo_order_fee_query_allocates_one_order_across_partial_deals() -> None:
    context = _TransactionContext(
        [
            _history_deal("order-1", "deal-1", quantity="2", price="100"),
            _history_deal("order-1", "deal-2", quantity="1", price="100"),
        ],
        [{"order_id": "order-1", "fee_amount": "3"}],
    )

    result = await _transaction_adapter(context).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )

    fees = {item.provider_transaction_id: item.fees for item in result.value.transactions}
    assert sorted(fee for fee in fees.values() if fee is not None) == [Decimal("1"), Decimal("2")]
    assert sum((fee or Decimal(0) for fee in fees.values()), Decimal(0)) == Decimal("3")
    assert "TRANSACTION_FEES_UNAVAILABLE" not in result.meta.warnings


@pytest.mark.asyncio
async def test_moomoo_order_fee_query_chunks_at_twenty_and_uses_scoped_limiter() -> None:
    rows = [_history_deal(f"order-{index}", f"deal-{index}") for index in range(21)]
    context = _TransactionContext(
        rows,
        [{"order_id": f"order-{index}", "total_fee": "1"} for index in range(21)],
    )
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

    result = await adapter.get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=30
    )

    assert len(result.value.transactions) == 21
    assert [len(call["order_id_list"]) for call in context.fee_calls] == [20, 1]
    fee_waits = [
        (operation, scope)
        for operation, scope in limiter.calls
        if operation is MoomooOpenDOperation.ACCOUNT_ORDER_FEES
    ]
    assert len(fee_waits) == 2
    assert len({scope for _operation, scope in fee_waits}) == 1
    assert "TRANSACTION_FEES_UNAVAILABLE" not in result.meta.warnings


@pytest.mark.asyncio
async def test_moomoo_partial_or_invalid_order_fee_response_keeps_gap_and_unknown_fees() -> None:
    partial_context = _TransactionContext(
        [
            _history_deal("order-1", "deal-1"),
            _history_deal("order-2", "deal-2"),
        ],
        [{"order_id": "order-1", "total_fee": "1"}],
    )
    partial_result = await _transaction_adapter(partial_context).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )
    partial_fees = {
        item.provider_transaction_id: item.fees for item in partial_result.value.transactions
    }
    assert sorted(fee for fee in partial_fees.values() if fee is not None) == [Decimal("1")]
    assert any(fee is None for fee in partial_fees.values())
    assert "TRANSACTION_FEES_UNAVAILABLE" in partial_result.meta.warnings
    assert "TRANSACTION_FEES_UNAVAILABLE" in partial_result.value.coverage[0].gap_codes

    invalid_context = _TransactionContext(
        [_history_deal("order-1", "deal-1")],
        [{"order_id": "order-1", "total_fee": "-1"}],
    )
    invalid_result = await _transaction_adapter(invalid_context).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )
    assert invalid_result.value.transactions[0].fees is None
    assert "TRANSACTION_FEES_UNAVAILABLE" in invalid_result.meta.warnings


@pytest.mark.asyncio
async def test_moomoo_order_fee_query_exception_is_fail_open() -> None:
    context = _TransactionContext(
        [_history_deal("order-1", "deal-1")],
        [],
        fee_error=RuntimeError("raw sdk payload must stay internal"),
    )

    result = await _transaction_adapter(context).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )

    assert len(result.value.transactions) == 1
    assert result.value.transactions[0].fees is None
    assert "TRANSACTION_FEES_UNAVAILABLE" in result.meta.warnings
    assert "raw sdk payload" not in repr(result)
