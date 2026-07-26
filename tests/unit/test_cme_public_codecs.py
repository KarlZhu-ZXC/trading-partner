"""CME public codec contract tests: success fixture + malformed fail-closed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from domain.common.errors import DataContractError
from infrastructure.providers.cross_asset.cme_public_codecs import (
    decode_product_calendar,
    decode_settlements,
    loads_cme_json,
)

_CALENDAR_OK = {
    "monthGroups": [
        {
            "expirationMonth": "DEC 26",
            "monthCode": "Z",
            "year": 2026,
            "lastTrade": "12/29/2026",
            "firstNotice": "11/30/2026",
            "expirationDate": "12/29/2026",
        },
        {
            "expirationMonth": "FEB 27",
            "monthCode": "G",
            "year": 2027,
            "lastTrade": "02/24/2027",
            "expirationDate": "02/24/2027",
        },
    ]
}

_SETTLEMENTS_OK = {
    "settlements": [
        {
            "month": "DEC 26",
            "monthCode": "Z",
            "year": "2026",
            "settle": "2,347.5",
            "volume": "120,000",
            "openInterest": "400,500",
        },
        {
            "month": "FEB 27",
            "monthCode": "G",
            "year": "2027",
            "settle": "2,360.0",
            "volume": "10,000",
            "openInterest": "50,000",
        },
    ],
    "status": "Final",
}


def test_decode_product_calendar_success() -> None:
    rows = decode_product_calendar(_CALENDAR_OK, root="GC")
    assert len(rows) == 2
    assert rows[0].contract_code == "GCZ26"
    assert rows[0].contract_month == "2026-12"
    assert rows[0].last_trade_date == date(2026, 12, 29)
    assert rows[1].contract_code == "GCG27"


def test_decode_settlements_success() -> None:
    doc = decode_settlements(
        _SETTLEMENTS_OK, root="GC", trade_date=date(2026, 7, 24)
    )
    assert doc.is_final is True
    assert len(doc.rows) == 2
    assert doc.rows[0].contract_code == "GCZ26"
    assert doc.rows[0].settlement == Decimal("2347.5")
    assert doc.rows[0].session_volume == Decimal("120000")
    assert doc.rows[0].open_interest == Decimal("400500")


def test_loads_json_rejects_malformed() -> None:
    with pytest.raises(DataContractError):
        loads_cme_json(b"not-json", operation="settlements")


def test_decode_settlements_malformed_payload() -> None:
    with pytest.raises(DataContractError):
        decode_settlements(["not-an-object"], root="GC", trade_date=date(2026, 7, 24))
