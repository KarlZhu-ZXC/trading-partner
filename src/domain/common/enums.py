"""Frozen domain and configuration enums.

Wire-format values are the member ``value`` strings. Once persisted or exposed
in Tool Schema they must not change without a migration.
"""

from enum import StrEnum


class Market(StrEnum):
    A_SHARE = "A_SHARE"
    US = "US"


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    OPTION = "option"


class Freshness(StrEnum):
    FRESH = "fresh"
    DELAYED = "delayed"
    STALE = "stale"
    UNKNOWN = "unknown"


class SourceRole(StrEnum):
    PRIMARY = "primary"
    FALLBACK = "fallback"
    SUPPLEMENTAL = "supplemental"


class TradingSession(StrEnum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    POST_MARKET = "post_market"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AdjustmentMethod(StrEnum):
    NONE = "none"
    SPLIT_ADJUSTED = "split_adjusted"
    SPLIT_AND_DIVIDEND_ADJUSTED = "split_and_dividend_adjusted"
    # Phase 1E append-only (A-share 前/后复权). Existing adjusted members keep
    # generic/US semantics and must not be reinterpreted as 前/后复权.
    FORWARD_ADJUSTED = "forward_adjusted"
    BACKWARD_ADJUSTED = "backward_adjusted"


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# --- Phase 1B research-state enums (wire values frozen once persisted) ---


class InvestmentCaseType(StrEnum):
    COMPANY = "company"
    THEME = "theme"
    MACRO = "macro"
    CATALYST = "catalyst"
    PORTFOLIO_CONCERN = "portfolio_concern"


class InvestmentCaseStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


class ThesisRole(StrEnum):
    PRIMARY = "primary"
    SUB = "sub"
    COMPETITOR = "competitor"
    BEAR = "bear"


class ThesisStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


class AssumptionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    RETIRED = "retired"


class InvalidationSeverity(StrEnum):
    SOFT = "soft"
    HARD = "hard"


class InvalidationStatus(StrEnum):
    ARMED = "armed"
    PARTIALLY_TRIGGERED = "partially_triggered"
    TRIGGERED = "triggered"
    REARMED = "rearmed"
    RETIRED = "retired"


class OpenQuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    STALE = "stale"
    CLOSED_WITHOUT_ANSWER = "closed_without_answer"


class WatchlistItemStatus(StrEnum):
    WATCHING = "watching"
    TRIGGERED = "triggered"
    PROMOTED_TO_CASE = "promoted_to_case"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class CandidateKind(StrEnum):
    THESIS_REVISION = "thesis_revision"
    ASSUMPTION = "assumption"
    INVALIDATION_CONDITION = "invalidation_condition"
    OPEN_QUESTION = "open_question"
    WATCHLIST_ITEM = "watchlist_item"
    CASE_STATUS_CHANGE = "case_status_change"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class ConfirmationMode(StrEnum):
    NORMAL = "normal"
    STRICT_REVIEW = "strict_review"


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InvestmentRating(StrEnum):
    AVOID = "avoid"
    WATCH = "watch"
    SPECULATIVE_BUY = "speculative_buy"
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


# --- Phase 1D instrument / provider enums (wire values frozen once persisted) ---


class DataCategory(StrEnum):
    """Vendor Chain and Core/Optional policy classification keys."""

    MARKET_QUOTE = "market_quote"
    MARKET_OHLCV = "market_ohlcv"
    MARKET_SNAPSHOT = "market_snapshot"
    FUNDAMENTALS = "fundamentals"
    FINANCIAL_STATEMENTS = "financial_statements"
    FILINGS = "filings"
    NEWS = "news"
    MACRO = "macro"
    SENTIMENT = "sentiment"
    PREDICTION_MARKET = "prediction_market"
    CAPITAL = "capital"
    ANNOUNCEMENTS = "announcements"
    LIMIT_UP = "limit_up"
    OPTIONS = "options"
    ACCOUNT = "account"
    INSTRUMENT_MASTER = "instrument_master"
    # Phase 1E append-only categories (wire values frozen).
    MARKET_STRUCTURE = "market_structure"
    # Current aggregate US breadth/rotation facts. Kept separate from order-book
    # market structure because it has a much longer safe cache lifetime.
    MARKET_BREADTH = "market_breadth"
    RESEARCH_REPORTS = "research_reports"
    INTERACTIVE_QA = "interactive_qa"
    CORPORATE_ACTIONS = "corporate_actions"
    # Phase 1G append-only category (wire value frozen).
    INSIDER_ACTIVITY = "insider_activity"


class DataCriticality(StrEnum):
    CORE = "core"
    OPTIONAL = "optional"


class VendorId(StrEnum):
    """Stable global vendor identifiers. Only members may appear in chain config.

    Phase 1D requires MOCK_* and NULL to be runnable. Remaining members freeze
    names for 1E+ adapters (may be UnimplementedVendor until then).
    """

    MOCK_A_SHARE = "mock_a_share"
    MOCK_US = "mock_us"
    NULL = "null"

    # A-share (1E)
    EASTMONEY = "eastmoney"
    TENCENT = "tencent"
    A_SHARE_FALLBACK = "a_share_fallback"
    # Phase 1E append-only vendor ids (wire values frozen).
    SINA = "sina"
    CNINFO = "cninfo"
    THS = "ths"
    CLS = "cls"
    SSE = "sse"
    SZSE = "szse"
    HKEX = "hkex"
    IWENCAI = "iwencai"

    # US / general (1F+)
    BROKER = "broker"
    YFINANCE = "yfinance"
    ALPHA_VANTAGE = "alpha_vantage"
    SEC_EDGAR = "sec_edgar"
    FRED = "fred"
    STOCKTWITS = "stocktwits"
    REDDIT = "reddit"
    POLYMARKET = "polymarket"
    SCHWAB = "schwab"
    MOOMOO = "moomoo"
    MANUAL_CSV = "manual_csv"

    # Instrument resolution sources
    LOCAL_MASTER = "local_master"
    SEED_FIXTURE = "seed_fixture"


class AliasType(StrEnum):
    SYMBOL = "symbol"
    NAME = "name"
    NAME_EN = "name_en"
    ISIN = "isin"
    CUSIP = "cusip"
    SEDOL = "sedol"
    FIGI = "figi"
    EXCHANGE_CODE = "exchange_code"
    LOCAL_CODE = "local_code"
    OPTION_OCC = "option_occ"
    PROVIDER_NATIVE = "provider_native"
    BLOOMBERG = "bloomberg"
    UNKNOWN = "unknown"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CacheDisposition(StrEnum):
    MISS = "miss"
    HIT = "hit"
    BYPASS = "bypass"
    STALE_HIT = "stale_hit"


class ResolveMatchType(StrEnum):
    EXACT_INSTRUMENT_ID = "exact_instrument_id"
    EXACT_SYMBOL = "exact_symbol"
    ALIAS = "alias"
    NORMALIZED_SYMBOL = "normalized_symbol"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class ProviderAttemptOutcome(StrEnum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    FAILURE = "failure"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    SKIPPED_CIRCUIT_OPEN = "skipped_circuit_open"
    SKIPPED_RATE_LIMITED = "skipped_rate_limited"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    CONTRACT_ERROR = "contract_error"
    TIMEOUT = "timeout"
    AUTH_ERROR = "auth_error"


# --- Phase 1C research-memory enums (wire values frozen once persisted) ---


class EvidenceType(StrEnum):
    # Common
    MARKET_SNAPSHOT = "market_snapshot"
    FUNDAMENTAL_SNAPSHOT = "fundamental_snapshot"
    FINANCIAL_STATEMENT = "financial_statement"
    COMPANY_ACTION = "company_action"
    COMPANY_NEWS = "company_news"
    GLOBAL_NEWS = "global_news"
    RESEARCH_REPORT = "research_report"
    TECHNICAL_SIGNAL = "technical_signal"
    SENTIMENT = "sentiment"
    MACRO = "macro"
    ACCOUNT_SNAPSHOT = "account_snapshot"
    PORTFOLIO_SNAPSHOT = "portfolio_snapshot"
    USER_OBSERVATION = "user_observation"

    # A-share
    A_SHARE_ANNOUNCEMENT = "a_share_announcement"
    A_SHARE_INTERACTIVE_QA = "a_share_interactive_qa"
    A_SHARE_ANALYST_REPORT = "a_share_analyst_report"
    A_SHARE_CONSENSUS_ESTIMATE = "a_share_consensus_estimate"
    A_SHARE_CAPITAL_FLOW = "a_share_capital_flow"
    A_SHARE_NORTHBOUND_FLOW = "a_share_northbound_flow"
    A_SHARE_CHIP_DISTRIBUTION = "a_share_chip_distribution"
    A_SHARE_DRAGON_TIGER = "a_share_dragon_tiger"
    A_SHARE_MARGIN_FINANCING = "a_share_margin_financing"
    A_SHARE_BLOCK_TRADE = "a_share_block_trade"
    A_SHARE_SHAREHOLDER_COUNT = "a_share_shareholder_count"
    A_SHARE_UNLOCK = "a_share_unlock"
    A_SHARE_DIVIDEND = "a_share_dividend"
    A_SHARE_ORDER_BOOK = "a_share_order_book"
    A_SHARE_TICK = "a_share_tick"
    A_SHARE_LIMIT_ECOLOGY = "a_share_limit_ecology"
    A_SHARE_MARKET_HEAT = "a_share_market_heat"
    A_SHARE_CONCEPT_HEAT = "a_share_concept_heat"
    A_SHARE_OPTION_SNAPSHOT = "a_share_option_snapshot"

    # US
    SEC_FILING = "sec_filing"
    SEC_COMPANY_FACT = "sec_company_fact"
    US_INSIDER_ACTIVITY = "us_insider_activity"
    US_10B5_1 = "us_10b5_1"
    US_PRE_POST_MARKET = "us_pre_post_market"
    US_NEWS_SENTIMENT = "us_news_sentiment"
    FRED_MACRO = "fred_macro"
    STOCKTWITS_SENTIMENT = "stocktwits_sentiment"
    REDDIT_SENTIMENT = "reddit_sentiment"
    PREDICTION_MARKET = "prediction_market"

    CORRECTION = "correction"


class EvidenceOrigin(StrEnum):
    EXTERNAL_FACT = "external_fact"
    USER_OBSERVATION = "user_observation"
    SYSTEM_DERIVED = "system_derived"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    UNCERTAIN = "uncertain"


class EvidenceQuality(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    UNVERIFIED = "unverified"


class ReliabilityLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ResearchReportType(StrEnum):
    DEEP_DIVE = "deep_dive"
    CATALYST_REVIEW = "catalyst_review"
    A_SHARE_MARKET_REVIEW = "a_share_market_review"
    US_MARKET_REVIEW = "us_market_review"
    PORTFOLIO_REVIEW = "portfolio_review"
    AD_HOC = "ad_hoc"


class ResearchEventType(StrEnum):
    COMPANY = "company"
    EARNINGS = "earnings"
    REGULATORY = "regulatory"
    CORPORATE_ACTION = "corporate_action"
    INDUSTRY = "industry"
    MACRO = "macro"
    POLICY = "policy"
    MARKET_STRUCTURE = "market_structure"
    CAPITAL_MARKET = "capital_market"
    OTHER = "other"


class DecisionType(StrEnum):
    WATCH = "watch"
    NO_ACTION = "no_action"
    INITIATE_INTENT = "initiate_intent"
    ADD_INTENT = "add_intent"
    HOLD = "hold"
    REDUCE_INTENT = "reduce_intent"
    EXIT_INTENT = "exit_intent"
    AVOID = "avoid"
    RESEARCH_MORE = "research_more"


class JournalEntryType(StrEnum):
    NOTE = "note"
    OBSERVATION = "observation"
    REFLECTION = "reflection"
    POSTMORTEM = "postmortem"
    QUESTION = "question"


class ResearchSearchEntityType(StrEnum):
    """Searchable research-memory entity types (wire values frozen)."""

    EVIDENCE = "evidence"
    REPORT = "report"
    EVENT = "event"
    DECISION = "decision"
    JOURNAL = "journal"


class ResearchTimelineEntityType(StrEnum):
    """Unified research timeline projection entity types (wire values frozen)."""

    EVIDENCE = "evidence"
    REPORT = "report"
    EVENT = "event"
    DECISION = "decision"
    JOURNAL = "journal"
    THESIS_REVISION = "thesis_revision"
    CANDIDATE_RESOLUTION = "candidate_resolution"
