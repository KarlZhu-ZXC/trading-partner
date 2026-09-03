"""A1 broker-statement parser and redacted inspection contracts."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from application.dto.performance_attribution import (
    AccountPerformanceDTO,
    InstrumentPerformanceDTO,
    PerformanceAttributionDTO,
)
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.services.performance_reconciliation_service import (
    PerformanceReconciliationService,
)
from conftest import FixedClock
from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.attribution.reconciliation_models import BrokerRealizedReconciliation
from domain.common.enums import Freshness, VendorId
from domain.common.errors import DataContractError
from infrastructure.providers.account.reconciliation_writer import (
    OwnerOnlyBrokerReconciliationWriter,
)
from infrastructure.providers.account.schwab_statement_csv import (
    SchwabRealizedGainLossCsvParser,
)

_NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)


class _AttributionReader:
    def __init__(self, data: PerformanceAttributionDTO | None = None) -> None:
        self.data = data
        self.requests: list[object] = []

    def get_performance_attribution(
        self, request: object
    ) -> ToolEnvelope[PerformanceAttributionDTO]:
        self.requests.append(request)
        assert self.data is not None
        return ToolEnvelope.success(
            request_id="req_test",
            market=None,
            as_of=_NOW,
            fetched_at=_NOW,
            freshness=Freshness.UNKNOWN,
            sources=(),
            data=self.data,
            degraded=self.data.status is AttributionStatus.INCOMPLETE,
            warnings=(
                WarningInfo(
                    code="ATTRIBUTION_INCOMPLETE",
                    message="Attribution is incomplete.",
                ),
            )
            if self.data.status is AttributionStatus.INCOMPLETE
            else (),
        )


class _DraftWriter:
    def __init__(self) -> None:
        self.values: list[BrokerRealizedReconciliation] = []

    def write_draft(self, value: BrokerRealizedReconciliation) -> str:
        self.values.append(value)
        return "receipts/draft.json"

_HEADER = (
    '"Symbol","Description","Closed Date","Opened Date","Quantity",'
    '"Proceeds/Share","Cost/Share","Total Proceeds","Cost Basis",'
    '"Gain/Loss $","Gain/Loss %","LT Gain/Loss","ST Gain/Loss",'
    '"Term","Unadjusted Cost","Wash Sale?","Disallowed Loss","Cost Basis Method"'
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
            '"17.14%","","$60.00","Short Term","$350.00","NO","","FIFO"',
            '"NVDA","NVIDIA","06/20/2026","06/01/2025","1",'
            '"150.00","100.00","$150.00","$100.00","$50.00",'
            '"50.00%","$50.00","","Long Term","$100.00","YES","$4.00","FIFO"',
            "Roth IRA ...9876",
            _HEADER,
            '"MSFT","Microsoft","06/25/2026","02/01/2026","3",'
            '"420.00","400.00","$1,260.00","$1,200.00","$60.00",'
            '"5.00%","","$60.00","Short Term","$1,200.00","NO","","FIFO"',
        )
    )


def _attribution(
    *,
    status: AttributionStatus = AttributionStatus.COMPLETE,
    warning_codes: tuple[str, ...] = (),
    after_fees: Decimal | None = Decimal("110.00"),
) -> PerformanceAttributionDTO:
    instruments = (
        InstrumentPerformanceDTO(
            instrument_id="equity:US:AAPL",
            currency="USD",
            ending_quantity=Decimal(0),
            open_cost_basis=Decimal(0),
            realized_pnl_before_fees=Decimal("60.00"),
            realized_pnl_after_fees=(
                Decimal("60.00") if after_fees is not None else None
            ),
            unrealized_pnl_before_fees=Decimal(0),
            broker_reported_unrealized_pnl=None,
            broker_reported_realized_pnl=None,
            dividend_income=Decimal(0),
            net_trading_pnl=(Decimal("60.00") if after_fees is not None else None),
            total_pnl=(Decimal("60.00") if after_fees is not None else None),
            known_fees=Decimal(0),
            fees_complete=after_fees is not None,
            matched_quantity=Decimal(2),
            activity_ids=("activity_aapl",),
            basis_checkpoint_ids=(),
            snapshot_id="snapshot_test",
            warning_codes=(),
        ),
        InstrumentPerformanceDTO(
            instrument_id="equity:US:NVDA",
            currency="USD",
            ending_quantity=Decimal(0),
            open_cost_basis=Decimal(0),
            realized_pnl_before_fees=Decimal("50.00"),
            realized_pnl_after_fees=(
                Decimal("50.00") if after_fees is not None else None
            ),
            unrealized_pnl_before_fees=Decimal(0),
            broker_reported_unrealized_pnl=None,
            broker_reported_realized_pnl=None,
            dividend_income=Decimal(0),
            net_trading_pnl=(Decimal("50.00") if after_fees is not None else None),
            total_pnl=(Decimal("50.00") if after_fees is not None else None),
            known_fees=Decimal(0),
            fees_complete=after_fees is not None,
            matched_quantity=Decimal(1),
            activity_ids=("activity_nvda",),
            basis_checkpoint_ids=(),
            snapshot_id="snapshot_test",
            warning_codes=(),
        ),
    )
    account = AccountPerformanceDTO(
        account_ref="schwab_durable_test",
        provider=VendorId.SCHWAB,
        currency="USD",
        cost_basis_method=CostBasisMethod.FIFO,
        snapshot_id="snapshot_test",
        snapshot_as_of=_NOW,
        realized_pnl_before_fees=Decimal("110.00"),
        realized_pnl_after_fees=after_fees,
        unrealized_pnl_before_fees=Decimal(0),
        broker_reported_unrealized_pnl=None,
        broker_reported_realized_pnl=None,
        dividends=Decimal(0),
        interest=Decimal(0),
        known_fees=Decimal(0),
        fees_complete=after_fees is not None,
        net_external_cash_flow=Decimal(0),
        instruments=instruments,
        status=status,
        warning_codes=warning_codes,
    )
    return PerformanceAttributionDTO(
        start=datetime(2026, 6, 1, tzinfo=UTC),
        end=datetime(2026, 6, 30, 23, 59, tzinfo=UTC),
        cost_basis_method=CostBasisMethod.FIFO,
        accounts=(account,),
        status=status,
        warning_codes=warning_codes,
        algorithm_version="performance_attribution_v1",
    )


def _service(
    root: Path,
    *,
    attribution: PerformanceAttributionDTO | None = None,
    writer: _DraftWriter | None = None,
) -> PerformanceReconciliationService:
    return PerformanceReconciliationService(
        SchwabRealizedGainLossCsvParser(root),
        _AttributionReader(attribution),
        writer or _DraftWriter(),
        FixedClock(_NOW),
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
    service = _service(root)

    result = service.inspect_schwab_realized_gain_loss("realized.csv")

    assert result.lot_count == 3
    assert len(result.accounts) == 2
    assert "lots" not in result.model_dump()


def test_service_compares_one_statement_account_with_durable_fifo(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    _write(root, "realized.csv", _valid_csv())
    writer = _DraftWriter()
    service = _service(root, attribution=_attribution(), writer=writer)
    inspected = service.inspect_schwab_realized_gain_loss("realized.csv")
    statement_ref = next(
        item.statement_account_ref
        for item in inspected.accounts
        if item.lot_count == 2
    )

    result = service.compare_schwab_realized_gain_loss(
        relative_path="realized.csv",
        durable_account_ref="schwab_durable_test",
        statement_account_ref=statement_ref,
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
    )

    assert result.reconciliation_status == "MATCHED"
    assert result.statement_total_realized_pnl == Decimal("110.00")
    assert result.system_total_realized_pnl_after_fees == Decimal("110.00")
    assert result.residual == Decimal(0)
    assert result.absolute_residual == Decimal(0)
    assert result.draft_artifact == "receipts/draft.json"
    assert len(writer.values) == 1
    assert {item.symbol for item in result.comparisons} == {"AAPL", "NVDA"}


def test_service_keeps_incomplete_attribution_and_residual_not_evaluated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "reconciliation"
    _write(root, "realized.csv", _valid_csv())
    service = _service(
        root,
        attribution=_attribution(
            status=AttributionStatus.INCOMPLETE,
            warning_codes=("TRANSACTION_FEES_UNAVAILABLE",),
            after_fees=None,
        ),
    )
    inspected = service.inspect_schwab_realized_gain_loss("realized.csv")
    statement_ref = next(
        item.statement_account_ref
        for item in inspected.accounts
        if item.lot_count == 2
    )

    result = service.compare_schwab_realized_gain_loss(
        relative_path="realized.csv",
        durable_account_ref="schwab_durable_test",
        statement_account_ref=statement_ref,
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
        write_draft=False,
    )

    assert result.reconciliation_status == "INCOMPLETE"
    assert result.residual is None
    assert "ACCOUNT_RESIDUAL_NOT_EVALUATED" in result.residual_codes
    assert "TRANSACTION_FEES_UNAVAILABLE" in result.attribution_warning_codes
    assert result.draft_artifact is None


def test_service_does_not_hide_offsetting_symbol_residuals(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    _write(root, "realized.csv", _valid_csv())
    attribution = _attribution()
    account = attribution.accounts[0]
    instruments = (
        account.instruments[0].model_copy(
            update={"realized_pnl_after_fees": Decimal("70.00")}
        ),
        account.instruments[1].model_copy(
            update={"realized_pnl_after_fees": Decimal("40.00")}
        ),
    )
    attribution = attribution.model_copy(
        update={"accounts": (account.model_copy(update={"instruments": instruments}),)}
    )
    service = _service(root, attribution=attribution)
    inspected = service.inspect_schwab_realized_gain_loss("realized.csv")
    statement_ref = next(
        item.statement_account_ref
        for item in inspected.accounts
        if item.lot_count == 2
    )

    result = service.compare_schwab_realized_gain_loss(
        relative_path="realized.csv",
        durable_account_ref="schwab_durable_test",
        statement_account_ref=statement_ref,
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
        write_draft=False,
    )

    assert result.residual == Decimal(0)
    assert result.reconciliation_status == "REVIEW_REQUIRED"
    assert all(
        "ABSOLUTE_RESIDUAL_ABOVE_TOLERANCE" in item.residual_codes
        for item in result.comparisons
    )


def test_service_requires_statement_account_selection_for_multi_account_export(
    tmp_path: Path,
) -> None:
    root = tmp_path / "reconciliation"
    _write(root, "realized.csv", _valid_csv())
    service = _service(root, attribution=_attribution())

    with pytest.raises(DataContractError) as caught:
        service.compare_schwab_realized_gain_loss(
            relative_path="realized.csv",
            durable_account_ref="schwab_durable_test",
            period_start=datetime(2026, 6, 1, tzinfo=UTC),
            period_end=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
        )
    assert caught.value.code == "SCHWAB_RECONCILIATION_ACCOUNT_AMBIGUOUS"


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


def test_owner_only_writer_creates_redacted_immutable_draft(tmp_path: Path) -> None:
    root = tmp_path / "reconciliation"
    _write(root, "realized.csv", _valid_csv())
    capture = _DraftWriter()
    service = _service(root, attribution=_attribution(), writer=capture)
    inspected = service.inspect_schwab_realized_gain_loss("realized.csv")
    statement_ref = next(
        item.statement_account_ref for item in inspected.accounts if item.lot_count == 2
    )
    service.compare_schwab_realized_gain_loss(
        relative_path="realized.csv",
        durable_account_ref="schwab_durable_test",
        statement_account_ref=statement_ref,
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
        write_draft=True,
    )
    writer = OwnerOnlyBrokerReconciliationWriter(root)

    artifact = writer.write_draft(capture.values[0])
    target = root / artifact
    payload = target.read_text(encoding="utf-8")

    assert artifact.startswith("receipts/schwab-realized-")
    assert target.stat().st_mode & 0o777 == 0o600
    assert (root / "receipts").stat().st_mode & 0o777 == 0o700
    assert "Individual Brokerage" not in payload
    assert "realized.csv" not in payload
    assert '"attribution_status":"COMPLETE"' in payload
    assert '"reconciliation_status":"MATCHED"' in payload
