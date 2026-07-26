"""Focused CME contract / continuous identity grammar tests."""

from __future__ import annotations

import pytest

from domain.common.errors import DataContractError, InvalidInstrument
from domain.cross_asset.cme_identity import (
    build_cme_continuous_symbol,
    is_legacy_us_continuous_proxy,
    parse_cme_continuous_code,
    parse_cme_contract_code,
    yahoo_symbol_for_cme_instrument,
)
from domain.cross_asset.enums import RollRule


def test_parse_cme_contract_code_gcz26() -> None:
    code = parse_cme_contract_code("GCZ26")
    assert code.root == "GC"
    assert code.month_code == "Z"
    assert code.year == 2026
    assert code.contract_month == "2026-12"
    assert code.yahoo_symbol == "GCZ26.CMX"
    assert code.instrument_id == "future:CME:GCZ26"
    assert code.exchange == "COMEX"


def test_parse_cme_contract_code_pl_nym() -> None:
    code = parse_cme_contract_code("PLV26")
    assert code.yahoo_symbol == "PLV26.NYM"
    assert code.exchange == "NYMEX"


def test_parse_cme_contract_code_rejects_unknown_root() -> None:
    with pytest.raises(InvalidInstrument):
        parse_cme_contract_code("CLZ26")


def test_yahoo_symbol_never_rewrites_us_proxy() -> None:
    assert is_legacy_us_continuous_proxy("future:US:GC=F")
    with pytest.raises(InvalidInstrument):
        yahoo_symbol_for_cme_instrument("future:US:GC=F")


def test_yahoo_symbol_maps_specific_only() -> None:
    assert yahoo_symbol_for_cme_instrument("future:CME:GCZ26") == "GCZ26.CMX"
    with pytest.raises(InvalidInstrument):
        yahoo_symbol_for_cme_instrument("future:CME:GC.v.0")


def test_continuous_symbol_grammar() -> None:
    assert build_cme_continuous_symbol("GC", RollRule.VOLUME, 0) == "GC.v.0"
    parsed = parse_cme_continuous_code("GC.oi.1")
    assert parsed.roll_rule is RollRule.OPEN_INTEREST
    assert parsed.rank == 1
    assert parsed.instrument_id == "future:CME:GC.oi.1"
    with pytest.raises(DataContractError):
        build_cme_continuous_symbol("GC", RollRule.CALENDAR, -1)
