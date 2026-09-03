"""Build the Monitoring service graph outside the bounded bootstrap facade."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.agent_model_provider import AgentModelProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.market_session_calendar import MarketSessionCalendar
from application.ports.monitor_judgment_provider import MonitorJudgmentProvider
from application.ports.monitor_repository import MonitorRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_event_analysis_service import MonitorEventAnalysisService
from application.services.monitor_fact_resolver import MonitorFactResolver
from application.services.monitor_judgment_service import MonitorJudgmentService
from application.services.monitor_schedule_service import MonitorScheduleService
from application.services.monitor_service import MonitorService
from application.services.risk_tool_coordinator import RiskToolCoordinator
from composition_root.market_facts import MarketFactsBundle
from infrastructure.calendars.a_share_market_session_calendar import (
    AShareMarketSessionCalendarAdapter,
)
from infrastructure.calendars.kr_market_session_calendar import XkrxMarketSessionCalendar
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.composition.runtime import (
    build_monitor_event_analysis_provider,
    build_monitor_judgment_fallback_provider,
    build_monitor_judgment_provider,
)
from infrastructure.config.settings import AppSettings
from infrastructure.providers.llm.routed import LLMResilienceController


@dataclass(frozen=True, slots=True)
class MonitoringBundle:
    us_calendar: MarketSessionCalendar
    schedule: MonitorScheduleService
    service: MonitorService
    evaluation: MonitorEvaluationService
    judgment_provider: MonitorJudgmentProvider | None
    judgment_fallback_provider: MonitorJudgmentProvider | None
    event_analysis_provider: AgentModelProvider | None


def build_monitoring_services(
    *,
    settings: AppSettings,
    repository: MonitorRepository,
    market_facts: MarketFactsBundle,
    risk: RiskToolCoordinator,
    research_uow_factory: Callable[[], ResearchUnitOfWork],
    a_share_calendar: AShareTradingCalendar,
    llm_resilience: LLMResilienceController,
    clock: Clock,
    id_generator: IdGenerator,
) -> MonitoringBundle:
    us_calendar = XnysMarketSessionCalendar()
    schedule = MonitorScheduleService(
        us_calendar=us_calendar,
        a_share_calendar=AShareMarketSessionCalendarAdapter(a_share_calendar),
        kr_calendar=XkrxMarketSessionCalendar(),
        post_market_delay_minutes=settings.post_market_sync_delay_minutes,
        weekend_rwa_proxy_enabled=settings.weekend_rwa_proxy_enabled,
        ig_weekend_gold_enabled=(
            settings.ig_weekend_gold_enabled and settings.apify_api_token is not None
        ),
    )
    service = MonitorService(
        repository,
        research_uow_factory,
        clock,
        id_generator,
        schedule,
    )
    fact_resolver = MonitorFactResolver(
        technical=market_facts.technical,
        a_share=market_facts.a_share,
        us_research=market_facts.us_research,
        us_context=market_facts.us_context,
        research_uow_factory=research_uow_factory,
    )
    judgment_provider = build_monitor_judgment_provider(settings)
    judgment_fallback = build_monitor_judgment_fallback_provider(settings)
    event_provider = build_monitor_event_analysis_provider(
        settings,
        resilience=llm_resilience,
    )
    judgment_service = (
        MonitorJudgmentService(
            repository,
            market_facts.market,
            judgment_provider,
            clock,
            id_generator,
            judgment_fallback,
        )
        if judgment_provider is not None
        else None
    )
    event_analysis_service = (
        MonitorEventAnalysisService(event_provider) if event_provider is not None else None
    )
    evaluation = MonitorEvaluationService(
        repository,
        market_facts.a_share,
        market_facts.market,
        risk,
        clock,
        id_generator,
        fact_resolver,
        judgment_service,
        session_calendars=schedule.session_calendars,
        provider_retry_attempts=3,
        provider_retry_delay_seconds=0.5,
        event_analysis_service=event_analysis_service,
    )
    return MonitoringBundle(
        us_calendar=us_calendar,
        schedule=schedule,
        service=service,
        evaluation=evaluation,
        judgment_provider=judgment_provider,
        judgment_fallback_provider=judgment_fallback,
        event_analysis_provider=event_provider,
    )


__all__ = ["MonitoringBundle", "build_monitoring_services"]
