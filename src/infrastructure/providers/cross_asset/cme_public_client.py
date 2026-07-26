"""CME Group public-reference adapter (free, no API key).

Authority for product specs (via versioned seeds with official URLs), contract
lifecycle from the public ProductCalendar endpoint, and EOD settlement/volume/OI
from the public Settlements endpoint. Delayed quotes are reference-only with a
disclosed ≥10 minute delay.

Never fabricates expiries or statistics. Failures are typed
``ProviderUnavailableError`` / ``NoMarketData`` / ``DataContractError``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.cross_asset.cme_identity import (
    CME_METAL_ROOTS,
    parse_cme_contract_code,
)
from domain.cross_asset.enums import (
    ContinuousAdjustment,
    ContractLifecycleStatus,
    RollRule,
)
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesProductDefinition,
)
from infrastructure.providers.cross_asset.cme_metal_product_seeds import (
    CME_GLOBEX_PRODUCT_IDS,
    SEED_SOURCE,
    all_seed_product_definitions,
    get_metal_product_seed,
    seed_product_definition,
)
from infrastructure.providers.cross_asset.cme_public_codecs import (
    CmeDelayedQuoteRow,
    decode_delayed_quotes,
    decode_product_calendar,
    decode_settlements,
    loads_cme_json,
)
from infrastructure.system.clock import SystemClock

_NY = ZoneInfo("America/New_York")
_HOST = "https://www.cmegroup.com"
_CALENDAR_PATH = "/CmeWS/mvc/ProductCalendar/Future/{product_id}"
_SETTLEMENTS_PATH = "/CmeWS/mvc/Settlements/Futures/Settlements/{product_id}/FUT"
_QUOTES_PATH = "/CmeWS/mvc/Quotes/Future/{product_id}/G"
# CME public delayed quotes are at least 10 minutes delayed.
_MIN_DELAY_SECONDS = 600
_REFERENCE_WARNINGS = (
    "CME_PUBLIC_REFERENCE_ONLY",
    "FUTURES_CONTRACT_NOT_SPOT",
    "OFFICIAL_SETTLEMENT_NOT_LAST_TRADE",
)
_SEED_WARNINGS = (
    "CME_PUBLIC_REFERENCE_ONLY",
    "FUTURES_CONTRACT_NOT_SPOT",
)
_JSON_CONTENT = ("application/json", "text/json", "text/plain", "*/*")
_CONTINUOUS_METHODOLOGY = "tp_continuous_v1"


def _meta(
    *,
    category: DataCategory,
    as_of: datetime,
    fetched_at: datetime,
    freshness: Freshness,
    session: TradingSession,
    data_delay_seconds: int | None,
    warnings: tuple[str, ...],
    adjustment: AdjustmentMethod | None = None,
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.CME_PUBLIC,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=fetched_at,
        freshness=freshness,
        session=session,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=adjustment,
        data_delay_seconds=data_delay_seconds,
        warnings=warnings,
    )


def _content_type_ok(headers: dict[str, str] | object) -> bool:
    if not isinstance(headers, dict):
        return False
    raw = headers.get("content-type") or headers.get("Content-Type")
    if not isinstance(raw, str) or not raw.strip():
        return False
    lowered = raw.split(";", 1)[0].strip().casefold()
    return any(token in lowered for token in _JSON_CONTENT)


def _product_key_parts(product_key: str) -> tuple[str, str]:
    if not isinstance(product_key, str) or ":" not in product_key:
        raise DataContractError(
            "product_key must match MARKET:ROOT",
            details={"field": "product_key", "rule": "format"},
        )
    market, root = product_key.split(":", 1)
    if market != Market.CME.value:
        raise DataContractError(
            "CME public adapter only serves CME product keys",
            details={"product_key": product_key, "rule": "market"},
        )
    root = root.strip().upper()
    if root not in CME_METAL_ROOTS:
        raise DataContractError(
            "unsupported CME metal root",
            details={
                "root": root,
                "allowed_roots": sorted(CME_METAL_ROOTS),
                "rule": "metal_roots_only",
            },
        )
    return market, root


def _ny_close(day: date) -> datetime:
    return datetime.combine(day, time(17, 0), tzinfo=_NY)


class CmePublicAdapter:
    """Implements futures reference + statistics ports over CME public endpoints."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 20.0,
        user_agent: str = "TradingPartner/1.0",
    ) -> None:
        if (
            not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be positive",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.CME_PUBLIC

    @property
    def provider_name(self) -> str:
        return VendorId.CME_PUBLIC.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.CME and category in {
            DataCategory.FUTURES_REFERENCE,
            DataCategory.FUTURES_STATISTICS,
        }

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "CME public adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        return now

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
        }

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "CME public endpoint rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "CME public endpoint access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "retryable": False,
                },
                retryable=False,
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "CME public endpoint HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str],
        operation: str,
    ) -> tuple[object, datetime]:
        request = HttpRequest(
            method="GET",
            url=f"{_HOST}{path}",
            params=params,
            headers=self._headers(),
            body=None,
            timeout_seconds=self._timeout_seconds,
        )
        response = await self._transport.send(request)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_http_status(response.status_code, operation=operation)
        if not _content_type_ok(dict(response.headers)):
            raise DataContractError(
                "CME public response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "content_type",
                },
            )
        return loads_cme_json(response.body, operation=operation), fetched_at

    async def get_product_definition(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[FuturesProductDefinition]:
        self._require_configured()
        self._require_as_of(as_of)
        _product_key_parts(product_key)
        definition = seed_product_definition(product_key)
        if definition is None:
            raise NoMarketData(
                "no CME metal product seed for product_key",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                },
            )
        if definition.definition_as_of > as_of and definition.valid_from > as_of:
            raise NoMarketData(
                "product definition not visible at as_of",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                },
            )
        fetched_at = self._clock.now()
        return ProviderSuccess(
            value=definition,
            meta=_meta(
                category=DataCategory.FUTURES_REFERENCE,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                session=TradingSession.UNKNOWN,
                data_delay_seconds=None,
                warnings=_SEED_WARNINGS,
            ),
        )

    async def list_contract_definitions(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FuturesContractDefinition, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        _, root = _product_key_parts(product_key)
        product = seed_product_definition(product_key)
        if product is None:
            raise NoMarketData(
                "no CME metal product seed for product_key",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                },
            )
        product_id_globex = CME_GLOBEX_PRODUCT_IDS[root]
        path = _CALENDAR_PATH.format(product_id=product_id_globex)
        try:
            payload, fetched_at = await self._get_json(
                path,
                params={},
                operation="product_calendar",
            )
        except (ProviderUnavailableError, ProviderRateLimitError, DataContractError):
            raise
        except Exception as exc:
            raise ProviderUnavailableError(
                "CME product calendar request failed",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "product_calendar",
                    "error_type": "transport",
                },
            ) from exc

        calendar_rows = decode_product_calendar(payload, root=root)
        if not calendar_rows:
            raise NoMarketData(
                "CME product calendar returned no outright contracts",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "FUTURES_CHAIN_UNAVAILABLE",
                },
            )

        as_of_date = as_of.astimezone(_NY).date()
        contracts: list[FuturesContractDefinition] = []
        for row in calendar_rows:
            expiration_at = (
                _ny_close(row.expiration_date) if row.expiration_date is not None else None
            )
            last_trade_at = (
                _ny_close(row.last_trade_date) if row.last_trade_date is not None else None
            )
            first_notice_at = (
                _ny_close(row.first_notice_date)
                if row.first_notice_date is not None
                else None
            )
            settlement_at = (
                _ny_close(row.settlement_date) if row.settlement_date is not None else None
            )
            status = ContractLifecycleStatus.ACTIVE
            lifecycle_end = row.last_trade_date or row.expiration_date
            if lifecycle_end is not None and lifecycle_end < as_of_date:
                status = ContractLifecycleStatus.EXPIRED
            instrument_id = build_instrument_id(
                AssetType.FUTURE, Market.CME, row.contract_code
            )
            contracts.append(
                FuturesContractDefinition(
                    instrument_id=instrument_id,
                    product_id=product.product_id,
                    contract_month=row.contract_month,
                    status=status,
                    definition_as_of=fetched_at if fetched_at <= as_of else as_of,
                    last_trade_at=last_trade_at,
                    expiration_at=expiration_at,
                    first_notice_at=first_notice_at,
                    settlement_at=settlement_at,
                    source=VendorId.CME_PUBLIC.value,
                )
            )
        contracts.sort(
            key=lambda c: (
                c.expiration_at or _ny_close(date.fromisoformat(f"{c.contract_month}-01")),
                c.instrument_id,
            )
        )
        return ProviderSuccess(
            value=tuple(contracts),
            meta=_meta(
                category=DataCategory.FUTURES_REFERENCE,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.DELAYED,
                session=TradingSession.UNKNOWN,
                data_delay_seconds=_MIN_DELAY_SECONDS,
                warnings=_REFERENCE_WARNINGS[:2],
            ),
        )

    async def resolve_continuous_mapping(
        self,
        series: ContinuousSeriesDefinition,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[ContinuousContractMapping, ...]]:
        """Resolve continuous→specific mappings for the requested window.

        Calendar rule uses product calendar order. Volume / open-interest rules
        require same-day settlements; when unavailable the adapter raises a
        typed ``ROLL_MAPPING_UNAVAILABLE`` error rather than guessing.
        """
        self._require_configured()
        self._require_as_of(as_of)
        require_aware_datetime(start, field_name="start")
        require_aware_datetime(end, field_name="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if series.adjustment is not ContinuousAdjustment.NONE:
            raise DataContractError(
                "Phase 3A only supports continuous adjustment=none",
                details={"adjustment": series.adjustment.value},
            )

        # Locate product_key from seed product_id.
        product_key: str | None = None
        for definition in all_seed_product_definitions():
            if definition.product_id == series.product_id:
                product_key = definition.product_key
                break
        if product_key is None:
            raise NoMarketData(
                "continuous series product is not a known CME metal seed",
                details={
                    "vendor": self.vendor_id.value,
                    "product_id": series.product_id,
                    "code": "ROLL_MAPPING_UNAVAILABLE",
                },
            )

        chain = await self.list_contract_definitions(product_key, as_of)
        contracts = chain.value
        if not contracts:
            raise NoMarketData(
                "no contracts available for continuous mapping",
                details={
                    "vendor": self.vendor_id.value,
                    "code": "ROLL_MAPPING_UNAVAILABLE",
                },
            )

        if series.roll_rule is RollRule.CALENDAR:
            active = [
                c
                for c in contracts
                if c.status is not ContractLifecycleStatus.EXPIRED
                and (
                    c.expiration_at is None
                    or c.expiration_at >= start
                )
            ]
            active.sort(
                key=lambda c: (
                    c.expiration_at
                    or _ny_close(date.fromisoformat(f"{c.contract_month}-01")),
                    c.instrument_id,
                )
            )
            if series.rank >= len(active):
                raise NoMarketData(
                    "calendar continuous rank exceeds available contracts",
                    details={
                        "vendor": self.vendor_id.value,
                        "rank": series.rank,
                        "available": len(active),
                        "code": "ROLL_MAPPING_UNAVAILABLE",
                    },
                )
            chosen = active[series.rank]
            mapping = ContinuousContractMapping(
                continuous_instrument_id=series.instrument_id,
                contract_instrument_id=chosen.instrument_id,
                effective_from=start,
                mapping_source=f"{VendorId.CME_PUBLIC.value}:calendar",
                effective_to=end if end > start else None,
            )
            fetched_at = self._clock.now()
            return ProviderSuccess(
                value=(mapping,),
                meta=_meta(
                    category=DataCategory.FUTURES_REFERENCE,
                    as_of=as_of,
                    fetched_at=fetched_at,
                    freshness=Freshness.DELAYED,
                    session=TradingSession.UNKNOWN,
                    data_delay_seconds=_MIN_DELAY_SECONDS,
                    warnings=_REFERENCE_WARNINGS[:2] + ("CONTINUOUS_FUTURES_ROLL_RISK",),
                ),
            )

        # volume / open_interest: need trade-date statistics for ranking.
        trade_date = min(as_of, end).astimezone(_NY).date()
        # Walk back a few sessions if needed (weekends).
        stats_success: ProviderSuccess[tuple[FuturesContractStatistics, ...]] | None = None
        for offset in range(0, 5):
            candidate = trade_date - timedelta(days=offset)
            try:
                stats_success = await self.get_contract_statistics(
                    tuple(c.instrument_id for c in contracts),
                    candidate,
                    as_of,
                )
                if stats_success.value:
                    trade_date = candidate
                    break
            except NoMarketData:
                continue
        if stats_success is None or not stats_success.value:
            raise NoMarketData(
                "volume/OI continuous mapping requires settlement statistics",
                details={
                    "vendor": self.vendor_id.value,
                    "roll_rule": series.roll_rule.value,
                    "code": "ROLL_MAPPING_UNAVAILABLE",
                },
            )

        by_id = {s.instrument_id: s for s in stats_success.value}
        ranked: list[tuple[Decimal, str]] = []
        for contract in contracts:
            if contract.status is ContractLifecycleStatus.EXPIRED:
                continue
            stat = by_id.get(contract.instrument_id)
            if stat is None:
                continue
            metric = (
                stat.session_volume
                if series.roll_rule is RollRule.VOLUME
                else stat.open_interest
            )
            if metric is None:
                continue
            ranked.append((metric, contract.instrument_id))
        # Higher volume/OI first; stable tie-break by instrument_id.
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if series.rank >= len(ranked):
            raise NoMarketData(
                "volume/OI continuous rank exceeds ranked contracts",
                details={
                    "vendor": self.vendor_id.value,
                    "rank": series.rank,
                    "available": len(ranked),
                    "code": "ROLL_MAPPING_UNAVAILABLE",
                },
            )
        chosen_id = ranked[series.rank][1]
        mapping = ContinuousContractMapping(
            continuous_instrument_id=series.instrument_id,
            contract_instrument_id=chosen_id,
            effective_from=start,
            mapping_source=(
                f"{VendorId.CME_PUBLIC.value}:{series.roll_rule.value}:{trade_date.isoformat()}"
            ),
            effective_to=end if end > start else None,
        )
        fetched_at = self._clock.now()
        return ProviderSuccess(
            value=(mapping,),
            meta=_meta(
                category=DataCategory.FUTURES_REFERENCE,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.DELAYED,
                session=TradingSession.UNKNOWN,
                data_delay_seconds=_MIN_DELAY_SECONDS,
                warnings=_REFERENCE_WARNINGS + ("CONTINUOUS_FUTURES_ROLL_RISK",),
            ),
        )

    async def get_contract_statistics(
        self,
        instrument_ids: tuple[str, ...],
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FuturesContractStatistics, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if type(trade_date) is not date:
            raise DataContractError(
                "trade_date must be a date",
                details={"field": "trade_date"},
            )
        if not instrument_ids:
            raise DataContractError(
                "instrument_ids must be non-empty",
                details={"field": "instrument_ids"},
            )

        # Group by root; CME settlements are per product.
        by_root: dict[str, list[str]] = {}
        for instrument_id in instrument_ids:
            code = parse_cme_contract_code(instrument_id.split(":", 2)[-1])
            by_root.setdefault(code.root, []).append(instrument_id)

        if len(by_root) != 1:
            raise DataContractError(
                "get_contract_statistics requires a single product root per call",
                details={
                    "roots": sorted(by_root),
                    "rule": "single_root",
                },
            )
        root = next(iter(by_root))
        product_id_globex = CME_GLOBEX_PRODUCT_IDS[root]
        path = _SETTLEMENTS_PATH.format(product_id=product_id_globex)
        # CME tradeDate wire form: MM/DD/YYYY
        trade_date_param = f"{trade_date.month:02d}/{trade_date.day:02d}/{trade_date.year:04d}"
        payload, fetched_at = await self._get_json(
            path,
            params={
                "tradeDate": trade_date_param,
                "strategy": "DEFAULT",
                "pageSize": "500",
            },
            operation="settlements",
        )
        document = decode_settlements(payload, root=root, trade_date=trade_date)
        if not document.rows:
            raise NoMarketData(
                "CME settlements returned no rows for trade_date",
                details={
                    "vendor": self.vendor_id.value,
                    "trade_date": trade_date.isoformat(),
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                },
            )

        wanted = set(instrument_ids)
        stats: list[FuturesContractStatistics] = []
        for row in document.rows:
            instrument_id = build_instrument_id(
                AssetType.FUTURE, Market.CME, row.contract_code
            )
            if instrument_id not in wanted:
                continue
            stats.append(
                FuturesContractStatistics(
                    instrument_id=instrument_id,
                    trade_date=document.trade_date,
                    settlement=row.settlement,
                    settlement_status=row.settlement_status,
                    session_volume=row.session_volume,
                    open_interest=row.open_interest,
                    published_at=document.published_at
                    if document.published_at <= as_of
                    else as_of,
                    source=VendorId.CME_PUBLIC.value,
                )
            )
        if not stats:
            raise NoMarketData(
                "CME settlements had no matching instruments",
                details={
                    "vendor": self.vendor_id.value,
                    "trade_date": trade_date.isoformat(),
                    "code": "NO_MARKET_DATA",
                },
            )
        # Publication is next-day for EOD; treat as delayed reference.
        return ProviderSuccess(
            value=tuple(stats),
            meta=_meta(
                category=DataCategory.FUTURES_STATISTICS,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.DELAYED,
                session=TradingSession.CLOSED,
                data_delay_seconds=_MIN_DELAY_SECONDS,
                warnings=_REFERENCE_WARNINGS,
            ),
        )

    async def list_delayed_quote_rows(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[CmeDelayedQuoteRow, ...]]:
        """Optional delayed quote chain (reference only, ≥10 min delay)."""
        self._require_configured()
        self._require_as_of(as_of)
        _, root = _product_key_parts(product_key)
        if get_metal_product_seed(product_key) is None:
            raise NoMarketData(
                "no CME metal product seed for product_key",
                details={"product_key": product_key},
            )
        product_id_globex = CME_GLOBEX_PRODUCT_IDS[root]
        path = _QUOTES_PATH.format(product_id=product_id_globex)
        payload, fetched_at = await self._get_json(
            path,
            params={},
            operation="delayed_quotes",
        )
        rows = decode_delayed_quotes(payload, root=root, as_of=as_of)
        if not rows:
            raise NoMarketData(
                "CME delayed quotes returned no rows",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "NO_MARKET_DATA",
                },
            )
        return ProviderSuccess(
            value=rows,
            meta=_meta(
                category=DataCategory.FUTURES_REFERENCE,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.DELAYED,
                session=TradingSession.UNKNOWN,
                data_delay_seconds=_MIN_DELAY_SECONDS,
                warnings=_REFERENCE_WARNINGS[:2] + ("BEST_EFFORT_PUBLIC_FEED_NO_SLA",),
            ),
        )


# Re-export seed helpers for service wiring.
__all__ = [
    "CmePublicAdapter",
    "SEED_SOURCE",
    "all_seed_product_definitions",
    "seed_product_definition",
]
