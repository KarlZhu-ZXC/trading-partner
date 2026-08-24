"""Infrastructure resource ownership and deterministic composition overrides."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.agent_attachment_store import AgentAttachmentStore
from application.ports.agent_model_provider import AgentModelProvider
from application.ports.agent_web_search_provider import AgentWebSearchProvider
from application.ports.clock import Clock
from application.ports.http_transport import HttpTransport
from application.ports.monitor_judgment_provider import MonitorJudgmentProvider
from application.ports.notification_sender import NotificationSender
from application.ports.trade_retro_narrative_provider import TradeRetroNarrativeProvider
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from domain.common.enums import VendorId
from infrastructure.attachments.agent import FileAgentAttachmentStore
from infrastructure.config.settings import PROJECT_ROOT, AppSettings
from infrastructure.persistence.database import SqlAlchemyDatabase
from infrastructure.providers.a_share.eastmoney_gate import EastmoneyRequestGate
from infrastructure.providers.llm import (
    BailianChatMonitorJudgmentProvider,
    BailianMonitorJudgmentProvider,
    BailianTradeRetroNarrativeProvider,
    DeepSeekMonitorJudgmentProvider,
    OpenAICompatibleModelProvider,
    OpenCodeGoModelProvider,
    OpenCodeGoMonitorJudgmentProvider,
    OpenCodeGoTradeRetroNarrativeProvider,
    OpenCodeZenModelProvider,
    OpenCodeZenMonitorJudgmentProvider,
)
from infrastructure.providers.llm.routed import (
    LLMResilienceController,
    RoutedAgentModelProvider,
)
from infrastructure.providers.llm.tavily_agent_web_search import (
    TavilyAgentWebSearchProvider,
)
from infrastructure.system.process_file_lock import ProcessFileLock


def build_agent_attachment_store(settings: AppSettings) -> AgentAttachmentStore | None:
    if not settings.agent_enabled and not settings.telegram_agent_enabled:
        return None
    return FileAgentAttachmentStore(PROJECT_ROOT / "data" / "agent" / "attachments")


def build_monitor_judgment_provider(
    settings: AppSettings,
) -> MonitorJudgmentProvider | None:
    if not settings.monitor_judgment_enabled:
        return None
    config = settings.resolved_monitor_judgment_config
    assert config is not None
    if settings.resolved_llm_provider_id in {"opencode_zen", "opencode_go"}:
        provider_type = (
            OpenCodeZenMonitorJudgmentProvider
            if settings.resolved_llm_provider_id == "opencode_zen"
            else OpenCodeGoMonitorJudgmentProvider
        )
        return provider_type(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            reasoning_effort=(
                settings.monitor_judgment_reasoning_effort
                or config.reasoning_effort
                or "max"
            ),
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=min(
                config.max_output_tokens,
                settings.monitor_judgment_max_output_tokens,
            ),
            proxy_url=settings.provider_proxy_url,
        )
    if config.api_style == "responses":
        return BailianMonitorJudgmentProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            reasoning_effort=(
                settings.monitor_judgment_reasoning_effort
                or config.reasoning_effort
                or "max"
            ),
            web_search_enabled=config.web_search_enabled,
            output_language=settings.llm_output_language,
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=min(
                config.max_output_tokens,
                settings.monitor_judgment_max_output_tokens,
            ),
            proxy_url=settings.provider_proxy_url,
        )
    if settings.resolved_llm_provider_id == "bailian":
        return BailianChatMonitorJudgmentProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            reasoning_effort=(
                settings.monitor_judgment_reasoning_effort
                or config.reasoning_effort
                or "max"
            ),
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=min(
                config.max_output_tokens,
                settings.monitor_judgment_max_output_tokens,
            ),
            proxy_url=settings.provider_proxy_url,
        )
    return DeepSeekMonitorJudgmentProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        reasoning_effort=(
            settings.monitor_judgment_reasoning_effort or config.reasoning_effort or "max"
        ),
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=min(
            config.max_output_tokens,
            settings.monitor_judgment_max_output_tokens,
        ),
        proxy_url=settings.provider_proxy_url,
    )


def build_monitor_judgment_fallback_provider(
    settings: AppSettings,
) -> MonitorJudgmentProvider | None:
    """Build the explicitly configured transient-error fallback."""
    if not settings.monitor_judgment_enabled:
        return None
    config = settings.resolved_monitor_judgment_fallback_config
    if config is None:
        return None
    if settings.monitor_judgment_fallback_provider in {"opencode_zen", "opencode_go"}:
        provider_type = (
            OpenCodeZenMonitorJudgmentProvider
            if settings.monitor_judgment_fallback_provider == "opencode_zen"
            else OpenCodeGoMonitorJudgmentProvider
        )
        return provider_type(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            reasoning_effort=config.reasoning_effort or "max",
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=min(
                config.max_output_tokens,
                settings.monitor_judgment_max_output_tokens,
            ),
            proxy_url=settings.provider_proxy_url,
        )
    if config.api_style == "responses":
        return BailianMonitorJudgmentProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            reasoning_effort=config.reasoning_effort or "max",
            web_search_enabled=False,
            output_language=settings.llm_output_language,
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=min(
                config.max_output_tokens,
                settings.monitor_judgment_max_output_tokens,
            ),
            proxy_url=settings.provider_proxy_url,
        )
    return DeepSeekMonitorJudgmentProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        reasoning_effort=config.reasoning_effort or "max",
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=min(
            config.max_output_tokens,
            settings.monitor_judgment_max_output_tokens,
        ),
        proxy_url=settings.provider_proxy_url,
    )


def build_trade_retro_narrative_provider(
    settings: AppSettings,
) -> TradeRetroNarrativeProvider | None:
    if not settings.trade_retro_llm_enabled:
        return None
    config = settings.resolved_llm_config
    if config is None:
        return None
    if settings.resolved_llm_provider_id == "opencode_go":
        return OpenCodeGoTradeRetroNarrativeProvider(
            config,
            max_output_tokens=min(
                config.max_output_tokens,
                settings.monitor_judgment_max_output_tokens,
            ),
            proxy_url=settings.provider_proxy_url,
        )
    if config.api_style != "responses":
        return None
    return BailianTradeRetroNarrativeProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        reasoning_effort=config.reasoning_effort or "max",
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=min(
            config.max_output_tokens,
            settings.monitor_judgment_max_output_tokens,
        ),
        proxy_url=settings.provider_proxy_url,
    )


def build_agent_model_provider(settings: AppSettings) -> AgentModelProvider | None:
    """Build the shared provider-neutral Agent model adapter when enabled."""

    if not settings.agent_enabled and not settings.telegram_agent_enabled:
        return None
    config = settings.resolved_llm_config
    if config is None:
        # Keep the rest of the application and durable Agent history available.
        # Channel adapters expose a secret-safe configuration diagnostic until a
        # complete endpoint is supplied. Partial generic configuration still
        # fails closed in ``resolved_llm_config`` without legacy-field mixing.
        return None
    if settings.resolved_llm_provider_id == "opencode_go":
        return OpenCodeGoModelProvider(config, proxy_url=settings.provider_proxy_url)
    if settings.resolved_llm_provider_id == "opencode_zen":
        return OpenCodeZenModelProvider(config, proxy_url=settings.provider_proxy_url)
    return OpenAICompatibleModelProvider(config, proxy_url=settings.provider_proxy_url)


def build_agent_model_providers(
    settings: AppSettings,
    *,
    resilience: LLMResilienceController | None = None,
) -> dict[str, AgentModelProvider]:
    """Build every configured Console-selectable Agent endpoint."""

    if not settings.agent_enabled and not settings.telegram_agent_enabled:
        return {}
    result: dict[str, AgentModelProvider] = {}
    for model_id, config in settings.resolved_agent_llm_configs.items():
        if model_id == "opencode_go":
            provider: AgentModelProvider = OpenCodeGoModelProvider(
                config, proxy_url=settings.provider_proxy_url
            )
        elif model_id == "opencode_zen":
            provider = OpenCodeZenModelProvider(
                config, proxy_url=settings.provider_proxy_url
            )
        else:
            provider = OpenAICompatibleModelProvider(
                config, proxy_url=settings.provider_proxy_url
            )
        if resilience is not None:
            route_vendor = (
                VendorId(settings.llm_provider)
                if model_id == "default"
                else VendorId(model_id)
            )
            provider = RoutedAgentModelProvider(
                provider,
                vendor=route_vendor,
                resilience=resilience,
            )
        result[model_id] = provider
    return result


def build_agent_web_search_provider(
    settings: AppSettings,
    model_providers: dict[str, AgentModelProvider],
    clock: Clock,
) -> AgentWebSearchProvider | None:
    """Use Tavily Search as the model-neutral Agent Web Search sidecar."""

    del model_providers  # Search is intentionally independent of answer-model routes.
    if (
        not settings.tavily_web_search_enabled
        or not settings.tavily_api_key
        or (not settings.agent_enabled and not settings.telegram_agent_enabled)
    ):
        return None
    return TavilyAgentWebSearchProvider(
        api_key=settings.tavily_api_key,
        clock=clock,
        base_url=settings.tavily_base_url,
        search_depth=settings.tavily_search_depth,
        timeout_seconds=settings.tavily_timeout_seconds,
        proxy_url=settings.provider_proxy_url,
    )


@dataclass(frozen=True, slots=True)
class CompositionOverrides:
    """Deterministic composition-only overrides; never a production mode switch."""

    clock: Clock | None = None
    a_share_transport: HttpTransport | None = None
    eastmoney_gate: EastmoneyRequestGate | None = None
    a_share_calendar: AShareTradingCalendar | None = None
    watchlist_provider: WatchlistSourceProvider | None = None
    notification_sender: NotificationSender | None = None
    # Legacy override name remains accepted for old test fixtures and scripts.
    monitor_notification_sender: NotificationSender | None = None


@dataclass(slots=True)
class RuntimeResources:
    """Own infrastructure resources and their deterministic shutdown order."""

    database: SqlAlchemyDatabase
    monitor_run_lock: ProcessFileLock
    post_market_sync_lock: ProcessFileLock
    a_share_transport: HttpTransport | None = None
    cross_asset_transport: HttpTransport | None = None
    notification_sender: NotificationSender | None = None
    monitor_judgment_provider: object | None = None
    monitor_judgment_fallback_provider: object | None = None
    trade_retro_narrative_provider: object | None = None
    agent_model_provider: AgentModelProvider | None = None
    agent_model_providers: dict[str, AgentModelProvider] = field(default_factory=dict)
    agent_attachment_store: AgentAttachmentStore | None = None
    agent_web_search_provider: AgentWebSearchProvider | None = None
    agent_turn_lock_factory: Callable[[str], ProcessFileLock] | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def monitor_notification_sender(self) -> NotificationSender | None:
        """Compatibility accessor for pre-0030 composition callers."""
        return self.notification_sender

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closed: set[int] = set()
            for transport in (
                self.a_share_transport,
                self.cross_asset_transport,
                self.notification_sender,
                self.monitor_judgment_provider,
                self.monitor_judgment_fallback_provider,
                self.trade_retro_narrative_provider,
                self.agent_model_provider,
                self.agent_web_search_provider,
                *self.agent_model_providers.values(),
            ):
                if transport is None or id(transport) in closed:
                    continue
                closed.add(id(transport))
                aclose = getattr(transport, "aclose", None)
                if callable(aclose):
                    await aclose()
        finally:
            self.database.close()
