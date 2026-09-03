from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.dto.portfolio import AccountGetSnapshotInput
from bootstrap import build_application
from domain.common.enums import AppEnvironment, LogLevel, VendorId
from domain.common.errors import ProviderAuthenticationError, ProviderUnavailableError
from domain.portfolio.enums import (
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from infrastructure.config.settings import AppSettings
from infrastructure.providers.account.schwab import (
    SchwabAccountAdapter,
    SchwabPyReadClient,
    SchwabReadClient,
)

_NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
_ACCOUNT_HASH = "encrypted-account-hash"


class _Client:
    def __init__(self) -> None:
        self.transaction_windows: list[tuple[datetime, datetime]] = []

    def account_numbers(self) -> object:
        return [
            {"accountNumber": "12345678", "hashValue": _ACCOUNT_HASH},
            {"accountNumber": "87654321", "hashValue": "not-selected"},
        ]

    def accounts_with_positions(self) -> object:
        return [
            {
                "securitiesAccount": {
                    "accountNumber": "12345678",
                    "currentBalances": {
                        "cashBalance": "1000.25",
                        "buyingPower": "2500",
                        "liquidationValue": "5300.50",
                        "marginBalance": "-400",
                    },
                    "positions": [
                        {
                            "longQuantity": "2",
                            "shortQuantity": "0",
                            "averageLongPrice": "100.50",
                            "marketValue": "250",
                            "longOpenProfitLoss": "49",
                            "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
                        },
                        {
                            "longQuantity": "0",
                            "shortQuantity": "1",
                            "averageShortPrice": "4.25",
                            "marketValue": "425",
                            "shortOpenProfitLoss": "25",
                            "instrument": {
                                "assetType": "OPTION",
                                "symbol": "NVDA  260619C00200000",
                            },
                        },
                    ],
                }
            }
        ]

    def orders(self, account_hash: str, start: datetime, end: datetime) -> object:
        assert account_hash == _ACCOUNT_HASH
        assert end - start == timedelta(days=60)
        return []

    def transactions(self, account_hash: str, start: datetime, end: datetime) -> object:
        assert account_hash == _ACCOUNT_HASH
        self.transaction_windows.append((start, end))
        return [
            {
                "activityId": 101,
                "time": "2026-07-10T15:00:00Z",
                "type": "TRADE",
                "fees": {"commission": "1.25"},
                "transferItems": [
                    {
                        "amount": "2",
                        "price": "150",
                        "instruction": "BUY",
                        "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
                    }
                ],
            },
            {
                "activityId": "102",
                "time": "2026-07-11T15:00:00Z",
                "type": "TRADE",
                "transferItems": [
                    {
                        "amount": "-1",
                        "price": "151",
                        "positionEffect": "CLOSING",
                        "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
                    }
                ],
            },
        ]


def _adapter(id_generator: object, fixed_clock: object, client: _Client) -> SchwabAccountAdapter:
    return SchwabAccountAdapter(
        id_generator,  # type: ignore[arg-type]
        enabled=True,
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://127.0.0.1:8182",
        token_path=Path("unused-in-injected-test.json"),
        account_hashes=(_ACCOUNT_HASH,),
        clock=fixed_clock,  # type: ignore[arg-type]
        client_factory=lambda: client,
    )


def test_schwab_runtime_never_starts_browser_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    called: list[tuple[object, ...]] = []

    def fake_client_from_token_file(*args: object, **_kwargs: object) -> object:
        called.append(args)
        return SimpleNamespace(session=object())

    monkeypatch.setattr("schwab.auth.client_from_token_file", fake_client_from_token_file)
    with pytest.raises(ProviderAuthenticationError, match="dedicated project OAuth setup"):
        SchwabPyReadClient(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="https://127.0.0.1:8182",
            token_path=token_path,
        )
    assert called == []

    token_path.write_text("{}", encoding="utf-8")
    client = SchwabPyReadClient(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://127.0.0.1:8182",
        token_path=token_path,
    )
    assert client is not None
    assert called == [(str(token_path), "client-id", "client-secret")]

    source = inspect.getsource(SchwabPyReadClient)
    assert "easy_client" not in source
    assert "client_from_login_flow" not in source


def test_schwab_read_http_failure_retains_safe_stage_and_status() -> None:
    class _Session:
        def request(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(status_code=503)

    client = object.__new__(SchwabPyReadClient)
    client._client = SimpleNamespace(session=_Session())  # type: ignore[attr-defined]

    with pytest.raises(ProviderUnavailableError) as failure:
        client.accounts_with_positions()

    assert failure.value.details == {
        "vendor": "schwab",
        "operation": "account_snapshot",
        "error_type": "http_failure",
        "status_code": 503,
        "status_class": "5xx",
    }


@pytest.mark.asyncio
async def test_schwab_snapshot_normalizes_selected_account_without_plain_identity(
    id_generator: object, fixed_clock: object
) -> None:
    result = await _adapter(id_generator, fixed_clock, _Client()).get_account_snapshots(as_of=_NOW)

    snapshot = result.value[0]
    assert snapshot.provider is VendorId.SCHWAB
    assert snapshot.cash == Decimal("1000.25")
    assert snapshot.net_assets == Decimal("5300.50")
    assert snapshot.margin_used == Decimal("400")
    assert snapshot.positions[0].instrument_id == "equity:US:NVDA"
    assert snapshot.positions[1].side is AccountPositionSide.SHORT
    assert snapshot.positions[1].instrument_id == "option:US:NVDA260619C00200000"
    assert snapshot.positions[1].market_value == Decimal("-425")
    assert snapshot.positions[0].market_price is None
    assert "BROKER_VALUATION_PRICE_DERIVED" in snapshot.warning_codes
    assert "PRICE_TIME_UNAVAILABLE" not in snapshot.warning_codes
    assert "12345678" not in repr(result)
    assert _ACCOUNT_HASH not in repr(result)


@pytest.mark.asyncio
async def test_schwab_snapshot_maps_raw_client_failure_to_typed_error(
    id_generator: object, fixed_clock: object
) -> None:
    class _BrokenClient(_Client):
        def account_numbers(self) -> object:
            raise AttributeError("raw client mismatch")

    adapter = _adapter(id_generator, fixed_clock, _BrokenClient())

    with pytest.raises(ProviderUnavailableError) as caught:
        await adapter.get_account_snapshots(as_of=_NOW)

    assert caught.value.code == "SCHWAB_ACCOUNT_SNAPSHOT_UNAVAILABLE"
    assert caught.value.details == {
        "vendor": "schwab",
        "operation": "account_snapshot",
    }


@pytest.mark.asyncio
async def test_schwab_cash_balance_never_falls_back_to_other_balance_fields(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    accounts = client.accounts_with_positions()
    balances = accounts[0]["securitiesAccount"]["currentBalances"]  # type: ignore[index]
    balances.pop("cashBalance")
    balances["totalCash"] = "999999"
    client.accounts_with_positions = lambda: accounts  # type: ignore[method-assign]

    result = await _adapter(id_generator, fixed_clock, client).get_account_snapshots(as_of=_NOW)

    assert result.value[0].cash is None
    assert "SCHWAB_CASH_BALANCE_UNAVAILABLE" in result.value[0].warning_codes


@pytest.mark.asyncio
async def test_schwab_unsupported_asset_is_explicitly_degraded_and_no_write_surface(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    accounts = client.accounts_with_positions()
    positions = accounts[0]["securitiesAccount"]["positions"]  # type: ignore[index]
    positions.append(
        {
            "longQuantity": "3",
            "instrument": {"assetType": "MUTUAL_FUND", "symbol": "SWPPX"},
        }
    )
    client.accounts_with_positions = lambda: accounts  # type: ignore[method-assign]

    result = await _adapter(id_generator, fixed_clock, client).get_account_snapshots(as_of=_NOW)
    source = inspect.getsource(SchwabPyReadClient)

    assert "SCHWAB_UNSUPPORTED_ASSET_TYPE" in result.value[0].warning_codes
    assert "SCHWAB_OPEN_ORDERS_NOT_INGESTED" not in result.value[0].warning_codes
    assert len(result.value[0].positions) == 2
    assert '"POST"' not in source and '"DELETE"' not in source
    assert not hasattr(SchwabReadClient, "place_order")


@pytest.mark.asyncio
async def test_schwab_open_orders_are_ingested_without_order_write_surface(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    client.orders = lambda *_args: [  # type: ignore[method-assign]
        {
            "orderId": 9001,
            "status": "WORKING",
            "orderType": "LIMIT",
            "quantity": "2",
            "filledQuantity": "0.5",
            "price": "149.25",
            "enteredTime": "2026-07-18T11:00:00Z",
            "orderLegCollection": [
                {
                    "instruction": "BUY",
                    "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
                }
            ],
        },
        {"orderId": 9002, "status": "FILLED"},
    ]

    result = await _adapter(id_generator, fixed_clock, client).get_account_snapshots(as_of=_NOW)
    order = result.value[0].open_orders[0]

    assert order.provider_order_id == "9001"
    assert order.instrument_id == "equity:US:NVDA"
    assert order.quantity == Decimal("2")
    assert order.filled_quantity == Decimal("0.5")
    assert order.limit_price == Decimal("149.25")
    assert "SCHWAB_OPEN_ORDERS_NOT_INGESTED" not in result.meta.warnings


@pytest.mark.asyncio
async def test_schwab_collective_investment_etf_is_preserved(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    accounts = client.accounts_with_positions()
    positions = accounts[0]["securitiesAccount"]["positions"]  # type: ignore[index]
    positions.append(
        {
            "longQuantity": "3",
            "marketValue": "150",
            "instrument": {
                "assetType": "COLLECTIVE_INVESTMENT",
                "type": "EXCHANGE_TRADED_FUND",
                "symbol": "SCHB",
            },
        }
    )
    client.accounts_with_positions = lambda: accounts  # type: ignore[method-assign]

    result = await _adapter(id_generator, fixed_clock, client).get_account_snapshots(as_of=_NOW)

    assert result.value[0].positions[-1].instrument_id == "etf:US:SCHB"
    assert "SCHWAB_UNSUPPORTED_ASSET_TYPE" not in result.value[0].warning_codes


@pytest.mark.asyncio
async def test_schwab_transactions_use_instruction_and_page_vendor_windows(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    result = await _adapter(id_generator, fixed_clock, client).get_account_transactions(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=_NOW,
        limit=10,
    )

    assert [item.side for item in result.value.transactions] == [
        AccountTransactionSide.SELL,
        AccountTransactionSide.BUY,
    ]
    assert result.value.transactions[1].fees == Decimal("1.25")
    assert "SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN" in result.meta.warnings
    assert "SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN" not in result.value.coverage[0].gap_codes
    assert client.transaction_windows[0][0] == datetime(2026, 1, 1, tzinfo=UTC)
    assert client.transaction_windows[-1][1] == _NOW
    assert len(client.transaction_windows) == 4
    assert all(end - start <= timedelta(days=60) for start, end in client.transaction_windows)
    assert "SCHWAB_TRANSACTION_WINDOW_PAGED" in result.meta.warnings
    assert "101" not in repr(result) and _ACCOUNT_HASH not in repr(result)


@pytest.mark.asyncio
async def test_schwab_cash_journal_items_are_preserved_as_instrumentless_activities(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    client.transactions = lambda *_args: [  # type: ignore[method-assign]
        {
            "activityId": 103,
            "time": "2026-07-10T15:00:00Z",
            "type": "JOURNAL",
            "transferItems": [
                {
                    "amount": "1770",
                    "instrument": {
                        "assetType": "CURRENCY",
                        "symbol": "CURRENCY_USD",
                    },
                }
            ],
        }
    ]

    result = await _adapter(id_generator, fixed_clock, client).get_account_transactions(
        start=datetime(2026, 7, 10, tzinfo=UTC),
        end=datetime(2026, 7, 11, tzinfo=UTC),
        limit=10,
    )

    assert len(result.value.transactions) == 1
    activity = result.value.transactions[0]
    assert activity.instrument_id is None
    assert activity.cash_amount == Decimal("1770")
    assert activity.currency == "USD"
    assert "SCHWAB_TRANSACTION_ITEM_OMITTED" not in result.meta.warnings


@pytest.mark.asyncio
async def test_schwab_dividend_description_uses_unique_exact_instrument_candidate(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    client.transactions = lambda *_args: [  # type: ignore[method-assign]
        {
            "activityId": 200,
            "time": "2026-07-01T15:00:00Z",
            "type": "TRADE",
            "transferItems": [{
                "amount": "10",
                "price": "160",
                "instruction": "BUY",
                "instrument": {"assetType": "EQUITY", "symbol": "SPG"},
            }],
        },
        {
            "activityId": 201,
            "time": "2026-07-10T15:00:00Z",
            "type": "DIVIDEND_OR_INTEREST",
            "description": "QUALIFIED DIVIDEND SPG",
            "netAmount": "12.50",
            "transferItems": [{
                "amount": "12.50",
                "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
            }],
        },
        {
            "activityId": 202,
            "time": "2026-07-11T15:00:00Z",
            "type": "DIVIDEND_OR_INTEREST",
            "description": "QUALIFIED DIVIDEND UNKNOWN",
            "netAmount": "1.00",
            "transferItems": [{
                "amount": "1.00",
                "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
            }],
        },
    ]

    result = await _adapter(id_generator, fixed_clock, client).get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 12, tzinfo=UTC),
        limit=10,
    )

    dividends = [
        item
        for item in result.value.transactions
        if item.kind is AccountTransactionKind.DIVIDEND
    ]
    assert [item.instrument_id for item in dividends] == [None, "equity:US:SPG"]
    assert dividends[1].mapping_version == "schwab_activity_v2"
    assert "SCHWAB_DIVIDEND_INSTRUMENT_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_schwab_trade_cash_legs_are_non_blocking_and_security_leg_is_preserved(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    client.transactions = lambda *_args: [  # type: ignore[method-assign]
        {
            "activityId": 104,
            "time": "2026-07-10T15:00:00Z",
            "type": "TRADE",
            "transferItems": [
                {
                    "amount": "0.01",
                    "instrument": {
                        "assetType": "CURRENCY",
                        "symbol": "CURRENCY_USD",
                    },
                },
                {
                    "amount": "-30",
                    "price": "96.026",
                    "instrument": {
                        "assetType": "COLLECTIVE_INVESTMENT",
                        "type": "EXCHANGE_TRADED_FUND",
                        "symbol": "SOXL",
                    },
                },
            ],
        }
    ]

    result = await _adapter(id_generator, fixed_clock, client).get_account_transactions(
        start=datetime(2026, 7, 10, tzinfo=UTC),
        end=datetime(2026, 7, 11, tzinfo=UTC),
        limit=10,
    )

    assert len(result.value.transactions) == 1
    assert result.value.transactions[0].instrument_id == "etf:US:SOXL"
    assert result.value.transactions[0].side is AccountTransactionSide.SELL
    assert "SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN" in result.meta.warnings
    assert "SCHWAB_TRANSACTION_ITEM_OMITTED" not in result.meta.warnings


def test_schwab_settings_redact_credentials_hashes_and_token_path(
    tmp_path: Path,
) -> None:
    common = dict(
        _env_file=None,
        app_name="schwab-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///data/test.db",
        mcp_server_name="schwab-test",
        default_timezone="UTC",
        provider_timeout_seconds=5,
        holdings_sources=("SCHWAB",),
        schwab_client_id="client-id",
        schwab_client_secret="client-secret",
        schwab_account_hashes=_ACCOUNT_HASH,
    )
    settings = AppSettings(**common)  # type: ignore[arg-type]
    redacted = settings.redacted_dict()

    assert redacted["schwab_client_id"] == "***REDACTED***"
    assert redacted["schwab_client_secret"] == "***REDACTED***"
    assert redacted["schwab_account_hashes"] == "***REDACTED***"
    assert redacted["schwab_token_path"] == "***REDACTED***"
    with pytest.raises(ValidationError, match="data/secrets"):
        AppSettings(**common, schwab_token_path=tmp_path / "token.json")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_schwab_is_wired_through_existing_tools_without_inventory_growth(
    test_settings: AppSettings,
) -> None:
    container = build_application(test_settings)
    try:
        envelope = await container.services.portfolio.get_account_snapshot(
            AccountGetSnapshotInput(providers=(VendorId.SCHWAB,))
        )
    finally:
        await container.aclose()

    assert envelope.ok is False
    assert envelope.errors[0].code == "PROVIDER_NOT_CONFIGURED"
