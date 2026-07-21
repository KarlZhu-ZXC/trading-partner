"""A-share sentiment product aggregation service (Phase 1E E4b).

``AShareSentimentService.get`` fans out per requested source with all sources
optional (SENTIMENT / INTERACTIVE_QA / NEWS). Partial failures produce warnings
and empty typed collections while overall ``ok`` remains true.

Strict Router ``result_validator``s reject malicious adapter/cache payloads
before DTO conversion. Optional validator failures become normal partial
warnings (never silently accepted). Meta warnings are preserved.

News reuses the existing ``AShareNewsProvider`` router path — never vendor HTTP
from this service. SentimentSignal never claims authority or bullish scores.

``concept_heat`` is an instrument-scoped Eastmoney concept-hit result. It is not
a global concept leaderboard and is never synthesized from THS stock tags.

The service is bootstrapped behind ``a_share_get_facts(operation="sentiment")``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from application.dto.a_share import (
    AShareSentimentSnapshotDTO,
    InteractiveQAItemDTO,
    NewsItemDTO,
    SentimentSignalDTO,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import (
    AShareInteractiveProvider,
    AShareNewsProvider,
    AShareSentimentProvider,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import SNAPSHOT_NEWS_LOOKBACK_DAYS
from application.services.component_settlement import settle_router_component
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType, SentimentSourceType
from domain.a_share.models import InteractiveQAItem, NewsItem, SentimentSignal
from domain.common.enums import (
    AssetType,
    DataCategory,
    DataCriticality,
    Market,
    ReliabilityLevel,
    VendorId,
)
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# v4 invalidates cached THS/Eastmoney rows written before low-reliability meta
# warnings became mandatory provenance. The wire/cache codec is unchanged.
OP_SENTIMENT = "a_share.sentiment.v4"
OP_INTERACTIVE_QA = "a_share.interactive_qa.v1"
OP_NEWS = "a_share.news.v1"

_DEFAULT_SOURCES: tuple[SentimentSourceType, ...] = tuple(SentimentSourceType)
_DEFAULT_QA_LIMIT = 40
_DEFAULT_NEWS_LIMIT = 40

_SENTIMENT_COMPONENT_ORDER = tuple(
    AShareComponentType(source.value) for source in SentimentSourceType
)

_SENTIMENT_SOURCES = frozenset(
    {
        SentimentSourceType.THS_HOT,
        SentimentSourceType.EASTMONEY_HOT,
        SentimentSourceType.CONCEPT_HEAT,
    }
)

_SOURCE_VENDOR: dict[SentimentSourceType, VendorId] = {
    SentimentSourceType.EASTMONEY_HOT: VendorId.EASTMONEY,
    SentimentSourceType.THS_HOT: VendorId.THS,
}

# A-share NEWS routing vendors (config/vendor_chains.default.yaml + CLS/EM adapters).
# Multi-vendor allowlist — do not invent a single-vendor constraint.
_SUPPORTED_A_SHARE_NEWS_VENDORS: frozenset[VendorId] = frozenset({VendorId.EASTMONEY, VendorId.CLS})

_ESTABLISHED_META_WARNING_CODES = frozenset(
    {
        "LOW_RELIABILITY_MARKET_SIGNAL",
        "PUBLICATION_TIME_UNKNOWN_EXCLUDED",
    }
)
_META_WARNING_MESSAGES: dict[str, str] = {
    "LOW_RELIABILITY_MARKET_SIGNAL": "Sentiment source carries low reliability",
    "PUBLICATION_TIME_UNKNOWN_EXCLUDED": ("Records with unknown publication time excluded"),
}


def _require_exact_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date (not datetime)",
            details={"field": field, "rule": "exact_date_type"},
        )
    return value


def _source_policy(source: SentimentSourceType) -> tuple[DataCategory, ToolDataPolicy]:
    if source in _SENTIMENT_SOURCES:
        category = DataCategory.SENTIMENT
        return category, ToolDataPolicy(
            tool_name=f"a_share_get_sentiment_snapshot.{source.value}",
            required_categories=(),
            optional_categories=(category,),
            category_chain_overrides={},
        )
    if source is SentimentSourceType.INTERACTIVE_QA:
        category = DataCategory.INTERACTIVE_QA
        return category, ToolDataPolicy(
            tool_name="a_share_get_sentiment_snapshot.interactive_qa",
            required_categories=(),
            optional_categories=(category,),
            category_chain_overrides={},
        )
    if source in {
        SentimentSourceType.COMPANY_NEWS,
        SentimentSourceType.MARKET_NEWS,
    }:
        category = DataCategory.NEWS
        return category, ToolDataPolicy(
            tool_name=f"a_share_get_sentiment_snapshot.{source.value}",
            required_categories=(),
            optional_categories=(category,),
            category_chain_overrides={},
        )
    raise DataContractError(
        "unknown sentiment source",
        details={"field": "sources", "source": getattr(source, "value", str(source))},
    )


@dataclass(frozen=True, slots=True)
class AShareSentimentResult:
    """Typed result wrapper for sentiment product aggregation."""

    ok: bool
    data: AShareSentimentSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(self.provenance, order=_SENTIMENT_COMPONENT_ORDER)
        if type(self.ok) is not bool:
            raise DataContractError(
                "ok must be exact bool",
                details={"field": "ok", "rule": "type", "type": type(self.ok).__name__},
            )
        if not isinstance(self.warnings, tuple):
            raise DataContractError(
                "warnings must be a tuple of WarningInfo",
                details={
                    "field": "warnings",
                    "rule": "type",
                    "type": type(self.warnings).__name__,
                },
            )
        for idx, warning in enumerate(self.warnings):
            if not isinstance(warning, WarningInfo):
                raise DataContractError(
                    "warnings elements must be WarningInfo",
                    details={"field": "warnings", "index": idx, "rule": "type"},
                )
        if self.ok:
            if self.data is None:
                raise DataContractError(
                    "AShareSentimentResult ok=True requires data non-None",
                    details={"field": "data", "rule": "ok_true_data_required"},
                )
            if self.error is not None:
                raise DataContractError(
                    "AShareSentimentResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            validate_data_provenance(self.data, self.provenance)
        else:
            if self.data is not None:
                raise DataContractError(
                    "AShareSentimentResult ok=False requires data is None",
                    details={"field": "data", "rule": "ok_false_data_none"},
                )
            if self.error is None or not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "AShareSentimentResult ok=False requires typed TradingPartnerError",
                    details={
                        "field": "error",
                        "rule": "ok_false_error_required",
                        "type": type(self.error).__name__,
                    },
                )


class AShareSentimentService:
    """E4b product service: hot lists, interactive QA, and news by source."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        sentiment_codec: ProviderCacheCodec[tuple[SentimentSignal, ...]],
        interactive_qa_codec: ProviderCacheCodec[tuple[InteractiveQAItem, ...]],
        news_codec: ProviderCacheCodec[tuple[NewsItem, ...]],
        qa_limit: int = _DEFAULT_QA_LIMIT,
        news_limit: int = _DEFAULT_NEWS_LIMIT,
        news_lookback_days: int = SNAPSHOT_NEWS_LOOKBACK_DAYS,
    ) -> None:
        if router is None or clock is None:
            raise DataContractError(
                "router and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        for name, codec in (
            ("sentiment_codec", sentiment_codec),
            ("interactive_qa_codec", interactive_qa_codec),
            ("news_codec", news_codec),
        ):
            if codec is None or not hasattr(codec, "codec_id"):
                raise DataContractError(
                    f"{name} must be a ProviderCacheCodec",
                    details={"field": name, "rule": "required"},
                )
        if not isinstance(qa_limit, int) or isinstance(qa_limit, bool) or qa_limit < 1:
            raise DataContractError(
                "qa_limit must be a positive int",
                details={"field": "qa_limit", "rule": "positive"},
            )
        if not isinstance(news_limit, int) or isinstance(news_limit, bool) or news_limit < 1:
            raise DataContractError(
                "news_limit must be a positive int",
                details={"field": "news_limit", "rule": "positive"},
            )
        if (
            not isinstance(news_lookback_days, int)
            or isinstance(news_lookback_days, bool)
            or news_lookback_days < 1
        ):
            raise DataContractError(
                "news_lookback_days must be a positive int",
                details={"field": "news_lookback_days", "rule": "positive"},
            )
        self._router = router
        self._clock = clock
        self._sentiment_codec = sentiment_codec
        self._interactive_qa_codec = interactive_qa_codec
        self._news_codec = news_codec
        self._qa_limit = qa_limit
        self._news_limit = news_limit
        self._news_lookback_days = news_lookback_days

    def _resolve_sources(
        self, sources: tuple[SentimentSourceType, ...]
    ) -> tuple[SentimentSourceType, ...]:
        if not isinstance(sources, tuple):
            raise DataContractError(
                "sources must be a tuple of SentimentSourceType",
                details={"field": "sources", "rule": "type"},
            )
        if not sources:
            return _DEFAULT_SOURCES
        seen: set[SentimentSourceType] = set()
        requested: set[SentimentSourceType] = set()
        for source in sources:
            if not isinstance(source, SentimentSourceType):
                raise DataContractError(
                    "sources elements must be SentimentSourceType",
                    details={"field": "sources", "rule": "type"},
                )
            if source in seen:
                raise DataContractError(
                    "sources must not contain duplicates",
                    details={"field": "sources", "rule": "unique"},
                )
            seen.add(source)
            requested.add(source)
        # Frozen enum order for deterministic per-source output.
        return tuple(s for s in SentimentSourceType if s in requested)

    def _require_instrument(self, instrument: Instrument | None) -> None:
        if instrument is None:
            return
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.A_SHARE:
            raise DataContractError(
                "instrument market must be A_SHARE",
                details={"field": "instrument", "rule": "market"},
            )
        if instrument.asset_type is AssetType.OPTION:
            raise DataContractError(
                "OPTION is not supported for sentiment",
                details={
                    "field": "instrument",
                    "rule": "asset_support",
                    "asset_type": instrument.asset_type.value,
                },
            )

    async def get(
        self,
        *,
        instrument: Instrument | None = None,
        sources: tuple[SentimentSourceType, ...] = (),
        trade_date: date | None = None,
        as_of: datetime,
    ) -> AShareSentimentResult:
        require_aware_datetime(as_of, field_name="as_of")
        # Sample clock once (no stepping-clock drift across fan-out).
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        self._require_instrument(instrument)
        ordered_sources = self._resolve_sources(sources)
        if trade_date is None:
            effective_trade_date = as_of.astimezone(_SHANGHAI).date()
        else:
            effective_trade_date = _require_exact_date(trade_date, field="trade_date")
        as_of_local = as_of.astimezone(_SHANGHAI).date()
        if effective_trade_date > as_of_local:
            raise DataContractError(
                "trade_date must not be later than the Asia/Shanghai local date of as_of",
                details={
                    "field": "trade_date",
                    "rule": "trade_date_not_after_as_of_local",
                },
            )

        warnings: list[WarningInfo] = []
        tasks: dict[SentimentSourceType, asyncio.Task[RouterExecutionResult[Any]]] = {}

        async with asyncio.TaskGroup() as tg:
            for source in ordered_sources:
                tasks[source] = tg.create_task(
                    settle_router_component(self._fetch_source(
                        source,
                        instrument=instrument,
                        trade_date=effective_trade_date,
                        as_of=as_of,
                    ))
                )

        signals: list[SentimentSignal] = []
        interactive_qa: list[InteractiveQAItem] = []
        company_news: list[NewsItem] = []
        market_news: list[NewsItem] = []
        provenance_items: list[AShareComponentProvenance] = []

        # Merge in ordered source sequence (not completion order).
        for source in ordered_sources:
            result = tasks[source].result()
            self._merge_warnings(warnings, result)
            if not result.ok or result.value is None:
                self._partial(warnings, source.value)
                continue
            value = result.value
            if result.meta is not None:
                is_signal = source in _SENTIMENT_SOURCES
                provenance_items.append(
                    component_provenance(
                        AShareComponentType(source.value),
                        result.meta,
                        value,
                        empty_reliability=ReliabilityLevel.LOW if is_signal else None,
                        empty_authoritative=False if is_signal else None,
                    )
                )
            if source in _SENTIMENT_SOURCES:
                if not isinstance(value, tuple):
                    self._partial(warnings, source.value)
                    continue
                signals.extend(value)
            elif source is SentimentSourceType.INTERACTIVE_QA:
                if not isinstance(value, tuple):
                    self._partial(warnings, source.value)
                    continue
                interactive_qa.extend(value)
            elif source is SentimentSourceType.COMPANY_NEWS:
                if not isinstance(value, tuple):
                    self._partial(warnings, source.value)
                    continue
                company_news.extend(value)
            elif source is SentimentSourceType.MARKET_NEWS:
                if not isinstance(value, tuple):
                    self._partial(warnings, source.value)
                    continue
                market_news.extend(value)

        # Deterministic global signal order: source enum, rank, instrument_id.
        source_order = {s: i for i, s in enumerate(SentimentSourceType)}
        signals.sort(
            key=lambda s: (
                source_order.get(s.source_type, 999),
                s.rank if s.rank is not None else 10**9,
                s.instrument_id or "",
            )
        )
        interactive_qa.sort(key=lambda q: (-q.answered_at.timestamp(), q.qa_key))
        company_news.sort(key=lambda n: (-n.published_at.timestamp(), n.news_key))
        market_news.sort(key=lambda n: (-n.published_at.timestamp(), n.news_key))
        provenance = tuple(provenance_items)

        dto = AShareSentimentSnapshotDTO(
            instrument_id=instrument.instrument_id if instrument is not None else None,
            trade_date=effective_trade_date,
            as_of=as_of,
            sources=ordered_sources,
            signals=tuple(SentimentSignalDTO.from_domain(s) for s in signals),
            interactive_qa=tuple(InteractiveQAItemDTO.from_domain(q) for q in interactive_qa),
            company_news=tuple(NewsItemDTO.from_domain(n) for n in company_news),
            market_news=tuple(NewsItemDTO.from_domain(n) for n in market_news),
            provenance=provenance_dtos(provenance),
        )
        # All sources optional — overall success even when every source is empty.
        return AShareSentimentResult(
            ok=True,
            data=dto,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

    async def _fetch_source(
        self,
        source: SentimentSourceType,
        *,
        instrument: Instrument | None,
        trade_date: date,
        as_of: datetime,
    ) -> RouterExecutionResult[Any]:
        category, policy = _source_policy(source)
        instrument_key = instrument.instrument_id if instrument is not None else "market"
        if source in _SENTIMENT_SOURCES:
            fingerprint = build_a_share_fingerprint(
                OP_SENTIMENT,
                instrument_key,
                {
                    "source": source.value,
                    "trade_date": trade_date.isoformat(),
                },
                as_of,
            )

            async def _call_sent(
                adapter: CategoryProvider,
            ) -> ProviderSuccess[tuple[SentimentSignal, ...]]:
                if not isinstance(adapter, AShareSentimentProvider):
                    raise DataContractError(
                        "adapter does not implement required A-share protocol",
                        details={"category": category.value},
                    )
                return await adapter.get_sentiment_signals(
                    instrument,
                    trade_date=trade_date,
                    sources=(source,),
                    as_of=as_of,
                )

            def _validator_sent(
                success: ProviderSuccess[tuple[SentimentSignal, ...]],
            ) -> None:
                self._validate_sentiment(
                    success,
                    source=source,
                    trade_date=trade_date,
                    instrument=instrument,
                    as_of=as_of,
                )

            return await self._router.execute(
                market=Market.A_SHARE,
                category=category,
                call=_call_sent,
                operation_name=OP_SENTIMENT,
                request_fingerprint=fingerprint,
                instrument=instrument,
                as_of=as_of,
                tool_policy=policy,
                bypass_cache=False,
                cache_codec=self._sentiment_codec,
                result_validator=_validator_sent,
            )

        if source is SentimentSourceType.INTERACTIVE_QA:
            if instrument is None:
                # Protocol requires instrument — typed empty via optional failure.
                return RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=DataCriticality.OPTIONAL,
                    meta=None,
                    attempts=(),
                    warnings=(),
                    error=DataContractError(
                        "interactive_qa requires instrument",
                        details={"field": "instrument", "rule": "required"},
                    ),
                )
            fingerprint = build_a_share_fingerprint(
                OP_INTERACTIVE_QA,
                instrument.instrument_id,
                {"limit": str(self._qa_limit)},
                as_of,
            )

            async def _call_qa(
                adapter: CategoryProvider,
            ) -> ProviderSuccess[tuple[InteractiveQAItem, ...]]:
                if not isinstance(adapter, AShareInteractiveProvider):
                    raise DataContractError(
                        "adapter does not implement required A-share protocol",
                        details={"category": category.value},
                    )
                return await adapter.get_interactive_qa(
                    instrument, limit=self._qa_limit, as_of=as_of
                )

            def _validator_qa(
                success: ProviderSuccess[tuple[InteractiveQAItem, ...]],
            ) -> None:
                self._validate_qa(success, as_of=as_of)

            return await self._router.execute(
                market=Market.A_SHARE,
                category=category,
                call=_call_qa,
                operation_name=OP_INTERACTIVE_QA,
                request_fingerprint=fingerprint,
                instrument=instrument,
                as_of=as_of,
                tool_policy=policy,
                bypass_cache=False,
                cache_codec=self._interactive_qa_codec,
                result_validator=_validator_qa,
            )

        # NEWS sources — reuse AShareNewsProvider only.
        end = as_of
        start = as_of - timedelta(days=self._news_lookback_days)
        news_instrument = instrument if source is SentimentSourceType.COMPANY_NEWS else None
        if source is SentimentSourceType.COMPANY_NEWS and instrument is None:
            return RouterExecutionResult(
                value=None,
                ok=False,
                criticality=DataCriticality.OPTIONAL,
                meta=None,
                attempts=(),
                warnings=(),
                error=DataContractError(
                    "company_news requires instrument",
                    details={"field": "instrument", "rule": "required"},
                ),
            )
        fingerprint = build_a_share_fingerprint(
            OP_NEWS,
            news_instrument.instrument_id if news_instrument is not None else "market",
            {
                "source": source.value,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": str(self._news_limit),
            },
            as_of,
        )

        async def _call_news(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[NewsItem, ...]]:
            if not isinstance(adapter, AShareNewsProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={"category": category.value},
                )
            return await adapter.get_news(
                news_instrument,
                start=start,
                end=end,
                limit=self._news_limit,
                as_of=as_of,
            )

        def _validator_news(
            success: ProviderSuccess[tuple[NewsItem, ...]],
        ) -> None:
            self._validate_news(
                success,
                as_of=as_of,
                start=start,
                end=end,
                source=source,
            )

        return await self._router.execute(
            market=Market.A_SHARE,
            category=category,
            call=_call_news,
            operation_name=OP_NEWS,
            request_fingerprint=fingerprint,
            instrument=news_instrument,
            as_of=as_of,
            tool_policy=policy,
            bypass_cache=False,
            cache_codec=self._news_codec,
            result_validator=_validator_news,
        )

    # --- strict validators (Router result_validator) --------------------------

    def _require_success(
        self, success: object, *, expected_category: DataCategory
    ) -> ProviderSuccess[object]:
        if type(success) is not ProviderSuccess:
            raise DataContractError(
                "provider call must return exact ProviderSuccess",
                details={
                    "field": "result",
                    "rule": "type",
                    "type": type(success).__name__,
                },
            )
        if success.meta.category is not expected_category:
            raise DataContractError(
                "meta.category must match expected category",
                details={
                    "field": "meta.category",
                    "rule": "category",
                    "expected": expected_category.value,
                },
            )
        return success

    def _validate_sentiment(
        self,
        success: ProviderSuccess[tuple[SentimentSignal, ...]],
        *,
        source: SentimentSourceType,
        trade_date: date,
        instrument: Instrument | None,
        as_of: datetime | None = None,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.SENTIMENT)
        # Response-meta provenance is required even for empty tuples. Concept
        # heat is explicitly Eastmoney-owned although it has no generic source map.
        expected_vendor = _SOURCE_VENDOR.get(source)
        if source is SentimentSourceType.CONCEPT_HEAT:
            expected_vendor = VendorId.EASTMONEY
        if expected_vendor is not None and success.meta.vendor is not expected_vendor:
            raise DataContractError(
                "sentiment meta.vendor must match requested source vendor",
                details={
                    "field": "meta.vendor",
                    "rule": "meta_vendor",
                    "expected": expected_vendor.value,
                },
            )
        value = success.value
        if not isinstance(value, tuple):
            raise DataContractError(
                "success.value must be a tuple of SentimentSignal",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(value).__name__,
                },
            )
        if source is SentimentSourceType.CONCEPT_HEAT and instrument is None:
            raise DataContractError(
                "concept heat requires a request instrument",
                details={"field": "instrument", "rule": "required"},
            )
        seen_ids: set[str] = set()
        concept_ids: set[str] = set()
        concept_labels: set[str] = set()
        concept_observed_at: datetime | None = None
        prev_key: tuple[int, str] | None = None
        for idx, item in enumerate(value):
            if type(item) is not SentimentSignal:
                raise DataContractError(
                    "sentiment elements must be exact SentimentSignal",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.source_type is not source:
                raise DataContractError(
                    "sentiment source_type must match requested source",
                    details={
                        "field": "source_type",
                        "index": idx,
                        "rule": "source",
                        "expected": source.value,
                    },
                )
            if item.trade_date != trade_date:
                raise DataContractError(
                    "sentiment trade_date must match request",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "trade_date",
                    },
                )
            if expected_vendor is not None and item.source_vendor is not expected_vendor:
                raise DataContractError(
                    "sentiment source_vendor must map to requested source",
                    details={
                        "field": "source_vendor",
                        "index": idx,
                        "rule": "source_vendor",
                        "expected": expected_vendor.value,
                    },
                )
            if item.is_authoritative is not False:
                raise DataContractError(
                    "SentimentSignal.is_authoritative must be exact False",
                    details={
                        "field": "is_authoritative",
                        "index": idx,
                        "rule": "not_authoritative",
                    },
                )
            if (
                instrument is not None
                and item.instrument_id is not None
                and item.instrument_id != instrument.instrument_id
            ):
                raise DataContractError(
                    "sentiment instrument_id must match request filter",
                    details={
                        "field": "instrument_id",
                        "index": idx,
                        "rule": "identity",
                    },
                )
            # No other source mixed into this request tuple.
            if item.source_type not in _SENTIMENT_SOURCES:
                raise DataContractError(
                    "unexpected non-sentiment source in SENTIMENT result",
                    details={"field": "source_type", "index": idx, "rule": "source"},
                )
            if source is SentimentSourceType.CONCEPT_HEAT:
                if (
                    instrument is None
                    or item.instrument_id is not None
                    or item.rank != idx + 1
                    or item.rank_change is not None
                ):
                    raise DataContractError(
                        "concept heat identity/rank mismatch",
                        details={"index": idx, "rule": "concept_contract"},
                    )
                if (
                    item.source_item_id is None
                    or re.fullmatch(r"BK[0-9]+", item.source_item_id) is None
                    or item.label is None
                    or item.concept_tags != (item.label,)
                    or item.observed_at is None
                    or item.heat_value is None
                    or not item.heat_value.is_finite()
                    or item.heat_value < 0
                    or item.reliability is not ReliabilityLevel.LOW
                ):
                    raise DataContractError(
                        "concept heat field mismatch",
                        details={"index": idx, "rule": "concept_contract"},
                    )
                if item.source_item_id in concept_ids or item.label in concept_labels:
                    raise DataContractError(
                        "concept heat identity must be unique",
                        details={"index": idx, "rule": "unique"},
                    )
                concept_ids.add(item.source_item_id)
                concept_labels.add(item.label)
                require_aware_datetime(item.observed_at, field_name="observed_at")
                cutoff = success.meta.as_of if as_of is None else as_of
                if (
                    item.observed_at.date() != trade_date
                    or item.observed_at > cutoff
                    or (
                        concept_observed_at is not None
                        and item.observed_at != concept_observed_at
                    )
                ):
                    raise DataContractError(
                        "concept heat observed time mismatch",
                        details={"index": idx, "rule": "observed_time"},
                    )
                concept_observed_at = item.observed_at
            iid = item.instrument_id or ""
            if iid and iid in seen_ids:
                raise DataContractError(
                    "sentiment instrument_id must be unique within source",
                    details={"field": "instrument_id", "index": idx, "rule": "unique"},
                )
            if iid:
                seen_ids.add(iid)
            rank = item.rank if item.rank is not None else 10**9
            order_key = (rank, iid)
            if prev_key is not None and order_key < prev_key:
                raise DataContractError(
                    "sentiment must be sorted by rank then instrument_id ascending",
                    details={"field": "rank", "index": idx, "rule": "sorted"},
                )
            prev_key = order_key

    def _validate_qa(
        self,
        success: ProviderSuccess[tuple[InteractiveQAItem, ...]],
        *,
        as_of: datetime,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.INTERACTIVE_QA)
        # Response-meta provenance: CNINFO only, including empty tuples.
        if success.meta.vendor is not VendorId.CNINFO:
            raise DataContractError(
                "interactive_qa meta.vendor must be CNINFO",
                details={
                    "field": "meta.vendor",
                    "rule": "meta_vendor",
                    "expected": VendorId.CNINFO.value,
                },
            )
        value = success.value
        if not isinstance(value, tuple):
            raise DataContractError(
                "success.value must be a tuple of InteractiveQAItem",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(value).__name__,
                },
            )
        seen: set[str] = set()
        prev_key: tuple[float, str] | None = None
        for idx, item in enumerate(value):
            if type(item) is not InteractiveQAItem:
                raise DataContractError(
                    "QA elements must be exact InteractiveQAItem",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.answered_at > as_of:
                raise DataContractError(
                    "answered_at must be <= as_of",
                    details={
                        "field": "answered_at",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            if item.asked_at is not None:
                if item.asked_at > as_of:
                    raise DataContractError(
                        "asked_at must be <= as_of",
                        details={
                            "field": "asked_at",
                            "index": idx,
                            "rule": "as_of_cutoff",
                        },
                    )
                if item.asked_at > item.answered_at:
                    raise DataContractError(
                        "asked_at must be <= answered_at",
                        details={
                            "field": "asked_at",
                            "index": idx,
                            "rule": "range_order",
                        },
                    )
            if item.qa_key in seen:
                raise DataContractError(
                    "qa_key must be unique",
                    details={"field": "qa_key", "index": idx, "rule": "unique"},
                )
            seen.add(item.qa_key)
            sort_key = (-item.answered_at.timestamp(), item.qa_key)
            if prev_key is not None and sort_key < prev_key:
                raise DataContractError(
                    "QA must be sorted answered_at desc, qa_key asc",
                    details={"field": "answered_at", "index": idx, "rule": "sorted"},
                )
            prev_key = sort_key

    def _validate_news(
        self,
        success: ProviderSuccess[tuple[NewsItem, ...]],
        *,
        as_of: datetime,
        start: datetime,
        end: datetime,
        source: SentimentSourceType,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.NEWS)
        # Multi-vendor NEWS chain (EASTMONEY, CLS); reject unsupported meta vendors.
        if success.meta.vendor not in _SUPPORTED_A_SHARE_NEWS_VENDORS:
            raise DataContractError(
                "news meta.vendor must be a supported A-share news provider",
                details={
                    "field": "meta.vendor",
                    "rule": "meta_vendor",
                    "allowed": tuple(sorted(v.value for v in _SUPPORTED_A_SHARE_NEWS_VENDORS)),
                },
            )
        value = success.value
        if not isinstance(value, tuple):
            raise DataContractError(
                "success.value must be a tuple of NewsItem",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(value).__name__,
                },
            )
        # NewsItem has no instrument_id field: company vs market scope is enforced
        # by adapter query (instrument vs None) and service instrument routing.
        del source  # scope enforced at fetch boundary
        seen: set[str] = set()
        prev_key: tuple[float, str] | None = None
        for idx, item in enumerate(value):
            if type(item) is not NewsItem:
                raise DataContractError(
                    "news elements must be exact NewsItem",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.published_at < start or item.published_at > end:
                raise DataContractError(
                    "news published_at outside requested [start,end]",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "window",
                    },
                )
            if item.published_at > as_of:
                raise DataContractError(
                    "news published_at must be <= as_of",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            if item.news_key in seen:
                raise DataContractError(
                    "news_key must be unique",
                    details={"field": "news_key", "index": idx, "rule": "unique"},
                )
            seen.add(item.news_key)
            sort_key = (-item.published_at.timestamp(), item.news_key)
            if prev_key is not None and sort_key < prev_key:
                raise DataContractError(
                    "news must be sorted published_at desc, news_key asc",
                    details={"field": "published_at", "index": idx, "rule": "sorted"},
                )
            prev_key = sort_key

    @staticmethod
    def _partial(warnings: list[WarningInfo], source: str) -> None:
        if not any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in warnings):
            warnings.append(
                WarningInfo(
                    code="PARTIAL_A_SHARE_SNAPSHOT",
                    message="One or more optional sentiment sources failed",
                    details={"source": source},
                )
            )

    @staticmethod
    def _merge_warnings(warnings: list[WarningInfo], result: RouterExecutionResult[object]) -> None:
        for w in result.warnings:
            if w not in warnings:
                warnings.append(w)
        if result.meta is not None:
            for code in result.meta.warnings:
                if code not in _ESTABLISHED_META_WARNING_CODES:
                    continue
                if any(x.code == code for x in warnings):
                    continue
                warnings.append(
                    WarningInfo(
                        code=code,
                        message=_META_WARNING_MESSAGES[code],
                        details={},
                    )
                )
