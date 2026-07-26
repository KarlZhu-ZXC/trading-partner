"""Fundamental and research-memory cache codecs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Final

# --- E3 codecs (§18.3) --------------------------------------------------------
from domain.a_share.enums import FinancialStatementType  # noqa: E402
from domain.a_share.models import (  # noqa: E402
    AnalystReportItem,
    AnnouncementItem,
    ConsensusEstimate,
    DividendRecord,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    NewsItem,
    UnlockRecord,
)
from domain.common.enums import (
    DataCategory,
    ReliabilityLevel,  # noqa: E402
)
from infrastructure.providers.a_share.codecs.base import (
    _VENDOR_BY_VALUE,
    AShareProviderCacheCodec,
    _contract_error,
    _decode_date,
    _decode_datetime,
    _decode_decimal,
    _decode_enum,
    _decode_int,
    _decode_optional_decimal,
    _decode_optional_int,
    _decode_optional_str,
    _decode_str,
    _encode_date,
    _encode_datetime,
    _encode_decimal,
    _encode_enum,
    _encode_int,
    _encode_optional_decimal,
    _encode_optional_int,
    _encode_optional_str,
    _encode_str,
    _require_mapping,
)

CODEC_FUNDAMENTALS: Final[str] = "a_share_fundamentals.v1"
CODEC_F10: Final[str] = "a_share_f10.v1"
CODEC_STATEMENTS: Final[str] = "a_share_statements.v2"
CODEC_REPORTS: Final[str] = "a_share_reports.v1"
CODEC_CONSENSUS: Final[str] = "a_share_consensus.v1"
CODEC_ANNOUNCEMENTS: Final[str] = "a_share_announcements.v1"
CODEC_CORPORATE_ACTIONS: Final[str] = "a_share_corporate_actions.v1"
CODEC_NEWS: Final[str] = "a_share_news.v1"

E3_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_FUNDAMENTALS,
        CODEC_F10,
        CODEC_STATEMENTS,
        CODEC_REPORTS,
        CODEC_CONSENSUS,
        CODEC_ANNOUNCEMENTS,
        CODEC_CORPORATE_ACTIONS,
        CODEC_NEWS,
    }
)

_RELIABILITY_BY_VALUE: Final[Mapping[str, ReliabilityLevel]] = {
    m.value: m for m in ReliabilityLevel
}
_FIN_STMT_BY_VALUE: Final[Mapping[str, FinancialStatementType]] = {
    m.value: m for m in FinancialStatementType
}


def _encode_optional_datetime(value: datetime | None, *, field: str) -> str | None:
    if value is None:
        return None
    return _encode_datetime(value, field=field)


def _decode_optional_datetime(value: object, *, field: str) -> datetime | None:
    if value is None:
        return None
    return _decode_datetime(value, field=field)


def _encode_metric_value(value: Decimal | str | int | None, *, field: str) -> object:
    if value is None:
        return None
    if type(value) is Decimal:
        return {"t": "d", "v": _encode_decimal(value, field=field)}
    if type(value) is int:
        return {"t": "i", "v": value}
    if isinstance(value, str):
        return {"t": "s", "v": value}
    raise _contract_error(
        f"{field} has unsupported metric value type",
        field=field,
        rule="value_type",
        type=type(value).__name__,
    )


def _decode_metric_value(value: object, *, field: str) -> Decimal | str | int | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _contract_error(
            f"{field} must be a typed metric value object",
            field=field,
            rule="type",
        )
    keys = frozenset(value)
    if keys != frozenset({"t", "v"}):
        raise _contract_error(
            f"{field} metric value keys invalid",
            field=field,
            rule="keys",
        )
    t = value["t"]
    if t == "d":
        return _decode_decimal(value["v"], field=field)
    if t == "i":
        return _decode_int(value["v"], field=field)
    if t == "s":
        return _decode_str(value["v"], field=field)
    raise _contract_error(
        f"{field} unknown metric value tag",
        field=field,
        rule="tag",
    )


_FUND_KEYS: Final[frozenset[str]] = frozenset(
    {"name", "value", "unit", "period_end", "published_at"}
)


def _encode_fundamental(item: FundamentalMetric) -> dict[str, object]:
    if not isinstance(item, FundamentalMetric):
        raise _contract_error(
            "value element must be FundamentalMetric",
            field="value",
            rule="type",
            type=type(item).__name__,
        )
    return {
        "name": _encode_str(item.name, field="name"),
        "value": _encode_metric_value(item.value, field="value"),
        "unit": _encode_optional_str(item.unit, field="unit"),
        "period_end": (
            _encode_date(item.period_end, field="period_end")
            if item.period_end is not None
            else None
        ),
        "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
    }


def _decode_fundamental(raw: object) -> FundamentalMetric:
    obj = _require_mapping(raw, field="value[]", required_keys=_FUND_KEYS)
    period_raw = obj["period_end"]
    period_end = None if period_raw is None else _decode_date(period_raw, field="period_end")
    return FundamentalMetric(
        name=_decode_str(obj["name"], field="name"),
        value=_decode_metric_value(obj["value"], field="value"),
        unit=_decode_optional_str(obj["unit"], field="unit"),
        period_end=period_end,
        published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
    )


def _encode_fundamentals(value: tuple[FundamentalMetric, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_fundamental(item) for item in value]


def _decode_fundamentals(raw: object) -> tuple[FundamentalMetric, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_fundamental(item) for item in raw)


_F10_KEYS: Final[frozenset[str]] = frozenset({"section", "title", "body", "as_of"})


def _encode_f10(item: F10Section) -> dict[str, object]:
    if not isinstance(item, F10Section):
        raise _contract_error("value element must be F10Section", field="value", rule="type")
    return {
        "section": _encode_str(item.section, field="section"),
        "title": _encode_str(item.title, field="title"),
        "body": _encode_str(item.body, field="body"),
        "as_of": _encode_datetime(item.as_of, field="as_of"),
    }


def _decode_f10(raw: object) -> F10Section:
    obj = _require_mapping(raw, field="value[]", required_keys=_F10_KEYS)
    return F10Section(
        section=_decode_str(obj["section"], field="section"),
        title=_decode_str(obj["title"], field="title"),
        body=_decode_str(obj["body"], field="body"),
        as_of=_decode_datetime(obj["as_of"], field="as_of"),
    )


def _encode_f10_sections(value: tuple[F10Section, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_f10(item) for item in value]


def _decode_f10_sections(raw: object) -> tuple[F10Section, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_f10(item) for item in raw)


_STMT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "statement_type",
        "period_end",
        "published_at",
        "item_code",
        "item_name",
        "value",
        "unit",
    }
)


def _encode_statement(item: FinancialStatementLine) -> dict[str, object]:
    if not isinstance(item, FinancialStatementLine):
        raise _contract_error(
            "value element must be FinancialStatementLine",
            field="value",
            rule="type",
        )
    return {
        "statement_type": _encode_enum(
            item.statement_type, field="statement_type", table=_FIN_STMT_BY_VALUE
        ),
        "period_end": _encode_date(item.period_end, field="period_end"),
        "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
        "item_code": _encode_str(item.item_code, field="item_code"),
        "item_name": _encode_str(item.item_name, field="item_name"),
        "value": _encode_optional_decimal(item.value, field="value"),
        "unit": _encode_str(item.unit, field="unit"),
    }


def _decode_statement(raw: object) -> FinancialStatementLine:
    obj = _require_mapping(raw, field="value[]", required_keys=_STMT_KEYS)
    return FinancialStatementLine(
        statement_type=_decode_enum(
            obj["statement_type"], field="statement_type", table=_FIN_STMT_BY_VALUE
        ),
        period_end=_decode_date(obj["period_end"], field="period_end"),
        published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
        item_code=_decode_str(obj["item_code"], field="item_code"),
        item_name=_decode_str(obj["item_name"], field="item_name"),
        value=_decode_optional_decimal(obj["value"], field="value"),
        unit=_decode_str(obj["unit"], field="unit"),
    )


def _encode_statements(value: tuple[FinancialStatementLine, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_statement(item) for item in value]


def _decode_statements(raw: object) -> tuple[FinancialStatementLine, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_statement(item) for item in raw)


_CONSENSUS_KEYS: Final[frozenset[str]] = frozenset(
    {"fiscal_year", "metric", "mean", "high", "low", "institution_count"}
)


def _encode_consensus_item(item: ConsensusEstimate) -> dict[str, object]:
    if not isinstance(item, ConsensusEstimate):
        raise _contract_error("value element must be ConsensusEstimate", field="value", rule="type")
    return {
        "fiscal_year": _encode_int(item.fiscal_year, field="fiscal_year"),
        "metric": _encode_str(item.metric, field="metric"),
        "mean": _encode_optional_decimal(item.mean, field="mean"),
        "high": _encode_optional_decimal(item.high, field="high"),
        "low": _encode_optional_decimal(item.low, field="low"),
        "institution_count": _encode_optional_int(
            item.institution_count, field="institution_count"
        ),
    }


def _decode_consensus_item(raw: object) -> ConsensusEstimate:
    obj = _require_mapping(raw, field="value[]", required_keys=_CONSENSUS_KEYS)
    return ConsensusEstimate(
        fiscal_year=_decode_int(obj["fiscal_year"], field="fiscal_year"),
        metric=_decode_str(obj["metric"], field="metric"),
        mean=_decode_optional_decimal(obj["mean"], field="mean"),
        high=_decode_optional_decimal(obj["high"], field="high"),
        low=_decode_optional_decimal(obj["low"], field="low"),
        institution_count=_decode_optional_int(obj["institution_count"], field="institution_count"),
    )


def _encode_consensus(value: tuple[ConsensusEstimate, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_consensus_item(item) for item in value]


def _decode_consensus(raw: object) -> tuple[ConsensusEstimate, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_consensus_item(item) for item in raw)


_REPORT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "report_key",
        "title",
        "institution",
        "analyst_names",
        "published_at",
        "rating",
        "target_price",
        "eps_forecasts",
        "source_url",
        "pdf_url",
    }
)


def _encode_report(item: AnalystReportItem) -> dict[str, object]:
    if not isinstance(item, AnalystReportItem):
        raise _contract_error("value element must be AnalystReportItem", field="value", rule="type")
    return {
        "report_key": _encode_str(item.report_key, field="report_key"),
        "title": _encode_str(item.title, field="title"),
        "institution": _encode_optional_str(item.institution, field="institution"),
        "analyst_names": list(item.analyst_names),
        "published_at": _encode_datetime(item.published_at, field="published_at"),
        "rating": _encode_optional_str(item.rating, field="rating"),
        "target_price": _encode_optional_decimal(item.target_price, field="target_price"),
        "eps_forecasts": _encode_consensus(item.eps_forecasts),
        "source_url": _encode_optional_str(item.source_url, field="source_url"),
        "pdf_url": _encode_optional_str(item.pdf_url, field="pdf_url"),
    }


def _decode_report(raw: object) -> AnalystReportItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_REPORT_KEYS)
    names_raw = obj["analyst_names"]
    if not isinstance(names_raw, list):
        raise _contract_error("analyst_names must be an array", field="analyst_names", rule="type")
    names = tuple(_decode_str(n, field="analyst_names[]") for n in names_raw)
    return AnalystReportItem(
        report_key=_decode_str(obj["report_key"], field="report_key"),
        title=_decode_str(obj["title"], field="title"),
        institution=_decode_optional_str(obj["institution"], field="institution"),
        analyst_names=names,
        published_at=_decode_datetime(obj["published_at"], field="published_at"),
        rating=_decode_optional_str(obj["rating"], field="rating"),
        target_price=_decode_optional_decimal(obj["target_price"], field="target_price"),
        eps_forecasts=_decode_consensus(obj["eps_forecasts"]),
        source_url=_decode_optional_str(obj["source_url"], field="source_url"),
        pdf_url=_decode_optional_str(obj["pdf_url"], field="pdf_url"),
    )


def _encode_reports(value: tuple[AnalystReportItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_report(item) for item in value]


def _decode_reports(raw: object) -> tuple[AnalystReportItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_report(item) for item in raw)


_ANN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "announcement_key",
        "title",
        "published_at",
        "category",
        "source_url",
        "pdf_url",
    }
)


def _encode_announcement(item: AnnouncementItem) -> dict[str, object]:
    if not isinstance(item, AnnouncementItem):
        raise _contract_error("value element must be AnnouncementItem", field="value", rule="type")
    return {
        "announcement_key": _encode_str(item.announcement_key, field="announcement_key"),
        "title": _encode_str(item.title, field="title"),
        "published_at": _encode_datetime(item.published_at, field="published_at"),
        "category": _encode_optional_str(item.category, field="category"),
        "source_url": _encode_str(item.source_url, field="source_url"),
        "pdf_url": _encode_optional_str(item.pdf_url, field="pdf_url"),
    }


def _decode_announcement(raw: object) -> AnnouncementItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_ANN_KEYS)
    return AnnouncementItem(
        announcement_key=_decode_str(obj["announcement_key"], field="announcement_key"),
        title=_decode_str(obj["title"], field="title"),
        published_at=_decode_datetime(obj["published_at"], field="published_at"),
        category=_decode_optional_str(obj["category"], field="category"),
        source_url=_decode_str(obj["source_url"], field="source_url"),
        pdf_url=_decode_optional_str(obj["pdf_url"], field="pdf_url"),
    )


def _encode_announcements(value: tuple[AnnouncementItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_announcement(item) for item in value]


def _decode_announcements(raw: object) -> tuple[AnnouncementItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_announcement(item) for item in raw)


_NEWS_KEYS: Final[frozenset[str]] = frozenset(
    {"news_key", "title", "summary", "published_at", "source_name", "source_url"}
)


def _encode_news_item(item: NewsItem) -> dict[str, object]:
    if not isinstance(item, NewsItem):
        raise _contract_error("value element must be NewsItem", field="value", rule="type")
    return {
        "news_key": _encode_str(item.news_key, field="news_key"),
        "title": _encode_str(item.title, field="title"),
        "summary": _encode_optional_str(item.summary, field="summary"),
        "published_at": _encode_datetime(item.published_at, field="published_at"),
        "source_name": _encode_str(item.source_name, field="source_name"),
        "source_url": _encode_optional_str(item.source_url, field="source_url"),
    }


def _decode_news_item(raw: object) -> NewsItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_NEWS_KEYS)
    return NewsItem(
        news_key=_decode_str(obj["news_key"], field="news_key"),
        title=_decode_str(obj["title"], field="title"),
        summary=_decode_optional_str(obj["summary"], field="summary"),
        published_at=_decode_datetime(obj["published_at"], field="published_at"),
        source_name=_decode_str(obj["source_name"], field="source_name"),
        source_url=_decode_optional_str(obj["source_url"], field="source_url"),
    )


def _encode_news(value: tuple[NewsItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_news_item(item) for item in value]


def _decode_news(raw: object) -> tuple[NewsItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_news_item(item) for item in raw)


_UNLOCK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "kind",
        "unlock_date",
        "published_at",
        "unlock_type",
        "unlock_shares",
        "tradable_shares",
        "market_value_cny",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_DIV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "kind",
        "fiscal_year",
        "plan_status",
        "ex_date",
        "cash_per_share",
        "bonus_shares_per_share",
        "transfer_shares_per_share",
        "published_at",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)


def _encode_action(item: UnlockRecord | DividendRecord) -> dict[str, object]:
    if isinstance(item, UnlockRecord):
        return {
            "kind": "unlock",
            "unlock_date": _encode_date(item.unlock_date, field="unlock_date"),
            "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
            "unlock_type": _encode_optional_str(item.unlock_type, field="unlock_type"),
            "unlock_shares": _encode_optional_int(item.unlock_shares, field="unlock_shares"),
            "tradable_shares": _encode_optional_int(item.tradable_shares, field="tradable_shares"),
            "market_value_cny": _encode_optional_decimal(
                item.market_value_cny, field="market_value_cny"
            ),
            "source_vendor": _encode_enum(
                item.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            "reliability": _encode_enum(
                item.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            "is_authoritative": bool(item.is_authoritative),
        }
    if isinstance(item, DividendRecord):
        return {
            "kind": "dividend",
            "fiscal_year": _encode_int(item.fiscal_year, field="fiscal_year"),
            "plan_status": _encode_str(item.plan_status, field="plan_status"),
            "ex_date": (
                _encode_date(item.ex_date, field="ex_date") if item.ex_date is not None else None
            ),
            "cash_per_share": _encode_optional_decimal(item.cash_per_share, field="cash_per_share"),
            "bonus_shares_per_share": _encode_optional_decimal(
                item.bonus_shares_per_share, field="bonus_shares_per_share"
            ),
            "transfer_shares_per_share": _encode_optional_decimal(
                item.transfer_shares_per_share, field="transfer_shares_per_share"
            ),
            "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
            "source_vendor": _encode_enum(
                item.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            "reliability": _encode_enum(
                item.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            "is_authoritative": bool(item.is_authoritative),
        }
    raise _contract_error(
        "value element must be UnlockRecord or DividendRecord",
        field="value",
        rule="type",
        type=type(item).__name__,
    )


def _decode_action(raw: object) -> UnlockRecord | DividendRecord:
    if not isinstance(raw, dict) or "kind" not in raw:
        raise _contract_error(
            "corporate action must be a tagged object", field="value", rule="type"
        )
    kind = raw["kind"]
    if kind == "unlock":
        obj = _require_mapping(raw, field="value[]", required_keys=_UNLOCK_KEYS)
        return UnlockRecord(
            unlock_date=_decode_date(obj["unlock_date"], field="unlock_date"),
            published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
            unlock_type=_decode_optional_str(obj["unlock_type"], field="unlock_type"),
            unlock_shares=_decode_optional_int(obj["unlock_shares"], field="unlock_shares"),
            tradable_shares=_decode_optional_int(obj["tradable_shares"], field="tradable_shares"),
            market_value_cny=_decode_optional_decimal(
                obj["market_value_cny"], field="market_value_cny"
            ),
            source_vendor=_decode_enum(
                obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            reliability=_decode_enum(
                obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            is_authoritative=bool(obj["is_authoritative"]),
        )
    if kind == "dividend":
        obj = _require_mapping(raw, field="value[]", required_keys=_DIV_KEYS)
        ex_raw = obj["ex_date"]
        ex_date = None if ex_raw is None else _decode_date(ex_raw, field="ex_date")
        return DividendRecord(
            fiscal_year=_decode_int(obj["fiscal_year"], field="fiscal_year"),
            plan_status=_decode_str(obj["plan_status"], field="plan_status"),
            ex_date=ex_date,
            cash_per_share=_decode_optional_decimal(obj["cash_per_share"], field="cash_per_share"),
            bonus_shares_per_share=_decode_optional_decimal(
                obj["bonus_shares_per_share"], field="bonus_shares_per_share"
            ),
            transfer_shares_per_share=_decode_optional_decimal(
                obj["transfer_shares_per_share"], field="transfer_shares_per_share"
            ),
            published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
            source_vendor=_decode_enum(
                obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            reliability=_decode_enum(
                obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            is_authoritative=bool(obj["is_authoritative"]),
        )
    raise _contract_error(
        "corporate action kind must be unlock or dividend",
        field="kind",
        rule="enum",
    )


def _encode_actions(value: tuple[UnlockRecord | DividendRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_action(item) for item in value]


def _decode_actions(raw: object) -> tuple[UnlockRecord | DividendRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_action(item) for item in raw)


def fundamentals_codec() -> AShareProviderCacheCodec[tuple[FundamentalMetric, ...]]:
    return AShareProviderCacheCodec(
        CODEC_FUNDAMENTALS,
        _encode_fundamentals,
        _decode_fundamentals,
        expected_category=DataCategory.FUNDAMENTALS,
    )


def f10_codec() -> AShareProviderCacheCodec[tuple[F10Section, ...]]:
    return AShareProviderCacheCodec(
        CODEC_F10,
        _encode_f10_sections,
        _decode_f10_sections,
        expected_category=DataCategory.FUNDAMENTALS,
    )


def statements_codec() -> AShareProviderCacheCodec[tuple[FinancialStatementLine, ...]]:
    return AShareProviderCacheCodec(
        CODEC_STATEMENTS,
        _encode_statements,
        _decode_statements,
        expected_category=DataCategory.FINANCIAL_STATEMENTS,
    )


def reports_codec() -> AShareProviderCacheCodec[tuple[AnalystReportItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_REPORTS,
        _encode_reports,
        _decode_reports,
        expected_category=DataCategory.RESEARCH_REPORTS,
    )


def consensus_codec() -> AShareProviderCacheCodec[tuple[ConsensusEstimate, ...]]:
    return AShareProviderCacheCodec(
        CODEC_CONSENSUS,
        _encode_consensus,
        _decode_consensus,
        expected_category=DataCategory.RESEARCH_REPORTS,
    )


def announcements_codec() -> AShareProviderCacheCodec[tuple[AnnouncementItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_ANNOUNCEMENTS,
        _encode_announcements,
        _decode_announcements,
        expected_category=DataCategory.ANNOUNCEMENTS,
    )


def corporate_actions_codec() -> AShareProviderCacheCodec[
    tuple[UnlockRecord | DividendRecord, ...]
]:
    return AShareProviderCacheCodec(
        CODEC_CORPORATE_ACTIONS,
        _encode_actions,
        _decode_actions,
        expected_category=DataCategory.CORPORATE_ACTIONS,
    )


def news_codec() -> AShareProviderCacheCodec[tuple[NewsItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_NEWS,
        _encode_news,
        _decode_news,
        expected_category=DataCategory.NEWS,
    )


E3_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_FUNDAMENTALS: fundamentals_codec,  # type: ignore[dict-item]
    CODEC_F10: f10_codec,  # type: ignore[dict-item]
    CODEC_STATEMENTS: statements_codec,  # type: ignore[dict-item]
    CODEC_REPORTS: reports_codec,  # type: ignore[dict-item]
    CODEC_CONSENSUS: consensus_codec,  # type: ignore[dict-item]
    CODEC_ANNOUNCEMENTS: announcements_codec,  # type: ignore[dict-item]
    CODEC_CORPORATE_ACTIONS: corporate_actions_codec,  # type: ignore[dict-item]
    CODEC_NEWS: news_codec,  # type: ignore[dict-item]
}

# ---------------------------------------------------------------------------

__all__ = [
    "CODEC_ANNOUNCEMENTS",
    "CODEC_CONSENSUS",
    "CODEC_CORPORATE_ACTIONS",
    "CODEC_F10",
    "CODEC_FUNDAMENTALS",
    "CODEC_NEWS",
    "CODEC_REPORTS",
    "CODEC_STATEMENTS",
    "E3_CODEC_IDS",
    "E3_CODECS",
    "announcements_codec",
    "consensus_codec",
    "corporate_actions_codec",
    "f10_codec",
    "fundamentals_codec",
    "news_codec",
    "reports_codec",
    "statements_codec",
]
