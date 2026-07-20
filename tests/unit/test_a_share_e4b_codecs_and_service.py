"""E4b codecs, fingerprints, validators, and limit/sentiment service unit tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from application.dto.a_share_provenance import provenance_dtos
from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.dto.tool_envelope import WarningInfo
from application.services.a_share_limit_up_service import (
    OP_LIMIT_CONTEXT,
    OP_LIMIT_REASON_TAGS,
    AShareLimitUpResult,
    AShareLimitUpService,
)
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_sentiment_service import (
    OP_SENTIMENT,
    AShareSentimentResult,
    AShareSentimentService,
)
from domain.a_share.enums import LimitPoolType, SentimentSourceType
from domain.a_share.models import (
    InteractiveQAItem,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    NewsItem,
    SentimentSignal,
)
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError, TradingPartnerError
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.codecs import (
    E4B_CODEC_IDS,
    interactive_qa_codec,
    limit_context_codec,
    news_codec,
    sentiment_codec,
)

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
TRADE_DATE = date(2024, 1, 16)


def _equity(symbol: str = "600519.SH") -> Instrument:
    return Instrument(
        instrument_id=f"equity:A_SHARE:{symbol}",
        symbol=symbol,
        name="test",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )


def _meta(
    category: DataCategory,
    *,
    vendor: VendorId = VendorId.EASTMONEY,
    warnings: tuple[str, ...] = (),
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=vendor,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=warnings,
    )


def _entry(
    *,
    pool: LimitPoolType = LimitPoolType.LIMIT_UP,
    code: str = "600519.SH",
    consecutive: int | None = 1,
    tags: tuple[str, ...] = (),
    vendor: VendorId = VendorId.EASTMONEY,
    reliability: ReliabilityLevel | None = None,
    trade_date: date = TRADE_DATE,
) -> LimitPoolEntry:
    if reliability is None:
        reliability = ReliabilityLevel.LOW if vendor is VendorId.THS else ReliabilityLevel.MEDIUM
    return LimitPoolEntry(
        pool_type=pool,
        trade_date=trade_date,
        instrument_id=f"equity:A_SHARE:{code}",
        name="test",
        last=Decimal("10"),
        change_percent=Decimal("10"),
        consecutive_limit_count=consecutive,
        days_and_boards=None,
        first_seal_at=None,
        last_seal_at=None,
        seal_amount_cny=None,
        broken_count=None,
        industry=None,
        reason_tags=tags,
        source_vendor=vendor,
        reliability=reliability,
    )


def _context(
    entries: tuple[LimitPoolEntry, ...] | None = None,
    *,
    trade_date: date = TRADE_DATE,
) -> LimitUpContext:
    if entries is None:
        # Frozen pool enum order then instrument_id.
        entries = (
            _entry(code="000001.SZ", consecutive=1),
            _entry(code="600519.SH", consecutive=2),
        )
    limit_up = tuple(e for e in entries if e.pool_type is LimitPoolType.LIMIT_UP)
    limit_down = sum(1 for e in entries if e.pool_type is LimitPoolType.LIMIT_DOWN)
    broken = sum(1 for e in entries if e.pool_type is LimitPoolType.BROKEN_LIMIT)
    by_count: dict[int, list[str]] = {}
    for e in limit_up:
        if e.consecutive_limit_count is None:
            continue
        by_count.setdefault(e.consecutive_limit_count, []).append(e.instrument_id)
    ladder = tuple(
        LimitUpLadderRung(
            consecutive_limit_count=c,
            instrument_count=len(set(ids)),
            instrument_ids=tuple(sorted(set(ids))),
        )
        for c, ids in sorted(by_count.items())
    )
    max_c = max(by_count) if by_count else None
    broken_rate = None
    if limit_up and broken:
        denom = len(limit_up) + broken
        broken_rate = (Decimal(broken) / Decimal(denom)).quantize(Decimal("0.0001"))
    elif broken and not limit_up:
        # only broken without limit_up in entries — rate None unless both pools
        pass
    return LimitUpContext(
        trade_date=trade_date,
        entries=entries,
        limit_up_count=len(limit_up),
        limit_down_count=limit_down,
        broken_limit_count=broken,
        broken_rate=broken_rate,
        max_consecutive_count=max_c,
        promotion_rate=None,
        ladder=ladder,
    )


def _enrichment_context(
    entries: tuple[LimitPoolEntry, ...] | None = None,
) -> LimitUpContext:
    if entries is None:
        entries = (
            _entry(
                code="600519.SH",
                consecutive=None,
                tags=("ths:白酒",),
                vendor=VendorId.THS,
            ),
        )
    return LimitUpContext(
        trade_date=TRADE_DATE,
        entries=entries,
        limit_up_count=len(entries),
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=None,
        promotion_rate=None,
        ladder=(),
    )


def _signal(
    source: SentimentSourceType = SentimentSourceType.EASTMONEY_HOT,
    rank: int = 1,
    *,
    code: str = "600519.SH",
    vendor: VendorId | None = None,
) -> SentimentSignal:
    if vendor is None:
        vendor = VendorId.THS if source is SentimentSourceType.THS_HOT else VendorId.EASTMONEY
    return SentimentSignal(
        source_type=source,
        trade_date=TRADE_DATE,
        instrument_id=f"equity:A_SHARE:{code}",
        rank=rank,
        rank_change=0,
        heat_value=None,
        concept_tags=(),
        label=None,
        source_vendor=vendor,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
    )


def test_e4b_codec_ids_complete() -> None:
    expected = frozenset(
        {
            "a_share_limit_context.v1",
            "a_share_sentiment.v2",
            "a_share_interactive_qa.v1",
        }
    )
    assert expected == E4B_CODEC_IDS


def _cache_entry(
    payload: str,
    *,
    category: DataCategory,
    vendor: VendorId = VendorId.EASTMONEY,
) -> Any:
    from application.dto.provider_state import CacheEntry

    return CacheEntry(
        key="v1|A_SHARE|e4b|fp",
        market=Market.A_SHARE,
        category=category,
        instrument_id=None,
        as_of=AS_OF,
        fetched_at=AS_OF,
        expires_at=AS_OF + timedelta(hours=1),
        freshness=Freshness.UNKNOWN,
        vendor=vendor,
        payload_json=payload,
    )


def test_limit_context_codec_roundtrip_no_float() -> None:
    codec = limit_context_codec()
    success = ProviderSuccess(value=_context(), meta=_meta(DataCategory.LIMIT_UP))
    encoded = codec.encode(success)
    assert "pickle" not in encoded
    decoded = codec.decode(_cache_entry(encoded, category=DataCategory.LIMIT_UP))
    assert decoded.value.limit_up_count == 2
    assert decoded.value.entries[0].last == Decimal("10")


def test_sentiment_and_qa_codec_roundtrip() -> None:
    sc = sentiment_codec()
    success = ProviderSuccess(
        value=(_signal(),),
        meta=_meta(DataCategory.SENTIMENT),
    )
    decoded = sc.decode(_cache_entry(sc.encode(success), category=DataCategory.SENTIMENT))
    assert decoded.value[0].rank == 1
    assert decoded.value[0].is_authoritative is False

    qa = InteractiveQAItem(
        qa_key="k1",
        question="q?",
        asked_at=None,
        answer="a",
        answered_at=AS_OF - timedelta(hours=1),
        source_url=None,
    )
    qc = interactive_qa_codec()
    q_success = ProviderSuccess(
        value=(qa,),
        meta=_meta(DataCategory.INTERACTIVE_QA, vendor=VendorId.CNINFO),
    )
    q_decoded = qc.decode(
        _cache_entry(
            qc.encode(q_success),
            category=DataCategory.INTERACTIVE_QA,
            vendor=VendorId.CNINFO,
        )
    )
    assert q_decoded.value[0].qa_key == "k1"


def test_fingerprint_stability_no_secrets() -> None:
    a = build_a_share_fingerprint(
        OP_LIMIT_CONTEXT,
        "market",
        {"pools": "limit_up,broken_limit", "trade_date": "2024-01-16"},
        AS_OF,
    )
    b = build_a_share_fingerprint(
        OP_LIMIT_CONTEXT,
        "market",
        {"trade_date": "2024-01-16", "pools": "limit_up,broken_limit"},
        AS_OF,
    )
    assert a == b
    assert "cookie" not in a.lower()
    assert "token" not in a.lower()
    s = build_a_share_fingerprint(
        OP_SENTIMENT,
        "equity:A_SHARE:600519.SH",
        {"source": "eastmoney_hot", "trade_date": "2024-01-16"},
        AS_OF,
    )
    assert s.startswith("v1|a_share.sentiment.v4|")


class _FakeCalendar:
    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5

    def previous_trading_day(self, day: date) -> date:
        d = day
        while True:
            d = d - timedelta(days=1)
            if d.weekday() < 5:
                return d

    def sessions_for(self, day: date) -> tuple:
        return ()

    @property
    def version(self) -> str:
        return "test"


class _FakeRouter:
    """Apply result_validator like the real engine so optional failures surface."""

    def __init__(self, results: dict[str, RouterExecutionResult[Any]]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
        self.calls.append(kwargs)
        key = kwargs["operation_name"]
        if key in self.results:
            result = self.results[key]
        else:
            cat = kwargs["category"]
            result = None
            for r in self.results.values():
                if r.meta is not None and r.meta.category is cat:
                    result = r
                    break
            if result is None:
                result = RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=DataCriticality.OPTIONAL,
                    meta=None,
                    attempts=(),
                    warnings=(),
                    error=DataContractError("missing fake result", details={}),
                )
        validator = kwargs.get("result_validator")
        if result.ok and result.value is not None and validator is not None:
            try:
                success = ProviderSuccess(value=result.value, meta=result.meta)
                validator(success)
            except TradingPartnerError as exc:
                return RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=result.criticality,
                    meta=None,
                    attempts=(),
                    warnings=result.warnings,
                    error=exc,
                )
        return result


def _ok(
    value: Any,
    *,
    category: DataCategory,
    vendor: VendorId = VendorId.EASTMONEY,
    meta_warnings: tuple[str, ...] = (),
    result_warnings: tuple[WarningInfo, ...] = (),
) -> RouterExecutionResult[Any]:
    items = getattr(value, "entries", value)
    if not isinstance(items, tuple):
        items = (items,)
    if (
        any(getattr(item, "reliability", None) is ReliabilityLevel.LOW for item in items)
        and "LOW_RELIABILITY_MARKET_SIGNAL" not in meta_warnings
    ):
        meta_warnings = (*meta_warnings, "LOW_RELIABILITY_MARKET_SIGNAL")
    return RouterExecutionResult(
        value=value,
        ok=True,
        criticality=DataCriticality.CORE
        if category is DataCategory.LIMIT_UP
        else DataCriticality.OPTIONAL,
        meta=_meta(category, vendor=vendor, warnings=meta_warnings),
        attempts=(),
        warnings=result_warnings,
        error=None,
    )


def _fail(
    category: DataCategory = DataCategory.SENTIMENT,
    *,
    result_warnings: tuple[WarningInfo, ...] = (),
) -> RouterExecutionResult[Any]:
    return RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.OPTIONAL,
        meta=None,
        attempts=(),
        warnings=result_warnings,
        error=DataContractError("optional failed", details={"category": category.value}),
    )


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._now


def _limit_svc(router: _FakeRouter, clock: _FixedClock | None = None) -> AShareLimitUpService:
    return AShareLimitUpService(
        router=router,  # type: ignore[arg-type]
        clock=clock or _FixedClock(AS_OF),
        calendar=_FakeCalendar(),  # type: ignore[arg-type]
        limit_context_codec=limit_context_codec(),
    )


def _sent_svc(router: _FakeRouter, clock: _FixedClock | None = None) -> AShareSentimentService:
    return AShareSentimentService(
        router=router,  # type: ignore[arg-type]
        clock=clock or _FixedClock(AS_OF),
        sentiment_codec=sentiment_codec(),
        interactive_qa_codec=interactive_qa_codec(),
        news_codec=news_codec(),
    )


# --- Limit-up service ---------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_up_service_default_pools_and_enrichment() -> None:
    # Full four-pool primary with empty non-LIMIT_UP pools: broken_rate is 0.0000
    # when both LIMIT_UP and BROKEN_LIMIT are requested and denom > 0.
    up = _entry(code="600519.SH", tags=())
    ctx = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(up,),
        limit_up_count=1,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=Decimal("0.0000"),
        max_consecutive_count=1,
        promotion_rate=None,
        ladder=(
            LimitUpLadderRung(
                consecutive_limit_count=1,
                instrument_count=1,
                instrument_ids=("equity:A_SHARE:600519.SH",),
            ),
        ),
    )
    enrich = _enrichment_context()
    router = _FakeRouter(
        {
            OP_LIMIT_CONTEXT: _ok(ctx, category=DataCategory.LIMIT_UP),
            OP_LIMIT_REASON_TAGS: _ok(enrich, category=DataCategory.LIMIT_UP, vendor=VendorId.THS),
        }
    )
    result = await _limit_svc(router).get(trade_date=TRADE_DATE, pools=(), as_of=AS_OF)
    assert result.ok is True
    assert result.data is not None
    assert tuple(item.component.value for item in result.provenance) == (
        "limit_context",
        "limit_reason_tags",
    )
    assert tuple(item.vendor for item in result.data.provenance) == (
        VendorId.EASTMONEY,
        VendorId.THS,
    )
    assert tuple(item.meta for item in result.provenance) == (
        router.results[OP_LIMIT_CONTEXT].meta,
        router.results[OP_LIMIT_REASON_TAGS].meta,
    )
    assert result.data.provenance == provenance_dtos(result.provenance)
    assert result.data.pools == tuple(LimitPoolType)
    assert result.data.context.entries[0].reason_tags == ("ths:白酒",)
    assert result.data.context.entries[0].source_vendor is VendorId.EASTMONEY
    # Factual summary not mutated by enrichment.
    assert result.data.context.limit_up_count == 1
    assert result.data.context.broken_rate == Decimal("0.0000")


@pytest.mark.asyncio
async def test_limit_up_service_required_failure_and_pool_subset() -> None:
    enrichment = _ok(
        _enrichment_context(),
        category=DataCategory.LIMIT_UP,
        vendor=VendorId.THS,
    )
    router = _FakeRouter(
        {
            OP_LIMIT_CONTEXT: _fail(DataCategory.LIMIT_UP),
            OP_LIMIT_REASON_TAGS: enrichment,
        }
    )
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.data is None
    assert isinstance(result.error, DataContractError)
    assert tuple(item.component.value for item in result.provenance) == ("limit_reason_tags",)
    assert result.provenance[0].meta is enrichment.meta


@pytest.mark.asyncio
async def test_limit_up_service_malicious_primary_fails_required() -> None:
    """Malicious adapter/cache primary fails before DTO conversion."""
    bad = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(_entry(vendor=VendorId.THS),),  # wrong primary vendor
        limit_up_count=1,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=1,
        promotion_rate=None,
        ladder=(
            LimitUpLadderRung(
                consecutive_limit_count=1,
                instrument_count=1,
                instrument_ids=("equity:A_SHARE:600519.SH",),
            ),
        ),
    )
    router = _FakeRouter({OP_LIMIT_CONTEXT: _ok(bad, category=DataCategory.LIMIT_UP)})
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.data is None
    assert isinstance(result.error, DataContractError)
    assert (result.error.details or {}).get("rule") == "primary_vendor"


@pytest.mark.asyncio
async def test_limit_up_service_malicious_enrichment_becomes_partial() -> None:
    ctx = _context(entries=(_entry(code="600519.SH", tags=()),))
    # Enrichment claims factual ladder — must be rejected as optional partial.
    bad_enrich = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(_entry(tags=("ths:x",), vendor=VendorId.THS),),
        limit_up_count=1,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=1,
        promotion_rate=None,
        ladder=(
            LimitUpLadderRung(
                consecutive_limit_count=1,
                instrument_count=1,
                instrument_ids=("equity:A_SHARE:600519.SH",),
            ),
        ),
    )
    router = _FakeRouter(
        {
            OP_LIMIT_CONTEXT: _ok(ctx, category=DataCategory.LIMIT_UP),
            OP_LIMIT_REASON_TAGS: _ok(
                bad_enrich, category=DataCategory.LIMIT_UP, vendor=VendorId.THS
            ),
        }
    )
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.context.entries[0].reason_tags == ()
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    assert tuple(item.component.value for item in result.provenance) == ("limit_context",)
    assert result.provenance[0].meta is router.results[OP_LIMIT_CONTEXT].meta
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_limit_up_service_rejects_duplicates_and_future_as_of() -> None:
    router = _FakeRouter({})
    svc = _limit_svc(router)
    with pytest.raises(DataContractError):
        await svc.get(
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP, LimitPoolType.LIMIT_UP),
            as_of=AS_OF,
        )
    with pytest.raises(DataContractError):
        await svc.get(
            trade_date=TRADE_DATE,
            pools=(),
            as_of=AS_OF + timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_limit_up_service_samples_clock_once() -> None:
    ctx = _context(entries=(_entry(code="600519.SH"),))
    router = _FakeRouter(
        {
            OP_LIMIT_CONTEXT: _ok(ctx, category=DataCategory.LIMIT_UP),
            OP_LIMIT_REASON_TAGS: _ok(
                _enrichment_context(),
                category=DataCategory.LIMIT_UP,
                vendor=VendorId.THS,
            ),
        }
    )
    clock = _FixedClock(AS_OF)
    await _limit_svc(router, clock).get(
        trade_date=TRADE_DATE, pools=(LimitPoolType.LIMIT_UP,), as_of=AS_OF
    )
    assert clock.calls == 1


@pytest.mark.asyncio
async def test_limit_up_taskgroup_out_of_order_deterministic() -> None:
    ctx = _context(entries=(_entry(code="600519.SH", tags=()),))
    enrich = _enrichment_context()

    class SlowPrimary(_FakeRouter):
        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            if kwargs["operation_name"] == OP_LIMIT_CONTEXT:
                await asyncio.sleep(0.02)
            return await super().execute(**kwargs)

    router = SlowPrimary(
        {
            OP_LIMIT_CONTEXT: _ok(ctx, category=DataCategory.LIMIT_UP),
            OP_LIMIT_REASON_TAGS: _ok(enrich, category=DataCategory.LIMIT_UP, vendor=VendorId.THS),
        }
    )
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE, pools=(LimitPoolType.LIMIT_UP,), as_of=AS_OF
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.context.entries[0].reason_tags == ("ths:白酒",)
    assert tuple(item.component.value for item in result.provenance) == (
        "limit_context",
        "limit_reason_tags",
    )
    assert result.data.provenance == provenance_dtos(result.provenance)


def test_limit_up_result_invariants() -> None:
    with pytest.raises(DataContractError):
        AShareLimitUpResult(ok=True, data=None, warnings=(), error=None, provenance=())
    with pytest.raises(DataContractError):
        AShareLimitUpResult(
            ok=False,
            data=None,
            warnings=(),
            error=None,  # type: ignore[arg-type]
            provenance=(),
        )


# --- Limit validators (malicious dimensions) ----------------------------------


def _limit_svc_bare() -> AShareLimitUpService:
    return _limit_svc(_FakeRouter({}))


def test_validate_primary_wrong_container_category_date() -> None:
    svc = _limit_svc_bare()
    with pytest.raises(DataContractError) as ei:
        svc._validate_primary_limit_context(
            "not-success",  # type: ignore[arg-type]
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei.value.details.get("rule") == "type"

    with pytest.raises(DataContractError) as ei2:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=_context(), meta=_meta(DataCategory.SENTIMENT)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei2.value.details.get("rule") == "category"

    wrong_date = _context(
        entries=(_entry(trade_date=date(2024, 1, 15)),),
        trade_date=date(2024, 1, 15),
    )
    with pytest.raises(DataContractError) as ei3:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=wrong_date, meta=_meta(DataCategory.LIMIT_UP)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei3.value.details.get("rule") == "trade_date"


def test_validate_primary_pool_only_order_unique_summary_ladder() -> None:
    svc = _limit_svc_bare()
    # Unrequested pool in entries.
    extra = _context(
        entries=(_entry(pool=LimitPoolType.LIMIT_DOWN, code="000002.SZ", consecutive=None),)
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=extra, meta=_meta(DataCategory.LIMIT_UP)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei.value.details.get("rule") == "requested_pool_only"

    # Unsorted instruments within LIMIT_UP.
    unsorted = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(
            _entry(code="600519.SH", consecutive=1),
            _entry(code="000001.SZ", consecutive=1),
        ),
        limit_up_count=2,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=1,
        promotion_rate=None,
        ladder=(
            LimitUpLadderRung(
                consecutive_limit_count=1,
                instrument_count=2,
                instrument_ids=(
                    "equity:A_SHARE:000001.SZ",
                    "equity:A_SHARE:600519.SH",
                ),
            ),
        ),
    )
    with pytest.raises(DataContractError) as ei2:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=unsorted, meta=_meta(DataCategory.LIMIT_UP)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei2.value.details.get("rule") == "sorted"

    # Inconsistent summary count.
    bad_count = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(_entry(code="600519.SH", consecutive=1),),
        limit_up_count=9,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=1,
        promotion_rate=None,
        ladder=(
            LimitUpLadderRung(
                consecutive_limit_count=1,
                instrument_count=1,
                instrument_ids=("equity:A_SHARE:600519.SH",),
            ),
        ),
    )
    with pytest.raises(DataContractError) as ei3:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=bad_count, meta=_meta(DataCategory.LIMIT_UP)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei3.value.details.get("rule") == "summary_count"

    # Wrong ladder.
    bad_ladder = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(_entry(code="600519.SH", consecutive=2),),
        limit_up_count=1,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=2,
        promotion_rate=None,
        ladder=(),
    )
    with pytest.raises(DataContractError) as ei4:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=bad_ladder, meta=_meta(DataCategory.LIMIT_UP)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei4.value.details.get("rule") == "ladder_derived"

    # broken_rate exact when both pools requested.
    both = _context(
        entries=(
            _entry(code="000001.SZ", consecutive=1),
            _entry(
                pool=LimitPoolType.BROKEN_LIMIT,
                code="000002.SZ",
                consecutive=None,
            ),
        )
    )
    assert both.broken_rate == Decimal("0.5000")
    svc._validate_primary_limit_context(
        ProviderSuccess(value=both, meta=_meta(DataCategory.LIMIT_UP)),
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP, LimitPoolType.BROKEN_LIMIT),
    )
    bad_rate = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=both.entries,
        limit_up_count=1,
        limit_down_count=0,
        broken_limit_count=1,
        broken_rate=Decimal("0.9999"),
        max_consecutive_count=1,
        promotion_rate=None,
        ladder=both.ladder,
    )
    with pytest.raises(DataContractError) as ei5:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=bad_rate, meta=_meta(DataCategory.LIMIT_UP)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP, LimitPoolType.BROKEN_LIMIT),
        )
    assert ei5.value.details.get("rule") == "broken_rate"


def test_validate_enrichment_ths_only_no_factual_summary() -> None:
    svc = _limit_svc_bare()
    ok = _enrichment_context()
    svc._validate_enrichment_limit_context(
        ProviderSuccess(value=ok, meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.THS)),
        trade_date=TRADE_DATE,
    )
    # Wrong vendor on enrichment row (meta still THS so row-level rule surfaces).
    wrong = _enrichment_context(entries=(_entry(tags=("x",), vendor=VendorId.EASTMONEY),))
    with pytest.raises(DataContractError) as ei:
        svc._validate_enrichment_limit_context(
            ProviderSuccess(value=wrong, meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.THS)),
            trade_date=TRADE_DATE,
        )
    assert ei.value.details.get("rule") == "enrichment_vendor"


def _empty_primary_context() -> LimitUpContext:
    return LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(),
        limit_up_count=0,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=None,
        promotion_rate=None,
        ladder=(),
    )


def _empty_enrichment_context() -> LimitUpContext:
    return LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(),
        limit_up_count=0,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=None,
        promotion_rate=None,
        ladder=(),
    )


def test_validate_primary_empty_requires_eastmoney_meta_vendor() -> None:
    """Empty primary still requires success.meta.vendor EASTMONEY."""
    svc = _limit_svc_bare()
    empty = _empty_primary_context()
    svc._validate_primary_limit_context(
        ProviderSuccess(value=empty, meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.EASTMONEY)),
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_primary_limit_context(
            ProviderSuccess(value=empty, meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.THS)),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei.value.details.get("rule") == "primary_meta_vendor"
    assert ei.value.details.get("field") == "meta.vendor"


def test_validate_enrichment_empty_requires_ths_meta_vendor() -> None:
    """Empty enrichment still requires success.meta.vendor THS."""
    svc = _limit_svc_bare()
    empty = _empty_enrichment_context()
    svc._validate_enrichment_limit_context(
        ProviderSuccess(value=empty, meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.THS)),
        trade_date=TRADE_DATE,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_enrichment_limit_context(
            ProviderSuccess(
                value=empty,
                meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.EASTMONEY),
            ),
            trade_date=TRADE_DATE,
        )
    assert ei.value.details.get("rule") == "enrichment_meta_vendor"
    assert ei.value.details.get("field") == "meta.vendor"


def test_validate_promotion_rate_must_be_none_primary_and_enrichment() -> None:
    """promotion_rate is fail-closed None until verified prior-day identity join."""
    svc = _limit_svc_bare()
    primary = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(),
        limit_up_count=0,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=None,
        promotion_rate=Decimal("0.5000"),
        ladder=(),
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_primary_limit_context(
            ProviderSuccess(
                value=primary,
                meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.EASTMONEY),
            ),
            trade_date=TRADE_DATE,
            pools=(LimitPoolType.LIMIT_UP,),
        )
    assert ei.value.details.get("rule") == "promotion_rate_unavailable"
    assert ei.value.details.get("field") == "promotion_rate"

    enrich = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(),
        limit_up_count=0,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=None,
        promotion_rate=Decimal("0.1000"),
        ladder=(),
    )
    with pytest.raises(DataContractError) as ei2:
        svc._validate_enrichment_limit_context(
            ProviderSuccess(value=enrich, meta=_meta(DataCategory.LIMIT_UP, vendor=VendorId.THS)),
            trade_date=TRADE_DATE,
        )
    assert ei2.value.details.get("rule") == "promotion_rate_unavailable"


@pytest.mark.asyncio
async def test_limit_up_empty_wrong_meta_primary_fails_required() -> None:
    """Wrong meta.vendor on empty primary fails required path (not silent accept)."""
    empty = _empty_primary_context()
    router = _FakeRouter(
        {
            OP_LIMIT_CONTEXT: _ok(empty, category=DataCategory.LIMIT_UP, vendor=VendorId.THS),
        }
    )
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.data is None
    assert isinstance(result.error, DataContractError)
    assert (result.error.details or {}).get("rule") == "primary_meta_vendor"


@pytest.mark.asyncio
async def test_limit_up_empty_wrong_meta_enrichment_partial() -> None:
    """Wrong meta.vendor on empty enrichment surfaces optional partial warning."""
    primary = _context(entries=(_entry(code="600519.SH", tags=()),))
    empty_enrich = _empty_enrichment_context()
    router = _FakeRouter(
        {
            OP_LIMIT_CONTEXT: _ok(primary, category=DataCategory.LIMIT_UP),
            OP_LIMIT_REASON_TAGS: _ok(
                empty_enrich,
                category=DataCategory.LIMIT_UP,
                vendor=VendorId.EASTMONEY,  # wrong; must be THS
            ),
        }
    )
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)


@pytest.mark.asyncio
async def test_limit_up_promotion_rate_non_none_primary_fails_required() -> None:
    bad = LimitUpContext(
        trade_date=TRADE_DATE,
        entries=(_entry(code="600519.SH"),),
        limit_up_count=1,
        limit_down_count=0,
        broken_limit_count=0,
        broken_rate=None,
        max_consecutive_count=1,
        promotion_rate=Decimal("0.2500"),
        ladder=(
            LimitUpLadderRung(
                consecutive_limit_count=1,
                instrument_count=1,
                instrument_ids=("equity:A_SHARE:600519.SH",),
            ),
        ),
    )
    router = _FakeRouter({OP_LIMIT_CONTEXT: _ok(bad, category=DataCategory.LIMIT_UP)})
    result = await _limit_svc(router).get(
        trade_date=TRADE_DATE,
        pools=(LimitPoolType.LIMIT_UP,),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert isinstance(result.error, DataContractError)
    assert (result.error.details or {}).get("rule") == "promotion_rate_unavailable"


# --- Sentiment service --------------------------------------------------------


@pytest.mark.asyncio
async def test_sentiment_service_all_optional_failures_still_ok() -> None:
    router = _FakeRouter({})
    result = await _sent_svc(router).get(
        instrument=_equity(),
        sources=tuple(SentimentSourceType),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.signals == ()
    assert result.data.interactive_qa == ()
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    # Default sources use frozen enum order.
    assert result.data.sources == tuple(SentimentSourceType)
    assert result.provenance == ()
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_sentiment_service_source_subset_default_order() -> None:
    router = _FakeRouter(
        {
            OP_SENTIMENT: _ok(
                (_signal(SentimentSourceType.EASTMONEY_HOT),),
                category=DataCategory.SENTIMENT,
            ),
        }
    )
    # Call order reversed relative to enum; output sources in enum order.
    result = await _sent_svc(router).get(
        instrument=_equity(),
        sources=(
            SentimentSourceType.EASTMONEY_HOT,
            SentimentSourceType.THS_HOT,
        ),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.sources == (
        SentimentSourceType.THS_HOT,
        SentimentSourceType.EASTMONEY_HOT,
    )
    assert tuple(item.component.value for item in result.provenance) == ("eastmoney_hot",)
    assert result.provenance[0].meta is router.results[OP_SENTIMENT].meta
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_sentiment_instrument_required_sources() -> None:
    router = _FakeRouter({})
    result = await _sent_svc(router).get(
        instrument=None,
        sources=(
            SentimentSourceType.INTERACTIVE_QA,
            SentimentSourceType.COMPANY_NEWS,
        ),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    assert result.data is not None
    assert result.data.interactive_qa == ()
    assert result.data.company_news == ()


@pytest.mark.asyncio
async def test_sentiment_service_deterministic_source_order_and_no_caller_vendor() -> None:
    sig_em = _signal(SentimentSourceType.EASTMONEY_HOT, rank=2)
    sig_ths = _signal(SentimentSourceType.THS_HOT, rank=1, vendor=VendorId.THS)
    qa = InteractiveQAItem(
        qa_key="k",
        question="q",
        asked_at=None,
        answer="a",
        answered_at=AS_OF - timedelta(minutes=5),
        source_url=None,
    )
    news = NewsItem(
        news_key="n1",
        title="t",
        summary=None,
        published_at=AS_OF - timedelta(hours=1),
        source_name="em",
        source_url=None,
    )

    class OrderedRouter(_FakeRouter):
        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            await asyncio.sleep(0)
            self.calls.append(kwargs)
            source = kwargs.get("request_fingerprint", "")
            validator = kwargs.get("result_validator")

            def _apply(
                value: Any, category: DataCategory, vendor: VendorId = VendorId.EASTMONEY
            ) -> RouterExecutionResult[Any]:
                result = _ok(value, category=category, vendor=vendor)
                if validator is not None:
                    try:
                        validator(ProviderSuccess(value=value, meta=result.meta))
                    except TradingPartnerError as exc:
                        return RouterExecutionResult(
                            value=None,
                            ok=False,
                            criticality=DataCriticality.OPTIONAL,
                            meta=None,
                            attempts=(),
                            warnings=(),
                            error=exc,
                        )
                return result

            if "ths_hot" in source:
                return _apply((sig_ths,), DataCategory.SENTIMENT, VendorId.THS)
            if "eastmoney_hot" in source:
                await asyncio.sleep(0.01)
                return _apply((sig_em,), DataCategory.SENTIMENT)
            if kwargs["category"] is DataCategory.INTERACTIVE_QA:
                return _apply((qa,), DataCategory.INTERACTIVE_QA, VendorId.CNINFO)
            if kwargs["category"] is DataCategory.NEWS:
                return _apply((news,), DataCategory.NEWS)
            if "concept_heat" in source:
                return _fail()
            return _fail()

    router = OrderedRouter({})
    result = await _sent_svc(router).get(
        instrument=_equity(),
        sources=(
            SentimentSourceType.EASTMONEY_HOT,
            SentimentSourceType.THS_HOT,
            SentimentSourceType.INTERACTIVE_QA,
            SentimentSourceType.COMPANY_NEWS,
        ),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    types = [s.source_type for s in result.data.signals]
    assert types == [
        SentimentSourceType.THS_HOT,
        SentimentSourceType.EASTMONEY_HOT,
    ]
    assert result.data.interactive_qa and result.data.company_news
    assert tuple(item.component.value for item in result.provenance) == (
        "ths_hot",
        "eastmoney_hot",
        "interactive_qa",
        "company_news",
    )
    assert tuple(item.meta.vendor for item in result.provenance) == (
        VendorId.THS,
        VendorId.EASTMONEY,
        VendorId.CNINFO,
        VendorId.EASTMONEY,
    )
    assert result.data.provenance == provenance_dtos(result.provenance)
    for call in router.calls:
        assert "vendor" not in call.get("request_fingerprint", "")
        assert call.get("result_validator") is not None


@pytest.mark.asyncio
async def test_sentiment_meta_warning_propagation() -> None:
    router = _FakeRouter(
        {
            OP_SENTIMENT: _ok(
                (_signal(),),
                category=DataCategory.SENTIMENT,
                meta_warnings=("LOW_RELIABILITY_MARKET_SIGNAL",),
            ),
        }
    )
    result = await _sent_svc(router).get(
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert any(w.code == "LOW_RELIABILITY_MARKET_SIGNAL" for w in result.warnings)


@pytest.mark.asyncio
async def test_sentiment_malicious_signal_optional_partial() -> None:
    # Wrong source_vendor for eastmoney_hot.
    bad = SentimentSignal(
        source_type=SentimentSourceType.EASTMONEY_HOT,
        trade_date=TRADE_DATE,
        instrument_id="equity:A_SHARE:600519.SH",
        rank=1,
        rank_change=None,
        heat_value=None,
        concept_tags=(),
        label=None,
        source_vendor=VendorId.THS,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
    )
    router = _FakeRouter({OP_SENTIMENT: _ok((bad,), category=DataCategory.SENTIMENT)})
    result = await _sent_svc(router).get(
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.signals == ()
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    assert result.provenance == ()
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_sentiment_samples_clock_once() -> None:
    router = _FakeRouter(
        {
            OP_SENTIMENT: _ok((_signal(),), category=DataCategory.SENTIMENT),
        }
    )
    clock = _FixedClock(AS_OF)
    await _sent_svc(router, clock).get(
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert clock.calls == 1


def test_sentiment_result_invariants_and_no_authoritative() -> None:
    with pytest.raises(DataContractError):
        AShareSentimentResult(ok=True, data=None, warnings=(), error=None, provenance=())
    with pytest.raises(DataContractError):
        SentimentSignal(
            source_type=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument_id=None,
            rank=1,
            rank_change=None,
            heat_value=None,
            concept_tags=(),
            label=None,
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.LOW,
            is_authoritative=True,
        )


# --- Sentiment validators -----------------------------------------------------


def _sent_svc_bare() -> AShareSentimentService:
    return _sent_svc(_FakeRouter({}))


def test_validate_sentiment_tuple_meta_source_vendor_order() -> None:
    svc = _sent_svc_bare()
    ok = (_signal(rank=1, code="000001.SZ"), _signal(rank=2, code="600519.SH"))
    svc._validate_sentiment(
        ProviderSuccess(value=ok, meta=_meta(DataCategory.SENTIMENT)),
        source=SentimentSourceType.EASTMONEY_HOT,
        trade_date=TRADE_DATE,
        instrument=None,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_sentiment(
            ProviderSuccess(value=list(ok), meta=_meta(DataCategory.SENTIMENT)),  # type: ignore[arg-type]
            source=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument=None,
        )
    assert ei.value.details.get("rule") == "type"

    with pytest.raises(DataContractError) as ei2:
        svc._validate_sentiment(
            ProviderSuccess(value=ok, meta=_meta(DataCategory.NEWS)),
            source=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument=None,
        )
    assert ei2.value.details.get("rule") == "category"

    # meta stays EASTMONEY so row-level source_type rule surfaces (not meta_vendor).
    wrong_src = (_signal(SentimentSourceType.THS_HOT, rank=1, vendor=VendorId.THS),)
    with pytest.raises(DataContractError) as ei3:
        svc._validate_sentiment(
            ProviderSuccess(
                value=wrong_src,
                meta=_meta(DataCategory.SENTIMENT, vendor=VendorId.EASTMONEY),
            ),
            source=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument=None,
        )
    assert ei3.value.details.get("rule") == "source"

    unsorted = (
        _signal(rank=2, code="600519.SH"),
        _signal(rank=1, code="000001.SZ"),
    )
    with pytest.raises(DataContractError) as ei4:
        svc._validate_sentiment(
            ProviderSuccess(value=unsorted, meta=_meta(DataCategory.SENTIMENT)),
            source=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument=None,
        )
    assert ei4.value.details.get("rule") == "sorted"

    # Identity filter.
    with pytest.raises(DataContractError) as ei5:
        svc._validate_sentiment(
            ProviderSuccess(
                value=(_signal(code="000001.SZ"),),
                meta=_meta(DataCategory.SENTIMENT),
            ),
            source=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument=_equity("600519.SH"),
        )
    assert ei5.value.details.get("rule") == "identity"


def test_validate_qa_cutoff_unique_order() -> None:
    svc = _sent_svc_bare()
    qa1 = InteractiveQAItem(
        qa_key="b",
        question="q2",
        asked_at=AS_OF - timedelta(hours=2),
        answer="a2",
        answered_at=AS_OF - timedelta(hours=1),
        source_url=None,
    )
    qa0 = InteractiveQAItem(
        qa_key="a",
        question="q1",
        asked_at=None,
        answer="a1",
        answered_at=AS_OF - timedelta(minutes=30),
        source_url=None,
    )
    # Sorted answered_at desc, qa_key asc.
    svc._validate_qa(
        ProviderSuccess(
            value=(qa0, qa1),
            meta=_meta(DataCategory.INTERACTIVE_QA, vendor=VendorId.CNINFO),
        ),
        as_of=AS_OF,
    )
    future = InteractiveQAItem(
        qa_key="f",
        question="q",
        asked_at=None,
        answer="a",
        answered_at=AS_OF + timedelta(seconds=1),
        source_url=None,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_qa(
            ProviderSuccess(
                value=(future,),
                meta=_meta(DataCategory.INTERACTIVE_QA, vendor=VendorId.CNINFO),
            ),
            as_of=AS_OF,
        )
    assert ei.value.details.get("rule") == "as_of_cutoff"

    with pytest.raises(DataContractError) as ei2:
        svc._validate_qa(
            ProviderSuccess(
                value=(qa1, qa0),
                meta=_meta(DataCategory.INTERACTIVE_QA, vendor=VendorId.CNINFO),
            ),
            as_of=AS_OF,
        )
    assert ei2.value.details.get("rule") == "sorted"


def test_validate_news_window_unique_order() -> None:
    svc = _sent_svc_bare()
    start = AS_OF - timedelta(days=7)
    n1 = NewsItem(
        news_key="n1",
        title="t1",
        summary=None,
        published_at=AS_OF - timedelta(hours=1),
        source_name="x",
        source_url=None,
    )
    n2 = NewsItem(
        news_key="n2",
        title="t2",
        summary=None,
        published_at=AS_OF - timedelta(hours=2),
        source_name="x",
        source_url=None,
    )
    svc._validate_news(
        ProviderSuccess(value=(n1, n2), meta=_meta(DataCategory.NEWS)),
        as_of=AS_OF,
        start=start,
        end=AS_OF,
        source=SentimentSourceType.MARKET_NEWS,
    )
    # CLS is an explicitly supported A-share news vendor.
    svc._validate_news(
        ProviderSuccess(
            value=(),
            meta=_meta(DataCategory.NEWS, vendor=VendorId.CLS),
        ),
        as_of=AS_OF,
        start=start,
        end=AS_OF,
        source=SentimentSourceType.MARKET_NEWS,
    )
    outside = NewsItem(
        news_key="old",
        title="t",
        summary=None,
        published_at=start - timedelta(seconds=1),
        source_name="x",
        source_url=None,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_news(
            ProviderSuccess(value=(outside,), meta=_meta(DataCategory.NEWS)),
            as_of=AS_OF,
            start=start,
            end=AS_OF,
            source=SentimentSourceType.MARKET_NEWS,
        )
    assert ei.value.details.get("rule") == "window"


def test_validate_empty_sentiment_meta_vendor_eastmoney_and_ths() -> None:
    """Empty eastmoney_hot / ths_hot still require matching meta.vendor."""
    svc = _sent_svc_bare()
    svc._validate_sentiment(
        ProviderSuccess(
            value=(),
            meta=_meta(DataCategory.SENTIMENT, vendor=VendorId.EASTMONEY),
        ),
        source=SentimentSourceType.EASTMONEY_HOT,
        trade_date=TRADE_DATE,
        instrument=None,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_sentiment(
            ProviderSuccess(
                value=(),
                meta=_meta(DataCategory.SENTIMENT, vendor=VendorId.THS),
            ),
            source=SentimentSourceType.EASTMONEY_HOT,
            trade_date=TRADE_DATE,
            instrument=None,
        )
    assert ei.value.details.get("rule") == "meta_vendor"
    assert ei.value.details.get("expected") == VendorId.EASTMONEY.value

    svc._validate_sentiment(
        ProviderSuccess(
            value=(),
            meta=_meta(DataCategory.SENTIMENT, vendor=VendorId.THS),
        ),
        source=SentimentSourceType.THS_HOT,
        trade_date=TRADE_DATE,
        instrument=None,
    )
    with pytest.raises(DataContractError) as ei2:
        svc._validate_sentiment(
            ProviderSuccess(
                value=(),
                meta=_meta(DataCategory.SENTIMENT, vendor=VendorId.EASTMONEY),
            ),
            source=SentimentSourceType.THS_HOT,
            trade_date=TRADE_DATE,
            instrument=None,
        )
    assert ei2.value.details.get("rule") == "meta_vendor"
    assert ei2.value.details.get("expected") == VendorId.THS.value


def test_validate_empty_qa_requires_cninfo_meta_vendor() -> None:
    svc = _sent_svc_bare()
    svc._validate_qa(
        ProviderSuccess(
            value=(),
            meta=_meta(DataCategory.INTERACTIVE_QA, vendor=VendorId.CNINFO),
        ),
        as_of=AS_OF,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_qa(
            ProviderSuccess(
                value=(),
                meta=_meta(DataCategory.INTERACTIVE_QA, vendor=VendorId.EASTMONEY),
            ),
            as_of=AS_OF,
        )
    assert ei.value.details.get("rule") == "meta_vendor"
    assert ei.value.details.get("expected") == VendorId.CNINFO.value


def test_validate_news_rejects_unsupported_meta_vendor() -> None:
    """NEWS allows EASTMONEY/CLS only — not a single-vendor invent constraint."""
    svc = _sent_svc_bare()
    start = AS_OF - timedelta(days=7)
    with pytest.raises(DataContractError) as ei:
        svc._validate_news(
            ProviderSuccess(
                value=(),
                meta=_meta(DataCategory.NEWS, vendor=VendorId.THS),
            ),
            as_of=AS_OF,
            start=start,
            end=AS_OF,
            source=SentimentSourceType.MARKET_NEWS,
        )
    assert ei.value.details.get("rule") == "meta_vendor"
    assert ei.value.details.get("field") == "meta.vendor"
    allowed = ei.value.details.get("allowed")
    assert allowed == ("cls", "eastmoney")


@pytest.mark.asyncio
async def test_sentiment_empty_wrong_meta_optional_partials() -> None:
    """Empty wrong-meta sources become optional PARTIAL warnings, overall ok."""
    router = _FakeRouter(
        {
            OP_SENTIMENT: _ok(
                (),
                category=DataCategory.SENTIMENT,
                vendor=VendorId.THS,  # wrong for eastmoney_hot
            ),
        }
    )
    result = await _sent_svc(router).get(
        sources=(SentimentSourceType.EASTMONEY_HOT,),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.signals == ()
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)

    class MultiSourceRouter(_FakeRouter):
        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            self.calls.append(kwargs)
            validator = kwargs.get("result_validator")
            fp = kwargs.get("request_fingerprint", "")
            cat = kwargs["category"]
            if "ths_hot" in fp:
                value: Any = ()
                vendor = VendorId.EASTMONEY  # wrong for ths_hot
                category = DataCategory.SENTIMENT
            elif cat is DataCategory.INTERACTIVE_QA:
                value = ()
                vendor = VendorId.THS  # wrong for QA
                category = DataCategory.INTERACTIVE_QA
            elif cat is DataCategory.NEWS:
                value = ()
                vendor = VendorId.SINA  # unsupported news meta vendor
                category = DataCategory.NEWS
            else:
                return _fail(cat)
            meta = _meta(category, vendor=vendor)
            if validator is not None:
                try:
                    validator(ProviderSuccess(value=value, meta=meta))
                except TradingPartnerError as exc:
                    return RouterExecutionResult(
                        value=None,
                        ok=False,
                        criticality=DataCriticality.OPTIONAL,
                        meta=None,
                        attempts=(),
                        warnings=(),
                        error=exc,
                    )
            return _ok(value, category=category, vendor=vendor)

    multi = MultiSourceRouter({})
    result2 = await _sent_svc(multi).get(
        instrument=_equity(),
        sources=(
            SentimentSourceType.THS_HOT,
            SentimentSourceType.INTERACTIVE_QA,
            SentimentSourceType.MARKET_NEWS,
        ),
        trade_date=TRADE_DATE,
        as_of=AS_OF,
    )
    assert result2.ok is True
    assert result2.data is not None
    assert result2.data.signals == ()
    assert result2.data.interactive_qa == ()
    assert result2.data.market_news == ()
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result2.warnings)


def test_public_mcp_surface_includes_phase1e_after_e5c() -> None:
    """Phase 1E tools remain in the expanded public surface."""
    from interfaces.mcp.server import (
        LEGACY_PUBLIC_TOOL_NAMES,
        PHASE1E_A_SHARE_TOOL_NAMES,
        PUBLIC_TOOL_NAMES,
    )

    assert len(LEGACY_PUBLIC_TOOL_NAMES) == 15
    assert len(PHASE1E_A_SHARE_TOOL_NAMES) == 2
    assert PHASE1E_A_SHARE_TOOL_NAMES.isdisjoint(LEGACY_PUBLIC_TOOL_NAMES)
    assert PHASE1E_A_SHARE_TOOL_NAMES <= PUBLIC_TOOL_NAMES
    assert len(PUBLIC_TOOL_NAMES) == 52
