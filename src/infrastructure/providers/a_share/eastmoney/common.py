"""Shared Eastmoney imports, constants, and endpoint contracts.

E2 capabilities: quote, OHLCV bars, order book, ticks, industry performance,
market board. All transport calls go through ``EastmoneyRequestGate``.

E3 capabilities: fundamentals, F10, financial-statement fallback, corporate
actions (unlock/dividend), report search, consensus, company/market news.

E4a capital methods include fund flow, northbound fallback, dragon tiger,
margin, block trades, and shareholder counts. Chip distribution remains
protocol-compatible but fails closed until an allowlisted upstream contract
is verified (no fabricated report names).

E4b: push2ex four limit pools (limit-up / broken / limit-down / previous
limit-up via live-verified getYesterdayZTPool), stockrank hot list
(``eastmoney_hot``), and instrument-scoped stockrank concept-hit counts
(``concept_heat``). Concept hits are not a global concept leaderboard.

Volume fields (quote f47, kline f56, book ladder, tick volume) are lots (手);
domain stores volume_shares = lots * 100 for EQUITY/ETF. INDEX does not invent
share volumes.

Market board is a multi-endpoint composition of allowlisted E2 endpoints only:
  - clist equity universe (breadth + turnover)
  - push2ex limit pools (limit-up / limit-down / broken)
  - clist industry board (industries rows)
"""

from __future__ import annotations

from collections.abc import Mapping as Mapping
from datetime import date as date
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from decimal import Decimal as Decimal
from typing import Any as Any

from application.dto.provider_routing import ProviderResultMeta as ProviderResultMeta
from application.dto.provider_routing import ProviderSuccess as ProviderSuccess
from application.ports.a_share_trading_calendar import (
    AShareTradingCalendar as AShareTradingCalendar,
)
from application.ports.clock import Clock as Clock
from application.ports.http_transport import HttpRequest as HttpRequest
from application.ports.http_transport import HttpResponse as HttpResponse
from application.ports.http_transport import HttpTransport as HttpTransport
from domain.a_share.current_clist_policy import (
    require_current_clist_trade_date as require_current_clist_trade_date,
)
from domain.a_share.enums import BarInterval as BarInterval
from domain.a_share.enums import FinancialStatementType as FinancialStatementType
from domain.a_share.enums import LimitPoolType as LimitPoolType
from domain.a_share.enums import SentimentSourceType as SentimentSourceType
from domain.a_share.enums import TickDirection as TickDirection
from domain.a_share.models import AnalystReportItem as AnalystReportItem
from domain.a_share.models import AShareBar as AShareBar
from domain.a_share.models import AShareQuote as AShareQuote
from domain.a_share.models import BlockTradeRecord as BlockTradeRecord
from domain.a_share.models import ChipDistributionBin as ChipDistributionBin
from domain.a_share.models import ChipDistributionSnapshot as ChipDistributionSnapshot
from domain.a_share.models import ConsensusEstimate as ConsensusEstimate
from domain.a_share.models import DividendRecord as DividendRecord
from domain.a_share.models import DragonTigerRecord as DragonTigerRecord
from domain.a_share.models import DragonTigerSeat as DragonTigerSeat
from domain.a_share.models import F10Section as F10Section
from domain.a_share.models import FinancialStatementLine as FinancialStatementLine
from domain.a_share.models import FundamentalMetric as FundamentalMetric
from domain.a_share.models import FundFlowPoint as FundFlowPoint
from domain.a_share.models import IndustryPerformanceRow as IndustryPerformanceRow
from domain.a_share.models import LimitPoolEntry as LimitPoolEntry
from domain.a_share.models import LimitUpContext as LimitUpContext
from domain.a_share.models import LimitUpLadderRung as LimitUpLadderRung
from domain.a_share.models import MarginRecord as MarginRecord
from domain.a_share.models import MarketBoardSnapshot as MarketBoardSnapshot
from domain.a_share.models import NewsItem as NewsItem
from domain.a_share.models import NorthboundFlowPoint as NorthboundFlowPoint
from domain.a_share.models import OrderBookLevel as OrderBookLevel
from domain.a_share.models import SentimentSignal as SentimentSignal
from domain.a_share.models import ShareholderCountRecord as ShareholderCountRecord
from domain.a_share.models import TradeTick as TradeTick
from domain.a_share.models import UnlockRecord as UnlockRecord
from domain.a_share.models import validate_order_book_levels as validate_order_book_levels
from domain.common.enums import AdjustmentMethod as AdjustmentMethod
from domain.common.enums import AssetType as AssetType
from domain.common.enums import CacheDisposition as CacheDisposition
from domain.common.enums import DataCategory as DataCategory
from domain.common.enums import Freshness as Freshness
from domain.common.enums import Market as Market
from domain.common.enums import ReliabilityLevel as ReliabilityLevel
from domain.common.enums import SourceRole as SourceRole
from domain.common.enums import TradingSession as TradingSession
from domain.common.enums import VendorId as VendorId
from domain.common.errors import DataContractError as DataContractError
from domain.common.errors import NoMarketData as NoMarketData
from domain.common.errors import PartialDataError as PartialDataError
from domain.common.errors import ProviderNotConfigured as ProviderNotConfigured
from domain.common.errors import ProviderUnavailableError as ProviderUnavailableError
from domain.common.errors import StaleMarketData as StaleMarketData
from domain.common.time import require_aware_datetime as require_aware_datetime
from domain.instruments.models import Instrument as Instrument
from domain.market.freshness import classify_freshness as classify_freshness
from domain.market.session import infer_session_basic as infer_session_basic
from infrastructure.providers.a_share._parsing import SHANGHAI as SHANGHAI
from infrastructure.providers.a_share._parsing import (
    combine_shanghai_date_time as combine_shanghai_date_time,
)
from infrastructure.providers.a_share._parsing import decimal_from_text as decimal_from_text
from infrastructure.providers.a_share._parsing import eastmoney_secid as eastmoney_secid
from infrastructure.providers.a_share._parsing import first_day_of_month as first_day_of_month
from infrastructure.providers.a_share._parsing import (
    instrument_id_from_code as instrument_id_from_code,
)
from infrastructure.providers.a_share._parsing import int_from_text as int_from_text
from infrastructure.providers.a_share._parsing import loads_json_decimal as loads_json_decimal
from infrastructure.providers.a_share._parsing import lots_to_shares as lots_to_shares
from infrastructure.providers.a_share._parsing import parse_shanghai_date as parse_shanghai_date
from infrastructure.providers.a_share._parsing import (
    parse_shanghai_datetime as parse_shanghai_datetime,
)
from infrastructure.providers.a_share._parsing import (
    publication_cutoff_keep as publication_cutoff_keep,
)
from infrastructure.providers.a_share._parsing import (
    require_a_share_instrument as require_a_share_instrument,
)
from infrastructure.providers.a_share._parsing import require_decimal as require_decimal
from infrastructure.providers.a_share._parsing import require_exact_date as require_exact_date
from infrastructure.providers.a_share._parsing import require_int as require_int
from infrastructure.providers.a_share._parsing import sanitize_public_url as sanitize_public_url
from infrastructure.providers.a_share._parsing import week_period_start as week_period_start
from infrastructure.providers.a_share.chip_distribution import ChipInputBar as ChipInputBar
from infrastructure.providers.a_share.chip_distribution import (
    derive_tp_chip_v1 as derive_tp_chip_v1,
)
from infrastructure.providers.a_share.eastmoney.client import (
    EastmoneyHttpClient as EastmoneyHttpClient,
)
from infrastructure.providers.a_share.eastmoney_gate import (
    EastmoneyRequestGate as EastmoneyRequestGate,
)
from infrastructure.system.clock import SystemClock as SystemClock

_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_DETAILS_URL = "https://push2.eastmoney.com/api/qt/stock/details/get"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# Public push2ex path casing (allowlist match is case-insensitive).
_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_DT_POOL_URL = "https://push2ex.eastmoney.com/getTopicDTPool"
_ZB_POOL_URL = "https://push2ex.eastmoney.com/getTopicZBPool"
# Live-verified 2026-07-17 previous limit-up pool (getLastZTPool is live 404).
_YESTERDAY_ZT_POOL_URL = "https://push2ex.eastmoney.com/getYesterdayZTPool"
# E3 frozen hosts (§20).
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"
_NEWS_LIST_URL = "https://np-weblist.eastmoney.com/getFastNewsList"
# Capital fund-flow (live-verified stock/fflow paths under §20 host family).
_INTRADAY_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_DAILY_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
# Live-verified 2026-07-17 stockrank hot list (POST JSON, empty body ok).
_STOCKRANK_ALL_CURRENT_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
_STOCKRANK_CONCEPT_HEAT_URL = "https://emappdata.eastmoney.com/stockrank/getHotStockRankList"

_POOL_URL_BY_TYPE: Mapping[LimitPoolType, str] = {
    LimitPoolType.LIMIT_UP: _ZT_POOL_URL,
    LimitPoolType.BROKEN_LIMIT: _ZB_POOL_URL,
    LimitPoolType.LIMIT_DOWN: _DT_POOL_URL,
    LimitPoolType.PREVIOUS_LIMIT_UP: _YESTERDAY_ZT_POOL_URL,
}

_POOL_SORT_BY_TYPE: Mapping[LimitPoolType, str] = {
    LimitPoolType.LIMIT_UP: "fbt:asc",
    LimitPoolType.BROKEN_LIMIT: "fbt:asc",
    LimitPoolType.LIMIT_DOWN: "fund:asc",
    LimitPoolType.PREVIOUS_LIMIT_UP: "fbt:asc",
}

# Eastmoney ``m`` market field → instrument suffix (live-observed).
_EM_M_TO_SUFFIX: Mapping[int, str] = {
    0: "SZ",
    1: "SH",
}

# Northbound mutual-type → domain channel (northbound only; skip southbound).
_NORTHBOUND_MUTUAL_TYPES: Mapping[str, str] = {
    "001": "sh",
    "002": "sz",
    "005": "total",
}

# Mutual amounts from RPT_MUTUAL_DEAL_HISTORY are in 百万元 (million CNY).
_MILLION_CNY = Decimal("1000000")

# Full A-share equity universe on Eastmoney clist. Order frozen for request
# identity; do not silently drop or broaden boards.
#
# Live probing showed standalone ``m:0+t:81`` and ``m:0+t:7`` return a mixed
# instrument bag (~12,445 rows) that exceeds the hard ceiling. The frozen filter
# is exact:
#   m:0+t:6          — Shenzhen main (selected Eastmoney segment behavior also
#                      covers GEM / ChiNext membership in this board layout)
#   m:0+t:80         — Shenzhen SME board segment retained by Eastmoney fs
#   m:1+t:2          — Shanghai main
#   m:1+t:23         — STAR
#   m:0+t:81+s:2048  — BSE restricted by s:2048 (not bare t:81 / t:7)
#
# Forbidden segments (must never reappear standalone): m:0+t:81, m:0+t:7.
EASTMONEY_A_SHARE_EQUITY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
EASTMONEY_INDUSTRY_BOARD_FS = "m:90+t:2"

# Large page size keeps production latency reasonable (~1 request for full A-share).
_CLIST_EQUITY_PAGE_SIZE = 5000
_CLIST_EQUITY_MAX_TOTAL = 12_000
_CLIST_EQUITY_MAX_PAGES = 8
_CLIST_INDUSTRY_PAGE_SIZE = 500
_CLIST_INDUSTRY_MAX_TOTAL = 1_000
_CLIST_INDUSTRY_MAX_PAGES = 4

# Public static routing identifiers for push2ex pools — NOT credentials.
# Never log the full query string in errors.
_PUSH2EX_UT = "7eea3edcaed734bea9cbfc24409ed989"
_PUSH2EX_DPT = "wz.ztzt"
_PUSH2EX_POOL_PAGE_SIZE = "10000"

_QUOTE_FIELDS = (
    "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f71,"
    "f86,f116,f117,f162,f167,f168,f169,f170,"
    "f19,f20,f17,f18,f15,f16,f13,f14,f11,f12,"
    "f39,f40,f37,f38,f35,f36,f33,f34,f31,f32"
)

_KLT_BY_INTERVAL: Mapping[BarInterval, str] = {
    BarInterval.ONE_MINUTE: "1",
    BarInterval.FIVE_MINUTES: "5",
    BarInterval.FIFTEEN_MINUTES: "15",
    BarInterval.THIRTY_MINUTES: "30",
    BarInterval.SIXTY_MINUTES: "60",
    BarInterval.ONE_DAY: "101",
    BarInterval.ONE_WEEK: "102",
    BarInterval.ONE_MONTH: "103",
}

_FQT_BY_ADJUSTMENT: Mapping[AdjustmentMethod, str] = {
    AdjustmentMethod.NONE: "0",
    AdjustmentMethod.FORWARD_ADJUSTED: "1",
    AdjustmentMethod.BACKWARD_ADJUSTED: "2",
}

_SUPPORTED_CATEGORIES = frozenset(
    {
        DataCategory.MARKET_QUOTE,
        DataCategory.MARKET_OHLCV,
        DataCategory.MARKET_STRUCTURE,
        DataCategory.FUNDAMENTALS,
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.CORPORATE_ACTIONS,
        DataCategory.RESEARCH_REPORTS,
        DataCategory.NEWS,
        DataCategory.CAPITAL,
        DataCategory.LIMIT_UP,
        DataCategory.SENTIMENT,
    }
)

_STATEMENT_REPORT_NAMES: Mapping[FinancialStatementType, str] = {
    FinancialStatementType.BALANCE_SHEET: "RPT_DMSK_FN_BALANCE",
    FinancialStatementType.INCOME_STATEMENT: "RPT_DMSK_FN_INCOME",
    FinancialStatementType.CASH_FLOW: "RPT_DMSK_FN_CASHFLOW",
}

_F10_SECTION_REPORTS: Mapping[str, str] = {
    "company": "RPT_F10_ORG_INFO",
    "business": "RPT_F10_MAINOP",
    "holders": "RPT_F10_HOLDERNUM",
}
