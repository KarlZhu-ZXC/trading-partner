"""Phase 1E E1: AppSettings A-share fields, cache_ttl_for, timeout_for, redaction."""

from __future__ import annotations

from pathlib import Path

from domain.common.enums import AppEnvironment, DataCategory, LogLevel
from infrastructure.config.settings import AppSettings


def _base_settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": "sqlite:////tmp/tp-e1.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return AppSettings(_env_file=None, **base)  # type: ignore[call-arg]


def test_phase1e_settings_defaults() -> None:
    s = _base_settings()
    assert s.eastmoney_enabled is True
    assert s.eastmoney_min_interval_seconds == 1.0
    assert s.eastmoney_jitter_seconds == 0.25
    assert s.tencent_enabled is True
    assert s.cninfo_enabled is True
    assert s.sina_enabled is True
    assert s.ths_enabled is True
    assert s.cls_enabled is True
    assert s.iwencai_enabled is False
    assert s.iwencai_api_key is None
    assert s.iwencai_base_url == "https://openapi.iwencai.com"
    assert s.http_max_response_bytes == 20_000_000
    assert s.a_share_current_window_seconds == 300
    assert s.a_share_max_fresh_seconds == 30
    assert s.a_share_max_delayed_seconds == 900


def test_cache_ttl_for_phase1e_categories() -> None:
    s = _base_settings()
    assert s.cache_ttl_for(DataCategory.MARKET_STRUCTURE) == 5
    assert s.cache_ttl_for(DataCategory.CAPITAL) == 30
    assert s.cache_ttl_for(DataCategory.LIMIT_UP) == 30
    assert s.cache_ttl_for(DataCategory.OPTIONS) == 15
    assert s.cache_ttl_for(DataCategory.SENTIMENT) == 30
    assert s.cache_ttl_for(DataCategory.INTERACTIVE_QA) == 300
    assert s.cache_ttl_for(DataCategory.RESEARCH_REPORTS) == 3600
    assert s.cache_ttl_for(DataCategory.CORPORATE_ACTIONS) == 21600
    assert s.cache_ttl_for(DataCategory.NEWS) == 300
    assert s.cache_ttl_for(DataCategory.ANNOUNCEMENTS) == 300
    assert s.cache_ttl_for(DataCategory.FUNDAMENTALS) == 21600
    assert s.cache_ttl_for(DataCategory.FINANCIAL_STATEMENTS) == 21600
    # Pre-1E categories retained.
    assert s.cache_ttl_for(DataCategory.MARKET_QUOTE) == 5
    assert s.cache_ttl_for(DataCategory.MARKET_OHLCV) == 300


def test_timeout_for_includes_market_structure() -> None:
    s = _base_settings(
        provider_timeout_default_seconds=30.0,
        provider_timeout_market_seconds=15.0,
    )
    assert s.timeout_for(DataCategory.MARKET_STRUCTURE) == 15.0
    assert s.timeout_for(DataCategory.MARKET_QUOTE) == 15.0
    assert s.timeout_for(DataCategory.CAPITAL) == 30.0
    assert s.timeout_for(DataCategory.RESEARCH_REPORTS) == 30.0


def test_iwencai_api_key_redacted() -> None:
    s = _base_settings(iwencai_api_key="REAL_IWENCAI_SECRET")
    redacted = s.redacted_dict()
    assert redacted["iwencai_api_key"] == "***REDACTED***"
    assert "REAL_IWENCAI_SECRET" not in repr(s)
    assert "REAL_IWENCAI_SECRET" not in str(s)
    assert redacted["eastmoney_enabled"] is True
    assert redacted["iwencai_enabled"] is False


def test_env_example_contains_phase1e_keys() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    required = [
        "EASTMONEY_ENABLED=true",
        "EASTMONEY_MIN_INTERVAL_SECONDS=1.0",
        "EASTMONEY_JITTER_SECONDS=0.25",
        "TENCENT_ENABLED=true",
        "CNINFO_ENABLED=true",
        "SINA_ENABLED=true",
        "THS_ENABLED=true",
        "CLS_ENABLED=true",
        "IWENCAI_ENABLED=false",
        "IWENCAI_API_KEY=",
        "IWENCAI_BASE_URL=https://openapi.iwencai.com",
        "HTTP_MAX_RESPONSE_BYTES=20000000",
        "A_SHARE_CURRENT_WINDOW_SECONDS=300",
        "A_SHARE_MAX_FRESH_SECONDS=30",
        "A_SHARE_MAX_DELAYED_SECONDS=900",
        "CACHE_TTL_MARKET_STRUCTURE_SECONDS=5",
        "CACHE_TTL_CAPITAL_SECONDS=30",
        "CACHE_TTL_OPTIONS_SECONDS=15",
    ]
    for key in required:
        assert key in text, f"missing .env.example key line: {key}"
