from __future__ import annotations

from typing import cast

from domain.common.enums import AppEnvironment, LogLevel, VendorId
from infrastructure.composition.runtime import (
    build_agent_model_providers,
    build_agent_web_search_provider,
    build_monitor_judgment_provider,
    build_trade_retro_narrative_provider,
)
from infrastructure.config.settings import AppSettings
from infrastructure.providers.llm.deepseek_monitor_judgment import (
    BailianChatMonitorJudgmentProvider,
)
from infrastructure.providers.llm.opencode_go import (
    OpenCodeGoModelProvider,
    OpenCodeGoMonitorJudgmentProvider,
    OpenCodeGoTradeRetroNarrativeProvider,
    OpenCodeZenModelProvider,
)
from infrastructure.providers.llm.routed import (
    LLMResilienceController,
    RoutedAgentModelProvider,
)
from infrastructure.providers.llm.tavily_agent_web_search import (
    TavilyAgentWebSearchProvider,
)
from infrastructure.system.clock import SystemClock


def test_generic_agent_endpoint_uses_configured_vendor_for_resilience_route() -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp-runtime-composition-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-runtime-composition-test",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        agent_enabled=True,
        llm_provider="deepseek",
        llm_base_url="https://llm.example/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )

    providers = build_agent_model_providers(
        settings,
        resilience=cast(LLMResilienceController, object()),
    )

    provider = providers["default"]
    assert isinstance(provider, RoutedAgentModelProvider)
    assert provider._vendor is VendorId.DEEPSEEK  # noqa: SLF001


def test_opencode_go_composition_supports_agent_and_monitor() -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp-runtime-composition-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-runtime-composition-test",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        agent_enabled=True,
        monitor_judgment_enabled=True,
        llm_provider="opencode_go",
        opencode_go_api_key="test-go-key",
    )

    agent_provider = build_agent_model_providers(settings)["opencode_go"]
    monitor_provider = build_monitor_judgment_provider(settings)

    assert isinstance(agent_provider, OpenCodeGoModelProvider)
    assert isinstance(monitor_provider, OpenCodeGoMonitorJudgmentProvider)


def test_opencode_go_composition_supports_trade_retro_narrative() -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp-runtime-composition-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-runtime-composition-test",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        trade_retro_llm_enabled=True,
        llm_provider="opencode_go",
        opencode_go_api_key="test-go-key",
        opencode_go_model="deepseek-v4-flash-vision-exp",
        llm_reasoning_effort="max",
    )

    provider = build_trade_retro_narrative_provider(settings)

    assert isinstance(provider, OpenCodeGoTradeRetroNarrativeProvider)
    assert provider.model == "deepseek-v4-flash-vision-exp"


def test_bailian_monitor_defaults_to_deepseek_json_chat_without_changing_agent() -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp-runtime-composition-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-runtime-composition-test",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        agent_enabled=True,
        monitor_judgment_enabled=True,
        llm_provider="bailian",
        bailian_api_key="test-bailian-key",
        tavily_api_key="test-tavily-key",
    )

    providers = build_agent_model_providers(settings)
    agent_provider = providers["bailian"]
    web_search = build_agent_web_search_provider(settings, providers, SystemClock())
    monitor_provider = build_monitor_judgment_provider(settings)

    assert agent_provider.model == "qwen3.8-max"  # type: ignore[attr-defined]
    assert isinstance(monitor_provider, BailianChatMonitorJudgmentProvider)
    assert monitor_provider.model == "deepseek-v4-flash-0731"
    assert isinstance(web_search, TavilyAgentWebSearchProvider)
    assert agent_provider.config.native_web_search == "disabled"  # type: ignore[attr-defined]


def test_agent_web_search_is_unavailable_without_tavily_key() -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp-runtime-composition-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-runtime-composition-test",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        agent_enabled=True,
        bailian_api_key="test-bailian-key",
    )

    assert build_agent_web_search_provider(settings, {}, SystemClock()) is None


def test_one_opencode_key_exposes_separate_zen_and_go_providers() -> None:
    settings = AppSettings(
        _env_file=None,
        app_name="tp-runtime-composition-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-runtime-composition-test",
        default_timezone="UTC",
        provider_timeout_seconds=1.0,
        agent_enabled=True,
        llm_provider="opencode_go",
        opencode_api_key="shared-opencode-key",
    )

    providers = build_agent_model_providers(settings)

    assert isinstance(providers["opencode_go"], OpenCodeGoModelProvider)
    assert isinstance(providers["opencode_zen"], OpenCodeZenModelProvider)
    configs = settings.resolved_agent_llm_configs
    assert configs["opencode_go"].api_key == "shared-opencode-key"
    assert configs["opencode_zen"].api_key == "shared-opencode-key"
    assert configs["opencode_go"].base_url.endswith("/zen/go/v1")
    assert configs["opencode_zen"].base_url.endswith("/zen/v1")
