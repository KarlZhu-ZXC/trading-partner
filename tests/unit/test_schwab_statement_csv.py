"""A1 broker-statement parser and redacted inspection contracts."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from application.services.performance_reconciliation_service import (
    PerformanceReconciliationService,
)
from domain.common.errors import DataContractError
from infrastructure.providers.account.schwab_statement_csv import (
    SchwabRealizedGainLossCsvParser,
)

_HEADER = (
    '"Symbol","Description","Closed Date","Opened Date","Quantity",'
    '"Proceeds/Share","Cost/Share","Total Proceeds","Cost Basis",'
    '"Gain/Loss $","Gain/Loss %","LT Gain/Loss","ST Gain/Loss",'
    '"Term","Unadjusted Cost","Wash Sale?","Disallowed Loss"'
)


def _write(root: Path, name: str, text: str) -> Path:
    root.mkdir(mode=0o700)
    path = root / name
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o644)
    return path


def _valid_csv() -> str:
    return "\n".join(
        (
            "Realized Gain/Loss - Lot Details",
            "Individual Brokerage ...1234",
            _HEADER,
            '"AAPL","Apple Inc","06/15/2026","01/10/2026","2",'
            '"205.00","175.00","$410.00","$350.00","$60.00",'
            '"17.14%","","$60.00","Short Term","$350.00","NO",""',
            '"NVDA","NVIDIA","06/20/2026","06/01/2025","1",'
            '"150.00","100.00","$150.00","$100.00","$50.00",'
            '"50.00%","$50.00","","Long Term","$100.00","YES","$4.00"',
            "Roth IRA ...9876",
            _HEADER,
            '"MSFT","Microsoft","06/25/2026","02/01/2026","3",'
            '"420.00","400.00","$1,260.00","$1,200.00","$60.00",'
            '"5.00%","","$60.00","Short Term","$1,200.00","NO",""',
        )
    )


def test_parser_reads_multi_account_lots_without_exposing_labels(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    source = _write(root, "realized.csv", _valid_csv())
    result = SchwabRealizedGainLossCsvParser(root).parse_realized_gain_loss("realized.csv")

    assert len(result.accounts) == 2
    assert len(result.lots) == 3
    assert result.accounts[0].statement_account_ref.startswith("schwab_statement_")
    assert "1234" not in json.dumps(result, default=str)
    brokerage_ref = next(
        item.statement_account_ref for item in result.lots if item.symbol == "AAPL"
    )
    brokerage = next(
        item for item in result.accounts if item.statement_account_ref == brokerage_ref
    )
    assert brokerage.total_proceeds == Decimal("560.00")
    assert brokerage.total_cost_basis == Decimal("450.00")
    assert brokerage.total_realized_pnl == Decimal("110.00")
    assert brokerage.total_long_term_pnl is None
    assert brokerage.total_wash_sale_disallowed is None
    nvda = next(item for item in result.lots if item.symbol == "NVDA")
    assert nvda.wash_sale_disallowed == Decimal("4.00")
    assert source.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700


def test_missing_official_cost_and_open_date_remain_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    content = "\n".join(
        (
            "Individual Brokerage ...1234",
            _HEADER,
            '"OLD","Legacy Security","06/15/2026","","2","205.00","",'
            '"$410.00","--","--","","","","Unknown","","NO",""',
        )
    )
    _write(root, "missing.csv", content)

    result = SchwabRealizedGainLossCsvParser(root).parse_realized_gain_loss("missing.csv")

    assert result.accounts[0].total_cost_basis is None
    assert result.accounts[0].total_realized_pnl is None
    assert result.lots[0].opened_date is None
    assert set(result.warning_codes) == {
        "SCHWAB_STATEMENT_COST_BASIS_UNAVAILABLE",
        "SCHWAB_STATEMENT_OPENED_DATE_UNAVAILABLE",
        "SCHWAB_STATEMENT_REALIZED_PNL_UNAVAILABLE",
    }


def test_service_returns_summary_only_not_raw_lots(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    _write(root, "realized.csv", _valid_csv())
    service = PerformanceReconciliationService(SchwabRealizedGainLossCsvParser(root))

    result = service.inspect_schwab_realized_gain_loss("realized.csv")

    assert result.lot_count == 3
    assert len(result.accounts) == 2
    assert "lots" not in result.model_dump()


@pytest.mark.parametrize(
    ("name", "content", "code"),
    (
        ("unknown.csv", "Symbol,Quantity\nAAPL,1\n", "SCHWAB_STATEMENT_FORMAT_ERROR"),
        (
            "bad-number.csv",
            "\n".join(
                (
                    "Individual Brokerage ...1234",
                    _HEADER,
                    '"AAPL","Apple","06/15/2026","01/10/2026","oops",'
                    '"205","175","410","350","60","","","60",'
                    '"Short Term","350","NO",""',
                )
            ),
            "SCHWAB_STATEMENT_FORMAT_ERROR",
        ),
    ),
)
def test_parser_rejects_unrecognized_or_malformed_exports(
    tmp_path: Path, name: str, content: str, code: str
) -> None:
    root = tmp_path / "reconciliation"
    _write(root, name, content)

    with pytest.raises(DataContractError) as caught:
        SchwabRealizedGainLossCsvParser(root).parse_realized_gain_loss(name)
    assert caught.value.code == code


def test_parser_rejects_traversal_absolute_path_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside.csv"
    outside.write_text(_valid_csv(), encoding="utf-8")
    link = root / "link.csv"
    link.symlink_to(outside)
    parser = SchwabRealizedGainLossCsvParser(root)

    for unsafe in ("../outside.csv", str(outside), "link.csv"):
        with pytest.raises(DataContractError) as caught:
            parser.parse_realized_gain_loss(unsafe)
        assert caught.value.code == "SCHWAB_STATEMENT_FILE_SECURITY_ERROR"


def test_parser_rejects_duplicate_closed_lot(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    first_lot = _valid_csv().splitlines()[3]
    content = "\n".join(("Individual Brokerage ...1234", _HEADER, first_lot, first_lot))
    _write(root, "duplicate.csv", content)

    with pytest.raises(DataContractError) as caught:
        SchwabRealizedGainLossCsvParser(root).parse_realized_gain_loss("duplicate.csv")
    assert caught.value.code == "SCHWAB_STATEMENT_FORMAT_ERROR"
    assert caught.value.details == {"rule": "unique_lot"}
