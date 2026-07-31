"""QuantConnect Free bridge: prepare LEAN code and import user-exported results."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from application.dto.error_mapper import to_error_info_from_exception
from application.dto.historical_validation import (
    HistoricalValidationCheckDTO,
    QuantConnectImportedDTO,
    QuantConnectImportInput,
    QuantConnectPreparedDTO,
    QuantConnectPrepareInput,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.historical_validation_artifact_repository import (
    HistoricalValidationArtifactRepository,
)
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import Freshness, Market, SourceRole
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix

_QUANTCONNECT_URL = "https://www.quantconnect.com/terminal"
_RESULTS_DOC_URL = "https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results"
_MANUAL_WARNING = WarningInfo(
    code="QUANTCONNECT_FREE_MANUAL_RUN_REQUIRED",
    message="QuantConnect Free requires the user to run the prepared strategy in the web IDE.",
)
_DATASET_WARNING = WarningInfo(
    code="REMOTE_DATASET_VERSION_UNAVAILABLE",
    message=(
        "The free web export does not provide an immutable QuantConnect dataset version; "
        "the result is not fully reproducible from local artifacts alone."
    ),
)

_NORMALIZED_METRICS: dict[str, str] = {
    "netprofit": "net_profit",
    "compoundingannualreturn": "cagr",
    "annualreturn": "cagr",
    "sharperatio": "sharpe_ratio",
    "probabilisticsharperatio": "probabilistic_sharpe_ratio",
    "drawdown": "max_drawdown",
    "totalorders": "total_orders",
    "totalfees": "total_fees",
    "portfolioturnover": "portfolio_turnover",
    "informationratio": "information_ratio",
    "alpha": "alpha",
    "beta": "beta",
    "winrate": "win_rate",
    "lossrate": "loss_rate",
    "profitlossratio": "profit_loss_ratio",
    "expectancy": "expectancy",
    "estimatedstrategycapacity": "estimated_strategy_capacity",
}


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _code_sha256(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _metric_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _scalar_mapping(value: object, *, limit: int = 200) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if len(result) >= limit:
            break
        if isinstance(key, str) and isinstance(item, (str, int, float, bool)):
            result[key] = str(item)
    return result


def _find_named(value: object, target: str) -> object | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() == target.casefold():
                return cast(object, item)
        for item in value.values():
            found = _find_named(item, target)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_named(item, target)
            if found is not None:
                return found
    return None


def _merged_statistics(
    statistics: Mapping[str, str], runtime: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Keep formal statistics authoritative while retaining conflicting runtime facts."""
    raw_statistics = dict(statistics)
    for key, value in runtime.items():
        if key not in raw_statistics:
            raw_statistics[key] = value
        elif raw_statistics[key] != value:
            raw_statistics[f"Runtime {key}"] = value
    return raw_statistics, {**runtime, **statistics}


def _benchmark_curve_metrics(charts: object) -> dict[str, str]:
    if not isinstance(charts, Mapping):
        return {}
    benchmark_chart = next(
        (
            value
            for key, value in charts.items()
            if isinstance(key, str) and key.casefold() == "benchmark"
        ),
        None,
    )
    if not isinstance(benchmark_chart, Mapping):
        return {}
    series = benchmark_chart.get("series")
    if not isinstance(series, Mapping):
        return {}
    benchmark_series = next(
        (
            value
            for key, value in series.items()
            if isinstance(key, str) and key.casefold() == "benchmark"
        ),
        None,
    )
    if not isinstance(benchmark_series, Mapping):
        return {}
    values = benchmark_series.get("values")
    if not isinstance(values, list):
        return {}

    points: list[tuple[float, float]] = []
    for item in values:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            points.append((float(item[0]), float(item[1])))
    if len(points) < 2 or points[0][1] <= 0 or points[-1][0] <= points[0][0]:
        return {}

    first_value = points[0][1]
    last_value = points[-1][1]
    years = (points[-1][0] - points[0][0]) / (365.2425 * 24 * 60 * 60)
    if years <= 0:
        return {}
    peak = first_value
    max_drawdown = 0.0
    for _, value in points:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    total_return = last_value / first_value - 1
    cagr = (last_value / first_value) ** (1 / years) - 1
    return {
        "benchmark_curve_total_return": f"{total_return * 100:.3f}%",
        "benchmark_curve_cagr": f"{cagr * 100:.3f}%",
        "benchmark_curve_max_drawdown": f"{max_drawdown * 100:.3f}%",
    }


def _result_period(configuration: object) -> tuple[str | None, str | None]:
    if not isinstance(configuration, Mapping):
        return None, None

    def normalized_date(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None

    return normalized_date(configuration.get("startDate")), normalized_date(
        configuration.get("endDate")
    )


def _validate_lean_source(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise DataContractError(
            "strategy_code is not valid Python",
            details={"line": exc.lineno or 0, "offset": exc.offset or 0},
        ) from exc
    algorithm_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "QCAlgorithm" for base in node.bases)
    ]
    if len(algorithm_classes) != 1:
        raise DataContractError(
            "strategy_code must define exactly one top-level QCAlgorithm subclass"
        )
    methods = {
        node.name
        for node in algorithm_classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "initialize" not in methods and "Initialize" not in methods:
        raise DataContractError("QCAlgorithm subclass must define initialize")


class HistoricalValidationService:
    def __init__(
        self,
        repository: HistoricalValidationArtifactRepository,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor

    def prepare_quantconnect(
        self, request: QuantConnectPrepareInput
    ) -> ToolEnvelope[QuantConnectPreparedDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            _validate_lean_source(request.strategy_code)
            request_payload = request.model_dump(mode="json")
            request_sha256 = _canonical_sha256(request_payload)
            validation_id = self._id_generator.new(EntityIdPrefix.VALIDATION)
            code_sha256 = _code_sha256(request.strategy_code)
            manifest: dict[str, object] = {
                "schema_version": "historical-validation-manifest.v1",
                "validation_id": validation_id,
                "platform": "QUANTCONNECT_FREE",
                "engine": "LEAN",
                "run_mode": "MANUAL_WEB",
                "created_at": now.isoformat(),
                "strategy": {
                    "name": request.strategy_name,
                    "hypothesis": request.hypothesis,
                    "case_id": request.case_id,
                    "code_sha256": code_sha256,
                    "parameters": request.parameters,
                },
                "data_contract": {
                    "market": "US",
                    "symbols": request.symbols,
                    "start_date": request.start_date.isoformat(),
                    "end_date": request.end_date.isoformat(),
                    "resolution": request.resolution,
                    "normalization_mode": request.normalization_mode,
                    "dataset_version": None,
                },
                "simulation_contract": {
                    "initial_cash": str(request.initial_cash),
                    "benchmark": request.benchmark,
                    "execution_effect": False,
                    "remote_run_attested": False,
                },
            }
            runbook = self._runbook(request.strategy_name)
            artifact = self._repository.prepare(
                validation_id=validation_id,
                idempotency_key=request.idempotency_key,
                request_sha256=request_sha256,
                strategy_code=request.strategy_code,
                manifest=manifest,
                runbook=runbook,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(
                    SourceReference(
                        name="QuantConnect Free Web",
                        role=SourceRole.PRIMARY,
                        url=_QUANTCONNECT_URL,
                    ),
                ),
                data=QuantConnectPreparedDTO(
                    validation_id=artifact.validation_id,
                    artifact_directory=str(artifact.artifact_directory),
                    main_file=str(artifact.main_file),
                    manifest_file=str(artifact.manifest_file),
                    runbook_file=str(artifact.runbook_file),
                    code_sha256=artifact.code_sha256,
                    manifest_sha256=artifact.manifest_sha256,
                    duplicate=artifact.duplicate,
                    manual_steps=(
                        "Open QuantConnect Free Web and create or open a Python project.",
                        "Replace main.py with the prepared main_file and compile it.",
                        "Run one backtest after checking dates, symbols, cash, and normalization.",
                        "From Overview choose Download Results to save the JSON file.",
                        "Call historical_validation_import with the downloaded JSON path.",
                    ),
                ),
                degraded=True,
                warnings=(_MANUAL_WARNING, _DATASET_WARNING),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolEnvelope.failure(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=now,
                errors=(to_error_info_from_exception(exc, self._secret_redactor),),
            )

    def import_quantconnect(
        self, request: QuantConnectImportInput
    ) -> ToolEnvelope[QuantConnectImportedDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            source_path = Path(request.results_path).expanduser().resolve(strict=True)
            if not source_path.is_file() or source_path.suffix.casefold() != ".json":
                raise DataContractError("results_path must point to a QuantConnect JSON file")
            if source_path.stat().st_size > 64 * 1024 * 1024:
                raise DataContractError("QuantConnect result JSON exceeds the 64 MiB import limit")
            raw = source_path.read_bytes()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DataContractError("results_path does not contain valid JSON") from exc
            if not isinstance(payload, Mapping):
                raise DataContractError("QuantConnect result JSON must have an object root")
            manifest = self._repository.load_manifest(request.validation_id)
            result_sha256 = hashlib.sha256(raw).hexdigest()
            statistics = _scalar_mapping(_find_named(payload, "statistics"))
            runtime = _scalar_mapping(_find_named(payload, "runtimeStatistics"))
            raw_statistics, normalization_statistics = _merged_statistics(statistics, runtime)
            normalized = {
                normalized_name: value
                for key, value in normalization_statistics.items()
                if (normalized_name := _NORMALIZED_METRICS.get(_metric_key(key))) is not None
            }
            charts = _find_named(payload, "charts")
            benchmark_metrics = _benchmark_curve_metrics(charts)
            normalized.update(benchmark_metrics)
            chart_names = tuple(str(key) for key in charts) if isinstance(charts, Mapping) else ()
            actual_period = _result_period(_find_named(payload, "algorithmConfiguration"))
            orders = _find_named(payload, "orders")
            order_count = len(orders) if isinstance(orders, (Mapping, list)) else None
            if order_count is None and "total_orders" in normalized:
                digits = re.sub(r"[^0-9]", "", normalized["total_orders"])
                order_count = int(digits) if digits else None
            checks = self._checks(
                raw_statistics=raw_statistics,
                chart_names=chart_names,
                order_count=order_count,
                manifest=manifest,
                benchmark_metrics=benchmark_metrics,
                actual_period=actual_period,
            )
            summary: dict[str, object] = {
                "schema_version": "historical-validation-result-summary.v2",
                "validation_id": request.validation_id,
                "platform": "QUANTCONNECT_FREE",
                "imported_at": now.isoformat(),
                "result_sha256": result_sha256,
                "backtest_url": request.backtest_url,
                "notes": request.notes,
                "normalized_metrics": normalized,
                "raw_statistics": raw_statistics,
                "chart_names": chart_names,
                "order_count": order_count,
                "checks": [check.model_dump(mode="json") for check in checks],
            }
            request_sha256 = _canonical_sha256(
                {
                    "validation_id": request.validation_id,
                    "result_sha256": result_sha256,
                    "backtest_url": request.backtest_url,
                    "notes": request.notes,
                }
            )
            artifact = self._repository.import_result(
                validation_id=request.validation_id,
                idempotency_key=request.idempotency_key,
                request_sha256=request_sha256,
                source_path=source_path,
                summary=summary,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(
                    SourceReference(
                        name="QuantConnect user-exported result",
                        role=SourceRole.PRIMARY,
                        url=request.backtest_url or _RESULTS_DOC_URL,
                        retrieved_at=now,
                    ),
                ),
                data=QuantConnectImportedDTO(
                    validation_id=request.validation_id,
                    result_file=str(artifact.result_file),
                    summary_file=str(artifact.summary_file),
                    result_sha256=artifact.result_sha256,
                    duplicate=artifact.duplicate,
                    normalized_metrics=normalized,
                    raw_statistics=raw_statistics,
                    chart_names=chart_names,
                    order_count=order_count,
                    checks=checks,
                    notes=request.notes,
                ),
                degraded=True,
                warnings=(
                    _DATASET_WARNING,
                    WarningInfo(
                        code="REMOTE_RUN_ATTESTATION_UNAVAILABLE",
                        message=(
                            "The downloaded file cannot prove that QuantConnect ran the exact "
                            "prepared code and parameters; compare the Code tab before export."
                        ),
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolEnvelope.failure(
                request_id=request_id,
                market=Market.US,
                as_of=now,
                fetched_at=now,
                errors=(to_error_info_from_exception(exc, self._secret_redactor),),
            )

    @staticmethod
    def _runbook(strategy_name: str) -> str:
        return f"""# QuantConnect Free runbook — {strategy_name}

1. Open https://www.quantconnect.com/terminal and sign in.
2. Create a Python project or reuse a dedicated Trading Partner project.
3. Replace `main.py` with the adjacent prepared file.
4. Compare the web Code tab with `manifest.json` and verify symbols, dates,
   resolution, normalization, initial cash, benchmark, fees, and slippage.
5. Build, then start exactly one backtest from the web UI.
6. On the result page open Overview and choose **Download Results**.
7. Keep the downloaded JSON private and import it through Trading Partner.

This package does not call QuantConnect, attest its remote dataset, confirm a
Thesis, alter a Trade Plan, or execute a live order.
"""

    @staticmethod
    def _checks(
        *,
        raw_statistics: dict[str, str],
        chart_names: tuple[str, ...],
        order_count: int | None,
        manifest: Mapping[str, object],
        benchmark_metrics: Mapping[str, str],
        actual_period: tuple[str | None, str | None],
    ) -> tuple[HistoricalValidationCheckDTO, ...]:
        strategy = manifest.get("strategy")
        prepared_hash = strategy.get("code_sha256") if isinstance(strategy, Mapping) else None
        data_contract = manifest.get("data_contract")
        expected_period = (
            (
                str(data_contract.get("start_date")),
                str(data_contract.get("end_date")),
            )
            if isinstance(data_contract, Mapping)
            else (None, None)
        )
        period_available = all(expected_period) and all(actual_period)
        period_matches = period_available and expected_period == actual_period
        return (
            HistoricalValidationCheckDTO(
                code="RESULT_STATISTICS_PRESENT",
                status="PASS" if raw_statistics else "WARN",
                message=(
                    "QuantConnect statistics were found."
                    if raw_statistics
                    else "No statistics mapping was found in the exported JSON."
                ),
            ),
            HistoricalValidationCheckDTO(
                code="EQUITY_CURVE_PRESENT",
                status=(
                    "PASS"
                    if any("strategy equity" in name.casefold() for name in chart_names)
                    else "WARN"
                ),
                message="Checks whether the export contains a Strategy Equity chart.",
            ),
            HistoricalValidationCheckDTO(
                code="ORDER_COUNT_AVAILABLE",
                status="PASS" if order_count is not None else "WARN",
                message="Checks whether orders or a total-order statistic can be counted.",
            ),
            HistoricalValidationCheckDTO(
                code="BENCHMARK_SERIES_PRESENT",
                status="PASS" if benchmark_metrics else "WARN",
                message=(
                    "The exported Benchmark curve was found and compared deterministically."
                    if benchmark_metrics
                    else "No usable exported Benchmark curve was found."
                ),
            ),
            HistoricalValidationCheckDTO(
                code="RESULT_PERIOD_MATCH",
                status=(
                    "PASS" if period_matches else "WARN" if period_available else "NOT_EVALUATED"
                ),
                message=(
                    "The exported run period matches the prepared data contract."
                    if period_matches
                    else (
                        f"Prepared period {expected_period[0]} to {expected_period[1]}; "
                        f"exported run period {actual_period[0]} to {actual_period[1]}."
                        if period_available
                        else "The exported run period could not be compared with the manifest."
                    )
                ),
            ),
            HistoricalValidationCheckDTO(
                code="SOURCE_CODE_MATCH",
                status="NOT_EVALUATED",
                message=(
                    "Free-web exports do not attest the prepared code hash "
                    f"{prepared_hash or 'unknown'}."
                ),
            ),
            HistoricalValidationCheckDTO(
                code="POINT_IN_TIME_DATASET_VERSION",
                status="NOT_EVALUATED",
                message="The remote QuantConnect dataset version is not exposed by this export.",
            ),
        )
