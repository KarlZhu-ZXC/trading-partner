"""A-share consensus, report, disclosure, news, and Q&A domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.a_share.model_validation import (
    _CONSENSUS_METRICS,
    _KEY_MAX,
    _METRIC_NAME_MAX,
    _NAME_MAX,
    _TITLE_MAX,
    _URL_MAX,
    _require_int,
    _require_optional_decimal,
    _require_optional_nonnegative_int,
    _require_optional_str,
    _require_str,
    _require_str_tuple,
    _require_tuple,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# ---------------------------------------------------------------------------
# §4.3 Research / disclosure / news
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsensusEstimate:
    fiscal_year: int
    metric: str
    mean: Decimal | None
    high: Decimal | None
    low: Decimal | None
    institution_count: int | None

    def __post_init__(self) -> None:
        year = _require_int(self.fiscal_year, field="fiscal_year")
        if year < 1990 or year > 2100:
            raise DataContractError(
                "fiscal_year out of range",
                details={"field": "fiscal_year", "rule": "year_range"},
            )
        metric = _require_str(self.metric, field="metric", max_len=_METRIC_NAME_MAX)
        if metric not in _CONSENSUS_METRICS:
            raise DataContractError(
                "metric must be eps|revenue|net_income",
                details={"field": "metric", "rule": "consensus_metric"},
            )
        mean = _require_optional_decimal(self.mean, field="mean")
        high = _require_optional_decimal(self.high, field="high")
        low = _require_optional_decimal(self.low, field="low")
        if high is not None and low is not None and high < low:
            raise DataContractError(
                "high must be >= low",
                details={"field": "high", "rule": "range_order"},
            )
        if mean is not None and high is not None and mean > high:
            raise DataContractError(
                "mean must be <= high",
                details={"field": "mean", "rule": "range_order"},
            )
        if mean is not None and low is not None and mean < low:
            raise DataContractError(
                "mean must be >= low",
                details={"field": "mean", "rule": "range_order"},
            )
        _require_optional_nonnegative_int(self.institution_count, field="institution_count")


@dataclass(frozen=True, slots=True)
class AnalystReportItem:
    report_key: str
    title: str
    institution: str | None
    analyst_names: tuple[str, ...]
    published_at: datetime
    rating: str | None
    target_price: Decimal | None
    eps_forecasts: tuple[ConsensusEstimate, ...]
    source_url: str | None
    pdf_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.report_key, field="report_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_optional_str(self.institution, field="institution", max_len=_NAME_MAX)
        names = _require_str_tuple(
            self.analyst_names, field="analyst_names", max_item_len=_NAME_MAX
        )
        object.__setattr__(self, "analyst_names", names)
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_optional_str(self.rating, field="rating", max_len=64)
        _require_optional_decimal(self.target_price, field="target_price")
        forecasts = _require_tuple(self.eps_forecasts, field="eps_forecasts")
        for idx, item in enumerate(forecasts):
            if not isinstance(item, ConsensusEstimate):
                raise DataContractError(
                    "eps_forecasts elements must be ConsensusEstimate",
                    details={"field": "eps_forecasts", "index": idx, "rule": "type"},
                )
        _require_optional_str(self.source_url, field="source_url", max_len=_URL_MAX)
        _require_optional_str(self.pdf_url, field="pdf_url", max_len=_URL_MAX)


@dataclass(frozen=True, slots=True)
class AnnouncementItem:
    announcement_key: str
    title: str
    published_at: datetime
    category: str | None
    source_url: str
    pdf_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.announcement_key, field="announcement_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_optional_str(self.category, field="category", max_len=100)
        _require_str(self.source_url, field="source_url", max_len=_URL_MAX)
        _require_optional_str(self.pdf_url, field="pdf_url", max_len=_URL_MAX)


@dataclass(frozen=True, slots=True)
class NewsItem:
    news_key: str
    title: str
    summary: str | None
    published_at: datetime
    source_name: str
    source_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.news_key, field="news_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_optional_str(self.summary, field="summary", max_len=4_000)
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.source_name, field="source_name", max_len=_NAME_MAX)
        _require_optional_str(self.source_url, field="source_url", max_len=_URL_MAX)


@dataclass(frozen=True, slots=True)
class InteractiveQAItem:
    qa_key: str
    question: str
    asked_at: datetime | None
    answer: str
    answered_at: datetime
    source_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.qa_key, field="qa_key", max_len=_KEY_MAX)
        _require_str(self.question, field="question", max_len=8_000)
        if self.asked_at is not None:
            require_aware_datetime(self.asked_at, field_name="asked_at")
        _require_str(self.answer, field="answer", max_len=20_000, allow_blank=True)
        require_aware_datetime(self.answered_at, field_name="answered_at")
        if self.asked_at is not None and self.answered_at < self.asked_at:
            raise DataContractError(
                "answered_at must be >= asked_at",
                details={"field": "answered_at", "rule": "range_order"},
            )
        _require_optional_str(self.source_url, field="source_url", max_len=_URL_MAX)


