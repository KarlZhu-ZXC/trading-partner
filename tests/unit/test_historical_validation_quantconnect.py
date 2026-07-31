from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from application.dto.historical_validation import (
    QuantConnectImportInput,
    QuantConnectPrepareInput,
)
from application.services.historical_validation_service import HistoricalValidationService
from infrastructure.artifacts.historical_validation import (
    FileHistoricalValidationArtifactRepository,
)

LEAN_CODE = """from AlgorithmImports import *

class HourlyTrend(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2008, 7, 1)
        self.set_end_date(2026, 7, 1)
        self.add_equity("SPY", Resolution.HOUR)
"""


def _service(
    tmp_path: Path,
    fixed_clock: object,
    id_generator: object,
    secret_redactor: object,
) -> HistoricalValidationService:
    return HistoricalValidationService(
        FileHistoricalValidationArtifactRepository(tmp_path / "artifacts"),
        fixed_clock,  # type: ignore[arg-type]
        id_generator,  # type: ignore[arg-type]
        secret_redactor,  # type: ignore[arg-type]
    )


def _prepare_request() -> QuantConnectPrepareInput:
    return QuantConnectPrepareInput(
        idempotency_key="test",
        strategy_name="SPY hourly baseline",
        hypothesis="Hourly trend should be compared with buy and hold.",
        symbols=("SPY",),
        start_date=date(2008, 7, 1),
        end_date=date(2026, 7, 1),
        resolution="hour",
        normalization_mode="split_adjusted",
        initial_cash="100000",
        benchmark="SPY",
        parameters={"lookback": "20"},
        strategy_code=LEAN_CODE,
    )


def test_prepare_is_idempotent_and_writes_a_private_lean_package(
    tmp_path: Path,
    fixed_clock: object,
    id_generator: object,
    secret_redactor: object,
) -> None:
    service = _service(tmp_path, fixed_clock, id_generator, secret_redactor)

    first = service.prepare_quantconnect(_prepare_request())
    second = service.prepare_quantconnect(_prepare_request())

    assert first.ok and first.data is not None
    assert second.ok and second.data is not None
    assert second.data.validation_id == first.data.validation_id
    assert second.data.duplicate is True
    assert Path(first.data.main_file).read_text(encoding="utf-8") == LEAN_CODE
    manifest = json.loads(Path(first.data.manifest_file).read_text(encoding="utf-8"))
    assert manifest["data_contract"]["normalization_mode"] == "split_adjusted"
    assert manifest["simulation_contract"]["execution_effect"] is False
    assert Path(first.data.main_file).stat().st_mode & 0o077 == 0


def test_import_extracts_metrics_and_keeps_reproducibility_checks_explicit(
    tmp_path: Path,
    fixed_clock: object,
    id_generator: object,
    secret_redactor: object,
) -> None:
    service = _service(tmp_path, fixed_clock, id_generator, secret_redactor)
    prepared = service.prepare_quantconnect(_prepare_request())
    assert prepared.data is not None
    download = tmp_path / "quantconnect-results.json"
    download.write_text(
        json.dumps(
            {
                "statistics": {
                    "Net Profit": "23.10%",
                    "Compounding Annual Return": "7.20%",
                    "Sharpe Ratio": "0.84",
                    "Drawdown": "12.30%",
                    "Total Orders": "18",
                },
                "charts": {"Strategy Equity": {"series": {}}},
                "orders": {"1": {}, "2": {}},
            }
        ),
        encoding="utf-8",
    )

    request = QuantConnectImportInput(
        idempotency_key="test-result",
        validation_id=prepared.data.validation_id,
        results_path=str(download),
        notes="First free-web smoke test",
    )
    result = service.import_quantconnect(request)
    replay = service.import_quantconnect(request)

    assert result.ok and result.degraded and result.data is not None
    assert result.data.normalized_metrics == {
        "net_profit": "23.10%",
        "cagr": "7.20%",
        "sharpe_ratio": "0.84",
        "max_drawdown": "12.30%",
        "total_orders": "18",
    }
    assert result.data.order_count == 2
    checks = {item.code: item.status for item in result.data.checks}
    assert checks["EQUITY_CURVE_PRESENT"] == "PASS"
    assert checks["SOURCE_CODE_MATCH"] == "NOT_EVALUATED"
    assert Path(result.data.result_file).read_bytes() == download.read_bytes()
    assert replay.ok and replay.data is not None and replay.data.duplicate is True


def test_import_prefers_formal_statistics_and_checks_benchmark_and_run_period(
    tmp_path: Path,
    fixed_clock: object,
    id_generator: object,
    secret_redactor: object,
) -> None:
    service = _service(tmp_path, fixed_clock, id_generator, secret_redactor)
    prepared = service.prepare_quantconnect(_prepare_request())
    assert prepared.data is not None
    download = tmp_path / "quantconnect-results.json"
    download.write_text(
        json.dumps(
            {
                "algorithmConfiguration": {
                    "startDate": "2008-07-01T00:00:00Z",
                    "endDate": "2026-05-01T23:59:59Z",
                },
                "statistics": {"Net Profit": "213.064%"},
                "runtimeStatistics": {"Net Profit": "$185,871.28"},
                "charts": {
                    "Benchmark": {
                        "series": {
                            "Benchmark": {
                                "values": [[1214870400, 100.0], [1780272000, 400.0]]
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = service.import_quantconnect(
        QuantConnectImportInput(
            idempotency_key="benchmark-result",
            validation_id=prepared.data.validation_id,
            results_path=str(download),
        )
    )

    assert result.ok and result.data is not None
    assert result.data.normalized_metrics["net_profit"] == "213.064%"
    assert result.data.raw_statistics["Runtime Net Profit"] == "$185,871.28"
    assert result.data.normalized_metrics["benchmark_curve_total_return"] == "300.000%"
    checks = {item.code: item.status for item in result.data.checks}
    assert checks["BENCHMARK_SERIES_PRESENT"] == "PASS"
    assert checks["RESULT_PERIOD_MATCH"] == "WARN"


def test_prepare_rejects_non_lean_python_without_executing_it(
    tmp_path: Path,
    fixed_clock: object,
    id_generator: object,
    secret_redactor: object,
) -> None:
    service = _service(tmp_path, fixed_clock, id_generator, secret_redactor)
    request = _prepare_request().model_copy(update={"strategy_code": "print('not LEAN')\n"})

    result = service.prepare_quantconnect(request)

    assert result.ok is False
    assert result.errors[0].code == "DATA_CONTRACT_ERROR"
