"""Build provider adapters, shared transports, and routing infrastructure."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.account_provider import AccountProvider
from application.ports.clock import Clock
from application.ports.commodity_spot_provider import CommoditySpotProvider
from application.ports.http_transport import HttpTransport
from application.ports.id_generator import IdGenerator
from application.ports.provider_route_history_store import ProviderRouteHistoryStore
from application.ports.secret_redactor import SecretRedactor
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from domain.common.enums import VendorId
from domain.watchlist.enums import WatchlistSource
from infrastructure.config.settings import AppSettings
from infrastructure.config.vendor_chain import YamlVendorChainConfig
from infrastructure.persistence.provider_state_backend import build_provider_state_backend
from infrastructure.persistence.reddit_state_store import build_reddit_state_store
from infrastructure.providers.a_share.cls import CLSAShareAdapter
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    EastmoneyRequestGate,
    get_production_eastmoney_request_gate,
)
from infrastructure.providers.a_share.exchanges import (
    SseAShareDisclosureAdapter,
    SzseAShareDisclosureAdapter,
)
from infrastructure.providers.a_share.hkex import HkexNorthboundAdapter
from infrastructure.providers.a_share.iwencai import IwencaiAShareAdapter
from infrastructure.providers.a_share.nahs import NahsHogCycleAdapter
from infrastructure.providers.a_share.sina import SinaAShareAdapter
from infrastructure.providers.a_share.tencent import TencentAShareAdapter
from infrastructure.providers.a_share.ths import ThsAShareAdapter
from infrastructure.providers.a_share.trading_calendar import (
    load_default_a_share_trading_calendar,
)
from infrastructure.providers.account.manual_csv import ManualCsvAccountAdapter
from infrastructure.providers.account.moomoo import MoomooAccountAdapter
from infrastructure.providers.account.schwab import SchwabAccountAdapter
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.httpx_transport import HttpxTransport
from infrastructure.providers.common.null_category_provider import NullCategoryProvider
from infrastructure.providers.common.rate_limiter import ProviderRateLimiter
from infrastructure.providers.cross_asset.cme_public_client import CmePublicAdapter
from infrastructure.providers.cross_asset.dce_official_client import DceOfficialAdapter
from infrastructure.providers.cross_asset.dukascopy_client import DukascopySpotAdapter
from infrastructure.providers.cross_asset.ig_weekend_gold import (
    IGWeekendGoldApifyAdapter,
    WeekendGoldFallbackSpotAdapter,
)
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDRateLimiter
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine
from infrastructure.providers.us.alpha_vantage_research import AlphaVantageResearchAdapter
from infrastructure.providers.us.eastmoney_futures import EastmoneyMetalFuturesAdapter
from infrastructure.providers.us.fred import FredMacroAdapter
from infrastructure.providers.us.moomoo_community import MoomooCommunityHeatAdapter
from infrastructure.providers.us.moomoo_sentiment import MoomooSentimentAdapter
from infrastructure.providers.us.polymarket import PolymarketPredictionAdapter
from infrastructure.providers.us.reddit import RedditSentimentAdapter
from infrastructure.providers.us.sec_research import SECResearchAdapter
from infrastructure.providers.us.sina_futures import SinaMetalFuturesAdapter
from infrastructure.providers.us.yahoo_finance_research import YahooFinanceResearchAdapter
from infrastructure.providers.watchlist.manual_csv import ManualCsvWatchlistAdapter
from infrastructure.providers.watchlist.moomoo import MoomooWatchlistAdapter
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)


@dataclass(frozen=True, slots=True)
class ProviderCompositionOverrides:
    """Optional deterministic seams supplied by the top-level composition root."""

    a_share_transport: HttpTransport | None = None
    eastmoney_gate: EastmoneyRequestGate | None = None
    a_share_calendar: AShareTradingCalendar | None = None
    watchlist_provider: WatchlistSourceProvider | None = None


@dataclass(frozen=True, slots=True)
class ProviderInfrastructure:
    """Provider graph plus only the adapters needed by application composition."""

    registry: VendorRegistry
    chain_config: YamlVendorChainConfig
    router_engine: ProviderRouterEngine
    route_history_store: ProviderRouteHistoryStore
    a_share_calendar: AShareTradingCalendar
    a_share_transport: HttpTransport
    owned_a_share_transport: HttpTransport | None
    owned_cross_asset_transport: HttpTransport | None
    cme_public: CmePublicAdapter
    dce_official: DceOfficialAdapter
    dukascopy: DukascopySpotAdapter
    commodity_spot: CommoditySpotProvider
    schwab_account: SchwabAccountAdapter
    moomoo_account: MoomooAccountAdapter
    manual_account: ManualCsvAccountAdapter
    account_providers: dict[VendorId, AccountProvider]
    watchlist_source: WatchlistSourceProvider


def enabled_account_provider_order(
    candidates: tuple[VendorId, ...],
    enabled_sources: tuple[str, ...],
) -> tuple[VendorId, ...]:
    """Keep configured account-source selection separate from route precedence."""

    enabled = frozenset(enabled_sources)
    return tuple(vendor for vendor in candidates if vendor.name in enabled)


def build_provider_infrastructure(
    settings: AppSettings,
    *,
    engine: Engine,
    clock: Clock,
    id_generator: IdGenerator,
    secret_redactor: SecretRedactor,
    overrides: ProviderCompositionOverrides | None = None,
) -> ProviderInfrastructure:
    """Construct every runtime Provider without importing application services."""

    overrides = overrides or ProviderCompositionOverrides()
    owned_a_share_transport: HttpTransport | None = None
    owned_cross_asset_transport: HttpTransport | None = None
    if overrides.a_share_transport is None:
        owned_a_share_transport = HttpxTransport(
            max_response_bytes=settings.http_max_response_bytes,
            timeout_seconds=settings.provider_timeout_market_seconds,
        )
    a_share_transport = overrides.a_share_transport or owned_a_share_transport
    assert a_share_transport is not None

    cross_asset_transport = a_share_transport
    if settings.provider_proxy_url is not None and overrides.a_share_transport is None:
        owned_cross_asset_transport = HttpxTransport(
            max_response_bytes=settings.http_max_response_bytes,
            timeout_seconds=settings.provider_timeout_market_seconds,
            proxy_url=settings.provider_proxy_url,
        )
        cross_asset_transport = owned_cross_asset_transport

    calendar = overrides.a_share_calendar or load_default_a_share_trading_calendar()
    eastmoney_gate = overrides.eastmoney_gate or get_production_eastmoney_request_gate(
        min_interval_seconds=settings.eastmoney_min_interval_seconds,
        jitter_seconds=settings.eastmoney_jitter_seconds,
    )
    registry = VendorRegistry()
    registry.register(VendorId.NULL, NullCategoryProvider())
    market_timeout = settings.provider_timeout_market_seconds
    registry.register(
        VendorId.TENCENT,
        TencentAShareAdapter(
            a_share_transport,
            calendar=calendar,
            clock=clock,
            enabled=settings.tencent_enabled,
            timeout_seconds=market_timeout,
            max_fresh_seconds=settings.a_share_max_fresh_seconds,
            max_delayed_seconds=settings.a_share_max_delayed_seconds,
        ),
    )
    registry.register(
        VendorId.EASTMONEY,
        EastmoneyAShareAdapter(
            a_share_transport,
            eastmoney_gate,
            calendar=calendar,
            clock=clock,
            enabled=settings.eastmoney_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
            max_fresh_seconds=settings.a_share_max_fresh_seconds,
            max_delayed_seconds=settings.a_share_max_delayed_seconds,
        ),
    )
    registry.register(
        VendorId.SINA,
        SinaAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.sina_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
            max_fresh_seconds=settings.a_share_max_fresh_seconds,
            max_delayed_seconds=settings.a_share_max_delayed_seconds,
        ),
    )
    registry.register(
        VendorId.CNINFO,
        CninfoAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.cninfo_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    registry.register(
        VendorId.NAHS,
        NahsHogCycleAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.nahs_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    registry.register(
        VendorId.THS,
        ThsAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.ths_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    registry.register(
        VendorId.CLS,
        CLSAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.cls_enabled,
            timeout_seconds=market_timeout,
        ),
    )
    registry.register(
        VendorId.SSE,
        SseAShareDisclosureAdapter(
            a_share_transport,
            clock=clock,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    registry.register(
        VendorId.SZSE,
        SzseAShareDisclosureAdapter(
            a_share_transport,
            clock=clock,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    registry.register(
        VendorId.HKEX,
        HkexNorthboundAdapter(
            a_share_transport,
            clock=clock,
            timeout_seconds=market_timeout,
        ),
    )
    registry.register(
        VendorId.IWENCAI,
        IwencaiAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.iwencai_enabled,
            api_key=settings.iwencai_api_key,
            base_url=settings.iwencai_base_url,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    registry.register(
        VendorId.YFINANCE,
        YahooFinanceResearchAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.yfinance_enabled,
            timeout_seconds=market_timeout,
            breadth_timeout_seconds=settings.provider_timeout_us_breadth_seconds,
            max_fresh_seconds=settings.us_max_fresh_seconds,
            max_delayed_seconds=settings.us_max_delayed_seconds,
        ),
    )
    registry.register(
        VendorId.SINA_FUTURES,
        SinaMetalFuturesAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.sina_enabled,
            timeout_seconds=market_timeout,
        ),
    )
    registry.register(
        VendorId.EASTMONEY_FUTURES,
        EastmoneyMetalFuturesAdapter(
            a_share_transport,
            eastmoney_gate,
            clock=clock,
            enabled=settings.eastmoney_enabled,
            timeout_seconds=market_timeout,
        ),
    )
    cme_public = CmePublicAdapter(
        cross_asset_transport,
        clock=clock,
        enabled=True,
        timeout_seconds=market_timeout,
    )
    dce_official = DceOfficialAdapter(
        cross_asset_transport,
        clock=clock,
        enabled=True,
        timeout_seconds=market_timeout,
    )
    dukascopy = DukascopySpotAdapter(
        cross_asset_transport,
        clock=clock,
        enabled=settings.dukascopy_enabled,
        api_key=settings.dukascopy_api_key,
        timeout_seconds=market_timeout,
        proxy_configured=settings.provider_proxy_url is not None,
    )
    ig_weekend_gold = IGWeekendGoldApifyAdapter(
        a_share_transport,
        clock=clock,
        enabled=settings.ig_weekend_gold_enabled,
        api_token=settings.apify_api_token,
        actor_id=settings.ig_weekend_gold_actor_id,
        cache_ttl_seconds=settings.ig_weekend_gold_cache_ttl_seconds,
        max_charge_usd=settings.ig_weekend_gold_max_charge_usd,
        timeout_seconds=settings.ig_weekend_gold_timeout_seconds,
    )
    commodity_spot = WeekendGoldFallbackSpotAdapter(
        dukascopy,
        ig_weekend_gold,
        clock=clock,
    )
    registry.register(VendorId.CME_PUBLIC, cme_public)
    registry.register(VendorId.DCE_OFFICIAL, dce_official)
    registry.register(VendorId.DUKASCOPY, dukascopy)
    registry.register(VendorId.IG_WEEKEND_GOLD, ig_weekend_gold)
    registry.register(
        VendorId.ALPHA_VANTAGE,
        AlphaVantageResearchAdapter(
            a_share_transport,
            api_keys=settings.alpha_vantage_api_keys,
            clock=clock,
            enabled=settings.alpha_vantage_enabled,
            timeout_seconds=market_timeout,
            max_fresh_seconds=settings.us_max_fresh_seconds,
            max_delayed_seconds=settings.us_max_delayed_seconds,
        ),
    )
    registry.register(
        VendorId.SEC_EDGAR,
        SECResearchAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.sec_edgar_enabled,
            sec_user_agent=settings.sec_user_agent,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    registry.register(
        VendorId.FRED,
        FredMacroAdapter(
            a_share_transport,
            api_key=settings.fred_api_key,
            clock=clock,
            enabled=settings.fred_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    registry.register(
        VendorId.MOOMOO_FEED,
        MoomooSentimentAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.moomoo_sentiment_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    registry.register(
        VendorId.REDDIT,
        RedditSentimentAdapter(
            a_share_transport,
            user_agent=settings.reddit_user_agent,
            subreddits=tuple(settings.reddit_subreddits.split(",")),
            clock=clock,
            enabled=settings.reddit_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
            min_interval_seconds=settings.reddit_min_interval_seconds,
            cache_ttl_seconds=settings.reddit_cache_ttl_seconds,
            cooldown_default_seconds=settings.reddit_cooldown_default_seconds,
            cooldown_max_seconds=settings.reddit_cooldown_max_seconds,
            apify_enabled=settings.reddit_apify_enabled,
            apify_api_token=settings.apify_api_token,
            apify_actor_id=settings.reddit_apify_actor_id,
            apify_subreddits=tuple(settings.reddit_apify_subreddits.split(",")),
            apify_lookback_days=settings.reddit_apify_lookback_map,
            apify_max_charge_usd=settings.reddit_apify_max_charge_usd,
            state_store=build_reddit_state_store(engine, clock, secret_redactor),
        ),
    )
    registry.register(
        VendorId.POLYMARKET,
        PolymarketPredictionAdapter(
            cross_asset_transport,
            clock=clock,
            enabled=settings.polymarket_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )

    moomoo_limiter = MoomooOpenDRateLimiter(
        settings.post_market_sync_lock_path.parent / "moomoo_opend_rate_limit.log"
    )
    registry.register(
        VendorId.MOOMOO,
        MoomooCommunityHeatAdapter(
            enabled=settings.moomoo_community_heat_enabled,
            host=settings.moomoo_host,
            port=settings.moomoo_port,
            clock=clock,
            opend_rate_limiter=moomoo_limiter,
        ),
    )
    moomoo_account = MoomooAccountAdapter(
        id_generator,
        enabled="MOOMOO" in settings.holdings_sources,
        host=settings.moomoo_host,
        port=settings.moomoo_port,
        account_ids=tuple(
            item.strip() for item in settings.moomoo_account_ids.split(",") if item.strip()
        ),
        clock=clock,
        opend_rate_limiter=moomoo_limiter,
    )
    manual_account = ManualCsvAccountAdapter(
        settings.manual_holdings_csv_path if "MANUAL_CSV" in settings.holdings_sources else None,
        id_generator,
        clock=clock,
    )
    schwab_account = SchwabAccountAdapter(
        id_generator,
        enabled="SCHWAB" in settings.holdings_sources,
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        redirect_uri=settings.schwab_redirect_uri,
        token_path=settings.schwab_token_path,
        account_hashes=tuple(
            item.strip() for item in settings.schwab_account_hashes.split(",") if item.strip()
        ),
        clock=clock,
    )
    registry.register(VendorId.SCHWAB, schwab_account)
    registry.register(VendorId.MANUAL_CSV, manual_account)

    watchlist_source = overrides.watchlist_provider
    if watchlist_source is None:
        if settings.watchlist_source == WatchlistSource.MOOMOO.value:
            watchlist_source = MoomooWatchlistAdapter(
                enabled=True,
                host=settings.moomoo_host,
                port=settings.moomoo_port,
                clock=clock,
                opend_rate_limiter=moomoo_limiter,
                security_corrections=MoomooSecurityCorrections.load_default(),
            )
        else:
            watchlist_source = ManualCsvWatchlistAdapter(
                settings.manual_watchlist_csv_path,
                default_group=settings.watchlist_default_group,
                clock=clock,
            )

    state_backend = build_provider_state_backend(engine, clock, secret_redactor)
    router_engine = ProviderRouterEngine(
        registry=registry,
        cache_store=state_backend.cache_store,
        health_store=state_backend.health_store,
        route_history_store=state_backend.route_history_store,
        rate_limiter=ProviderRateLimiter(
            state_backend.rate_limit_store,
            clock,
            max_wait_seconds=settings.provider_rate_limit_max_wait_seconds,
        ),
        circuit_breaker=CircuitBreaker(
            clock,
            failure_threshold=settings.circuit_failure_threshold,
            recovery_timeout_seconds=settings.circuit_recovery_timeout_seconds,
            half_open_max_calls=settings.circuit_half_open_max_calls,
        ),
        clock=clock,
        id_generator=id_generator,
        settings=settings,
    )
    return ProviderInfrastructure(
        registry=registry,
        chain_config=YamlVendorChainConfig.load(settings.vendor_chain_path),
        router_engine=router_engine,
        route_history_store=state_backend.route_history_store,
        a_share_calendar=calendar,
        a_share_transport=a_share_transport,
        owned_a_share_transport=owned_a_share_transport,
        owned_cross_asset_transport=owned_cross_asset_transport,
        cme_public=cme_public,
        dce_official=dce_official,
        dukascopy=dukascopy,
        commodity_spot=commodity_spot,
        schwab_account=schwab_account,
        moomoo_account=moomoo_account,
        manual_account=manual_account,
        account_providers={
            VendorId.SCHWAB: schwab_account,
            VendorId.MOOMOO: moomoo_account,
            VendorId.MANUAL_CSV: manual_account,
        },
        watchlist_source=watchlist_source,
    )
