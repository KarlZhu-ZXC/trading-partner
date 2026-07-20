from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError
from domain.common.values import build_instrument_id
from domain.watchlist.enums import WatchlistSource
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDOperation
from infrastructure.providers.watchlist.manual_csv import ManualCsvWatchlistAdapter
from infrastructure.providers.watchlist.moomoo import MoomooWatchlistAdapter
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 18, 12, tzinfo=UTC)


# Unit-test only: production Moomoo default is 10 requests / 30s; tests that exercise
# add/remove + post-mutation verification exceed that budget and would sleep ~30s.
_TEST_RATE_LIMIT: dict[str, int | float] = {
    "rate_limit_requests": 10_000,
    "rate_limit_window_seconds": 0.001,
}


class _FakeMoomooContext:
    def __init__(self) -> None:
        self.calls: list[str | tuple[str, ...]] = []
        self.groups = [
            {"group_name": "Favorites", "group_type": "SYSTEM"},
            {"group_name": "自定义组", "group_type": "SYSTEM"},
            {"group_name": "My List", "group_type": "CUSTOM"},
        ]
        self.memberships = {
            "Favorites": [
                {"code": "FX.XAUUSD", "name": "GOLD", "stock_type": "FUTURE"},
                {"code": "US.NVDA", "name": "NVIDIA", "stock_type": "STOCK"},
            ],
            "自定义组": [],
            "My List": [],
        }

    def get_user_security_group(self) -> tuple[int, list[dict[str, object]]]:
        self.calls.append("get_user_security_group")
        return 0, list(self.groups)

    def get_user_security(self, group_name: str) -> tuple[int, list[dict[str, object]]]:
        self.calls.append(("get_user_security", group_name))
        rows = list(self.memberships.get(group_name, ()))
        return 0, rows

    def modify_user_security(
        self, group_name: str, op: str, code_list: list[str]
    ) -> tuple[int, str]:
        self.calls.append(("modify_user_security", group_name, op, tuple(code_list)))
        codes = list(code_list)
        if op == "ADD":
            for code in codes:
                rows = self.memberships.setdefault(group_name, [])
                if any(row["code"] == code for row in rows):
                    continue
                rows.append({"code": code, "name": "Manual", "stock_type": "STOCK"})
            return 0, "success"
        if op == "DEL":
            for code in codes:
                self.memberships[group_name] = [
                    row for row in self.memberships[group_name] if row["code"] != code
                ]
            return 0, "success"
        return 1, "bad op"

    def close(self) -> None:
        self.calls.append("close")


class _RecordingLimiter:
    def __init__(self) -> None:
        self.calls: list[MoomooOpenDOperation] = []

    def wait(
        self,
        operation: MoomooOpenDOperation,
        *,
        scope: str | None = None,
    ) -> None:
        assert scope is None
        self.calls.append(operation)


@pytest.mark.asyncio
async def test_watchlist_source_dto_basics() -> None:
    from application.dto.watchlist_source import WatchlistSourceMembership

    with pytest.raises(DataContractError, match="not be blank"):
        WatchlistSourceMembership(
            source=WatchlistSource.MOOMOO,
            group_name="   ",
            provider_code="US.NVDA",
            display_name="NVIDIA",
            instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "NVDA"),  # type: ignore[arg-type]
            provider_asset_type=None,
            research_supported=True,
            group_writable=True,
        )


@pytest.mark.asyncio
async def test_moomoo_adapter_parses_unicode_and_unsupported_codes() -> None:
    context = _FakeMoomooContext()
    limiter = _RecordingLimiter()
    adapter = MoomooWatchlistAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        opend_rate_limiter=limiter,
    )

    groups = await adapter.list_groups()
    assert len(groups) == 3
    favorites = groups[0]
    assert favorites.name == "Favorites"
    assert favorites.writable is True
    manual = groups[1]
    assert manual.name == "自定义组"
    assert manual.writable is False

    memberships = await adapter.list_memberships(None)
    assert len(memberships) == 2
    unsupported = memberships[0]
    assert unsupported.provider_code == "FX.XAUUSD"
    assert unsupported.research_supported is False
    assert unsupported.instrument_id is None

    assert ("close" in context.calls) and (context.calls[-1] == "close")
    assert limiter.calls == [
        MoomooOpenDOperation.WATCHLIST_GROUPS,
        MoomooOpenDOperation.WATCHLIST_GROUPS,
        MoomooOpenDOperation.WATCHLIST_MEMBERS,
        MoomooOpenDOperation.WATCHLIST_MEMBERS,
        MoomooOpenDOperation.WATCHLIST_MEMBERS,
    ]


@pytest.mark.asyncio
async def test_moomoo_adapter_maps_provider_idx_to_domain_index() -> None:
    context = _FakeMoomooContext()
    context.memberships["My List"] = [
        {"code": "US..NDX", "name": "Nasdaq 100", "stock_type": "IDX"},
        {"code": "US..SPX", "name": "S&P 500", "stock_type": "IDX"},
    ]
    adapter = MoomooWatchlistAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        **_TEST_RATE_LIMIT,
    )

    memberships = await adapter.list_memberships("My List")

    assert [item.instrument_id for item in memberships] == [
        "index:US:.NDX",
        "index:US:.SPX",
    ]
    assert all(item.provider_asset_type == "IDX" for item in memberships)
    assert all(item.research_supported for item in memberships)


@pytest.mark.asyncio
async def test_moomoo_adapter_applies_tracked_spg_correction() -> None:
    context = _FakeMoomooContext()
    context.memberships["My List"] = [
        {
            "code": "US.SPG",
            "name": "Simon Property",
            "stock_type": "ETF",
            "listing_date": "1970-01-01",
        }
    ]
    adapter = MoomooWatchlistAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        security_corrections=MoomooSecurityCorrections.load_default(),
        **_TEST_RATE_LIMIT,
    )

    membership = (await adapter.list_memberships("My List"))[0]

    assert membership.instrument_id == "equity:US:SPG"
    assert membership.display_name == "Simon Property Group, Inc."
    assert membership.provider_asset_type == "ETF"
    assert membership.research_supported is True


@pytest.mark.asyncio
async def test_moomoo_adapter_add_remove_additional_verification() -> None:
    context = _FakeMoomooContext()
    adapter = MoomooWatchlistAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        **_TEST_RATE_LIMIT,
    )

    added = await adapter.add_membership(
        group_name="Favorites",
        provider_code="US.MSFT",
        display_name="Microsoft",
    )
    assert added.provider_code == "US.MSFT"
    # Post-add verification request (must remain; do not drop to dodge rate limits).
    refreshed = await adapter.list_memberships("Favorites")
    assert any(m.provider_code == "US.MSFT" for m in refreshed)
    removed = await adapter.remove_membership(group_name="Favorites", provider_code="US.MSFT")
    assert removed.provider_code == "US.MSFT"
    # Post-remove verification request (must remain).
    after = await adapter.list_memberships("Favorites")
    assert all(m.provider_code != "US.MSFT" for m in after)

    # add: group + before memberships + modify + after memberships
    # list: group + memberships; remove: same four; list again: group + memberships
    assert context.calls.count("get_user_security_group") == 4
    assert context.calls.count(("get_user_security", "Favorites")) == 6
    assert ("modify_user_security", "Favorites", "ADD", ("US.MSFT",)) in context.calls
    assert ("modify_user_security", "Favorites", "DEL", ("US.MSFT",)) in context.calls


@pytest.mark.asyncio
async def test_moomoo_adapter_rejects_readonly_group_and_non_loopback() -> None:
    context = _FakeMoomooContext()
    readonly = _FakeMoomooContext()
    readonly.groups[0]["group_type"] = "SYSTEM"
    readonly.memberships = {"Only": []}

    adapter = MoomooWatchlistAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        **_TEST_RATE_LIMIT,
    )
    with pytest.raises(DataContractError, match="read-only"):
        await adapter.add_membership(
            group_name="自定义组",
            provider_code="US.TSLA",
            display_name="Tesla",
        )
    with pytest.raises(DataContractError, match="watchlist host must be loopback"):
        MoomooWatchlistAdapter(
            enabled=True,
            host="10.0.0.1",
            port=11111,
            clock=_Clock(),
            context_factory=lambda _host, _port: readonly,
            **_TEST_RATE_LIMIT,
        )


@pytest.mark.asyncio
async def test_moomoo_adapter_fanout_limit() -> None:
    context = _FakeMoomooContext()
    adapter = MoomooWatchlistAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,
        max_groups_per_refresh=1,
        **_TEST_RATE_LIMIT,
    )
    with pytest.raises(DataContractError, match="group fanout"):
        await adapter.list_memberships(None)


@pytest.mark.asyncio
async def test_manual_csv_adapter_v1_smoke_and_formula_rejection(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.v1.csv"
    path.write_text(
        "schema_version,group_name,instrument_id,display_name\n1,核心,equity:US:NVDA,NVIDIA\n",
        encoding="utf-8-sig",
    )
    adapter = ManualCsvWatchlistAdapter(path, clock=_Clock())
    assert len(await adapter.list_groups()) == 1
    rows = await adapter.list_memberships()
    assert rows[0].instrument_id == build_instrument_id(AssetType.EQUITY, Market.US, "NVDA")
    with pytest.raises(DataContractError, match="formula"):
        path.write_text(
            "schema_version,group_name,instrument_id,display_name\n"
            "1,=evil,equity:US:MSFT,Microsoft\n",
            encoding="utf-8-sig",
        )
        await ManualCsvWatchlistAdapter(path, clock=_Clock()).list_memberships()


@pytest.mark.asyncio
async def test_manual_csv_add_remove_is_atomic_and_verifiable(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.v1.csv"
    path.write_text(
        "schema_version,group_name,instrument_id,display_name\n1,Main,equity:US:NVDA,NVIDIA\n",
        encoding="utf-8",
    )
    adapter = ManualCsvWatchlistAdapter(path, clock=_Clock())

    added = await adapter.add_membership(
        group_name="Main",
        provider_code="equity:US:MSFT",
        display_name="Microsoft",
    )
    assert added.provider_code == "equity:US:MSFT"

    after_add = await adapter.list_memberships("Main")
    assert len(after_add) == 2

    removed = await adapter.remove_membership(group_name="Main", provider_code="equity:US:MSFT")
    assert removed.provider_code == "equity:US:MSFT"
    after_remove = await adapter.list_memberships("Main")
    assert len(after_remove) == 1


@pytest.mark.asyncio
async def test_manual_csv_rejects_duplicate_and_bad_schema(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.v1.csv"
    path.write_text(
        "schema_version,group_name,instrument_id,display_name\n2,Main,equity:US:NVDA,NVIDIA\n",
        encoding="utf-8",
    )
    with pytest.raises(DataContractError, match="schema_version"):
        await ManualCsvWatchlistAdapter(path, clock=_Clock()).list_memberships()

    path.write_text(
        "schema_version,group_name,instrument_id,display_name\n"
        "1,Main,equity:US:NVDA,NVIDIA\n"
        "1,Main,equity:US:NVDA,Duplicate\n",
        encoding="utf-8",
    )
    with pytest.raises(DataContractError, match="duplicate"):
        await ManualCsvWatchlistAdapter(path, clock=_Clock()).list_memberships()
