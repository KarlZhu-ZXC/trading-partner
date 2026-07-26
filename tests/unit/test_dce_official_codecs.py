"""DCE official codec tests: LH contractInfo/dayQuotes success + malformed reject."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from domain.common.errors import DataContractError
from infrastructure.providers.cross_asset.dce_official_codecs import (
    decode_contract_info,
    decode_day_quotes,
    loads_dce_json,
)

_CONTRACT_INFO_OK = {
    "success": True,
    "data": [
        {
            "contractId": "lh2609",
            "variety": "生猪",
            "varietyOrder": "lh",
            "unit": 16,
            "tick": 5,
            "startTradeDate": "20250916",
            "endTradeDate": "20260914",
            "endDeliveryDate": "20260918",
            "tradeType": "1",
        },
        {
            "contractId": "LH2611",
            "variety": "生猪",
            "varietyOrder": "LH",
            "unit": 16,
            "tick": 5,
            "startTradeDate": "20251114",
            "endTradeDate": "20261112",
            "endDeliveryDate": "20261116",
            "tradeType": "1",
        },
        # Non-LH variety — ignored.
        {
            "contractId": "i2609",
            "variety": "铁矿石",
            "varietyOrder": "i",
            "startTradeDate": "20250901",
            "endTradeDate": "20260915",
            "endDeliveryDate": "20260920",
            "tradeType": "1",
        },
        # Summary row — ignored.
        {
            "contractId": "",
            "variety": "生猪小计",
            "varietyOrder": "lh",
            "tradeType": "1",
        },
        # Option-like row — ignored.
        {
            "contractId": "lh2609-C-14000",
            "variety": "生猪",
            "varietyOrder": "lh",
            "tradeType": "2",
            "optionSeries": "lh2609",
        },
    ],
}

_DAY_QUOTES_OK = {
    "success": True,
    "data": [
        {
            "variety": "生猪",
            "contractId": "lh2609",
            "open": "14000",
            "high": "14150",
            "low": "13950",
            "close": "14080",
            "lastClear": "13980",
            "clearPrice": "14040",
            "volumn": "12345",
            "openInterest": "56789",
            "turnover": "1000000",
            "tradeType": "1",
        },
        {
            "variety": "生猪",
            "contractId": "LH2611",
            "open": "13800",
            "high": "13900",
            "low": "13700",
            "close": "13850",
            "clearPrice": "13820",
            "volumn": "8000",
            "openInterest": "40000",
            "tradeType": "1",
        },
        {
            "variety": "生猪小计",
            "contractId": "",
            "clearPrice": "0",
            "volumn": "0",
            "openInterest": "0",
        },
        {
            "variety": "铁矿石",
            "contractId": "i2609",
            "clearPrice": "800",
            "volumn": "1",
            "openInterest": "2",
            "tradeType": "1",
        },
    ],
}


def test_decode_contract_info_lh_success_normalizes_case() -> None:
    rows = decode_contract_info(_CONTRACT_INFO_OK)
    assert len(rows) == 2
    assert rows[0].contract_code == "LH2609"
    assert rows[0].contract_month == "2026-09"
    assert rows[0].start_trade_date == date(2025, 9, 16)
    assert rows[0].last_trade_date == date(2026, 9, 14)
    assert rows[0].delivery_date == date(2026, 9, 18)
    assert rows[1].contract_code == "LH2611"
    assert rows[1].contract_month == "2026-11"


def test_decode_contract_info_malformed_lh_row_fails_closed() -> None:
    payload = {
        "data": [
            {
                "contractId": "lh99",  # malformed LH identity
                "variety": "生猪",
                "varietyOrder": "lh",
                "startTradeDate": "20250101",
                "endTradeDate": "20260101",
                "endDeliveryDate": "20260105",
                "tradeType": "1",
            }
        ]
    }
    with pytest.raises(DataContractError) as exc:
        decode_contract_info(payload)
    assert exc.value.details.get("rule") == "contract_id"


def test_decode_day_quotes_lh_success_preserves_decimal() -> None:
    doc = decode_day_quotes(_DAY_QUOTES_OK, trade_date=date(2026, 7, 24))
    assert doc.trade_date == date(2026, 7, 24)
    assert len(doc.rows) == 2
    by_code = {r.contract_code: r for r in doc.rows}
    assert by_code["LH2609"].settlement == Decimal("14040")
    assert by_code["LH2609"].session_volume == Decimal("12345")
    assert by_code["LH2609"].open_interest == Decimal("56789")
    assert by_code["LH2609"].open == Decimal("14000")
    assert by_code["LH2609"].close == Decimal("14080")
    assert by_code["LH2611"].settlement == Decimal("13820")


def test_loads_json_rejects_malformed_and_html() -> None:
    with pytest.raises(DataContractError):
        loads_dce_json(b"not-json", operation="contract_info")
    with pytest.raises(DataContractError) as exc:
        loads_dce_json(b"<!DOCTYPE html><html></html>", operation="day_quotes")
    assert exc.value.details.get("rule") == "html_body"
