"""Typed application settings loaded from the project-root ``.env`` file."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, DotEnvSettingsSource, NoDecode, SettingsConfigDict

from domain.common.enums import AppEnvironment, DataCategory, LogLevel
from domain.common.errors import ConfigurationError
from infrastructure.config.llm import LLMEndpointConfig, resolve_llm_endpoint_config

# settings.py → config → infrastructure → src → PROJECT_ROOT
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Default relative path (single tracked source at project root). Exact match is
# required for the installed-wheel packaged-copy fallback (Phase 1D v1.29).
_DEFAULT_VENDOR_CHAIN_RELATIVE = Path("config/vendor_chains.default.yaml")
# Wheel force-include lands a copy beside this module; not a second source file.
PACKAGED_VENDOR_CHAIN_PATH = Path(__file__).resolve().parent / "vendor_chains.default.yaml"
# Phase 1E E1: packaged A-share trading calendar (force-include; not bootstrap-wired).
PACKAGED_A_SHARE_TRADING_CALENDAR_PATH = (
    Path(__file__).resolve().parent / "a_share_trading_calendar.v1.json"
)
# Phase 1E E3: packaged CNINFO orgId map (force-include; not bootstrap-wired).
PACKAGED_CNINFO_ORG_MAP_PATH = Path(__file__).resolve().parent / "cninfo_org_map.v1.json"

_SECRET_FIELD_NAMES = frozenset(
    {
        "alpha_vantage_api_keys",
        "fred_api_key",
        "iwencai_api_key",
        "schwab_client_id",
        "schwab_client_secret",
        "schwab_account_hashes",
        "schwab_token_path",
        "provider_proxy_url",
        "apify_api_token",
        "dukascopy_api_key",
        "telegram_bot_token",
        "telegram_chat_id",
        "llm_api_key",
        "bailian_api_key",
        "deepseek_api_key",
        "retro_obsidian_journal_dir",
    }
)

# Phase 1E §22 shortest safe TTL per category (seconds).
_CACHE_TTL_BY_CATEGORY: dict[str, str] = {
    "MARKET_QUOTE": "cache_ttl_market_quote_seconds",
    "MARKET_SNAPSHOT": "cache_ttl_market_snapshot_seconds",
    "MARKET_OHLCV": "cache_ttl_market_ohlcv_seconds",
    "MARKET_STRUCTURE": "cache_ttl_market_structure_seconds",
    "MARKET_BREADTH": "cache_ttl_us_market_breadth_seconds",
    "INSTRUMENT_MASTER": "cache_ttl_instrument_master_seconds",
    "CAPITAL": "cache_ttl_capital_seconds",
    "LIMIT_UP": "cache_ttl_limit_up_seconds",
    "OPTIONS": "cache_ttl_options_seconds",
    "SENTIMENT": "cache_ttl_sentiment_seconds",
    "INTERACTIVE_QA": "cache_ttl_interactive_qa_seconds",
    "RESEARCH_REPORTS": "cache_ttl_research_reports_seconds",
    "CORPORATE_ACTIONS": "cache_ttl_corporate_actions_seconds",
    "NEWS": "cache_ttl_news_seconds",
    "ANNOUNCEMENTS": "cache_ttl_announcements_seconds",
    "FUNDAMENTALS": "cache_ttl_fundamentals_seconds",
    "FINANCIAL_STATEMENTS": "cache_ttl_financial_statements_seconds",
    # Phase 1G research categories.
    "FILINGS": "cache_ttl_filings_seconds",
    "INSIDER_ACTIVITY": "cache_ttl_insider_activity_seconds",
}

_MARKET_TIMEOUT_CATEGORIES = frozenset(
    {
        DataCategory.MARKET_QUOTE,
        DataCategory.MARKET_OHLCV,
        DataCategory.MARKET_SNAPSHOT,
        DataCategory.MARKET_STRUCTURE,
        DataCategory.MARKET_BREADTH,
    }
)

_REDACTED = "***REDACTED***"
_CREDENTIAL_URL_RE = re.compile(r"://[^/@:\s]+:[^/@\s]+@")


class AppSettings(BaseSettings):
    """Root configuration. Extra env keys are ignored for forward compatibility."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    app_name: str = Field(min_length=1)
    app_env: AppEnvironment
    log_level: LogLevel
    database_url: str = Field(min_length=1)
    mcp_server_name: str = Field(min_length=1)
    default_timezone: str = Field(min_length=1)
    provider_timeout_seconds: float = Field(gt=0)

    # Non-secret path to Vendor Chain YAML. Relative paths resolve against
    # PROJECT_ROOT; default relative may fall back to the packaged wheel copy.
    vendor_chain_path: Path = Field(default=_DEFAULT_VENDOR_CHAIN_RELATIVE)

    # Provider timeouts (Phase 1D D5b). Market categories use market override.
    provider_timeout_default_seconds: float = Field(default=30.0, gt=0)
    provider_timeout_market_seconds: float = Field(default=15.0, gt=0)
    provider_timeout_us_breadth_seconds: float = Field(default=30.0, gt=0)

    # Same-vendor retry (Phase 1D D5b).
    provider_retry_max_attempts: int = Field(default=2, ge=1)
    provider_retry_base_delay_seconds: float = Field(default=0.05, ge=0)
    provider_retry_max_delay_seconds: float = Field(default=1.0, ge=0)
    provider_rate_limit_max_wait_seconds: float = Field(
        default=5.0,
        ge=0,
        allow_inf_nan=False,
    )

    # Circuit breaker (Phase 1D D5b). Unwired until Router (D6+).
    circuit_failure_threshold: int = Field(default=5, ge=1)
    circuit_recovery_timeout_seconds: float = Field(default=60.0, gt=0)
    circuit_half_open_max_calls: int = Field(default=1, ge=1)
    enable_circuit_breaker: bool = True

    # Auth failure chain fallback (D6 readiness; unwired in D5b).
    auth_failure_fallback: bool = False

    # Provider cache TTL overrides (seconds). Phase 1D §12.5 + Phase 1E §22.
    cache_ttl_market_quote_seconds: int = Field(default=5, gt=0)
    cache_ttl_market_snapshot_seconds: int = Field(default=30, gt=0)
    cache_ttl_market_ohlcv_seconds: int = Field(default=300, gt=0)
    cache_ttl_market_structure_seconds: int = Field(default=5, gt=0)
    cache_ttl_us_market_breadth_seconds: int = Field(default=900, gt=0)
    cache_ttl_instrument_master_seconds: int = Field(default=86400, gt=0)
    cache_ttl_capital_seconds: int = Field(default=30, gt=0)
    cache_ttl_limit_up_seconds: int = Field(default=30, gt=0)
    cache_ttl_options_seconds: int = Field(default=15, gt=0)
    cache_ttl_sentiment_seconds: int = Field(default=30, gt=0)
    cache_ttl_interactive_qa_seconds: int = Field(default=300, gt=0)
    cache_ttl_research_reports_seconds: int = Field(default=3600, gt=0)
    cache_ttl_corporate_actions_seconds: int = Field(default=21600, gt=0)
    cache_ttl_news_seconds: int = Field(default=300, gt=0)
    cache_ttl_announcements_seconds: int = Field(default=300, gt=0)
    cache_ttl_fundamentals_seconds: int = Field(default=21600, gt=0)
    cache_ttl_financial_statements_seconds: int = Field(default=21600, gt=0)
    cache_ttl_filings_seconds: int = Field(default=3600, gt=0)
    cache_ttl_insider_activity_seconds: int = Field(default=3600, gt=0)
    cache_ttl_default_seconds: int = Field(default=300, gt=0)
    enable_provider_cache: bool = True

    # Stale guard (Phase 1D D7). Unwired until Router (D6b+).
    stale_guard_max_age_seconds: int = Field(default=86400, ge=0)
    stale_guard_respect_session: bool = True
    stale_guard_allow_closed_last_bar: bool = True

    # Freshness thresholds (Phase 1D D7). Unwired until Router (D6b+).
    freshness_max_fresh_seconds: int = Field(default=60, ge=0)
    freshness_max_delayed_seconds: int = Field(default=900, ge=0)

    # Phase 1E A-share vendor enablement / transport (safe defaults; no live wiring in E1).
    eastmoney_enabled: bool = True
    eastmoney_min_interval_seconds: float = Field(default=1.0, gt=0)
    eastmoney_jitter_seconds: float = Field(default=0.25, ge=0)
    tencent_enabled: bool = True
    cninfo_enabled: bool = True
    nahs_enabled: bool = True
    sina_enabled: bool = True
    ths_enabled: bool = True
    cls_enabled: bool = True
    iwencai_enabled: bool = False
    iwencai_api_key: str | None = None
    iwencai_base_url: str = Field(default="https://openapi.iwencai.com", min_length=1)
    http_max_response_bytes: int = Field(default=20_000_000, gt=0)
    a_share_current_window_seconds: int = Field(default=300, ge=0)
    a_share_max_fresh_seconds: int = Field(default=30, ge=0)
    a_share_max_delayed_seconds: int = Field(default=900, ge=0)

    # Phase 1F US market vendor enablement / freshness (safe defaults; no live wiring in F1).
    yfinance_enabled: bool = True
    alpha_vantage_enabled: bool = True
    us_current_window_seconds: int = Field(default=300, ge=0)
    us_max_fresh_seconds: int = Field(default=30, ge=0)
    us_max_delayed_seconds: int = Field(default=900, ge=0)
    moomoo_overnight_quote_enabled: bool = True

    # Phase 3A Dukascopy Jetta data (OTC metals / rolling CFDs). The current
    # dukascopy-node strategy is keyless; the old Trading Tools key is optional.
    dukascopy_enabled: bool = True
    dukascopy_api_key: str | None = None

    # Phase 1G US research / SEC (safe defaults; no live wiring in G1).
    # sec_user_agent is secret-free identification; blank normalizes to None.
    sec_edgar_enabled: bool = True
    sec_user_agent: str | None = None
    us_research_current_window_seconds: int = Field(default=21600, ge=0)

    # Phase 1H optional context providers.
    fred_enabled: bool = True
    moomoo_sentiment_enabled: bool = True
    moomoo_community_heat_enabled: bool = False
    moomoo_community_heat_limit: int = Field(default=20, ge=1, le=200)
    reddit_enabled: bool = True
    reddit_subreddits: str = "wallstreetbets,stocks,investing"
    reddit_apify_enabled: bool = False
    reddit_apify_actor_id: str = Field(default="harshmaur/reddit-scraper", min_length=1)
    reddit_apify_subreddits: str = (
        "stocks,investing,securityanalysis,valueinvesting,wallstreetbets,shortsqueeze"
    )
    reddit_apify_lookback_days_by_subreddit: str = (
        "stocks:7,investing:14,securityanalysis:30,valueinvesting:30,"
        "wallstreetbets:3,shortsqueeze:2"
    )
    reddit_apify_max_charge_usd: Decimal = Field(default=Decimal("0.20"), gt=0)
    apify_api_token: str | None = None
    # Keyless weekend references: Binance PAXG/USDC for XAUUSD and
    # Hyperliquid XYZ CL/USDC for the Dukascopy light-oil CFD.
    weekend_rwa_proxy_enabled: bool = True
    weekend_rwa_proxy_cache_ttl_seconds: int = Field(default=60, ge=10, le=3600)
    weekend_rwa_proxy_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ig_weekend_gold_enabled: bool = False
    ig_weekend_gold_actor_id: str = Field(default="apify/web-scraper", min_length=1)
    ig_weekend_gold_cache_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    ig_weekend_gold_max_charge_usd: Decimal = Field(default=Decimal("0.03"), gt=0)
    ig_weekend_gold_timeout_seconds: float = Field(default=120.0, ge=30, le=300)
    polymarket_enabled: bool = True
    reddit_user_agent: str = Field(default="TradingPartner/1.0", min_length=1)
    reddit_min_interval_seconds: float = Field(default=6.0, ge=0)
    reddit_cache_ttl_seconds: int = Field(default=3600, gt=0)
    reddit_cooldown_default_seconds: int = Field(default=900, gt=0)
    reddit_cooldown_max_seconds: int = Field(default=3600, gt=0)
    # General outbound proxy for CME/DCE/Dukascopy, weekend references,
    # Polymarket, and Telegram.
    provider_proxy_url: str | None = None

    # Phase 1I read-only account providers. Sources are additive; no unlock/order
    # credentials exist. Environment values use a JSON array.
    holdings_sources: tuple[Literal["SCHWAB", "MOOMOO", "MANUAL_CSV"], ...] = ()
    moomoo_host: str = "127.0.0.1"
    moomoo_port: int = Field(default=11111, ge=1, le=65535)
    moomoo_account_ids: str = ""
    moomoo_account_base_currency: Literal["USD", "HKD", "CNH", "JPY", "SGD"] = "USD"
    manual_holdings_csv_path: Path | None = None

    # Phase 2 Watchlist Hub. Exactly one upstream is active at runtime.
    watchlist_source: Literal["MOOMOO", "MANUAL_CSV"] = "MOOMOO"
    watchlist_default_group: str = Field(default="Favorites", min_length=1)
    manual_watchlist_csv_path: Path | None = None
    post_market_sync_delay_minutes: int = Field(default=10, ge=0, le=120)
    post_market_sync_lock_path: Path = Path("data/locks/post_market_sync.lock")
    telegram_agent_lock_path: Path = Path("data/locks/telegram_agent.lock")

    # Scheduled Schwab SGOV Shadow plan. Installing the dedicated launchd job is
    # the enablement gate; these values only define its calculation policy.
    sgov_shadow_hard_cash_floor: Decimal = Field(default=Decimal("2000"), ge=0)
    sgov_shadow_operational_buffer: Decimal = Field(default=Decimal("200"), ge=0)
    sgov_shadow_minimum_order_notional: Decimal = Field(default=Decimal("1000"), gt=0)

    # Deterministic outbound delivery. Telegram never receives commands and
    # cannot mutate research, portfolio, or trading state. Legacy Monitor-prefixed
    # environment names remain accepted during the generic-Outbox migration.
    notifications_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "notifications_enabled",
            "NOTIFICATIONS_ENABLED",
            "monitor_notifications_enabled",
            "MONITOR_NOTIFICATIONS_ENABLED",
        ),
    )
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_agent_user_id: str | None = None
    telegram_message_thread_id: int | None = Field(default=None, ge=1)
    notification_max_attempts: int = Field(
        default=5,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "notification_max_attempts",
            "NOTIFICATION_MAX_ATTEMPTS",
            "monitor_notification_max_attempts",
            "MONITOR_NOTIFICATION_MAX_ATTEMPTS",
        ),
    )
    notification_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        validation_alias=AliasChoices(
            "notification_ttl_hours",
            "NOTIFICATION_TTL_HOURS",
            "monitor_notification_event_ttl_hours",
            "MONITOR_NOTIFICATION_EVENT_TTL_HOURS",
        ),
    )
    # Optional server-side structured judgment for composite Monitors. These are
    # shared LLM defaults; the Monitor is currently the only runtime consumer.
    # Shared Agent runtime feature flags.  Agent code consumes only the generic
    # LLM_* fields below; legacy provider fields remain readable for one
    # compatibility cycle and are normalized at ``resolved_llm_config``.
    agent_enabled: bool = False
    telegram_agent_enabled: bool = False
    llm_api_style: Literal["chat_completions", "responses"] = "chat_completions"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_reasoning_mode: Literal["none", "effort", "thinking"] = "none"
    llm_native_web_search: Literal["disabled", "responses_web_search"] = "disabled"
    llm_native_web_extractor: Literal[
        "disabled", "responses_web_extractor"
    ] = "disabled"
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    llm_max_output_tokens: int = Field(default=8000, gt=0, le=100_000)

    monitor_judgment_enabled: bool = False
    llm_provider: Literal["bailian", "deepseek"] = "bailian"
    bailian_api_key: str | None = None
    bailian_base_url: str = Field(
        default="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("bailian_base_url", "BAILIAN_BASE_URL"),
    )
    bailian_model: str = Field(
        default="qwen3.8-max",
        validation_alias=AliasChoices("bailian_model", "BAILIAN_MODEL"),
    )
    bailian_web_search_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "bailian_web_search_enabled",
            "BAILIAN_WEB_SEARCH_ENABLED",
            "llm_web_search_enabled",
            "LLM_WEB_SEARCH_ENABLED",
        ),
    )
    bailian_web_extractor_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "bailian_web_extractor_enabled",
            "BAILIAN_WEB_EXTRACTOR_ENABLED",
        ),
    )
    deepseek_api_key: str | None = None
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices(
            "deepseek_base_url",
            "DEEPSEEK_BASE_URL",
            "monitor_judgment_base_url",
            "MONITOR_JUDGMENT_BASE_URL",
        ),
    )
    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices(
            "deepseek_model",
            "DEEPSEEK_MODEL",
            "monitor_judgment_model",
            "MONITOR_JUDGMENT_MODEL",
        ),
    )
    # ``max`` preserves the legacy Monitor default.  An explicitly blank
    # generic value normalizes to ``None`` so ``LLM_REASONING_MODE=none`` can
    # remain a complete provider-neutral configuration.
    llm_reasoning_effort: str | None = "max"
    llm_output_language: Literal["zh-CN"] = "zh-CN"
    monitor_judgment_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    monitor_judgment_max_output_tokens: int = Field(default=8000, ge=512, le=8000)
    monitor_judgment_reasoning_effort: str | None = None
    monitor_judgment_fallback_provider: Literal["bailian", "deepseek"] | None = None
    monitor_judgment_fallback_model: str | None = None
    monitor_judgment_fallback_reasoning_effort: str | None = None
    # Trade Retro always persists deterministic findings. This switch controls
    # only the optional Chinese narrative layer.
    trade_retro_llm_enabled: bool = True
    retro_obsidian_journal_dir: Path | None = None
    notification_batch_size: int = Field(
        default=20,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "notification_batch_size",
            "NOTIFICATION_BATCH_SIZE",
            "monitor_notification_batch_size",
            "MONITOR_NOTIFICATION_BATCH_SIZE",
        ),
    )

    # Schwab read-only accounts. schwab-py owns the rotating token file.
    schwab_client_id: str | None = None
    schwab_client_secret: str | None = None
    schwab_redirect_uri: str = Field(default="https://127.0.0.1:8182", min_length=1)
    schwab_token_path: Path = Path("data/secrets/schwab_tokens.json")
    schwab_account_hashes: str = ""

    # Optional provider secrets — not required for Phase 1A mock providers.
    # Environment value is a comma-separated string. Order defines failover priority.
    alpha_vantage_api_keys: Annotated[tuple[str, ...], NoDecode] = Field(default=(), max_length=8)
    fred_api_key: str | None = None

    @field_validator(
        "app_name",
        "database_url",
        "mcp_server_name",
        "default_timezone",
        "schwab_redirect_uri",
        mode="before",
    )
    @classmethod
    def _reject_blank_strings(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be empty or whitespace")
        return value

    @field_validator("vendor_chain_path", mode="before")
    @classmethod
    def _coerce_vendor_chain_path(cls, value: object) -> object:
        if value is None:
            return _DEFAULT_VENDOR_CHAIN_RELATIVE
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("must not be empty or whitespace")
            return Path(stripped)
        return value

    @field_validator(
        "fred_api_key",
        "iwencai_api_key",
        "sec_user_agent",
        "schwab_client_id",
        "schwab_client_secret",
        "provider_proxy_url",
        "apify_api_token",
        "dukascopy_api_key",
        "telegram_bot_token",
        "telegram_chat_id",
        "telegram_message_thread_id",
        "llm_api_key",
        "bailian_api_key",
        "deepseek_api_key",
        mode="before",
    )
    @classmethod
    def _empty_secret_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def _empty_generic_llm_value_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _normalize_llm_reasoning_effort(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator(
        "monitor_judgment_reasoning_effort",
        "monitor_judgment_fallback_provider",
        "monitor_judgment_fallback_model",
        "monitor_judgment_fallback_reasoning_effort",
        mode="before",
    )
    @classmethod
    def _normalize_monitor_judgment_fallback(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return (stripped.lower() or None)
        return value

    @field_validator("alpha_vantage_api_keys", mode="before")
    @classmethod
    def _normalize_alpha_vantage_api_keys(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            if not value.strip():
                return ()
            value = value.split(",")
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("alpha_vantage_api_keys entries must be non-empty strings")
            key = item.strip()
            if key not in normalized:
                normalized.append(key)
        return tuple(normalized)

    @field_validator("provider_proxy_url")
    @classmethod
    def _validate_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("proxy URL must be an HTTP(S) proxy URL")
        return value

    @field_validator(
        "manual_holdings_csv_path",
        "manual_watchlist_csv_path",
        "retro_obsidian_journal_dir",
        mode="before",
    )
    @classmethod
    def _empty_manual_path_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("watchlist_source", mode="before")
    @classmethod
    def _normalize_watchlist_source(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("holdings_sources")
    @classmethod
    def _holdings_sources_must_be_unique(
        cls,
        value: tuple[Literal["SCHWAB", "MOOMOO", "MANUAL_CSV"], ...],
    ) -> tuple[Literal["SCHWAB", "MOOMOO", "MANUAL_CSV"], ...]:
        if len(value) != len(set(value)):
            raise ValueError("holdings_sources entries must be unique")
        return value

    @field_validator("watchlist_default_group", mode="before")
    @classmethod
    def _normalize_watchlist_default_group(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("watchlist_default_group must not be blank")
        return value

    @field_validator("schwab_token_path", mode="before")
    @classmethod
    def _schwab_token_path_is_json(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("schwab_token_path must not be blank")
            value = Path(value)
        if isinstance(value, Path) and value.suffix.lower() != ".json":
            raise ValueError("schwab_token_path must be a JSON file")
        return value

    @field_validator("post_market_sync_lock_path", mode="before")
    @classmethod
    def _normalize_post_market_sync_lock_path(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("post_market_sync_lock_path must not be blank")
            return Path(value)
        if value is None:
            raise ValueError("post_market_sync_lock_path must not be blank")
        return value

    @field_validator("telegram_agent_lock_path", mode="before")
    @classmethod
    def _normalize_telegram_agent_lock_path(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("telegram_agent_lock_path must not be blank")
            return Path(value)
        if value is None:
            raise ValueError("telegram_agent_lock_path must not be blank")
        return value

    @field_validator("telegram_agent_user_id", mode="before")
    @classmethod
    def _normalize_telegram_agent_user_id(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip()
        if re.fullmatch(r"[0-9]+", normalized) is None:
            raise ValueError("telegram_agent_user_id must be numeric")
        return normalized

    @field_validator("moomoo_host")
    @classmethod
    def _moomoo_must_be_local(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("moomoo_host must be local")
        return value

    @field_validator("reddit_subreddits", "reddit_apify_subreddits", mode="before")
    @classmethod
    def _normalize_reddit_subreddits(cls, value: object) -> str:
        if value is None:
            return "wallstreetbets,stocks,investing"
        if not isinstance(value, str):
            raise ValueError("reddit_subreddits must be a comma-separated string")
        parts: list[str] = []
        for raw in value.split(","):
            item = raw.strip().lower()
            if not item:
                raise ValueError("reddit_subreddits entries must be non-empty")
            if re.fullmatch(r"[a-z0-9_]+", item) is None:
                raise ValueError("reddit_subreddits entries must be lowercase alnum or underscore")
            if item in parts:
                raise ValueError("reddit_subreddits entries must be unique")
            parts.append(item)
        if len(parts) > 10:
            raise ValueError("reddit_subreddits may contain at most 10 entries")
        if not parts:
            raise ValueError("reddit_subreddits must not be empty")
        return ",".join(parts)

    @field_validator("reddit_apify_lookback_days_by_subreddit", mode="before")
    @classmethod
    def _normalize_reddit_apify_lookbacks(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("reddit Apify lookbacks must be a comma-separated mapping")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_entry in value.split(","):
            name, separator, raw_days = raw_entry.strip().partition(":")
            name = name.strip().lower()
            raw_days = raw_days.strip()
            if not separator or not name or not raw_days.isdigit():
                raise ValueError("reddit Apify lookbacks must use subreddit:days entries")
            days = int(raw_days)
            if re.fullmatch(r"[a-z0-9_]+", name) is None or not 1 <= days <= 365:
                raise ValueError("invalid Reddit Apify subreddit lookback")
            if name in seen:
                raise ValueError("Reddit Apify lookback subreddits must be unique")
            seen.add(name)
            normalized.append(f"{name}:{days}")
        if not normalized:
            raise ValueError("Reddit Apify lookbacks must not be empty")
        return ",".join(normalized)

    @model_validator(mode="after")
    def _reddit_apify_lookbacks_cover_subreddits(self) -> Self:
        configured = set(self.reddit_apify_subreddits.split(","))
        mapped = {
            entry.partition(":")[0]
            for entry in self.reddit_apify_lookback_days_by_subreddit.split(",")
        }
        if mapped != configured:
            raise ValueError("Reddit Apify lookbacks must exactly cover configured subreddits")
        return self

    @property
    def reddit_apify_lookback_map(self) -> dict[str, int]:
        return {
            name: int(days)
            for entry in self.reddit_apify_lookback_days_by_subreddit.split(",")
            for name, _, days in (entry.partition(":"),)
        }

    @model_validator(mode="after")
    def _validate_reddit_cooldown_bounds(self) -> Self:
        if self.reddit_cooldown_max_seconds < self.reddit_cooldown_default_seconds:
            raise ValueError(
                "reddit_cooldown_max_seconds must be >= reddit_cooldown_default_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_telegram_notification_configuration(self) -> Self:
        if not self.notifications_enabled:
            return self
        if self.telegram_bot_token is None or self.telegram_chat_id is None:
            raise ValueError(
                "Telegram bot token and chat id are required when notifications are enabled"
            )
        if re.fullmatch(r"-?[0-9]+|@[A-Za-z0-9_]{5,}", self.telegram_chat_id) is None:
            raise ValueError("telegram_chat_id must be a numeric id or @channel username")
        return self

    @model_validator(mode="after")
    def _validate_monitor_judgment_configuration(self) -> Self:
        if self.monitor_judgment_enabled:
            config = self.resolved_llm_config
            if config is None:
                legacy_key = (
                    "BAILIAN_API_KEY" if self.llm_provider == "bailian" else "DEEPSEEK_API_KEY"
                )
                raise ValueError(
                    f"{legacy_key} or a complete generic LLM endpoint is required "
                    "when Monitor judgment is enabled"
                )
            if (
                not self._generic_llm_explicit
                and self.llm_provider == "deepseek"
                and self.llm_reasoning_effort == "xhigh"
            ):
                raise ValueError("DeepSeek Monitor judgment supports high or max effort")
            try:
                fallback = self.resolved_monitor_judgment_fallback_config
            except ConfigurationError as exc:
                raise ValueError(exc.message) from exc
            if fallback is not None and (
                fallback.base_url == config.base_url and fallback.model == config.model
            ):
                raise ValueError(
                    "Monitor judgment fallback provider/model must differ from the primary"
                )
        if not self.bailian_base_url.startswith("https://"):
            raise ValueError("Bailian base URL must use https")
        if not self.deepseek_base_url.startswith("https://"):
            raise ValueError("DeepSeek base URL must use https")
        return self

    @model_validator(mode="after")
    def _validate_monitor_judgment_fallback_pair(self) -> Self:
        provider_set = self.monitor_judgment_fallback_provider is not None
        model_set = self.monitor_judgment_fallback_model is not None
        if provider_set != model_set:
            raise ValueError(
                "MONITOR_JUDGMENT_FALLBACK_PROVIDER and "
                "MONITOR_JUDGMENT_FALLBACK_MODEL must be configured together"
            )
        return self

    @property
    def _generic_llm_explicit(self) -> bool:
        """Whether any new generic endpoint field was explicitly supplied."""

        # Empty placeholders in the tracked .env.example must not disable the
        # one-cycle legacy fallback.  A non-default capability/limit is still
        # explicit even when the endpoint fields are incomplete, and therefore
        # fails closed instead of borrowing a legacy key.
        if any(
            value is not None for value in (self.llm_base_url, self.llm_api_key, self.llm_model)
        ):
            return True
        return any(
            (field in self.model_fields_set and value != default)
            for field, value, default in (
                ("llm_api_style", self.llm_api_style, "chat_completions"),
                ("llm_reasoning_mode", self.llm_reasoning_mode, "none"),
                ("llm_native_web_search", self.llm_native_web_search, "disabled"),
                ("llm_native_web_extractor", self.llm_native_web_extractor, "disabled"),
                ("llm_timeout_seconds", self.llm_timeout_seconds, 120.0),
                ("llm_max_output_tokens", self.llm_max_output_tokens, 8000),
            )
        )

    @property
    def resolved_llm_config(self) -> LLMEndpointConfig | None:
        """Return the one endpoint shape shared by Agent, Monitor and Retro.

        With no new generic fields and no legacy key configured, an endpoint is
        simply unavailable (the normal default for a disabled Agent).  A
        partially supplied generic endpoint raises a typed configuration error
        rather than borrowing fields from Bailian/DeepSeek.
        """

        if (
            not self._generic_llm_explicit
            and self.bailian_api_key is None
            and self.deepseek_api_key is None
        ):
            return None
        return resolve_llm_endpoint_config(
            generic_explicit=self._generic_llm_explicit,
            api_style=self.llm_api_style,
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
            model=self.llm_model,
            reasoning_mode=self.llm_reasoning_mode,
            reasoning_effort=self.llm_reasoning_effort,
            native_web_search=self.llm_native_web_search,
            native_web_extractor=self.llm_native_web_extractor,
            timeout_seconds=self.llm_timeout_seconds,
            max_output_tokens=self.llm_max_output_tokens,
            legacy_provider=self.llm_provider,
            bailian_api_key=self.bailian_api_key,
            bailian_base_url=self.bailian_base_url,
            bailian_model=self.bailian_model,
            bailian_web_search_enabled=self.bailian_web_search_enabled,
            bailian_web_extractor_enabled=self.bailian_web_extractor_enabled,
            deepseek_api_key=self.deepseek_api_key,
            deepseek_base_url=self.deepseek_base_url,
            deepseek_model=self.deepseek_model,
        )

    @property
    def resolved_monitor_judgment_fallback_config(self) -> LLMEndpointConfig | None:
        """Resolve the optional Monitor-only fallback endpoint.

        Provider credentials and endpoint stay in configuration. Durable
        judgments record only which provider/model actually produced that run.
        """

        provider = self.monitor_judgment_fallback_provider
        model = self.monitor_judgment_fallback_model
        if provider is None or model is None:
            return None

        primary = self.resolved_llm_config
        if provider == "bailian":
            if primary is not None and primary.api_style == "responses":
                base_url = primary.base_url
                api_key = primary.api_key
                timeout_seconds = primary.timeout_seconds
                max_output_tokens = primary.max_output_tokens
            else:
                if self.bailian_api_key is None:
                    raise ConfigurationError(
                        "BAILIAN_API_KEY is required for the Monitor judgment fallback"
                    )
                base_url = self.bailian_base_url
                api_key = self.bailian_api_key
                timeout_seconds = self.monitor_judgment_timeout_seconds
                max_output_tokens = self.monitor_judgment_max_output_tokens
            return LLMEndpointConfig(
                api_style="responses",
                base_url=base_url,
                api_key=api_key,
                model=model,
                reasoning_mode="effort",
                reasoning_effort=(
                    self.monitor_judgment_fallback_reasoning_effort
                    or self.monitor_judgment_reasoning_effort
                    or self.llm_reasoning_effort
                ),
                native_web_search="disabled",
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )

        if primary is not None and primary.api_style == "chat_completions":
            base_url = primary.base_url
            api_key = primary.api_key
            timeout_seconds = primary.timeout_seconds
            max_output_tokens = primary.max_output_tokens
        else:
            if self.deepseek_api_key is None:
                raise ConfigurationError(
                    "DEEPSEEK_API_KEY is required for the Monitor judgment fallback"
                )
            base_url = self.deepseek_base_url
            api_key = self.deepseek_api_key
            timeout_seconds = self.monitor_judgment_timeout_seconds
            max_output_tokens = self.monitor_judgment_max_output_tokens
        return LLMEndpointConfig(
            api_style="chat_completions",
            base_url=base_url,
            api_key=api_key,
            model=model,
            reasoning_mode="thinking",
            reasoning_effort=(
                self.monitor_judgment_fallback_reasoning_effort
                or self.monitor_judgment_reasoning_effort
                or self.llm_reasoning_effort
            ),
            native_web_search="disabled",
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )

    @property
    def resolved_llm_endpoint_config(self) -> LLMEndpointConfig | None:
        """Long-form alias for callers that prefer the endpoint terminology."""

        return self.resolved_llm_config

    @property
    def llm_endpoint_config(self) -> LLMEndpointConfig | None:
        """Compatibility alias used by composition callers."""

        return self.resolved_llm_config

    @property
    def resolved_config(self) -> LLMEndpointConfig | None:
        """Short alias retained for early Agent integration callers."""

        return self.resolved_llm_config

    def resolved_llm_config_redacted(self) -> dict[str, object] | None:
        config = self.resolved_llm_config
        return None if config is None else config.redacted_dict()

    @model_validator(mode="after")
    def _validate_agent_llm_configuration(self) -> Self:
        if self.agent_enabled or self.telegram_agent_enabled:
            try:
                _ = self.resolved_llm_config
            except ConfigurationError as exc:
                # A partially supplied generic endpoint must never borrow the
                # missing values from a legacy provider configuration.
                raise ValueError(exc.message) from exc
        return self

    @field_validator("iwencai_base_url", mode="before")
    @classmethod
    def _normalize_iwencai_base_url(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("must not be empty or whitespace")
            return stripped.rstrip("/")
        return value

    @field_validator(
        "a_share_current_window_seconds",
        "a_share_max_fresh_seconds",
        "a_share_max_delayed_seconds",
        "us_current_window_seconds",
        "us_max_fresh_seconds",
        "us_max_delayed_seconds",
        "us_research_current_window_seconds",
        mode="before",
    )
    @classmethod
    def _require_a_share_nonnegative_int(cls, value: object) -> object:
        """Reject Python coercions while retaining decimal-string env parsing."""
        if isinstance(value, (bool, float)):
            raise ValueError("must be a nonnegative exact int")
        if isinstance(value, int):
            if value < 0:
                raise ValueError("must be a nonnegative exact int")
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is not None:
            return value
        raise ValueError("must be a nonnegative exact int or pure decimal string")

    @model_validator(mode="after")
    def _normalize_sqlite_url_and_paths(self) -> Self:
        url = self.database_url
        if url.startswith("sqlite:///"):
            path_part = url.removeprefix("sqlite:///")
            # Absolute path: sqlite:////abs/path → path_part starts with /
            if not path_part.startswith("/"):
                absolute = (PROJECT_ROOT / path_part).resolve()
                object.__setattr__(self, "database_url", f"sqlite:///{absolute}")

        chain_path = self.vendor_chain_path
        if not chain_path.is_absolute():
            project_candidate = (PROJECT_ROOT / chain_path).resolve()
            if project_candidate.is_file():
                resolved = project_candidate
            elif (
                chain_path == _DEFAULT_VENDOR_CHAIN_RELATIVE
                and PACKAGED_VENDOR_CHAIN_PATH.is_file()
            ):
                # Installed wheel: project-root config is absent; use packaged copy.
                resolved = PACKAGED_VENDOR_CHAIN_PATH.resolve()
            else:
                # Custom relative (or missing default with no package data): keep
                # project-root candidate so the loader raises a clear config error.
                resolved = project_candidate
            object.__setattr__(self, "vendor_chain_path", resolved)

        token_path = self.schwab_token_path
        if not token_path.is_absolute():
            token_path = (PROJECT_ROOT / token_path).resolve()
        secret_root = (PROJECT_ROOT / "data" / "secrets").resolve()
        if not token_path.is_relative_to(secret_root):
            raise ValueError("schwab_token_path must be under project data/secrets")
        object.__setattr__(self, "schwab_token_path", token_path)

        manual_watchlist_path = self.manual_watchlist_csv_path
        if manual_watchlist_path is not None and not manual_watchlist_path.is_absolute():
            manual_watchlist_path = (PROJECT_ROOT / manual_watchlist_path).resolve()
        object.__setattr__(self, "manual_watchlist_csv_path", manual_watchlist_path)

        retro_root = self.retro_obsidian_journal_dir
        if retro_root is not None and not retro_root.is_absolute():
            retro_root = (PROJECT_ROOT / retro_root).resolve()
        object.__setattr__(self, "retro_obsidian_journal_dir", retro_root)

        lock_path = self.post_market_sync_lock_path
        if not lock_path.is_absolute():
            lock_path = (PROJECT_ROOT / lock_path).resolve()
        data_root = (PROJECT_ROOT / "data").resolve()
        if not lock_path.is_relative_to(data_root):
            raise ValueError("post_market_sync_lock_path must be under project data")
        object.__setattr__(self, "post_market_sync_lock_path", lock_path)

        telegram_agent_lock_path = self.telegram_agent_lock_path
        if not telegram_agent_lock_path.is_absolute():
            telegram_agent_lock_path = (PROJECT_ROOT / telegram_agent_lock_path).resolve()
        if not telegram_agent_lock_path.is_relative_to(data_root):
            raise ValueError("telegram_agent_lock_path must be under project data")
        object.__setattr__(self, "telegram_agent_lock_path", telegram_agent_lock_path)
        return self

    @model_validator(mode="after")
    def _retry_max_delay_ge_base(self) -> Self:
        if self.provider_retry_max_delay_seconds < self.provider_retry_base_delay_seconds:
            raise ValueError(
                "provider_retry_max_delay_seconds must be >= provider_retry_base_delay_seconds"
            )
        return self

    @model_validator(mode="after")
    def _freshness_delayed_ge_fresh(self) -> Self:
        if self.freshness_max_delayed_seconds < self.freshness_max_fresh_seconds:
            raise ValueError("freshness_max_delayed_seconds must be >= freshness_max_fresh_seconds")
        return self

    @model_validator(mode="after")
    def _a_share_delayed_ge_fresh(self) -> Self:
        if self.a_share_max_delayed_seconds < self.a_share_max_fresh_seconds:
            raise ValueError("a_share_max_delayed_seconds must be >= a_share_max_fresh_seconds")
        return self

    @model_validator(mode="after")
    def _us_delayed_ge_fresh(self) -> Self:
        if self.us_max_delayed_seconds < self.us_max_fresh_seconds:
            raise ValueError("us_max_delayed_seconds must be >= us_max_fresh_seconds")
        return self

    @classmethod
    def load(cls, env_file: Path | None = None) -> AppSettings:
        """Load settings for source or installed layouts.

        * **Source layout** — ``PROJECT_ROOT/pyproject.toml`` exists: default to
          the project-root ``.env`` when present; otherwise process environment
          only (``_env_file=None``).
        * **Installed layout** — source marker absent but
          ``PACKAGED_VENDOR_CHAIN_PATH`` is a file: process environment only;
          never implicit cwd / home / parent ``.env``.
        * **Invalid layout** — neither: raise the safe ``PROJECT_ROOT`` guard
          ``ConfigurationError``.

        Explicit ``env_file`` is allowed in both valid layouts and must exist.
        """
        source_layout = (PROJECT_ROOT / "pyproject.toml").is_file()
        installed_layout = not source_layout and PACKAGED_VENDOR_CHAIN_PATH.is_file()
        if not source_layout and not installed_layout:
            raise ConfigurationError(
                "PROJECT_ROOT guard failed: pyproject.toml not found at expected root",
                details={"project_root": str(PROJECT_ROOT)},
            )

        try:
            if env_file is not None:
                path = Path(env_file)
                if not path.is_file():
                    raise ConfigurationError(
                        f"env_file does not exist: {path}",
                        details={"env_file": str(path)},
                    )
                # An explicitly selected runtime file is the process contract.
                # Loading it as init data keeps ambient variables inherited from
                # desktop hosts or CI from silently redirecting its database or
                # Provider configuration.
                values = DotEnvSettingsSource(
                    cls,
                    env_file=path,
                    env_file_encoding="utf-8",
                )()
                return cls(_env_file=None, **values)  # type: ignore[call-arg]
            if source_layout:
                default_env = PROJECT_ROOT / ".env"
                if default_env.is_file():
                    return cls(_env_file=default_env)  # type: ignore[call-arg]
                # Fall back to process environment only (tests / CI).
                return cls(_env_file=None)  # type: ignore[call-arg]
            # Installed layout: process environment only — no implicit .env scan.
            return cls(_env_file=None)  # type: ignore[call-arg]
        except ConfigurationError:
            raise
        except Exception as exc:  # pydantic ValidationError etc.
            raise ConfigurationError(
                f"Failed to load AppSettings: {exc}",
                details={"error_type": type(exc).__name__},
            ) from exc

    def cache_ttl_for(self, category: DataCategory) -> int:
        """Return positive cache TTL seconds for ``category`` (1D §12.5 + 1E §22)."""
        attr = _CACHE_TTL_BY_CATEGORY.get(category.name)
        if attr is not None:
            return int(getattr(self, attr))
        return self.cache_ttl_default_seconds

    def timeout_for(self, category: DataCategory) -> float:
        """Return provider timeout seconds for ``category`` (design §12.1 / 1E).

        Breadth uses its own bounded-batch override; other market categories use
        the market override. Remaining categories use the default. The global
        timeout remains a compatibility setting for existing deployments.
        """
        if category is DataCategory.MARKET_BREADTH:
            return self.provider_timeout_us_breadth_seconds
        if category in _MARKET_TIMEOUT_CATEGORIES:
            return self.provider_timeout_market_seconds
        return self.provider_timeout_default_seconds

    def redacted_dict(self) -> dict[str, object]:
        """Return a plain dict safe for logging and debugging."""
        raw = self.model_dump()
        result: dict[str, object] = {}
        for key, value in raw.items():
            if key in _SECRET_FIELD_NAMES and value not in (None, "", (), []):
                result[key] = _REDACTED
            elif key in {
                "database_url",
                "llm_base_url",
                "bailian_base_url",
                "deepseek_base_url",
            } and isinstance(value, str):
                result[key] = _REDACTED if _CREDENTIAL_URL_RE.search(value) else value
            elif isinstance(value, Path):
                # Non-secret paths (e.g. vendor_chain_path) appear unredacted.
                result[key] = str(value)
            else:
                # Enums → value for stable wire-like representation
                if hasattr(value, "value") and not isinstance(value, str | int | float | bool):
                    result[key] = value.value
                else:
                    result[key] = value
        return result

    def __repr__(self) -> str:
        return f"AppSettings({self.redacted_dict()!r})"

    def __str__(self) -> str:
        return repr(self)

    def model_dump_redacted(self) -> dict[str, Any]:
        return self.redacted_dict()
