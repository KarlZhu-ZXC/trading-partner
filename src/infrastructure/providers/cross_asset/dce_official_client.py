"""DCE official publicweb adapter (free EOD, no API key).

Authority for LH product specs (versioned seed + official URL), specific
contract identities from ``contractInfo``, and EOD settlement/volume/OI from
``dayQuotes``. Runtime calls DCE directly; never depends on AKShare.

HTTP 401/403/412 map to a stable non-retryable access/entitlement error
(``DCE_OFFICIAL_ACCESS_RESTRICTED``). 429 and 5xx remain retryable. Never
solves CAPTCHA, browser-automates, or uses paid/third-party fallbacks.
"""

from __future__ import annotations

import json
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
from domain.cross_asset.dce_identity import (
    DCE_LH_PRODUCT_KEY,
    parse_dce_lh_instrument_id,
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
from infrastructure.providers.cross_asset.dce_lh_product_seeds import (
    all_seed_product_definitions,
    seed_product_definition,
)
from infrastructure.providers.cross_asset.dce_official_codecs import (
    decode_contract_info,
    decode_day_quotes,
    loads_dce_json,
)
from infrastructure.system.clock import SystemClock

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HOST = "http://www.dce.com.cn"
_CONTRACT_INFO_PATH = "/dcereport/publicweb/tradepara/contractInfo"
_DAY_QUOTES_PATH = "/dcereport/publicweb/dailystat/dayQuotes"
_ALLOWED_PATHS = frozenset({_CONTRACT_INFO_PATH, _DAY_QUOTES_PATH})
_MAX_BODY_BYTES = 8_000_000
_JSON_CONTENT = ("application/json", "text/json", "text/plain", "*/*")

_REFERENCE_WARNINGS = (
    "DCE_OFFICIAL_REFERENCE_ONLY",
    "DCE_OFFICIAL_EOD_ONLY",
    "FUTURES_CONTRACT_NOT_SPOT",
)
_STATS_WARNINGS = _REFERENCE_WARNINGS + ("OFFICIAL_SETTLEMENT_NOT_LAST_TRADE",)
_SEED_WARNINGS = (
    "DCE_OFFICIAL_REFERENCE_ONLY",
    "FUTURES_CONTRACT_NOT_SPOT",
)

_ACCESS_RESTRICTED_CODE = "DCE_OFFICIAL_ACCESS_RESTRICTED"


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
        vendor=VendorId.DCE_OFFICIAL,
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
    if market != Market.DCE.value:
        raise DataContractError(
            "DCE official adapter only serves DCE product keys",
            details={"product_key": product_key, "rule": "market"},
        )
    root = root.strip().upper()
    if product_key != DCE_LH_PRODUCT_KEY and f"DCE:{root}" != DCE_LH_PRODUCT_KEY:
        raise DataContractError(
            "unsupported DCE product root; Phase 3A-4 supports LH only",
            details={
                "root": root,
                "allowed_roots": ["LH"],
                "rule": "lh_only",
            },
        )
    if root != "LH":
        raise DataContractError(
            "unsupported DCE product root; Phase 3A-4 supports LH only",
            details={
                "root": root,
                "allowed_roots": ["LH"],
                "rule": "lh_only",
            },
        )
    return market, root


def _shanghai_close(day: date) -> datetime:
    return datetime.combine(day, time(15, 0), tzinfo=_SHANGHAI)


class DceOfficialAdapter:
    """Implements futures reference + statistics ports over DCE official EOD endpoints."""

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
        return VendorId.DCE_OFFICIAL

    @property
    def provider_name(self) -> str:
        return VendorId.DCE_OFFICIAL.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.DCE and category in {
            DataCategory.FUTURES_REFERENCE,
            DataCategory.FUTURES_STATISTICS,
        }

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "DCE official adapter is disabled",
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
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": self._user_agent,
            "Referer": "http://www.dce.com.cn/",
        }

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "DCE official endpoint rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "code": "PROVIDER_RATE_LIMIT_ERROR",
                },
            )
        # 401/403/412: anti-bot / entitlement / access restriction.
        # Non-retryable; never solve CAPTCHA or fall back to paid sources.
        if status_code in {401, 403, 412}:
            raise ProviderUnavailableError(
                "DCE official endpoint access restricted",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "access_restricted",
                    "code": _ACCESS_RESTRICTED_CODE,
                    "status_class": f"{status_code // 100}xx",
                    "retryable": False,
                },
                retryable=False,
                code=_ACCESS_RESTRICTED_CODE,
            )
        if status_code < 200 or status_code >= 300:
            # 5xx and other transport statuses remain retryable by default.
            raise ProviderUnavailableError(
                "DCE official endpoint HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    async def _post_json(
        self,
        path: str,
        *,
        payload: dict[str, object],
        operation: str,
    ) -> tuple[object, datetime]:
        if path not in _ALLOWED_PATHS:
            raise DataContractError(
                "DCE path is not in the fixed allowlist",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "path_allowlist",
                },
            )
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(body) > _MAX_BODY_BYTES:
            raise DataContractError(
                "DCE request body exceeds maximum size",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "body_size",
                },
            )
        request = HttpRequest(
            method="POST",
            url=f"{_HOST}{path}",
            params={},
            headers=self._headers(),
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        try:
            response = await self._transport.send(request)
        except (
            ProviderUnavailableError,
            ProviderRateLimitError,
            DataContractError,
            ProviderNotConfigured,
        ):
            raise
        except Exception as exc:
            raise ProviderUnavailableError(
                "DCE official request failed",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "transport",
                },
            ) from exc
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_http_status(response.status_code, operation=operation)
        if not _content_type_ok(dict(response.headers)):
            raise DataContractError(
                "DCE official response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "content_type",
                },
            )
        if len(response.body) > _MAX_BODY_BYTES:
            raise DataContractError(
                "DCE official response body exceeds maximum size",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "body_size",
                },
            )
        return loads_dce_json(response.body, operation=operation), fetched_at

    async def get_product_definition(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[FuturesProductDefinition]:
        self._require_configured()
        self._require_as_of(as_of)
        _product_key_parts(product_key)
        normalized = f"DCE:{product_key.split(':', 1)[1].strip().upper()}"
        definition = seed_product_definition(normalized)
        if definition is None:
            raise NoMarketData(
                "no DCE LH product seed for product_key",
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
        _product_key_parts(product_key)
        product = seed_product_definition(DCE_LH_PRODUCT_KEY)
        if product is None:
            raise NoMarketData(
                "no DCE LH product seed for product_key",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                },
            )
        payload, fetched_at = await self._post_json(
            _CONTRACT_INFO_PATH,
            payload={
                "lang": "zh",
                "tradeType": "1",
                "varietyId": "all",
            },
            operation="contract_info",
        )
        calendar_rows = decode_contract_info(payload, operation="contract_info")
        if not calendar_rows:
            raise NoMarketData(
                "DCE contractInfo returned no LH contracts",
                details={
                    "vendor": self.vendor_id.value,
                    "product_key": product_key,
                    "code": "FUTURES_CHAIN_UNAVAILABLE",
                },
            )

        as_of_date = as_of.astimezone(_SHANGHAI).date()
        contracts: list[FuturesContractDefinition] = []
        for row in calendar_rows:
            last_trade_at = (
                _shanghai_close(row.last_trade_date)
                if row.last_trade_date is not None
                else None
            )
            # Last trade is the practical expiry for physical LH futures.
            expiration_at = last_trade_at
            first_trade_at = (
                _shanghai_close(row.start_trade_date)
                if row.start_trade_date is not None
                else None
            )
            delivery_start = row.delivery_date
            delivery_end = row.delivery_date
            status = ContractLifecycleStatus.ACTIVE
            lifecycle_end = row.last_trade_date
            if lifecycle_end is not None and lifecycle_end < as_of_date:
                status = ContractLifecycleStatus.EXPIRED
            instrument_id = build_instrument_id(
                AssetType.FUTURE, Market.DCE, row.contract_code
            )
            contracts.append(
                FuturesContractDefinition(
                    instrument_id=instrument_id,
                    product_id=product.product_id,
                    contract_month=row.contract_month,
                    status=status,
                    definition_as_of=fetched_at if fetched_at <= as_of else as_of,
                    first_trade_at=first_trade_at,
                    last_trade_at=last_trade_at,
                    expiration_at=expiration_at,
                    delivery_start=delivery_start,
                    delivery_end=delivery_end,
                    source=VendorId.DCE_OFFICIAL.value,
                )
            )
        contracts.sort(
            key=lambda c: (
                c.expiration_at
                or _shanghai_close(date.fromisoformat(f"{c.contract_month}-01")),
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
                data_delay_seconds=None,
                warnings=_REFERENCE_WARNINGS,
            ),
        )

    async def resolve_continuous_mapping(
        self,
        series: ContinuousSeriesDefinition,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[ContinuousContractMapping, ...]]:
        """Calendar continuous only; never substitutes a main-continuous quote."""
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
        product_key: str | None = None
        for definition in all_seed_product_definitions():
            if definition.product_id == series.product_id:
                product_key = definition.product_key
                break
        if product_key is None:
            raise NoMarketData(
                "continuous series product is not the DCE LH seed",
                details={
                    "vendor": self.vendor_id.value,
                    "product_id": series.product_id,
                    "code": "ROLL_MAPPING_UNAVAILABLE",
                },
            )
        if series.roll_rule is not RollRule.CALENDAR:
            # Volume/OI continuous is supported via statistics; no main-cont.
            chain = await self.list_contract_definitions(product_key, as_of)
            contracts = chain.value
            trade_date = min(as_of, end).astimezone(_SHANGHAI).date()
            stats_success: ProviderSuccess[tuple[FuturesContractStatistics, ...]] | None
            stats_success = None
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
                    "volume/OI continuous mapping requires dayQuotes statistics",
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
                    f"{VendorId.DCE_OFFICIAL.value}:"
                    f"{series.roll_rule.value}:{trade_date.isoformat()}"
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
                    data_delay_seconds=None,
                    warnings=_REFERENCE_WARNINGS + ("CONTINUOUS_FUTURES_ROLL_RISK",),
                ),
            )

        chain = await self.list_contract_definitions(product_key, as_of)
        active = [
            c
            for c in chain.value
            if c.status is not ContractLifecycleStatus.EXPIRED
            and (c.expiration_at is None or c.expiration_at >= start)
        ]
        active.sort(
            key=lambda c: (
                c.expiration_at
                or _shanghai_close(date.fromisoformat(f"{c.contract_month}-01")),
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
            mapping_source=f"{VendorId.DCE_OFFICIAL.value}:calendar",
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
                data_delay_seconds=None,
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
        # Validate all requested instruments are DCE LH specifics.
        for instrument_id in instrument_ids:
            parse_dce_lh_instrument_id(instrument_id)

        trade_date_param = (
            f"{trade_date.year:04d}{trade_date.month:02d}{trade_date.day:02d}"
        )
        payload, fetched_at = await self._post_json(
            _DAY_QUOTES_PATH,
            payload={
                "contractId": "",
                "lang": "zh",
                "optionSeries": "",
                "statisticsType": "0",
                "tradeDate": trade_date_param,
                "tradeType": "1",
                "varietyId": "all",
            },
            operation="day_quotes",
        )
        document = decode_day_quotes(
            payload, trade_date=trade_date, operation="day_quotes"
        )
        if not document.rows:
            raise NoMarketData(
                "DCE dayQuotes returned no LH rows for trade_date",
                details={
                    "vendor": self.vendor_id.value,
                    "trade_date": trade_date.isoformat(),
                    "code": "NO_MARKET_DATA",
                },
            )

        wanted = set(instrument_ids)
        stats: list[FuturesContractStatistics] = []
        for row in document.rows:
            instrument_id = build_instrument_id(
                AssetType.FUTURE, Market.DCE, row.contract_code
            )
            if instrument_id not in wanted:
                continue
            published_at = document.published_at
            if published_at > as_of:
                published_at = as_of
            stats.append(
                FuturesContractStatistics(
                    instrument_id=instrument_id,
                    trade_date=document.trade_date,
                    settlement=row.settlement,
                    settlement_status=row.settlement_status,
                    session_volume=row.session_volume,
                    open_interest=row.open_interest,
                    published_at=published_at,
                    source=VendorId.DCE_OFFICIAL.value,
                )
            )
        if not stats:
            raise NoMarketData(
                "DCE dayQuotes had no matching LH instruments",
                details={
                    "vendor": self.vendor_id.value,
                    "trade_date": trade_date.isoformat(),
                    "code": "NO_MARKET_DATA",
                },
            )
        return ProviderSuccess(
            value=tuple(stats),
            meta=_meta(
                category=DataCategory.FUTURES_STATISTICS,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.DELAYED,
                session=TradingSession.CLOSED,
                data_delay_seconds=None,
                warnings=_STATS_WARNINGS,
            ),
        )
