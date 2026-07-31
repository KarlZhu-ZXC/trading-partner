"""Industry-cycle and company-operating A-share domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.a_share.enums import (
    CompanyDocumentParseStatus,
    CompanyDocumentType,
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from domain.a_share.model_validation import (
    _EQUITY_ONLY,
    _KEY_MAX,
    _SOURCE_URL_MAX,
    _TITLE_MAX,
    _UNIT_MAX,
    _URL_MAX,
    _require_a_share_instrument_id,
    _require_date,
    _require_decimal,
    _require_int,
    _require_nonnegative_int,
    _require_optional_str,
    _require_str,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


@dataclass(frozen=True, slots=True)
class IndustryMetricObservation:
    """One normalized, publication-aware industry metric observation."""

    metric_code: str
    value: Decimal
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    published_at: datetime
    source_url: str
    geography: str = "CN"
    measurement_basis: IndustryMeasurementBasis = IndustryMeasurementBasis.PERIOD_AVERAGE
    is_estimated: bool = False
    methodology_version: str = "nahs_publication_v1"
    methodology_break: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.metric_code, field="metric_code", max_len=100)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,99}", self.metric_code) is None:
            raise DataContractError("metric_code must use lower_snake_case")
        number = _require_decimal(self.value, field="value")
        if number < 0:
            raise DataContractError("value must be nonnegative")
        _require_str(self.unit, field="unit", max_len=_UNIT_MAX)
        _require_date(self.period_start, field="period_start")
        _require_date(self.period_end, field="period_end")
        if self.period_end < self.period_start:
            raise DataContractError("period_end must be >= period_start")
        if not isinstance(self.frequency, IndustryMetricFrequency):
            raise DataContractError("frequency must be IndustryMetricFrequency")
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.source_url, field="source_url", max_len=_SOURCE_URL_MAX)
        _require_str(self.geography, field="geography", max_len=32)
        if not isinstance(self.measurement_basis, IndustryMeasurementBasis):
            raise DataContractError("measurement_basis must be IndustryMeasurementBasis")
        if type(self.is_estimated) is not bool:
            raise DataContractError("is_estimated must be bool")
        _require_str(self.methodology_version, field="methodology_version", max_len=100)
        if self.methodology_break is not None:
            _require_str(self.methodology_break, field="methodology_break", max_len=500)


@dataclass(frozen=True, slots=True)
class IndustryCycleSnapshot:
    """Extensible industry indicator dataset with no embedded cycle verdict."""

    cycle: IndustryCycleType
    dataset_code: str
    as_of: datetime
    observations: tuple[IndustryMetricObservation, ...]
    missing_components: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.cycle, IndustryCycleType):
            raise DataContractError("cycle must be IndustryCycleType")
        _require_str(self.dataset_code, field="dataset_code", max_len=100)
        require_aware_datetime(self.as_of, field_name="as_of")
        if not isinstance(self.observations, tuple) or not self.observations:
            raise DataContractError("observations must be a non-empty tuple")
        if any(not isinstance(item, IndustryMetricObservation) for item in self.observations):
            raise DataContractError("observations elements must be IndustryMetricObservation")
        expected = tuple(
            sorted(self.observations, key=lambda item: (item.period_end, item.metric_code))
        )
        if self.observations != expected:
            raise DataContractError("observations must be ordered by period_end and metric_code")
        if not isinstance(self.missing_components, tuple) or any(
            not isinstance(item, str) or not item for item in self.missing_components
        ):
            raise DataContractError("missing_components must be non-blank strings")


@dataclass(frozen=True, slots=True)
class DocumentParseReceipt:
    """Auditable receipt for one official disclosure download/parse attempt."""

    announcement_key: str
    title: str
    document_type: CompanyDocumentType
    published_at: datetime
    source_url: str
    pdf_url: str | None
    parser_version: str
    page_count: int | None
    status: CompanyDocumentParseStatus
    extracted_metric_count: int
    warning_code: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.announcement_key, field="announcement_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        if not isinstance(self.document_type, CompanyDocumentType):
            raise DataContractError("document_type must be CompanyDocumentType")
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.source_url, field="source_url", max_len=_URL_MAX)
        _require_optional_str(self.pdf_url, field="pdf_url", max_len=_URL_MAX)
        _require_str(self.parser_version, field="parser_version", max_len=100)
        if self.page_count is not None:
            _require_nonnegative_int(self.page_count, field="page_count")
        if not isinstance(self.status, CompanyDocumentParseStatus):
            raise DataContractError("status must be CompanyDocumentParseStatus")
        count = _require_nonnegative_int(
            self.extracted_metric_count, field="extracted_metric_count"
        )
        if self.status is CompanyDocumentParseStatus.PARSED and count < 1:
            raise DataContractError("parsed receipt requires extracted_metric_count >= 1")
        if self.status is not CompanyDocumentParseStatus.PARSED and count != 0:
            raise DataContractError("non-parsed receipt must have extracted_metric_count == 0")
        if self.warning_code is not None:
            _require_str(self.warning_code, field="warning_code", max_len=128)


@dataclass(frozen=True, slots=True)
class CompanyOperatingMetricObservation:
    """One company-disclosed operating metric with explicit period and basis."""

    instrument_id: str
    metric_code: str
    value: Decimal
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    measurement_basis: IndustryMeasurementBasis
    published_at: datetime
    source_url: str
    parser_version: str
    pdf_url: str | None = None
    announcement_key: str | None = None
    is_audited: bool = False
    is_estimated: bool = False

    def __post_init__(self) -> None:
        _require_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_EQUITY_ONLY,
        )
        _require_str(self.metric_code, field="metric_code", max_len=100)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,99}", self.metric_code) is None:
            raise DataContractError("metric_code must use lower_snake_case")
        number = _require_decimal(self.value, field="value")
        if number < 0:
            raise DataContractError("value must be nonnegative")
        _require_str(self.unit, field="unit", max_len=_UNIT_MAX)
        _require_date(self.period_start, field="period_start")
        _require_date(self.period_end, field="period_end")
        if self.period_end < self.period_start:
            raise DataContractError("period_end must be >= period_start")
        if not isinstance(self.frequency, IndustryMetricFrequency):
            raise DataContractError("frequency must be IndustryMetricFrequency")
        if not isinstance(self.measurement_basis, IndustryMeasurementBasis):
            raise DataContractError("measurement_basis must be IndustryMeasurementBasis")
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.source_url, field="source_url", max_len=_SOURCE_URL_MAX)
        _require_str(self.parser_version, field="parser_version", max_len=100)
        _require_optional_str(self.pdf_url, field="pdf_url", max_len=_URL_MAX)
        _require_optional_str(self.announcement_key, field="announcement_key", max_len=_KEY_MAX)
        if type(self.is_audited) is not bool:
            raise DataContractError("is_audited must be bool")
        if type(self.is_estimated) is not bool:
            raise DataContractError("is_estimated must be bool")


@dataclass(frozen=True, slots=True)
class CompanyOperatingMetricsSnapshot:
    """Bounded company operating-metric package with document parse receipts."""

    instrument_id: str
    as_of: datetime
    lookback_months: int
    observations: tuple[CompanyOperatingMetricObservation, ...]
    documents: tuple[DocumentParseReceipt, ...]
    missing_metric_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_EQUITY_ONLY,
        )
        require_aware_datetime(self.as_of, field_name="as_of")
        months = _require_int(self.lookback_months, field="lookback_months")
        if months < 1 or months > 240:
            raise DataContractError("lookback_months must be in 1..240")
        if not isinstance(self.observations, tuple):
            raise DataContractError("observations must be a tuple")
        if any(
            not isinstance(item, CompanyOperatingMetricObservation)
            for item in self.observations
        ):
            raise DataContractError(
                "observations elements must be CompanyOperatingMetricObservation"
            )
        for item in self.observations:
            if item.instrument_id != self.instrument_id:
                raise DataContractError("observation instrument_id must match snapshot")
        expected = tuple(
            sorted(
                self.observations,
                key=lambda item: (
                    -item.period_end.toordinal(),
                    item.metric_code,
                    item.measurement_basis.value,
                ),
            )
        )
        if self.observations != expected:
            raise DataContractError(
                "observations must be ordered newest period_end first, then metric_code"
            )
        if len(self.observations) > 200:
            raise DataContractError("observations must not exceed 200 rows")
        if not isinstance(self.documents, tuple):
            raise DataContractError("documents must be a tuple")
        if any(not isinstance(item, DocumentParseReceipt) for item in self.documents):
            raise DataContractError("documents elements must be DocumentParseReceipt")
        if not isinstance(self.missing_metric_codes, tuple) or any(
            not isinstance(item, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,99}", item) is None
            for item in self.missing_metric_codes
        ):
            raise DataContractError("missing_metric_codes must be lower_snake_case strings")

