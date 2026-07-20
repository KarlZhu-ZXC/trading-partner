from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from application.dto.portfolio import AccountGetSnapshotInput
from bootstrap import build_application
from domain.common.enums import AppEnvironment, LogLevel, VendorId
from domain.portfolio.enums import AccountPositionSide, AccountTransactionSide
from infrastructure.config.settings import AppSettings
from infrastructure.providers.account.schwab import SchwabAccountAdapter, SchwabReadClient
from interfaces.mcp.server import PUBLIC_TOOL_NAMES

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
                        "amount": "1",
                        "price": "151",
                        "instruction": "SELL",
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
    assert snapshot.positions[1].market_value == Decimal("-425")
    assert snapshot.positions[0].market_price is None
    assert "12345678" not in repr(result)
    assert _ACCOUNT_HASH not in repr(result)


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
    source = Path("src/infrastructure/providers/account/schwab.py").read_text(encoding="utf-8")

    assert "SCHWAB_UNSUPPORTED_ASSET_TYPE" in result.value[0].warning_codes
    assert "SCHWAB_OPEN_ORDERS_NOT_INGESTED" in result.value[0].warning_codes
    assert len(result.value[0].positions) == 2
    assert "/orders" not in source
    assert '"POST"' not in source and '"DELETE"' not in source
    assert not hasattr(SchwabReadClient, "place_order")


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
async def test_schwab_transactions_use_instruction_and_clamp_vendor_window(
    id_generator: object, fixed_clock: object
) -> None:
    client = _Client()
    result = await _adapter(id_generator, fixed_clock, client).get_account_transactions(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=_NOW,
        limit=10,
    )

    assert [item.side for item in result.value] == [
        AccountTransactionSide.SELL,
        AccountTransactionSide.BUY,
    ]
    assert result.value[1].fees == Decimal("1.25")
    assert client.transaction_windows == [(datetime(2026, 5, 19, 12, tzinfo=UTC), _NOW)]
    assert "SCHWAB_TRANSACTION_WINDOW_CLAMPED" in result.meta.warnings
    assert "101" not in repr(result) and _ACCOUNT_HASH not in repr(result)


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
        envelope = await container.portfolio_tool_coordinator.get_account_snapshot(
            AccountGetSnapshotInput(providers=(VendorId.SCHWAB,))
        )
    finally:
        await container.aclose()

    assert envelope.ok is False
    assert envelope.errors[0].code == "PROVIDER_NOT_CONFIGURED"
    assert len(PUBLIC_TOOL_NAMES) == 52
