"""A-share business enums (Phase 1E).

Wire-format values are the member ``value`` strings. Once exposed in Tool Schema
they must not change without a migration.
"""

from enum import StrEnum


class AShareSnapshotDetail(StrEnum):
    SUMMARY = "summary"
    FULL = "full"


class AShareMarketScope(StrEnum):
    INSTRUMENT = "instrument"
    INDUSTRY = "industry"
    MARKET = "market"


class AShareComponentType(StrEnum):
    QUOTE = "quote"
    FUNDAMENTALS = "fundamentals"
    STATEMENTS = "statements"
    F10 = "f10"
    ANNOUNCEMENTS = "announcements"
    NEWS = "news"
    CORPORATE_ACTIONS = "corporate_actions"
    BARS = "bars"
    ORDER_BOOK = "order_book"
    TICKS = "ticks"
    INDUSTRIES = "industries"
    MARKET_BOARD = "market_board"
    INTRADAY_FLOW = "intraday_flow"
    DAILY_FLOW = "daily_flow"
    NORTHBOUND = "northbound"
    DRAGON_TIGER = "dragon_tiger"
    MARGIN = "margin"
    BLOCK_TRADE = "block_trade"
    SHAREHOLDER_COUNT = "shareholder_count"
    CHIP_DISTRIBUTION = "chip_distribution"
    UNLOCK = "unlock"
    DIVIDEND = "dividend"
    LIMIT_CONTEXT = "limit_context"
    LIMIT_REASON_TAGS = "limit_reason_tags"
    EASTMONEY_HOT = "eastmoney_hot"
    THS_HOT = "ths_hot"
    CONCEPT_HEAT = "concept_heat"
    INTERACTIVE_QA = "interactive_qa"
    COMPANY_NEWS = "company_news"
    MARKET_NEWS = "market_news"
    OPTION_SNAPSHOT = "option_snapshot"
    REPORTS = "reports"
    CONSENSUS = "consensus"
    INDUSTRY_CYCLE = "industry_cycle"
    COMPANY_OPERATING_METRICS = "company_operating_metrics"


class IndustryCycleType(StrEnum):
    HOG = "hog"


class IndustryMetricFrequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEAR = "half_year"
    ANNUAL = "annual"


class IndustryMeasurementBasis(StrEnum):
    """How an industry observation relates to its stated period."""

    PERIOD_AVERAGE = "period_average"
    PERIOD_END = "period_end"
    PERIOD_TOTAL = "period_total"
    YTD_TOTAL = "ytd_total"
    POLICY_BASELINE = "policy_baseline"


class CompanyDocumentType(StrEnum):
    """Closed document classes used by company operating-metric parse receipts."""

    MONTHLY_OPERATING_BRIEF = "monthly_operating_brief"
    EARNINGS_FORECAST = "earnings_forecast"
    QUARTERLY_REPORT = "quarterly_report"
    HALF_YEAR_REPORT = "half_year_report"
    ANNUAL_REPORT = "annual_report"
    OTHER = "other"


class CompanyDocumentParseStatus(StrEnum):
    """Deterministic parse/download outcome for one official announcement PDF."""

    PARSED = "parsed"
    NO_METRICS = "no_metrics"
    DOWNLOAD_FAILED = "download_failed"
    PARSE_FAILED = "parse_failed"
    INVALID_PDF = "invalid_pdf"
    UNSUPPORTED_URL = "unsupported_url"


class BarInterval(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    SIXTY_MINUTES = "60m"
    ONE_DAY = "1d"
    ONE_WEEK = "1w"
    ONE_MONTH = "1mo"


class TickDirection(StrEnum):
    BUY = "buy"
    SELL = "sell"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class FinancialStatementType(StrEnum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"


class CapitalMetricType(StrEnum):
    INTRADAY_FLOW = "intraday_flow"
    DAILY_FLOW = "daily_flow"
    NORTHBOUND = "northbound"
    DRAGON_TIGER = "dragon_tiger"
    MARGIN = "margin"
    BLOCK_TRADE = "block_trade"
    SHAREHOLDER_COUNT = "shareholder_count"
    CHIP_DISTRIBUTION = "chip_distribution"
    UNLOCK = "unlock"
    DIVIDEND = "dividend"


class LimitPoolType(StrEnum):
    LIMIT_UP = "limit_up"
    BROKEN_LIMIT = "broken_limit"
    LIMIT_DOWN = "limit_down"
    PREVIOUS_LIMIT_UP = "previous_limit_up"


class SentimentSourceType(StrEnum):
    THS_HOT = "ths_hot"
    EASTMONEY_HOT = "eastmoney_hot"
    CONCEPT_HEAT = "concept_heat"
    INTERACTIVE_QA = "interactive_qa"
    COMPANY_NEWS = "company_news"
    MARKET_NEWS = "market_news"


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"
