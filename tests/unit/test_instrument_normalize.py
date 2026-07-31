"""Unit tests for pure instrument symbol normalization (Phase 1D D1)."""

from __future__ import annotations

import pytest

from domain.common.enums import AssetType, Market
from domain.common.errors import InvalidInstrument
from domain.instruments.normalize import NormalizedSymbol, normalize_symbol_input


class TestAShareNormalize:
    def test_plain_six_digit_heuristic_sse(self) -> None:
        result = normalize_symbol_input(Market.A_SHARE, "600519")
        assert result.canonical_candidate == "600519.SH"
        assert result.local_code == "600519"
        assert result.exchange_hint == "SSE"
        assert result.display_symbol == "600519.SH"

    def test_dot_suffix_sh(self) -> None:
        result = normalize_symbol_input(Market.A_SHARE, "600519.SH")
        assert result.canonical_candidate == "600519.SH"
        assert result.exchange_hint == "SSE"

    def test_sse_suffix_maps_to_sh(self) -> None:
        result = normalize_symbol_input(Market.A_SHARE, "600519.SSE")
        assert result.canonical_candidate == "600519.SH"
        assert result.exchange_hint == "SSE"

    def test_compact_suffix_and_prefix_forms(self) -> None:
        for raw in ("600519sh", "600519SH", "sh600519", "SH600519"):
            result = normalize_symbol_input(Market.A_SHARE, raw)
            assert result.canonical_candidate == "600519.SH", raw
            assert result.local_code == "600519", raw

    def test_sz_prefix_and_suffix(self) -> None:
        for raw in ("sz000001", "000001.SZ", "000001.SZSE", "000001"):
            result = normalize_symbol_input(Market.A_SHARE, raw)
            assert result.canonical_candidate == "000001.SZ", raw
            assert result.exchange_hint == "SZSE", raw

    def test_chinext_30_prefix(self) -> None:
        result = normalize_symbol_input(Market.A_SHARE, "300750")
        assert result.canonical_candidate == "300750.SZ"
        assert result.exchange_hint == "SZSE"

    def test_star_market_68_prefix(self) -> None:
        result = normalize_symbol_input(Market.A_SHARE, "688981")
        assert result.canonical_candidate == "688981.SH"

    def test_bse_heuristic_and_suffix(self) -> None:
        result = normalize_symbol_input(Market.A_SHARE, "830799")
        assert result.canonical_candidate == "830799.BJ"
        assert result.exchange_hint == "BSE"
        result2 = normalize_symbol_input(Market.A_SHARE, "430047.BJ")
        assert result2.canonical_candidate == "430047.BJ"

    def test_etf_and_index_codes_share_symbol_rules(self) -> None:
        etf = normalize_symbol_input(Market.A_SHARE, "510300.SH", asset_type_hint=AssetType.ETF)
        assert etf.canonical_candidate == "510300.SH"
        assert etf.asset_type_hint is AssetType.ETF
        idx = normalize_symbol_input(Market.A_SHARE, "000300.SH", asset_type_hint=AssetType.INDEX)
        assert idx.canonical_candidate == "000300.SH"

    def test_unresolved_exchange_does_not_invent_suffix(self) -> None:
        # Prefix 99 is outside frozen heuristic table.
        result = normalize_symbol_input(Market.A_SHARE, "990001")
        assert result.canonical_candidate == "990001"
        assert result.local_code == "990001"
        assert result.exchange_hint is None
        assert "a_share_exchange_unresolved" in result.warnings

    def test_fullwidth_digits(self) -> None:
        # Full-width ６００５１９
        result = normalize_symbol_input(Market.A_SHARE, "６００５１９.ＳＨ")
        assert result.canonical_candidate == "600519.SH"

    def test_option_contract_code(self) -> None:
        result = normalize_symbol_input(
            Market.A_SHARE,
            "10007601.SH",
            asset_type_hint=AssetType.OPTION,
        )
        assert result.canonical_candidate == "10007601.SH"
        assert result.local_code == "10007601"
        assert result.exchange_hint == "SSE"
        assert result.asset_type_hint is AssetType.OPTION

    def test_empty_and_invalid(self) -> None:
        with pytest.raises(InvalidInstrument):
            normalize_symbol_input(Market.A_SHARE, "   ")
        with pytest.raises(InvalidInstrument):
            normalize_symbol_input(Market.A_SHARE, "NVDA")


class TestUsNormalize:
    def test_yahoo_continuous_future_requires_explicit_future_hint(self) -> None:
        result = normalize_symbol_input(
            Market.US,
            " gc=f ",
            asset_type_hint=AssetType.FUTURE,
        )
        assert result.canonical_candidate == "GC=F"
        assert result.local_code == "GC"
        assert result.asset_type_hint is AssetType.FUTURE
        assert "continuous_future_roll_risk" in result.warnings

        with pytest.raises(InvalidInstrument, match="continuous future"):
            normalize_symbol_input(
                Market.US,
                "XAUUSD",
                asset_type_hint=AssetType.FUTURE,
            )

    def test_case_and_dollar(self) -> None:
        for raw in ("nvda", "NVDA", "$NVDA", "$nvda"):
            result = normalize_symbol_input(Market.US, raw)
            assert result.canonical_candidate == "NVDA", raw

    def test_class_share_slash_and_hyphen(self) -> None:
        assert normalize_symbol_input(Market.US, "BRK/B").canonical_candidate == "BRK.B"
        assert normalize_symbol_input(Market.US, "BRK-B").canonical_candidate == "BRK.B"
        assert normalize_symbol_input(Market.US, "brk.b").canonical_candidate == "BRK.B"

    def test_strips_exchange_suffix(self) -> None:
        result = normalize_symbol_input(Market.US, "NVDA.NASDAQ")
        assert result.canonical_candidate == "NVDA"
        assert result.exchange_hint == "NASDAQ"

    def test_rejects_embedded_whitespace(self) -> None:
        with pytest.raises(InvalidInstrument) as exc_info:
            normalize_symbol_input(Market.US, "BRK B")
        assert exc_info.value.details.get("reason") == "embedded_whitespace"

    def test_index_caret_form(self) -> None:
        result = normalize_symbol_input(Market.US, "^GSPC")
        assert result.canonical_candidate == "GSPC"
        assert result.display_symbol == "^GSPC"
        assert result.exchange_hint == "INDEX"
        assert result.asset_type_hint is AssetType.INDEX
        assert any("caret" in w for w in result.warnings)

    def test_occ_option(self) -> None:
        occ = "NVDA260717C00150000"
        result = normalize_symbol_input(Market.US, occ, asset_type_hint=AssetType.OPTION)
        assert result.canonical_candidate == occ
        assert result.local_code == "NVDA"
        assert result.asset_type_hint is AssetType.OPTION

    def test_occ_hinted_strips_spaces_and_separators(self) -> None:
        """Design §5.4.5: remove spaces/separators before OCC validation when OPTION-hinted."""
        result = normalize_symbol_input(
            Market.US,
            "NVDA 260717 C 00150000",
            asset_type_hint=AssetType.OPTION,
        )
        assert result.canonical_candidate == "NVDA260717C00150000"
        assert result.local_code == "NVDA"
        assert result.asset_type_hint is AssetType.OPTION

        # Hyphen / slash separators also stripped on OCC path.
        for raw in (
            "NVDA-260717-C-00150000",
            "NVDA/260717/C/00150000",
            "nvda 260717 c 00150000",
        ):
            spaced = normalize_symbol_input(Market.US, raw, asset_type_hint=AssetType.OPTION)
            assert spaced.canonical_candidate == "NVDA260717C00150000", raw

    def test_occ_auto_detect_without_hint(self) -> None:
        occ = "AAPL260116P00180000"
        result = normalize_symbol_input(Market.US, occ)
        assert result.canonical_candidate == occ
        assert result.asset_type_hint is AssetType.OPTION

    def test_occ_auto_detect_spaced_without_hint(self) -> None:
        """Spaced OCC shape after separator strip still auto-detects without OPTION hint."""
        result = normalize_symbol_input(Market.US, "AAPL 260116 P 00180000")
        assert result.canonical_candidate == "AAPL260116P00180000"
        assert result.asset_type_hint is AssetType.OPTION

    def test_occ_invalid_date(self) -> None:
        with pytest.raises(InvalidInstrument) as exc_info:
            normalize_symbol_input(
                Market.US,
                "NVDA261332C00150000",  # month 13
                asset_type_hint=AssetType.OPTION,
            )
        assert exc_info.value.details.get("reason") == "occ_format"

    def test_occ_invalid_format(self) -> None:
        with pytest.raises(InvalidInstrument) as exc_info:
            normalize_symbol_input(
                Market.US,
                "NOTANOPTION",
                asset_type_hint=AssetType.OPTION,
            )
        assert exc_info.value.details.get("reason") == "occ_format"

    def test_rejects_invalid_charset(self) -> None:
        with pytest.raises(InvalidInstrument) as exc_info:
            normalize_symbol_input(Market.US, "NVDA!")
        assert exc_info.value.details.get("reason") == "charset"

    def test_etf_hint_uses_equity_symbol_rules(self) -> None:
        result = normalize_symbol_input(Market.US, "spy", asset_type_hint=AssetType.ETF)
        assert result.canonical_candidate == "SPY"
        assert result.asset_type_hint is AssetType.ETF


def test_normalized_symbol_is_frozen() -> None:
    result = normalize_symbol_input(Market.US, "NVDA")
    assert isinstance(result, NormalizedSymbol)
    with pytest.raises(AttributeError):
        result.canonical_candidate = "X"  # type: ignore[misc]


def test_kr_normalizes_yahoo_security_and_index_aliases() -> None:
    samsung = normalize_symbol_input(
        Market.KR, "005930.KS", asset_type_hint=AssetType.EQUITY
    )
    kosdaq = normalize_symbol_input(Market.KR, "^KQ11")

    assert samsung.canonical_candidate == "005930"
    assert samsung.exchange_hint == "KOSPI"
    assert kosdaq.canonical_candidate == "KQ11"
    assert kosdaq.asset_type_hint is AssetType.INDEX
    assert kosdaq.exchange_hint == "KOSDAQ"
