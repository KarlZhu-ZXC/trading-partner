"""DTOs for the manual QuantConnect Free historical-validation bridge."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuantConnectPrepareInput(_DTO):
    idempotency_key: str = Field(min_length=1, max_length=128)
    strategy_name: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=2_000)
    symbols: tuple[str, ...] = Field(min_length=1, max_length=20)
    start_date: date
    end_date: date
    resolution: Literal["hour", "daily"] = "hour"
    normalization_mode: Literal[
        "raw", "split_adjusted", "adjusted", "total_return"
    ] = "split_adjusted"
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)
    benchmark: str = Field(default="SPY", min_length=1, max_length=32)
    parameters: dict[str, str] = Field(default_factory=dict)
    strategy_code: str = Field(min_length=1, max_length=131_072)
    case_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("strategy_name", "hypothesis")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(symbol.strip().upper() for symbol in value)
        if any(not symbol or len(symbol) > 32 for symbol in normalized):
            raise ValueError("symbols must be bounded non-blank identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("symbols must not contain duplicates")
        return normalized

    @field_validator("benchmark")
    @classmethod
    def normalize_benchmark(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 30:
            raise ValueError("parameters may contain at most 30 entries")
        normalized: dict[str, str] = {}
        for key, item in value.items():
            clean_key = key.strip()
            clean_value = item.strip()
            if not clean_key or len(clean_key) > 64 or len(clean_value) > 256:
                raise ValueError("parameter keys and values must be bounded strings")
            if clean_key in normalized:
                raise ValueError("parameter keys must be unique after normalization")
            normalized[clean_key] = clean_value
        return normalized

    @field_validator("strategy_code")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("strategy_code must not contain NUL bytes")
        return value.rstrip() + "\n"

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class QuantConnectImportInput(_DTO):
    idempotency_key: str = Field(min_length=1, max_length=128)
    validation_id: str = Field(min_length=1, max_length=128)
    results_path: str = Field(min_length=1, max_length=4_096)
    backtest_url: str | None = Field(default=None, max_length=2_048)
    notes: str | None = Field(default=None, max_length=2_000)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip().lower()


class HistoricalValidationCheckDTO(_DTO):
    code: str
    status: Literal["PASS", "WARN", "NOT_EVALUATED"]
    message: str


class QuantConnectPreparedDTO(_DTO):
    validation_id: str
    status: Literal["PREPARED"] = "PREPARED"
    platform: Literal["QUANTCONNECT_FREE"] = "QUANTCONNECT_FREE"
    artifact_directory: str
    main_file: str
    manifest_file: str
    runbook_file: str
    code_sha256: str
    manifest_sha256: str
    duplicate: bool
    manual_steps: tuple[str, ...]
    execution_effect: Literal[False] = False


class QuantConnectImportedDTO(_DTO):
    validation_id: str
    status: Literal["RESULT_IMPORTED"] = "RESULT_IMPORTED"
    platform: Literal["QUANTCONNECT_FREE"] = "QUANTCONNECT_FREE"
    result_file: str
    summary_file: str
    result_sha256: str
    duplicate: bool
    normalized_metrics: dict[str, str]
    raw_statistics: dict[str, str]
    chart_names: tuple[str, ...]
    order_count: int | None
    checks: tuple[HistoricalValidationCheckDTO, ...]
    notes: str | None
    execution_effect: Literal[False] = False
