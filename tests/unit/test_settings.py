"""AppSettings load, root guard, and redaction tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from domain.common.enums import AppEnvironment, DataCategory, LogLevel
from domain.common.errors import ConfigurationError
from infrastructure.config import settings as settings_module
from infrastructure.config.settings import AppSettings


def test_construct_without_env_file(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.DEBUG,
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        mcp_server_name="tp",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
    )
    assert settings.app_env is AppEnvironment.TEST
    assert settings.holdings_sources == ()


def test_holdings_sources_are_multi_select_and_unique() -> None:
    settings = _base_settings(holdings_sources=("SCHWAB", "MOOMOO"))
    assert settings.holdings_sources == ("SCHWAB", "MOOMOO")
    with pytest.raises(ValidationError):
        _base_settings(holdings_sources=("SCHWAB", "SCHWAB"))
    with pytest.raises(ValidationError):
        _base_settings(holdings_sources=("IBKR",))  # type: ignore[arg-type]


def test_phase2_watchlist_defaults() -> None:
    settings = _base_settings()
    assert settings.watchlist_source == "MOOMOO"
    assert settings.watchlist_default_group == "Favorites"
    assert settings.manual_watchlist_csv_path is None
    assert settings.post_market_sync_delay_minutes == 10
    assert settings.sgov_shadow_hard_cash_floor == Decimal("2000")
    assert settings.sgov_shadow_operational_buffer == Decimal("200")
    assert settings.sgov_shadow_minimum_order_notional == Decimal("1000")
    assert (
        settings.post_market_sync_lock_path
        == (settings_module.PROJECT_ROOT / "data/locks/post_market_sync.lock").resolve()
    )
    assert settings.post_market_sync_lock_path.is_relative_to(
        (settings_module.PROJECT_ROOT / "data").resolve()
    )
    assert settings.telegram_agent_lock_path == (
        settings_module.PROJECT_ROOT / "data/locks/telegram_agent.lock"
    ).resolve()


def test_phase2_watchlist_settings_normalize_and_resolve_path() -> None:
    settings = _base_settings(
        watchlist_source=" manual_csv ",  # type: ignore[arg-type]
        watchlist_default_group=" 手工观察 ",
        manual_watchlist_csv_path="data/watchlist.v1.csv",
        post_market_sync_delay_minutes=25,
        post_market_sync_lock_path=" data/locks/custom_post_market.lock ",
    )
    assert settings.watchlist_source == "MANUAL_CSV"
    assert settings.watchlist_default_group == "手工观察"
    assert (
        settings.manual_watchlist_csv_path
        == (settings_module.PROJECT_ROOT / "data/watchlist.v1.csv").resolve()
    )
    assert settings.post_market_sync_delay_minutes == 25
    assert (
        settings.post_market_sync_lock_path
        == (settings_module.PROJECT_ROOT / "data/locks/custom_post_market.lock").resolve()
    )


def test_phase2_watchlist_source_and_default_group_validation() -> None:
    for source in ("ALL", "SCHWAB", "", "MOOMOO,MANUAL_CSV"):
        with pytest.raises(ValidationError):
            _base_settings(watchlist_source=source)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _base_settings(watchlist_default_group="   ")


def test_post_market_sync_delay_minutes_validation_and_bounds() -> None:
    for delay in (-1, 121):
        with pytest.raises(ValidationError):
            _base_settings(post_market_sync_delay_minutes=delay)
    for delay in (0, 10, 120):
        settings = _base_settings(post_market_sync_delay_minutes=delay)
        assert settings.post_market_sync_delay_minutes == delay


def test_post_market_sync_lock_path_rejects_outside_data_and_blank() -> None:
    with pytest.raises(ValidationError, match="post_market_sync_lock_path"):
        _base_settings(post_market_sync_lock_path="/tmp/outside.lock")
    with pytest.raises(ValidationError, match="post_market_sync_lock_path"):
        _base_settings(post_market_sync_lock_path="   ")
    with pytest.raises(ValidationError, match="post_market_sync_lock_path"):
        _base_settings(post_market_sync_lock_path="data/../README.md")


def test_telegram_agent_lock_path_rejects_outside_data_and_blank() -> None:
    with pytest.raises(ValidationError, match="telegram_agent_lock_path"):
        _base_settings(telegram_agent_lock_path="/tmp/telegram-agent.lock")
    with pytest.raises(ValidationError, match="telegram_agent_lock_path"):
        _base_settings(telegram_agent_lock_path="   ")
    with pytest.raises(ValidationError, match="telegram_agent_lock_path"):
        _base_settings(telegram_agent_lock_path="data/../README.md")


def test_telegram_agent_user_id_is_optional_numeric_allowlist() -> None:
    assert _base_settings(telegram_agent_user_id=" 42 ").telegram_agent_user_id == "42"
    assert _base_settings(telegram_agent_user_id=None).telegram_agent_user_id is None
    with pytest.raises(ValidationError, match="telegram_agent_user_id"):
        _base_settings(telegram_agent_user_id="group-member")


def test_telegram_notifications_are_optional_but_complete_when_enabled() -> None:
    disabled = _base_settings()
    assert disabled.notifications_enabled is False
    assert disabled.telegram_bot_token is None
    assert disabled.telegram_chat_id is None

    enabled = _base_settings(
        notifications_enabled=True,
        telegram_bot_token="123456:example_bot_token",
        telegram_chat_id="-1001234567890",
        telegram_message_thread_id=42,
    )
    assert enabled.telegram_chat_id == "-1001234567890"
    assert enabled.telegram_message_thread_id == 42

    with pytest.raises(ValidationError, match="token and chat id"):
        _base_settings(notifications_enabled=True)
    with pytest.raises(ValidationError, match="numeric id"):
        _base_settings(
            notifications_enabled=True,
            telegram_bot_token="123456:example_bot_token",
            telegram_chat_id="not a chat",
        )


def test_legacy_monitor_notification_setting_names_remain_readable() -> None:
    settings = _base_settings(
        MONITOR_NOTIFICATIONS_ENABLED=True,
        MONITOR_NOTIFICATION_MAX_ATTEMPTS=4,
        MONITOR_NOTIFICATION_EVENT_TTL_HOURS=12,
        MONITOR_NOTIFICATION_BATCH_SIZE=9,
        telegram_bot_token="123456:example_bot_token",
        telegram_chat_id="-1001234567890",
    )

    assert settings.notifications_enabled is True
    assert settings.notification_max_attempts == 4
    assert settings.notification_ttl_hours == 12
    assert settings.notification_batch_size == 9


def test_monitor_judgment_uses_bailian_qwen_defaults_and_requires_key() -> None:
    defaults = _base_settings()
    assert defaults.llm_provider == "bailian"
    assert defaults.bailian_model == "qwen3.8-max"
    assert defaults.llm_reasoning_effort == "max"
    assert defaults.bailian_web_search_enabled is True
    assert defaults.llm_output_language == "zh-CN"
    assert defaults.deepseek_model == "deepseek-v4-flash"

    with pytest.raises(ValidationError, match="BAILIAN_API_KEY"):
        _base_settings(monitor_judgment_enabled=True)
    enabled = _base_settings(
        monitor_judgment_enabled=True,
        bailian_api_key="test-bailian-secret",
    )
    assert enabled.bailian_base_url.startswith("https://token-plan.")

    with pytest.raises(ValidationError, match="DEEPSEEK_API_KEY"):
        _base_settings(monitor_judgment_enabled=True, llm_provider="deepseek")
    deepseek = _base_settings(
        monitor_judgment_enabled=True,
        llm_provider="deepseek",
        deepseek_api_key="test-deepseek-secret",
    )
    assert deepseek.deepseek_base_url == "https://api.deepseek.com"
    assert deepseek.deepseek_model == "deepseek-v4-flash"


def test_monitor_judgment_fallback_is_explicit_and_reuses_bailian_endpoint() -> None:
    disabled = _base_settings(
        monitor_judgment_enabled=True,
        bailian_api_key="test-bailian-secret",
        monitor_judgment_fallback_provider="",
        monitor_judgment_fallback_model="   ",
    )
    assert disabled.resolved_monitor_judgment_fallback_config is None

    settings = _base_settings(
        monitor_judgment_enabled=True,
        bailian_api_key="test-bailian-secret",
        monitor_judgment_fallback_provider="bailian",
        monitor_judgment_fallback_model="deepseek-v4-flash-0731",
        monitor_judgment_reasoning_effort="high",
        monitor_judgment_fallback_reasoning_effort="max",
    )
    fallback = settings.resolved_monitor_judgment_fallback_config
    assert fallback is not None
    assert fallback.api_style == "responses"
    assert fallback.base_url == settings.bailian_base_url
    assert fallback.model == "deepseek-v4-flash-0731"
    assert fallback.reasoning_effort == "max"
    assert fallback.native_web_search == "disabled"


def test_monitor_judgment_fallback_requires_a_complete_distinct_pair() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        _base_settings(monitor_judgment_fallback_provider="bailian")
    with pytest.raises(ValidationError, match="must differ from the primary"):
        _base_settings(
            monitor_judgment_enabled=True,
            bailian_api_key="test-bailian-secret",
            monitor_judgment_fallback_provider="bailian",
            monitor_judgment_fallback_model="qwen3.8-max",
        )


def test_agent_generic_llm_config_wins_without_legacy_field_mixing() -> None:
    settings = _base_settings(
        agent_enabled=True,
        llm_api_style="responses",
        llm_base_url="https://generic.example/v1",
        llm_api_key="generic-secret",
        llm_model="generic-model",
        llm_reasoning_mode="effort",
        llm_reasoning_effort="high",
        llm_native_web_search="responses_web_search",
        llm_native_web_extractor="responses_web_extractor",
        bailian_api_key="legacy-secret",
    )
    resolved = settings.resolved_llm_config
    assert resolved is not None
    assert resolved.api_style == "responses"
    assert resolved.api_key == "generic-secret"
    assert resolved.model == "generic-model"
    assert resolved.native_web_search == "responses_web_search"
    assert resolved.native_web_extractor == "responses_web_extractor"
    assert settings.resolved_llm_config_redacted()["api_key"] == "***REDACTED***"  # type: ignore[index]
    assert "generic-secret" not in repr(settings)

    shared = _base_settings(
        monitor_judgment_enabled=True,
        llm_api_style="responses",
        llm_base_url="https://shared.example/v1",
        llm_api_key="shared-secret",
        llm_model="shared-model",
    )
    assert shared.resolved_llm_config is not None
    assert shared.resolved_llm_config.model == "shared-model"


def test_agent_partial_generic_config_does_not_borrow_legacy_values() -> None:
    with pytest.raises(ValidationError, match="Generic LLM configuration is incomplete"):
        _base_settings(
            agent_enabled=True,
            llm_base_url="https://generic.example/v1",
            bailian_api_key="legacy-secret",
        )


def test_agent_model_catalog_exposes_each_configured_legacy_endpoint() -> None:
    settings = _base_settings(
        agent_enabled=True,
        llm_provider="bailian",
        bailian_api_key="bailian-secret",
        deepseek_api_key="deepseek-secret",
    )

    configs = settings.resolved_agent_llm_configs

    assert tuple(configs) == ("bailian", "deepseek")
    assert configs["bailian"].model == "qwen3.8-max"
    assert configs["bailian"].native_web_search == "responses_web_search"
    assert configs["bailian"].native_web_extractor == "responses_web_extractor"
    assert configs["deepseek"].model == "deepseek-v4-flash"
    assert configs["deepseek"].native_web_search == "disabled"
    assert settings.default_agent_llm_id == "bailian"


def test_env_example_contains_required_keys() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    for line in (
        "HOLDINGS_SOURCES=[]",
        "WATCHLIST_SOURCE=MOOMOO",
        "WATCHLIST_DEFAULT_GROUP=Favorites",
        "MANUAL_WATCHLIST_CSV_PATH=",
        "POST_MARKET_SYNC_DELAY_MINUTES=10",
        "POST_MARKET_SYNC_LOCK_PATH=data/locks/post_market_sync.lock",
        "PROVIDER_TIMEOUT_DEFAULT_SECONDS=30.0",
        "PROVIDER_TIMEOUT_MARKET_SECONDS=15.0",
        "PROVIDER_RETRY_MAX_ATTEMPTS=2",
        "PROVIDER_RETRY_BASE_DELAY_SECONDS=0.05",
        "PROVIDER_RETRY_MAX_DELAY_SECONDS=1.0",
        "AUTH_FAILURE_FALLBACK=false",
        "CIRCUIT_FAILURE_THRESHOLD=5",
        "CIRCUIT_RECOVERY_TIMEOUT_SECONDS=60.0",
        "CIRCUIT_HALF_OPEN_MAX_CALLS=1",
        "ENABLE_CIRCUIT_BREAKER=true",
        "STALE_GUARD_MAX_AGE_SECONDS=86400",
        "STALE_GUARD_RESPECT_SESSION=true",
        "STALE_GUARD_ALLOW_CLOSED_LAST_BAR=true",
        "FRESHNESS_MAX_FRESH_SECONDS=60",
        "FRESHNESS_MAX_DELAYED_SECONDS=900",
        "A_SHARE_CURRENT_WINDOW_SECONDS=300",
        "A_SHARE_MAX_FRESH_SECONDS=30",
        "A_SHARE_MAX_DELAYED_SECONDS=900",
        "PROVIDER_PROXY_URL=",
        "NOTIFICATIONS_ENABLED=false",
        "TELEGRAM_BOT_TOKEN=",
        "TELEGRAM_CHAT_ID=",
        "TELEGRAM_MESSAGE_THREAD_ID=",
        "NOTIFICATION_MAX_ATTEMPTS=5",
        "NOTIFICATION_TTL_HOURS=24",
        "NOTIFICATION_BATCH_SIZE=20",
        "LLM_PROVIDER=bailian",
        "BAILIAN_API_KEY=",
        "BAILIAN_MODEL=qwen3.8-max",
        "BAILIAN_WEB_SEARCH_ENABLED=true",
        "DEEPSEEK_API_KEY=",
        "DEEPSEEK_BASE_URL=https://api.deepseek.com",
        "DEEPSEEK_MODEL=deepseek-v4-flash",
        "LLM_REASONING_EFFORT=max",
        "LLM_OUTPUT_LANGUAGE=zh-CN",
        "MONITOR_JUDGMENT_FALLBACK_PROVIDER=",
        "MONITOR_JUDGMENT_FALLBACK_MODEL=",
        "MONITOR_JUDGMENT_REASONING_EFFORT=",
        "MONITOR_JUDGMENT_FALLBACK_REASONING_EFFORT=",
    ):
        assert line in text, f"missing .env.example key line: {line}"


def test_relative_sqlite_url_resolved_to_project_root() -> None:
    settings = AppSettings(
        app_name="tp",
        app_env=AppEnvironment.DEVELOPMENT,
        log_level=LogLevel.INFO,
        database_url="sqlite:///data/rel_test.db",
        mcp_server_name="tp",
        default_timezone="UTC",
        provider_timeout_seconds=30.0,
    )
    assert settings.database_url.startswith("sqlite:///")
    path = settings.database_url.removeprefix("sqlite:///")
    assert Path(path).is_absolute()
    assert path.endswith("data/rel_test.db") or path.endswith("data\\rel_test.db")


def test_redacted_dict_hides_secrets_and_non_secret_fields() -> None:
    settings = AppSettings(
        app_name="tp",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="postgresql://user:SuperSecretPass@localhost:5432/db",
        mcp_server_name="tp",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        alpha_vantage_api_keys=("REAL_SECRET_KEY", "SECOND_REAL_SECRET_KEY"),
        schwab_client_secret="REAL_SCHWAB_SECRET",
        provider_proxy_url="http://general-user:general-secret@127.0.0.1:7891",
        telegram_bot_token="REAL_TELEGRAM_TOKEN",
        telegram_chat_id="123456789",
        bailian_api_key="REAL_BAILIAN_SECRET",
        deepseek_api_key="REAL_DEEPSEEK_SECRET",
    )
    redacted = settings.redacted_dict()
    assert redacted["alpha_vantage_api_keys"] == "***REDACTED***"
    assert redacted["schwab_client_secret"] == "***REDACTED***"
    assert redacted["database_url"] == "***REDACTED***"
    assert "REAL_SECRET_KEY" not in repr(settings)
    assert "SECOND_REAL_SECRET_KEY" not in repr(settings)
    assert "REAL_SCHWAB_SECRET" not in str(settings)
    assert redacted["provider_proxy_url"] == "***REDACTED***"
    assert redacted["telegram_bot_token"] == "***REDACTED***"
    assert redacted["telegram_chat_id"] == "***REDACTED***"
    assert redacted["bailian_api_key"] == "***REDACTED***"
    assert redacted["deepseek_api_key"] == "***REDACTED***"
    assert "general-secret" not in repr(settings)
    assert "REAL_TELEGRAM_TOKEN" not in repr(settings)
    assert "REAL_BAILIAN_SECRET" not in repr(settings)
    assert "REAL_DEEPSEEK_SECRET" not in repr(settings)
    assert redacted["provider_timeout_default_seconds"] == 30.0
    assert redacted["provider_timeout_market_seconds"] == 15.0
    assert redacted["provider_retry_max_attempts"] == 2
    assert redacted["provider_retry_base_delay_seconds"] == 0.05
    assert redacted["provider_retry_max_delay_seconds"] == 1.0
    assert redacted["circuit_failure_threshold"] == 5
    assert redacted["circuit_recovery_timeout_seconds"] == 60.0
    assert redacted["circuit_half_open_max_calls"] == 1
    assert redacted["enable_circuit_breaker"] is True
    assert redacted["auth_failure_fallback"] is False
    assert redacted["http_max_response_bytes"] == 20_000_000
    assert redacted["stale_guard_max_age_seconds"] == 86400
    assert redacted["stale_guard_respect_session"] is True
    assert redacted["stale_guard_allow_closed_last_bar"] is True
    assert redacted["freshness_max_fresh_seconds"] == 60
    assert redacted["freshness_max_delayed_seconds"] == 900
    assert "SuperSecretPass" not in repr(settings)
    assert "SuperSecretPass" not in str(settings)
    assert "vendor_chain_path" in redacted
    assert redacted["vendor_chain_path"] == str(settings.vendor_chain_path)
    assert redacted["vendor_chain_path"] != "***REDACTED***"


def test_general_proxy_is_validated_and_redacted() -> None:
    settings = _base_settings(provider_proxy_url="http://127.0.0.1:7891")
    assert settings.provider_proxy_url == "http://127.0.0.1:7891"
    assert settings.redacted_dict()["provider_proxy_url"] == "***REDACTED***"
    with pytest.raises(ValidationError):
        _base_settings(provider_proxy_url="socks5://127.0.0.1:7891")


def test_alpha_vantage_key_pool_loads_comma_separated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "ALPHA_VANTAGE_API_KEYS",
        " first-test-key , second-test-key,first-test-key ",
    )
    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="tp",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        mcp_server_name="tp",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
    )

    assert settings.alpha_vantage_api_keys == ("first-test-key", "second-test-key")
    assert settings.redacted_dict()["alpha_vantage_api_keys"] == "***REDACTED***"


def test_plain_fred_api_key_alias_is_loaded_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("FRED_API_KEY", "test-fred-secret")
    settings = AppSettings(
        app_name="tp",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        mcp_server_name="tp",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
    )
    assert settings.fred_api_key == "test-fred-secret"
    assert settings.redacted_dict()["fred_api_key"] == "***REDACTED***"
    assert "SuperSecretPass" not in repr(settings)


def test_blank_app_name_rejected() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            app_name="   ",
            app_env=AppEnvironment.TEST,
            log_level=LogLevel.INFO,
            database_url="sqlite:////tmp/x.db",
            mcp_server_name="tp",
            default_timezone="UTC",
            provider_timeout_seconds=1.0,
        )


def test_invalid_timeout_rejected() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            app_name="tp",
            app_env=AppEnvironment.TEST,
            log_level=LogLevel.INFO,
            database_url="sqlite:////tmp/x.db",
            mcp_server_name="tp",
            default_timezone="UTC",
            provider_timeout_seconds=0,
        )


def test_root_guard_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_root = tmp_path / "not_a_project"
    fake_root.mkdir()
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", fake_root)
    # Ensure packaged marker is also absent (invalid layout, not installed).
    monkeypatch.setattr(
        settings_module,
        "PACKAGED_VENDOR_CHAIN_PATH",
        tmp_path / "missing_vendor_chains.default.yaml",
    )
    with pytest.raises(ConfigurationError, match="PROJECT_ROOT guard"):
        AppSettings.load()


def _clear_trading_partner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)


def _write_min_env(path: Path, *, app_name: str, db: Path, timeout: str = "9") -> None:
    path.write_text(
        "\n".join(
            [
                f"APP_NAME={app_name}",
                "APP_ENV=test",
                "LOG_LEVEL=INFO",
                f"DATABASE_URL=sqlite:///{db}",
                "MCP_SERVER_NAME=mcp-test",
                "DEFAULT_TIMEZONE=UTC",
                f"PROVIDER_TIMEOUT_SECONDS={timeout}",
            ]
        ),
        encoding="utf-8",
    )


def test_load_source_layout_uses_project_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source layout: PROJECT_ROOT/pyproject.toml present → project-root .env."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    db = root / "db.sqlite"
    _write_min_env(root / ".env", app_name="source-layout", db=db, timeout="11")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", root)
    _clear_trading_partner_env(monkeypatch)
    # Process env must not be required; project-root .env supplies values.
    settings = AppSettings.load()
    assert settings.app_name == "source-layout"
    assert settings.provider_timeout_seconds == 11.0


def test_load_installed_layout_process_env_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed layout: no pyproject marker but packaged chain exists."""
    fake_root = tmp_path / "site-packages-parent"
    fake_root.mkdir()
    packaged = tmp_path / "packaged" / "vendor_chains.default.yaml"
    packaged.parent.mkdir()
    packaged.write_text("chains: {}\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(settings_module, "PACKAGED_VENDOR_CHAIN_PATH", packaged)
    _clear_trading_partner_env(monkeypatch)
    db = tmp_path / "installed.db"
    monkeypatch.setenv("APP_NAME", "installed-layout")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MCP_SERVER_NAME", "mcp-installed")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "7")
    settings = AppSettings.load()
    assert settings.app_name == "installed-layout"
    assert settings.provider_timeout_seconds == 7.0
    assert settings.mcp_server_name == "mcp-installed"


def test_load_installed_ignores_cwd_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed layout must not pick up cwd/home/parent .env files."""
    fake_root = tmp_path / "site-packages-parent"
    fake_root.mkdir()
    packaged = tmp_path / "packaged" / "vendor_chains.default.yaml"
    packaged.parent.mkdir()
    packaged.write_text("chains: {}\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(settings_module, "PACKAGED_VENDOR_CHAIN_PATH", packaged)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    db_cwd = tmp_path / "cwd.db"
    _write_min_env(cwd / ".env", app_name="from-cwd-env", db=db_cwd, timeout="99")
    monkeypatch.chdir(cwd)

    _clear_trading_partner_env(monkeypatch)
    db = tmp_path / "process.db"
    monkeypatch.setenv("APP_NAME", "from-process-env")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MCP_SERVER_NAME", "mcp-process")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "13")

    settings = AppSettings.load()
    assert settings.app_name == "from-process-env"
    assert settings.provider_timeout_seconds == 13.0
    assert settings.mcp_server_name == "mcp-process"


def test_load_installed_explicit_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed layout still accepts an explicit env_file that exists."""
    fake_root = tmp_path / "site-packages-parent"
    fake_root.mkdir()
    packaged = tmp_path / "packaged" / "vendor_chains.default.yaml"
    packaged.parent.mkdir()
    packaged.write_text("chains: {}\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(settings_module, "PACKAGED_VENDOR_CHAIN_PATH", packaged)

    env_path = tmp_path / "explicit.env"
    db = tmp_path / "explicit.db"
    _write_min_env(env_path, app_name="installed-explicit", db=db, timeout="17")
    _clear_trading_partner_env(monkeypatch)

    settings = AppSettings.load(env_file=env_path)
    assert settings.app_name == "installed-explicit"
    assert settings.provider_timeout_seconds == 17.0


def test_load_invalid_layout_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither source marker nor packaged chain → safe PROJECT_ROOT guard."""
    fake_root = tmp_path / "not_a_project"
    fake_root.mkdir()
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(
        settings_module,
        "PACKAGED_VENDOR_CHAIN_PATH",
        tmp_path / "missing_vendor_chains.default.yaml",
    )
    with pytest.raises(ConfigurationError, match="PROJECT_ROOT guard"):
        AppSettings.load()


def test_load_explicit_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure PROJECT_ROOT remains real so guard passes.
    env_path = tmp_path / "test.env"
    db = tmp_path / "db.sqlite"
    env_path.write_text(
        "\n".join(
            [
                "APP_NAME=from-file",
                "APP_ENV=test",
                "LOG_LEVEL=INFO",
                f"DATABASE_URL=sqlite:///{db}",
                "MCP_SERVER_NAME=mcp-test",
                "DEFAULT_TIMEZONE=UTC",
                "PROVIDER_TIMEOUT_SECONDS=9",
            ]
        ),
        encoding="utf-8",
    )
    # An explicit runtime file is authoritative over ambient host variables.
    for key in list(__import__("os").environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "ambient-must-not-win")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ambient.sqlite'}")
    settings = AppSettings.load(env_file=env_path)
    assert settings.app_name == "from-file"
    assert settings.database_url == f"sqlite:///{db}"
    assert settings.provider_timeout_seconds == 9.0


def test_missing_env_file_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        AppSettings.load(env_file=tmp_path / "nope.env")


def _base_settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": "sqlite:////tmp/d5b_settings.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 30.0,
    }
    base.update(overrides)
    # Isolate field defaults from host .env (model_config env_file=".env").
    return AppSettings(_env_file=None, **base)  # type: ignore[call-arg]


def test_d5b_resilience_defaults() -> None:
    s = _base_settings()
    assert s.provider_timeout_default_seconds == 30.0
    assert s.provider_timeout_market_seconds == 15.0
    assert s.provider_retry_max_attempts == 2
    assert s.provider_retry_base_delay_seconds == 0.05
    assert s.provider_retry_max_delay_seconds == 1.0
    assert s.provider_rate_limit_max_wait_seconds == 5.0
    assert s.circuit_failure_threshold == 5
    assert s.circuit_recovery_timeout_seconds == 60.0
    assert s.circuit_half_open_max_calls == 1
    assert s.enable_circuit_breaker is True
    assert s.auth_failure_fallback is False
    assert s.http_max_response_bytes == 20_000_000
    assert s.reddit_min_interval_seconds == 6.0
    assert s.reddit_cache_ttl_seconds == 3600
    assert s.reddit_cooldown_default_seconds == 900
    assert s.reddit_cooldown_max_seconds == 3600
    assert s.reddit_subreddits == "wallstreetbets,stocks,investing"
    assert s.reddit_apify_enabled is False
    assert s.reddit_apify_actor_id == "harshmaur/reddit-scraper"
    assert s.reddit_apify_subreddits == (
        "stocks,investing,securityanalysis,valueinvesting,wallstreetbets,shortsqueeze"
    )
    assert s.reddit_apify_lookback_map == {
        "stocks": 7,
        "investing": 14,
        "securityanalysis": 30,
        "valueinvesting": 30,
        "wallstreetbets": 3,
        "shortsqueeze": 2,
    }
    assert s.reddit_apify_max_charge_usd == Decimal("0.20")
    assert s.apify_api_token is None
    assert s.weekend_rwa_proxy_enabled is True
    assert s.weekend_rwa_proxy_cache_ttl_seconds == 60
    assert s.weekend_rwa_proxy_timeout_seconds == 10.0
    assert s.ig_weekend_gold_enabled is False
    assert s.ig_weekend_gold_actor_id == "apify/web-scraper"
    assert s.ig_weekend_gold_cache_ttl_seconds == 600
    assert s.ig_weekend_gold_max_charge_usd == Decimal("0.03")
    assert s.ig_weekend_gold_timeout_seconds == 120.0
    assert s.moomoo_sentiment_enabled is True


def test_reddit_subreddits_normalizes_input() -> None:
    s = _base_settings(
        reddit_subreddits=" WallStreetBets , STOCKS, investing_extra ",
    )
    assert s.reddit_subreddits == "wallstreetbets,stocks,investing_extra"


def test_reddit_apify_subreddits_normalizes_input() -> None:
    s = _base_settings(
        reddit_apify_subreddits=" Stocks , SecurityAnalysis , ValueInvesting ",
        reddit_apify_lookback_days_by_subreddit=("stocks:7,securityanalysis:30,valueinvesting:30"),
    )
    assert s.reddit_apify_subreddits == "stocks,securityanalysis,valueinvesting"


def test_reddit_apify_lookbacks_normalize_and_require_exact_coverage() -> None:
    s = _base_settings(
        reddit_apify_subreddits="stocks,shortsqueeze",
        reddit_apify_lookback_days_by_subreddit=" Stocks : 7 , ShortSqueeze:2 ",
    )
    assert s.reddit_apify_lookback_days_by_subreddit == "stocks:7,shortsqueeze:2"
    assert s.reddit_apify_lookback_map == {"stocks": 7, "shortsqueeze": 2}

    with pytest.raises(ValidationError):
        _base_settings(
            reddit_apify_subreddits="stocks,shortsqueeze",
            reddit_apify_lookback_days_by_subreddit="stocks:7",
        )
    with pytest.raises(ValidationError):
        _base_settings(
            reddit_apify_subreddits="stocks",
            reddit_apify_lookback_days_by_subreddit="stocks:0",
        )


def test_reddit_subreddits_rejects_illegal_configurations() -> None:
    with pytest.raises(ValidationError):
        _base_settings(reddit_subreddits="   ")
    with pytest.raises(ValidationError):
        _base_settings(reddit_subreddits="wallstreetbets,wallstreetbets")
    with pytest.raises(ValidationError):
        _base_settings(reddit_subreddits="a,b,c,d,e,f,g,h,i,j,k")
    with pytest.raises(ValidationError):
        _base_settings(reddit_subreddits="wallstreetbets,abc-def")
    with pytest.raises(ValidationError):
        _base_settings(
            reddit_cooldown_default_seconds=901,
            reddit_cooldown_max_seconds=900,
        )


def test_timeout_for_market_vs_default() -> None:
    s = _base_settings(
        provider_timeout_default_seconds=30.0,
        provider_timeout_market_seconds=15.0,
    )
    market = {
        DataCategory.MARKET_QUOTE,
        DataCategory.MARKET_OHLCV,
        DataCategory.MARKET_SNAPSHOT,
        DataCategory.MARKET_STRUCTURE,
    }
    for category in market:
        assert s.timeout_for(category) == 15.0
    for category in DataCategory:
        if category in market:
            continue
        assert s.timeout_for(category) == 30.0


def test_timeout_for_respects_overrides() -> None:
    s = _base_settings(
        provider_timeout_default_seconds=9.0,
        provider_timeout_market_seconds=3.0,
    )
    assert s.timeout_for(DataCategory.NEWS) == 9.0
    assert s.timeout_for(DataCategory.MARKET_QUOTE) == 3.0


def test_d5b_positive_and_nonnegative_validation() -> None:
    with pytest.raises(ValidationError):
        _base_settings(provider_timeout_default_seconds=0)
    with pytest.raises(ValidationError):
        _base_settings(provider_timeout_market_seconds=-1)
    with pytest.raises(ValidationError):
        _base_settings(provider_retry_max_attempts=0)
    with pytest.raises(ValidationError):
        _base_settings(provider_retry_base_delay_seconds=-0.01)
    with pytest.raises(ValidationError):
        _base_settings(provider_rate_limit_max_wait_seconds=-0.01)
    with pytest.raises(ValidationError):
        _base_settings(circuit_failure_threshold=0)
    with pytest.raises(ValidationError):
        _base_settings(circuit_recovery_timeout_seconds=0)
    with pytest.raises(ValidationError):
        _base_settings(circuit_half_open_max_calls=0)
    with pytest.raises(ValidationError):
        _base_settings(
            provider_retry_base_delay_seconds=1.0,
            provider_retry_max_delay_seconds=0.5,
        )
    with pytest.raises(ValidationError):
        _base_settings(stale_guard_max_age_seconds=-1)
    with pytest.raises(ValidationError):
        _base_settings(freshness_max_fresh_seconds=-1)
    with pytest.raises(ValidationError):
        _base_settings(freshness_max_delayed_seconds=-1)
    with pytest.raises(ValidationError):
        _base_settings(
            freshness_max_fresh_seconds=100,
            freshness_max_delayed_seconds=50,
        )
    # Also validate allowed zero thresholds for stale/freshness knobs.
    settings = _base_settings(
        stale_guard_max_age_seconds=0,
        freshness_max_fresh_seconds=0,
        freshness_max_delayed_seconds=0,
    )
    assert settings.stale_guard_max_age_seconds == 0
    assert settings.freshness_max_fresh_seconds == 0
    assert settings.freshness_max_delayed_seconds == 0


def test_d5b_env_keys_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "d5b.env"
    db = tmp_path / "db.sqlite"
    env_path.write_text(
        "\n".join(
            [
                "APP_NAME=d5b",
                "APP_ENV=test",
                "LOG_LEVEL=INFO",
                f"DATABASE_URL=sqlite:///{db}",
                "MCP_SERVER_NAME=mcp-test",
                "DEFAULT_TIMEZONE=UTC",
                "PROVIDER_TIMEOUT_SECONDS=30",
                "PROVIDER_TIMEOUT_DEFAULT_SECONDS=22",
                "PROVIDER_TIMEOUT_MARKET_SECONDS=11",
                "PROVIDER_RETRY_MAX_ATTEMPTS=3",
                "PROVIDER_RETRY_BASE_DELAY_SECONDS=0.1",
                "PROVIDER_RETRY_MAX_DELAY_SECONDS=2.0",
                "PROVIDER_RATE_LIMIT_MAX_WAIT_SECONDS=7.5",
                "AUTH_FAILURE_FALLBACK=true",
                "CIRCUIT_FAILURE_THRESHOLD=7",
                "CIRCUIT_RECOVERY_TIMEOUT_SECONDS=90",
                "CIRCUIT_HALF_OPEN_MAX_CALLS=2",
                "ENABLE_CIRCUIT_BREAKER=false",
            ]
        ),
        encoding="utf-8",
    )
    for key in list(__import__("os").environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    settings = AppSettings.load(env_file=env_path)
    assert settings.provider_timeout_default_seconds == 22.0
    assert settings.provider_timeout_market_seconds == 11.0
    assert settings.provider_retry_max_attempts == 3
    assert settings.provider_retry_base_delay_seconds == 0.1
    assert settings.provider_retry_max_delay_seconds == 2.0
    assert settings.provider_rate_limit_max_wait_seconds == 7.5
    assert settings.auth_failure_fallback is True
    assert settings.circuit_failure_threshold == 7
    assert settings.circuit_recovery_timeout_seconds == 90.0
    assert settings.circuit_half_open_max_calls == 2
    assert settings.enable_circuit_breaker is False
    assert settings.timeout_for(DataCategory.MARKET_OHLCV) == 11.0
    assert settings.timeout_for(DataCategory.FUNDAMENTALS) == 22.0


def test_d7_stale_and_freshness_defaults() -> None:
    s = _base_settings()
    assert s.stale_guard_max_age_seconds == 86400
    assert s.stale_guard_respect_session is True
    assert s.stale_guard_allow_closed_last_bar is True
    assert s.freshness_max_fresh_seconds == 60
    assert s.freshness_max_delayed_seconds == 900


def test_d7_env_keys_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "d7.env"
    db = tmp_path / "db.sqlite"
    env_path.write_text(
        "\n".join(
            [
                "APP_NAME=d7",
                "APP_ENV=test",
                "LOG_LEVEL=INFO",
                f"DATABASE_URL=sqlite:///{db}",
                "MCP_SERVER_NAME=mcp-test",
                "DEFAULT_TIMEZONE=UTC",
                "PROVIDER_TIMEOUT_SECONDS=30",
                "STALE_GUARD_MAX_AGE_SECONDS=3600",
                "STALE_GUARD_RESPECT_SESSION=false",
                "STALE_GUARD_ALLOW_CLOSED_LAST_BAR=true",
                "FRESHNESS_MAX_FRESH_SECONDS=30",
                "FRESHNESS_MAX_DELAYED_SECONDS=600",
            ]
        ),
        encoding="utf-8",
    )
    for key in list(__import__("os").environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    settings = AppSettings.load(env_file=env_path)
    assert settings.stale_guard_max_age_seconds == 3600
    assert settings.stale_guard_respect_session is False
    assert settings.stale_guard_allow_closed_last_bar is True
    assert settings.freshness_max_fresh_seconds == 30
    assert settings.freshness_max_delayed_seconds == 600


def test_e5_a_share_window_defaults() -> None:
    settings = _base_settings()
    assert settings.a_share_current_window_seconds == 300
    assert settings.a_share_max_fresh_seconds == 30
    assert settings.a_share_max_delayed_seconds == 900


def test_e5_a_share_windows_reject_python_coercions_and_negative() -> None:
    # Keep the 3×5 matrix explicitly in test logic for clarity and future edits.
    for field in (
        "a_share_current_window_seconds",
        "a_share_max_fresh_seconds",
        "a_share_max_delayed_seconds",
    ):
        for value in (True, False, 1.0, 0.0, -1):
            with pytest.raises(ValidationError):
                _base_settings(**{field: value})


def test_e5_a_share_delayed_must_not_be_below_fresh() -> None:
    with pytest.raises(ValidationError, match="a_share_max_delayed_seconds"):
        _base_settings(
            a_share_max_fresh_seconds=31,
            a_share_max_delayed_seconds=30,
        )
    settings = _base_settings(
        a_share_current_window_seconds=0,
        a_share_max_fresh_seconds=0,
        a_share_max_delayed_seconds=0,
    )
    assert settings.a_share_current_window_seconds == 0


def test_e5_a_share_window_env_decimal_strings_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "a-share-windows.env"
    db = tmp_path / "db.sqlite"
    _write_min_env(env_path, app_name="a-share-windows", db=db)
    with env_path.open("a", encoding="utf-8") as stream:
        stream.write(
            "\nA_SHARE_CURRENT_WINDOW_SECONDS=120"
            "\nA_SHARE_MAX_FRESH_SECONDS=10"
            "\nA_SHARE_MAX_DELAYED_SECONDS=600\n"
        )
    _clear_trading_partner_env(monkeypatch)
    settings = AppSettings.load(env_file=env_path)
    assert settings.a_share_current_window_seconds == 120
    assert settings.a_share_max_fresh_seconds == 10
    assert settings.a_share_max_delayed_seconds == 600


def test_e5_a_share_window_env_rejects_non_decimal_strings_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for raw in ["1.5", "not-a-number", "+1", "-1", "abc"]:
        for field_key in [
            "A_SHARE_CURRENT_WINDOW_SECONDS",
            "A_SHARE_MAX_FRESH_SECONDS",
            "A_SHARE_MAX_DELAYED_SECONDS",
        ]:
            env_path = tmp_path / f"invalid-a-share-window-{field_key.lower()}.env"
            _write_min_env(
                env_path,
                app_name=f"invalid-{field_key.lower()}",
                db=tmp_path / "db.sqlite",
            )
            with env_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\n{field_key}={raw}\n")
            _clear_trading_partner_env(monkeypatch)
            with pytest.raises(ConfigurationError):
                AppSettings.load(env_file=env_path)
