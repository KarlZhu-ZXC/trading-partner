"""Stable public façade for frozen A-share domain models.

Capability implementations live in focused sibling modules. Existing imports from
``domain.a_share.models`` remain compatible.
"""

from domain.a_share import calendar_models as _calendar_models
from domain.a_share import capital_models as _capital_models
from domain.a_share import fundamental_models as _fundamental_models
from domain.a_share import industry_models as _industry_models
from domain.a_share import market_context_models as _market_context_models
from domain.a_share import market_models as _market_models
from domain.a_share import research_models as _research_models
from domain.a_share import signal_option_models as _signal_option_models

CompanyOperatingMetricObservation = _industry_models.CompanyOperatingMetricObservation
CompanyOperatingMetricsSnapshot = _industry_models.CompanyOperatingMetricsSnapshot
DocumentParseReceipt = _industry_models.DocumentParseReceipt
IndustryCycleSnapshot = _industry_models.IndustryCycleSnapshot
IndustryMetricObservation = _industry_models.IndustryMetricObservation

AShareBar = _market_models.AShareBar
AShareQuote = _market_models.AShareQuote
OrderBookLevel = _market_models.OrderBookLevel
TradeTick = _market_models.TradeTick
validate_order_book_levels = _market_models.validate_order_book_levels

F10Section = _fundamental_models.F10Section
FinancialStatementLine = _fundamental_models.FinancialStatementLine
FundamentalMetric = _fundamental_models.FundamentalMetric

AnalystReportItem = _research_models.AnalystReportItem
AnnouncementItem = _research_models.AnnouncementItem
ConsensusEstimate = _research_models.ConsensusEstimate
InteractiveQAItem = _research_models.InteractiveQAItem
NewsItem = _research_models.NewsItem

IndustryPerformanceRow = _market_context_models.IndustryPerformanceRow
MarketBoardSnapshot = _market_context_models.MarketBoardSnapshot

BlockTradeRecord = _capital_models.BlockTradeRecord
ChipDistributionBin = _capital_models.ChipDistributionBin
ChipDistributionSnapshot = _capital_models.ChipDistributionSnapshot
DividendRecord = _capital_models.DividendRecord
DragonTigerRecord = _capital_models.DragonTigerRecord
DragonTigerSeat = _capital_models.DragonTigerSeat
FundFlowPoint = _capital_models.FundFlowPoint
MarginRecord = _capital_models.MarginRecord
NorthboundFlowPoint = _capital_models.NorthboundFlowPoint
ShareholderCountRecord = _capital_models.ShareholderCountRecord
UnlockRecord = _capital_models.UnlockRecord

EtfOptionContract = _signal_option_models.EtfOptionContract
EtfOptionQuote = _signal_option_models.EtfOptionQuote
EtfOptionSnapshot = _signal_option_models.EtfOptionSnapshot
LimitPoolEntry = _signal_option_models.LimitPoolEntry
LimitUpContext = _signal_option_models.LimitUpContext
LimitUpLadderRung = _signal_option_models.LimitUpLadderRung
OptionGreeks = _signal_option_models.OptionGreeks
SentimentSignal = _signal_option_models.SentimentSignal

TradingSessionWindow = _calendar_models.TradingSessionWindow
