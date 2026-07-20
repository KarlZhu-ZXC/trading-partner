"""MockInstrumentResolver tests."""

from __future__ import annotations

import pytest

from application.services.mock_instrument_resolver import MockInstrumentResolver
from domain.common.enums import Market
from domain.common.errors import InvalidInstrument


def test_resolve_known() -> None:
    r = MockInstrumentResolver()
    a = r.resolve(Market.A_SHARE, "600519.SH")
    assert a.instrument_id == "equity:A_SHARE:600519.SH"
    u = r.resolve(Market.US, "NVDA")
    assert u.instrument_id == "equity:US:NVDA"


def test_resolve_unknown() -> None:
    r = MockInstrumentResolver()
    with pytest.raises(InvalidInstrument):
        r.resolve(Market.US, "AAPL")
    with pytest.raises(InvalidInstrument):
        r.resolve(Market.A_SHARE, "000001.SZ")
