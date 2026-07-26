"""Unit tests for instrument identity models and factories (Phase 1D D1)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.common.enums import AliasType, AssetType, Market
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.values import build_instrument_id
from domain.instruments.identity import (
    assert_instrument_id_matches,
    build_canonical_instrument,
)
from domain.instruments.models import Instrument, InstrumentAlias

# Valid uuid7-shaped token (version nibble 7, RFC4122 variant).
_UUID7 = "01901945-7f5d-7cc3-98c4-dc0c0c07398f"
_ALIAS_ID = f"alias_{_UUID7}"


class TestBuildCanonicalInstrument:
    def test_builds_a_share_equity(self) -> None:
        inst = build_canonical_instrument(
            asset_type=AssetType.EQUITY,
            market=Market.A_SHARE,
            canonical_symbol="600519.SH",
            name="贵州茅台",
            exchange="SSE",
            currency="CNY",
            timezone="Asia/Shanghai",
            country="CN",
        )
        assert inst.instrument_id == "equity:A_SHARE:600519.SH"
        assert inst.symbol == "600519.SH"
        assert inst.is_active is True
        assert inst.listing_status == "active"
        assert inst.metadata_version == 1
        assert inst.country == "CN"

    def test_builds_us_equity_with_defaults_compatible_with_phase1a(self) -> None:
        inst = build_canonical_instrument(
            asset_type=AssetType.EQUITY,
            market=Market.US,
            canonical_symbol="NVDA",
            name="NVIDIA Corporation",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        )
        assert inst.instrument_id == build_instrument_id(AssetType.EQUITY, Market.US, "NVDA")
        # Phase 1A-style positional construction still works via defaults on Instrument.
        legacy = Instrument(
            instrument_id=inst.instrument_id,
            symbol="NVDA",
            name="NVIDIA Corporation",
            market=Market.US,
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            asset_type=AssetType.EQUITY,
        )
        assert legacy.is_active is True
        assert legacy.listing_status == "active"

    def test_etf_index_option_ids(self) -> None:
        etf = build_canonical_instrument(
            asset_type=AssetType.ETF,
            market=Market.US,
            canonical_symbol="SPY",
            name="SPDR S&P 500 ETF Trust",
            exchange="ARCA",
            currency="USD",
            timezone="America/New_York",
        )
        assert etf.instrument_id == "etf:US:SPY"

        idx = build_canonical_instrument(
            asset_type=AssetType.INDEX,
            market=Market.A_SHARE,
            canonical_symbol="000300.SH",
            name="沪深300",
            exchange="SSE",
            currency="CNY",
            timezone="Asia/Shanghai",
        )
        assert idx.instrument_id == "index:A_SHARE:000300.SH"

        opt = build_canonical_instrument(
            asset_type=AssetType.OPTION,
            market=Market.US,
            canonical_symbol="NVDA260717C00150000",
            name="NVDA 260717 C 150",
            exchange="CBOE",
            currency="USD",
            timezone="America/New_York",
            underlying_instrument_id="equity:US:NVDA",
            multiplier=Decimal("100"),
        )
        assert opt.instrument_id == "option:US:NVDA260717C00150000"
        assert opt.underlying_instrument_id == "equity:US:NVDA"
        assert opt.multiplier == Decimal("100")

    def test_rejects_whitespace_and_colon_in_symbol(self) -> None:
        with pytest.raises(DataContractError):
            build_canonical_instrument(
                asset_type=AssetType.EQUITY,
                market=Market.US,
                canonical_symbol=" NVDA ",
                name="n",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
            )
        with pytest.raises(DataContractError):
            build_canonical_instrument(
                asset_type=AssetType.EQUITY,
                market=Market.US,
                canonical_symbol="NV:DA",
                name="n",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
            )

    def test_rejects_empty_exchange_currency_timezone(self) -> None:
        with pytest.raises(DataContractError):
            build_canonical_instrument(
                asset_type=AssetType.EQUITY,
                market=Market.US,
                canonical_symbol="NVDA",
                name="n",
                exchange="",
                currency="USD",
                timezone="America/New_York",
            )


class TestInstrumentInvariants:
    def test_mismatched_instrument_id_rejected(self) -> None:
        with pytest.raises(DataContractError):
            Instrument(
                instrument_id="equity:US:AAPL",
                symbol="NVDA",
                name="NVIDIA",
                market=Market.US,
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                asset_type=AssetType.EQUITY,
            )

    def test_invalid_listing_status(self) -> None:
        with pytest.raises(DataContractError):
            build_canonical_instrument(
                asset_type=AssetType.EQUITY,
                market=Market.US,
                canonical_symbol="NVDA",
                name="n",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                listing_status="trading",
            )

    def test_assert_instrument_id_matches(self) -> None:
        inst = build_canonical_instrument(
            asset_type=AssetType.EQUITY,
            market=Market.US,
            canonical_symbol="NVDA",
            name="n",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        )
        assert_instrument_id_matches(inst)

    def test_assert_instrument_id_matches_detects_tampered_fields(self) -> None:
        """Helper re-checks identity even if construction invariants were bypassed."""
        inst = build_canonical_instrument(
            asset_type=AssetType.EQUITY,
            market=Market.US,
            canonical_symbol="NVDA",
            name="n",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        )
        object.__setattr__(inst, "symbol", "AAPL")
        with pytest.raises(DataContractError):
            assert_instrument_id_matches(inst)


class TestInstrumentAlias:
    def test_valid_alias(self) -> None:
        alias = InstrumentAlias(
            alias_id=_ALIAS_ID,
            instrument_id="equity:A_SHARE:600519.SH",
            alias_type=AliasType.NAME,
            alias_value="贵州茅台",
            alias_value_raw="贵州茅台",
            market=Market.A_SHARE,
            source="local_seed",
            is_primary=True,
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
        assert alias.alias_id.startswith("alias_")
        assert alias.alias_type is AliasType.NAME

    def test_alias_id_format_without_entity_id_prefix(self) -> None:
        """alias_<uuid7> is a wire string pattern, not EntityIdPrefix.ALIAS."""
        assert "alias" not in {p.value for p in EntityIdPrefix}
        with pytest.raises(DataContractError) as exc_info:
            InstrumentAlias(
                alias_id="alias_not-a-uuid",
                instrument_id="equity:US:NVDA",
                alias_type=AliasType.SYMBOL,
                alias_value="nvda",
                alias_value_raw="nvda",
                market=Market.US,
                source="user",
                is_primary=False,
                created_at=datetime(2026, 7, 16, tzinfo=UTC),
            )
        assert "alias_<uuid7>" in exc_info.value.message

    def test_rejects_naive_created_at(self) -> None:
        with pytest.raises(DataContractError):
            InstrumentAlias(
                alias_id=_ALIAS_ID,
                instrument_id="equity:US:NVDA",
                alias_type=AliasType.LOCAL_CODE,
                alias_value="nvda",
                alias_value_raw="nvda",
                market=Market.US,
                source="user",
                is_primary=False,
                created_at=datetime(2026, 7, 16),
            )

    def test_local_code_and_provider_native_types(self) -> None:
        for alias_type, value in (
            (AliasType.LOCAL_CODE, "600519"),
            (AliasType.PROVIDER_NATIVE, "em:1.600519"),
            (AliasType.OPTION_OCC, "nvda260717c00150000"),
        ):
            alias = InstrumentAlias(
                alias_id=_ALIAS_ID,
                instrument_id="equity:A_SHARE:600519.SH",
                alias_type=alias_type,
                alias_value=value,
                alias_value_raw=value,
                market=Market.A_SHARE,
                source="provider:mock_a_share",
                is_primary=False,
                created_at=datetime(2026, 7, 16, tzinfo=UTC),
            )
            assert alias.alias_type is alias_type

    def test_alias_value_is_normalized_lookup_key(self) -> None:
        """alias_value must be non-empty and already stripped (lookup key)."""
        for bad in ("", "   ", " nvda", "nvda ", "\tnvda", "nvda\n"):
            with pytest.raises(DataContractError) as exc_info:
                InstrumentAlias(
                    alias_id=_ALIAS_ID,
                    instrument_id="equity:US:NVDA",
                    alias_type=AliasType.SYMBOL,
                    alias_value=bad,
                    alias_value_raw="nvda",
                    market=Market.US,
                    source="user",
                    is_primary=False,
                    created_at=datetime(2026, 7, 16, tzinfo=UTC),
                )
            assert "alias_value" in exc_info.value.message

    def test_alias_value_raw_allows_surrounding_whitespace(self) -> None:
        """alias_value_raw may preserve raw formatting; must have non-ws content."""
        alias = InstrumentAlias(
            alias_id=_ALIAS_ID,
            instrument_id="equity:US:NVDA",
            alias_type=AliasType.SYMBOL,
            alias_value="nvda",
            alias_value_raw="  NVDA  ",
            market=Market.US,
            source="user",
            is_primary=False,
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
        assert alias.alias_value == "nvda"
        assert alias.alias_value_raw == "  NVDA  "

    def test_alias_value_raw_rejects_whitespace_only(self) -> None:
        for bad in ("", "   ", "\t\n"):
            with pytest.raises(DataContractError) as exc_info:
                InstrumentAlias(
                    alias_id=_ALIAS_ID,
                    instrument_id="equity:US:NVDA",
                    alias_type=AliasType.SYMBOL,
                    alias_value="nvda",
                    alias_value_raw=bad,
                    market=Market.US,
                    source="user",
                    is_primary=False,
                    created_at=datetime(2026, 7, 16, tzinfo=UTC),
                )
            assert "alias_value_raw" in exc_info.value.message

    def test_instrument_id_rejects_whitespace_padding(self) -> None:
        for bad in ("", "   ", " equity:US:NVDA", "equity:US:NVDA ", "\tequity:US:NVDA"):
            with pytest.raises(DataContractError) as exc_info:
                InstrumentAlias(
                    alias_id=_ALIAS_ID,
                    instrument_id=bad,
                    alias_type=AliasType.SYMBOL,
                    alias_value="nvda",
                    alias_value_raw="nvda",
                    market=Market.US,
                    source="user",
                    is_primary=False,
                    created_at=datetime(2026, 7, 16, tzinfo=UTC),
                )
            assert "instrument_id" in exc_info.value.message

    def test_instrument_id_rejects_invalid_public_identity(self) -> None:
        for bad in (
            "not-an-id",
            "equity:NVDA",
            "stock:US:NVDA",
            "equity:XX:NVDA",
            "equity:US:",
        ):
            with pytest.raises(DataContractError) as exc_info:
                InstrumentAlias(
                    alias_id=_ALIAS_ID,
                    instrument_id=bad,
                    alias_type=AliasType.SYMBOL,
                    alias_value="nvda",
                    alias_value_raw="nvda",
                    market=Market.US,
                    source="user",
                    is_primary=False,
                    created_at=datetime(2026, 7, 16, tzinfo=UTC),
                )
            message = exc_info.value.message
            assert "instrument_id" in message or "asset_type" in message or "symbol" in message

    def test_instrument_id_market_must_match_alias_market(self) -> None:
        with pytest.raises(DataContractError) as exc_info:
            InstrumentAlias(
                alias_id=_ALIAS_ID,
                instrument_id="equity:US:NVDA",
                alias_type=AliasType.SYMBOL,
                alias_value="nvda",
                alias_value_raw="nvda",
                market=Market.A_SHARE,
                source="user",
                is_primary=False,
                created_at=datetime(2026, 7, 16, tzinfo=UTC),
            )
        assert "market" in exc_info.value.message
        assert exc_info.value.details.get("parsed_market") == "US"
        assert exc_info.value.details.get("market") == "A_SHARE"

    def test_source_accepts_frozen_grammar(self) -> None:
        for source in (
            "local_seed",
            "user",
            "provider:mock_a_share",
            "provider:null",
            "provider:local_master",
            "provider:eastmoney",
        ):
            alias = InstrumentAlias(
                alias_id=_ALIAS_ID,
                instrument_id="equity:US:NVDA",
                alias_type=AliasType.SYMBOL,
                alias_value="nvda",
                alias_value_raw="nvda",
                market=Market.US,
                source=source,
                is_primary=False,
                created_at=datetime(2026, 7, 16, tzinfo=UTC),
            )
            assert alias.source == source

    def test_source_rejects_padding_and_unknown_grammar(self) -> None:
        for bad in (
            "",
            "   ",
            " local_seed",
            "local_seed ",
            "user\n",
            "provider: mock_a_share",
            "provider:mock_a_share ",
            " provider:mock_a_share",
            "provider:",
            "provider:not_a_vendor",
            "seed",
            "local",
            "PROVIDER:mock_a_share",
            "Provider:mock_a_share",
        ):
            with pytest.raises(DataContractError) as exc_info:
                InstrumentAlias(
                    alias_id=_ALIAS_ID,
                    instrument_id="equity:US:NVDA",
                    alias_type=AliasType.SYMBOL,
                    alias_value="nvda",
                    alias_value_raw="nvda",
                    market=Market.US,
                    source=bad,
                    is_primary=False,
                    created_at=datetime(2026, 7, 16, tzinfo=UTC),
                )
            message = exc_info.value.message
            assert "source" in message or "vendor" in message


class TestPhase1DEnumsWireValues:
    def test_data_category_and_vendor_samples(self) -> None:
        from domain.common.enums import (
            DataCategory,
            DataCriticality,
            ResolveMatchType,
            VendorId,
        )

        assert DataCategory.MARKET_SNAPSHOT.value == "market_snapshot"
        assert DataCategory.INSTRUMENT_MASTER.value == "instrument_master"
        assert DataCriticality.CORE.value == "core"
        assert VendorId.MOCK_A_SHARE.value == "mock_a_share"
        assert VendorId.LOCAL_MASTER.value == "local_master"
        assert VendorId.NULL.value == "null"
        assert ResolveMatchType.EXACT_INSTRUMENT_ID.value == "exact_instrument_id"

    def test_entity_id_prefix_still_frozen_without_alias(self) -> None:
        expected = {
            "req",
            "case",
            "thesis",
            "rev",
            "evidence",
            "report",
            "event",
            "decision",
            "journal",
            "watch_group",
            "watch_membership",
            "watch_mutation",
            "risk_policy",
            "monitor",
            "monitor_event",
            "monitor_run",
            "monitor_resolution",
            "snapshot",
            "run",
            "audit",
            "futures_product",
            "futures_product_version",
            "futures_contract_version",
            "trade_plan",
        }
        assert {p.value for p in EntityIdPrefix} == expected
