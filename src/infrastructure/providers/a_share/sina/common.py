"""Shared Sina imports, constants, and endpoint contracts.

Primary owner of financial statements via the frozen host/path family:
``quotes.sina.cn/.../CompanyFinanceService.getFinanceReport2022``.

E4a: daily fund-flow fallback only at the live-verified exact endpoint
``vip.stock.finance.sina.com.cn/.../MoneyFlow.ssl_qsfx_zjlrqs`` (allowlisted).
Does not claim intraday/northbound/margin/etc.

E4c: ETF option chain/quotes/Greeks via live-verified exact endpoints
``stock.finance.sina.com.cn/.../StockOptionService.getStockName``,
``.../getRemainderDay``, and ``hq.sinajs.cn/list`` (allowlisted). Field maps
locked to official Sina ``optionCommon20191223.js`` (CON_OP / CON_SO).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.enums import BarInterval, FinancialStatementType, OptionType
from domain.a_share.models import (
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    FinancialStatementLine,
    FundFlowPoint,
    OptionGreeks,
)
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    StaleMarketData,
)
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument
from domain.market.freshness import classify_freshness
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    SHANGHAI,
    combine_shanghai_date_time,
    content_type_matches,
    decimal_from_text,
    decode_text,
    int_from_text,
    loads_json_decimal,
    loads_json_decimal_declared,
    parse_shanghai_date,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_decimal,
    require_nonnegative_exact_int,
)
from infrastructure.providers.a_share.sina.client import SinaHttpClient
from infrastructure.system.clock import SystemClock

# Frozen §20 host/path (allowlist is case-insensitive).
_STATEMENTS_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/"
    "CompanyFinanceService.getFinanceReport2022"
)
# Live-verified 2026-07-17 capital daily-flow fallback (exact host/path only).
# Content-Type is application/json; charset=gbk; r1/r2/r3 nets may be absent.
_DAILY_FLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
    "json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"
)
# Live-verified 2026-07-17 ETF option metadata (exact host/path only).
_OPTION_STOCK_NAME_URL = (
    "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
    "StockOptionService.getStockName"
)
_OPTION_REMAINDER_URL = (
    "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
    "StockOptionService.getRemainderDay"
)
_HQ_LIST_URL = "https://hq.sinajs.cn/list"
_SINA_REFERER = "https://finance.sina.com.cn/"

_SOURCE_BY_TYPE: Mapping[FinancialStatementType, str] = {
    FinancialStatementType.BALANCE_SHEET: "fzb",
    FinancialStatementType.INCOME_STATEMENT: "lrb",
    FinancialStatementType.CASH_FLOW: "llb",
}

_FINANCIAL_ITEM_CODES: Mapping[FinancialStatementType, Mapping[str, str]] = {
    FinancialStatementType.BALANCE_SHEET: {
        "CURFDS": "cash_and_equivalents",
        "TRADFINASSET": "short_term_investments",
        "ACCORECE": "accounts_receivable",
        "INVE": "inventory",
        "TOTCURRASSET": "current_assets",
        "TOTASSET": "total_assets",
        "SHORTTERMBORR": "short_term_debt",
        "DUENONCLIAB": "current_portion_long_term_debt",
        "TOTALCURRLIAB": "current_liabilities",
        "LONGBORR": "long_term_debt",
        "BDSPAYA": "bonds_payable",
        "TOTLIAB": "total_liabilities",
        "RIGHAGGR": "stockholders_equity",
    },
    FinancialStatementType.INCOME_STATEMENT: {
        "BIZTOTINCO": "total_revenue",
        "BIZINCO": "revenue",
        "BIZCOST": "cost_of_revenue",
        "DEVEEXPE": "research_and_development",
        "SALESEXPE": "selling_expense",
        "MANAEXPE": "general_and_administrative_expense",
        "FINEXPE": "finance_expense",
        "PERPROFIT": "operating_income",
        "NETPROFIT": "net_income",
        "PARENETP": "net_income_attributable_parent",
        "BASICEPS": "eps_basic",
        "DILUTEDEPS": "eps_diluted",
    },
    FinancialStatementType.CASH_FLOW: {
        "MANANETR": "operating_cash_flow",
        "ACQUASSETCASH": "capital_expenditure",
        "INVNETCASHFLOW": "investing_cash_flow",
        "FINNETCFLOW": "financing_cash_flow",
        "CASHNETR": "cash_change",
    },
}

_PAPER_PREFIX: Mapping[str, str] = {"SH": "sh", "SZ": "sz", "BJ": "bj"}

_HQ_CONTENT = (
    "application/javascript",
    "text/javascript",
    "text/plain",
    "application/octet-stream",
)
_SUPPORTED = frozenset(
    {
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.CAPITAL,
        DataCategory.OPTIONS,
    }
)

# Frozen supported ETF option underlyings (pre-network reject otherwise).
# (exchange Chinese name, cate code used by StockOptionService).
_ETF_OPTION_MAP: Mapping[str, tuple[str, str]] = {
    "510050.SH": ("上交所", "50ETF"),
    "510300.SH": ("上交所", "300ETF"),
    "510500.SH": ("上交所", "500ETF"),
    "588000.SH": ("上交所", "科创50"),
    "588080.SH": ("上交所", "科创板50"),
    "159901.SZ": ("深交所", "159901"),
    "159915.SZ": ("深交所", "159915"),
    "159919.SZ": ("深交所", "159919"),
    "159922.SZ": ("深交所", "159922"),
}

# Fail-closed line grammar: each nonblank line must be exactly one assignment.
_HQ_VAR_LINE_RE = re.compile(
    r'^var hq_str_(?P<sym>[A-Za-z0-9_]+)="(?P<body>[^"\\\r\n]*)";$'
)
_OP_LIST_TOKEN_RE = re.compile(r"^CON_OP_(\d+)$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
# Trading code: stock6 + C/P + YYMM + alphabetic adjustment marker + digits.
_SO_TRADING_CODE_RE = re.compile(
    r"^(?P<stock>\d{6})(?P<side>[CP])(?P<yymm>\d{4})(?P<adj>[A-Za-z])(?P<digits>\d+)$"
)

# Deterministic HQ batching for CON_OP/CON_SO list calls (eliminates URL-length caps).
_HQ_MAX_SYMBOLS: int = 80

# CON_OP field indices (official optionCommon20191223.js).
_OP_LAST = 2
_OP_OI = 5
_OP_STRIKE = 7
_OP_ASK5_PRICE = 12  # ask5..ask1 price/qty pairs through index 21
_OP_BID1_PRICE = 22  # bid1..bid5 price/qty pairs through index 31
_OP_QUOTE_AT = 32
_OP_VOLUME = 41
_OP_MIN_FIELDS = 45

# CON_SO field indices.
_SO_DELTA = 5
_SO_GAMMA = 6
_SO_THETA = 7
_SO_VEGA = 8
_SO_IV = 9
_SO_TRADING_CODE = 12
_SO_STRIKE = 13
_SO_THEORETICAL = 15
_SO_MIN_FIELDS = 17


class _EtfOptionKey(NamedTuple):
    exchange: str
    cate: str
    stock_id: str
    board_prefix: str  # "sh" or "sz"
    underlying_instrument_id: str

__all__ = [
    "AssetType",
    "BarInterval",
    "CacheDisposition",
    "Clock",
    "DataCategory",
    "DataContractError",
    "Decimal",
    "EtfOptionContract",
    "EtfOptionQuote",
    "EtfOptionSnapshot",
    "FinancialStatementLine",
    "FinancialStatementType",
    "Freshness",
    "FundFlowPoint",
    "HttpRequest",
    "HttpTransport",
    "Instrument",
    "Mapping",
    "Market",
    "NamedTuple",
    "NoMarketData",
    "OptionGreeks",
    "OptionType",
    "ProviderNotConfigured",
    "ProviderResultMeta",
    "ProviderSuccess",
    "ReliabilityLevel",
    "SHANGHAI",
    "SinaHttpClient",
    "SourceRole",
    "StaleMarketData",
    "SystemClock",
    "TradingSession",
    "VendorId",
    "_DAILY_FLOW_URL",
    "_ETF_OPTION_MAP",
    "_EtfOptionKey",
    "_FINANCIAL_ITEM_CODES",
    "_HQ_CONTENT",
    "_HQ_LIST_URL",
    "_HQ_MAX_SYMBOLS",
    "_HQ_VAR_LINE_RE",
    "_MONTH_RE",
    "_OPTION_REMAINDER_URL",
    "_OPTION_STOCK_NAME_URL",
    "_OP_ASK5_PRICE",
    "_OP_BID1_PRICE",
    "_OP_LAST",
    "_OP_LIST_TOKEN_RE",
    "_OP_MIN_FIELDS",
    "_OP_OI",
    "_OP_QUOTE_AT",
    "_OP_STRIKE",
    "_OP_VOLUME",
    "_PAPER_PREFIX",
    "_SINA_REFERER",
    "_SOURCE_BY_TYPE",
    "_SO_DELTA",
    "_SO_GAMMA",
    "_SO_IV",
    "_SO_MIN_FIELDS",
    "_SO_STRIKE",
    "_SO_THEORETICAL",
    "_SO_THETA",
    "_SO_TRADING_CODE",
    "_SO_TRADING_CODE_RE",
    "_SO_VEGA",
    "_STATEMENTS_URL",
    "_SUPPORTED",
    "annotations",
    "build_instrument_id",
    "classify_freshness",
    "combine_shanghai_date_time",
    "content_type_matches",
    "date",
    "datetime",
    "decimal_from_text",
    "decode_text",
    "infer_session_basic",
    "int_from_text",
    "loads_json_decimal",
    "loads_json_decimal_declared",
    "parse_shanghai_date",
    "parse_shanghai_datetime",
    "publication_cutoff_keep",
    "re",
    "require_a_share_instrument",
    "require_aware_datetime",
    "require_decimal",
    "require_nonnegative_exact_int",
]
