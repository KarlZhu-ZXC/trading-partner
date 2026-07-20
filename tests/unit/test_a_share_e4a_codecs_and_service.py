"""E4a codecs, fingerprints, validators, and capital service unit tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    provenance_dtos,
)
from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import (
    AShareDailyFlowProvider,
    AShareIntradayFlowProvider,
)
from application.services.a_share_capital_service import (
    OP_DAILY_FLOW,
    OP_NORTHBOUND,
    AShareCapitalResult,
    AShareCapitalService,
)
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import (
    CAPITAL_DEFAULT_SUMMARY_METRICS,
    CAPITAL_METRIC_CHAIN_OVERRIDES,
    capital_metric_router_policy,
)
from domain.a_share.enums import AShareComponentType, BarInterval, CapitalMetricType
from domain.a_share.models import (
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    DragonTigerRecord,
    FundFlowPoint,
    MarginRecord,
    NorthboundFlowPoint,
    ShareholderCountRecord,
    TradingSessionWindow,
    UnlockRecord,
)
from domain.common.enums import (
    AdjustmentMethod,
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
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.codecs import (
    E4A_CODEC_IDS,
    block_trades_codec,
    chip_distribution_codec,
    corporate_actions_codec,
    daily_flow_codec,
    dragon_tiger_codec,
    intraday_flow_codec,
    margin_codec,
    northbound_codec,
    shareholder_counts_codec,
)

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)


def _equity(asset: AssetType = AssetType.EQUITY) -> Instrument:
    return Instrument(
        instrument_id=f"{asset.value}:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="test",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=asset,
    )


def _meta(category: DataCategory = DataCategory.CAPITAL) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
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
        warnings=(),
    )


def _flow_point(ts: datetime | None = None) -> FundFlowPoint:
    return FundFlowPoint(
        occurred_at=ts or datetime(2024, 1, 15, 7, 0, tzinfo=UTC),
        interval=BarInterval.ONE_DAY,
        main_net_cny=Decimal("1"),
        super_large_net_cny=Decimal("0"),
        large_net_cny=Decimal("0"),
        medium_net_cny=Decimal("0"),
        small_net_cny=Decimal("0"),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )


def _chip_snapshot() -> ChipDistributionSnapshot:
    return ChipDistributionSnapshot(
        as_of=AS_OF,
        bins=(ChipDistributionBin(Decimal("1"), Decimal("2"), Decimal("1")),),
        profit_ratio=Decimal("0.4"),
        average_cost=Decimal("1.5"),
        concentration_90=Decimal("0.1"),
        concentration_70=Decimal("0.05"),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
        calculation_method="turnover_decay_uniform_range",
        algorithm_version="tp_chip_v1",
        lookback_sessions=120,
        input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        bar_trade_date=AS_OF.date(),
    )


def test_e4a_codec_ids_complete() -> None:
    expected = frozenset(
        {
            "a_share_intraday_flow.v1",
            "a_share_daily_flow.v1",
            "a_share_northbound.v1",
            "a_share_dragon_tiger.v1",
            "a_share_margin.v1",
            "a_share_block_trades.v1",
            "a_share_shareholder_counts.v1",
            "a_share_chip_distribution.v2",
        }
    )
    assert expected == E4A_CODEC_IDS


def _cache_entry(payload: str, *, vendor: VendorId = VendorId.EASTMONEY) -> Any:
    from application.dto.provider_state import CacheEntry

    return CacheEntry(
        key="v1|A_SHARE|capital|equity:A_SHARE:600519.SH|fp",
        market=Market.A_SHARE,
        category=DataCategory.CAPITAL,
        instrument_id="equity:A_SHARE:600519.SH",
        as_of=AS_OF,
        fetched_at=AS_OF,
        expires_at=AS_OF + timedelta(hours=1),
        freshness=Freshness.UNKNOWN,
        vendor=vendor,
        payload_json=payload,
    )


def test_fund_flow_codec_roundtrip_no_float() -> None:
    codec = daily_flow_codec()
    value = (_flow_point(),)
    success = ProviderSuccess(value=value, meta=_meta())
    encoded = codec.encode(success)
    assert "pickle" not in encoded
    decoded = codec.decode(_cache_entry(encoded))
    assert decoded.value[0].main_net_cny == Decimal("1")


def test_northbound_codec_and_chip_roundtrip() -> None:
    nb = (
        NorthboundFlowPoint(
            trade_date=date(2024, 1, 15),
            channel="total",
            net_buy_cny=Decimal("100"),
            buy_cny=Decimal("200"),
            sell_cny=Decimal("100"),
            disclosure_note=None,
            source_vendor=VendorId.HKEX,
            reliability=ReliabilityLevel.HIGH,
            is_authoritative=True,
        ),
    )
    codec = northbound_codec()
    # meta.vendor must match CacheEntry.vendor for codec identity checks.
    success = ProviderSuccess(
        value=nb,
        meta=ProviderResultMeta(
            vendor=VendorId.HKEX,
            category=DataCategory.CAPITAL,
            role=SourceRole.PRIMARY,
            as_of=AS_OF,
            fetched_at=AS_OF,
            freshness=Freshness.UNKNOWN,
            session=TradingSession.UNKNOWN,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=(),
        ),
    )
    encoded = codec.encode(success)
    decoded = codec.decode(_cache_entry(encoded, vendor=VendorId.HKEX))
    assert decoded.value[0].is_authoritative is True

    chip = ChipDistributionSnapshot(
        as_of=AS_OF,
        bins=(
            ChipDistributionBin(
                price_low=Decimal("1"),
                price_high=Decimal("2"),
                holding_ratio=Decimal("1"),
            ),
        ),
        profit_ratio=Decimal("0.4"),
        average_cost=Decimal("1.5"),
        concentration_90=Decimal("0.1"),
        concentration_70=Decimal("0.05"),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
        calculation_method="turnover_decay_uniform_range",
        algorithm_version="tp_chip_v1",
        lookback_sessions=120,
        input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        bar_trade_date=AS_OF.date(),
    )
    c_codec = chip_distribution_codec()
    c_success = ProviderSuccess(value=chip, meta=_meta())
    c_enc = c_codec.encode(c_success)
    c_dec = c_codec.decode(_cache_entry(c_enc))
    assert c_dec.value.bins[0].holding_ratio == Decimal("1")


def test_fingerprint_stable_and_secret_free() -> None:
    fp1 = build_a_share_fingerprint(
        OP_DAILY_FLOW,
        "equity:A_SHARE:600519.SH",
        {"end": "2024-01-16", "start": "2024-01-01", "metric": "daily_flow"},
        AS_OF,
    )
    fp2 = build_a_share_fingerprint(
        OP_DAILY_FLOW,
        "equity:A_SHARE:600519.SH",
        {"metric": "daily_flow", "start": "2024-01-01", "end": "2024-01-16"},
        AS_OF,
    )
    assert fp1 == fp2
    assert "cookie" not in fp1.lower()
    assert "token" not in fp1.lower()
    assert "Authorization" not in fp1


def test_capital_chain_overrides_frozen() -> None:
    assert CAPITAL_METRIC_CHAIN_OVERRIDES[CapitalMetricType.NORTHBOUND] == (
        VendorId.HKEX,
        VendorId.EASTMONEY,
    )
    assert CAPITAL_METRIC_CHAIN_OVERRIDES[CapitalMetricType.DRAGON_TIGER] == (
        VendorId.EASTMONEY,
        VendorId.SSE,
        VendorId.SZSE,
    )
    assert CAPITAL_METRIC_CHAIN_OVERRIDES[CapitalMetricType.DAILY_FLOW] == (
        VendorId.EASTMONEY,
        VendorId.SINA,
    )
    pol = capital_metric_router_policy(CapitalMetricType.NORTHBOUND, required=True)
    assert pol.category_chain_overrides[DataCategory.CAPITAL][0] is VendorId.HKEX


def test_capital_result_invariants() -> None:
    with pytest.raises(DataContractError):
        AShareCapitalResult(ok=True, data=None, warnings=(), error=None, provenance=())
    with pytest.raises(DataContractError):
        AShareCapitalResult(
            ok=False,
            data=None,
            warnings=(),
            error=None,
            provenance=(),
        )


class _FakeRouter:
    def __init__(self, results: dict[str, RouterExecutionResult[Any]]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
        self.calls.append(kwargs)
        op = kwargs["operation_name"]
        # For corporate actions unlock/dividend share OP name — use tool policy name.
        policy = kwargs.get("tool_policy")
        if policy is not None and "unlock" in policy.tool_name:
            key = "unlock"
        elif policy is not None and "dividend" in policy.tool_name:
            key = "dividend"
        else:
            key = op
        if key not in self.results and op in self.results:
            key = op
        return self.results[key]


class _Clock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _Calendar:
    version = "test.v1"

    def is_trading_day(self, day: date) -> bool:
        return day == AS_OF.date()

    def previous_trading_day(self, day: date) -> date:
        return day - timedelta(days=1)

    def sessions_for(self, day: date) -> tuple[TradingSessionWindow, ...]:
        if not self.is_trading_day(day):
            return ()
        return (
            TradingSessionWindow(
                session=TradingSession.REGULAR,
                start_at=AS_OF - timedelta(hours=5, minutes=30),
                end_at=AS_OF,
            ),
        )


def _ok_result(
    value: Any,
    category: DataCategory = DataCategory.CAPITAL,
    *,
    result_warnings: tuple[WarningInfo, ...] = (),
    meta_warnings: tuple[str, ...] = (),
) -> RouterExecutionResult[Any]:
    meta = ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
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
        warnings=meta_warnings,
    )
    return RouterExecutionResult(
        value=value,
        ok=True,
        criticality=DataCriticality.CORE,
        meta=meta,
        attempts=(),
        warnings=result_warnings,
        error=None,
    )


def _fail_result(
    msg: str = "fail",
    *,
    result_warnings: tuple[WarningInfo, ...] = (),
) -> RouterExecutionResult[Any]:
    return RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.CORE,
        meta=None,
        attempts=(),
        warnings=result_warnings,
        error=DataContractError(msg, details={"rule": "test"}),
    )


def _service(router: _FakeRouter) -> AShareCapitalService:
    return AShareCapitalService(
        router=router,  # type: ignore[arg-type]
        clock=_Clock(AS_OF),
        calendar=_Calendar(),
        intraday_flow_codec=intraday_flow_codec(),
        daily_flow_codec=daily_flow_codec(),
        northbound_codec=northbound_codec(),
        dragon_tiger_codec=dragon_tiger_codec(),
        margin_codec=margin_codec(),
        block_trades_codec=block_trades_codec(),
        shareholder_counts_codec=shareholder_counts_codec(),
        chip_distribution_codec=chip_distribution_codec(),
        corporate_actions_codec=corporate_actions_codec(),
    )


@pytest.mark.asyncio
async def test_default_summary_daily_required_optional_partial() -> None:
    router = _FakeRouter(
        {
            OP_DAILY_FLOW: _ok_result((_flow_point(),)),
            "a_share.margin.v1": _fail_result("margin down"),
            "a_share.shareholder_counts.v1": _ok_result(()),
            "a_share.chip_distribution.v2": _fail_result("chip down"),
            "unlock": _ok_result(()),
            "dividend": _ok_result(()),
        }
    )
    # Map unlock/dividend corporate actions op
    router.results["a_share.corporate_actions.v1"] = _ok_result(())
    svc = _service(router)
    result = await svc.get(instrument=_equity(), metrics=(), as_of=AS_OF)
    assert result.ok is True
    assert result.data is not None
    assert result.data.metrics == CAPITAL_DEFAULT_SUMMARY_METRICS
    assert result.data.daily_flow
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    assert tuple(item.component.value for item in result.provenance) == (
        "daily_flow",
        "shareholder_count",
        "unlock",
        "dividend",
    )
    assert result.data.provenance == provenance_dtos(result.provenance)
    assert tuple(item.meta for item in result.provenance) == (
        router.results[OP_DAILY_FLOW].meta,
        router.results["a_share.shareholder_counts.v1"].meta,
        router.results["a_share.corporate_actions.v1"].meta,
        router.results["a_share.corporate_actions.v1"].meta,
    )


@pytest.mark.asyncio
async def test_explicit_required_failure_no_partial_data() -> None:
    router = _FakeRouter(
        {
            OP_DAILY_FLOW: _ok_result((_flow_point(),)),
            "a_share.margin.v1": _fail_result("margin down"),
        }
    )
    svc = _service(router)
    result = await svc.get(
        instrument=_equity(),
        metrics=(CapitalMetricType.DAILY_FLOW, CapitalMetricType.MARGIN),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.data is None
    assert result.error is not None
    assert tuple(item.component.value for item in result.provenance) == ("daily_flow",)
    assert result.provenance[0].meta is router.results[OP_DAILY_FLOW].meta


@pytest.mark.asyncio
async def test_explicit_failure_retains_successes_in_caller_metric_order() -> None:
    router = _FakeRouter(
        {
            "a_share.margin.v1": _ok_result(()),
            OP_DAILY_FLOW: _fail_result("daily down"),
            OP_NORTHBOUND: _ok_result(()),
        }
    )
    result = await _service(router).get(
        instrument=_equity(),
        metrics=(
            CapitalMetricType.MARGIN,
            CapitalMetricType.DAILY_FLOW,
            CapitalMetricType.NORTHBOUND,
        ),
        as_of=AS_OF,
    )
    assert result.ok is False and result.data is None
    assert tuple(item.component.value for item in result.provenance) == (
        "margin",
        "northbound",
    )
    assert tuple(item.meta for item in result.provenance) == (
        router.results["a_share.margin.v1"].meta,
        router.results[OP_NORTHBOUND].meta,
    )


def test_capital_failure_rejects_non_capital_provenance_component() -> None:
    unrelated = AShareComponentProvenance(
        component=AShareComponentType.QUOTE,
        meta=_meta(),
        reliability=None,
        is_authoritative=None,
        is_derived=False,
    )
    with pytest.raises(DataContractError, match="unrelated component"):
        AShareCapitalResult(
            ok=False,
            data=None,
            warnings=(),
            error=DataContractError("daily down"),
            provenance=(unrelated,),
        )


@pytest.mark.asyncio
async def test_explicit_metrics_all_required_success_order() -> None:
    router = _FakeRouter(
        {
            "a_share.margin.v1": _ok_result(()),
            OP_DAILY_FLOW: _ok_result((_flow_point(),)),
        }
    )
    svc = _service(router)
    result = await svc.get(
        instrument=_equity(),
        metrics=(CapitalMetricType.MARGIN, CapitalMetricType.DAILY_FLOW),
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.metrics == (
        CapitalMetricType.MARGIN,
        CapitalMetricType.DAILY_FLOW,
    )
    assert tuple(item.component.value for item in result.provenance) == (
        "margin",
        "daily_flow",
    )
    assert tuple(item.meta for item in result.provenance) == (
        router.results["a_share.margin.v1"].meta,
        router.results[OP_DAILY_FLOW].meta,
    )
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_required_components_all_settle_and_choose_frozen_failure_order() -> None:
    """A late unexpected failure cannot cancel a sibling or leak its text."""

    class _SettlingRouter(_FakeRouter):
        def __init__(self) -> None:
            super().__init__({})
            self.daily_finished = False

        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            op = kwargs["operation_name"]
            self.calls.append(kwargs)
            if op == OP_DAILY_FLOW:
                await asyncio.sleep(0)
                self.daily_finished = True
                return _ok_result((_flow_point(),))
            if op == "a_share.margin.v1":
                await asyncio.sleep(0.001)
                raise RuntimeError("provider body: secret-url-and-payload")
            raise AssertionError(f"unexpected operation {op}")

    router = _SettlingRouter()
    result = await _service(router).get(
        instrument=_equity(),
        metrics=(CapitalMetricType.MARGIN, CapitalMetricType.DAILY_FLOW),
        as_of=AS_OF,
    )

    assert router.daily_finished is True
    assert {call["operation_name"] for call in router.calls} == {
        "a_share.margin.v1",
        OP_DAILY_FLOW,
    }
    assert result.ok is False
    assert result.data is None
    assert result.error is not None
    assert str(result.error) == "Unexpected A-share component failure"
    assert "secret-url-and-payload" not in str(result.error)
    assert tuple(item.component.value for item in result.provenance) == ("daily_flow",)


@pytest.mark.asyncio
async def test_multiple_component_failures_use_metric_order_not_completion_order() -> None:
    class _TwoFailuresRouter(_FakeRouter):
        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            op = kwargs["operation_name"]
            self.calls.append(kwargs)
            if op == OP_DAILY_FLOW:
                await asyncio.sleep(0)
                raise DataContractError("daily failure")
            if op == "a_share.margin.v1":
                await asyncio.sleep(0.001)
                raise DataContractError("margin failure")
            raise AssertionError(f"unexpected operation {op}")

    result = await _service(_TwoFailuresRouter({})).get(
        instrument=_equity(),
        metrics=(CapitalMetricType.MARGIN, CapitalMetricType.DAILY_FLOW),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.error is not None
    assert str(result.error) == "margin failure"


@pytest.mark.asyncio
async def test_option_asset_rejected() -> None:
    router = _FakeRouter({})
    svc = _service(router)
    with pytest.raises(DataContractError):
        await svc.get(
            instrument=_equity(AssetType.OPTION),
            metrics=(CapitalMetricType.DAILY_FLOW,),
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_index_requires_northbound_only() -> None:
    router = _FakeRouter(
        {
            "a_share.northbound.v1": _ok_result(()),
        }
    )
    svc = _service(router)
    with pytest.raises(DataContractError):
        await svc.get(
            instrument=_equity(AssetType.INDEX),
            metrics=(CapitalMetricType.DAILY_FLOW,),
            as_of=AS_OF,
        )
    result = await svc.get(
        instrument=None,
        metrics=(CapitalMetricType.NORTHBOUND,),
        as_of=AS_OF,
    )
    assert result.ok is True


@pytest.mark.asyncio
async def test_deterministic_merge_under_out_of_order_completion() -> None:
    """TaskGroup completion order must not affect metrics field order."""
    order_log: list[str] = []

    class SlowRouter(_FakeRouter):
        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            op = kwargs["operation_name"]
            if op == OP_DAILY_FLOW:
                await asyncio.sleep(0.02)
                order_log.append("daily_done")
            else:
                order_log.append("margin_done")
            return await super().execute(**kwargs)

    router = SlowRouter(
        {
            OP_DAILY_FLOW: _ok_result((_flow_point(),)),
            "a_share.margin.v1": _ok_result(()),
        }
    )
    svc = _service(router)
    result = await svc.get(
        instrument=_equity(),
        metrics=(CapitalMetricType.DAILY_FLOW, CapitalMetricType.MARGIN),
        as_of=AS_OF,
    )
    assert result.ok
    assert result.data is not None
    assert result.data.metrics[0] is CapitalMetricType.DAILY_FLOW
    assert tuple(item.component.value for item in result.provenance) == (
        "daily_flow",
        "margin",
    )
    assert result.data.provenance == provenance_dtos(result.provenance)
    assert "margin_done" in order_log
    assert "daily_done" in order_log


@pytest.mark.asyncio
async def test_cancellation_no_orphan_tasks() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class HangRouter:
        async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return _ok_result(())

    svc = _service(HangRouter())  # type: ignore[arg-type]
    task = asyncio.create_task(
        svc.get(
            instrument=_equity(),
            metrics=(CapitalMetricType.DAILY_FLOW,),
            as_of=AS_OF,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Structured concurrency: hang is cancelled with the group.
    assert cancelled.is_set()


def test_protocol_runtime_checkable() -> None:
    assert isinstance(object(), AShareDailyFlowProvider) is False
    assert isinstance(object(), AShareIntradayFlowProvider) is False


def test_codec_rejects_float_in_domain() -> None:
    with pytest.raises(DataContractError):
        FundFlowPoint(
            occurred_at=AS_OF,
            interval=BarInterval.ONE_DAY,
            main_net_cny=1.5,  # type: ignore[arg-type]
            super_large_net_cny=None,
            large_net_cny=None,
            medium_net_cny=None,
            small_net_cny=None,
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=False,
        )


# --- Validator parity / malicious adapter injection ---------------------------


def _svc() -> AShareCapitalService:
    return _service(_FakeRouter({}))


def test_validate_fund_flow_interval_and_sorted_unique() -> None:
    svc = _svc()
    ok_daily = ProviderSuccess(
        value=(_flow_point(datetime(2024, 1, 14, 7, 0, tzinfo=UTC)), _flow_point()),
        meta=_meta(),
    )
    svc._validate_fund_flow(ok_daily, as_of=AS_OF, expected_interval=BarInterval.ONE_DAY)

    wrong_interval = ProviderSuccess(
        value=(
            FundFlowPoint(
                occurred_at=datetime(2024, 1, 15, 1, 30, tzinfo=UTC),
                interval=BarInterval.ONE_MINUTE,
                main_net_cny=Decimal("1"),
                super_large_net_cny=None,
                large_net_cny=None,
                medium_net_cny=None,
                small_net_cny=None,
                source_vendor=VendorId.EASTMONEY,
                reliability=ReliabilityLevel.MEDIUM,
                is_authoritative=False,
            ),
        ),
        meta=_meta(),
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_fund_flow(wrong_interval, as_of=AS_OF, expected_interval=BarInterval.ONE_DAY)
    assert ei.value.details.get("rule") == "interval"

    unsorted = ProviderSuccess(
        value=(_flow_point(), _flow_point(datetime(2024, 1, 14, 7, 0, tzinfo=UTC))),
        meta=_meta(),
    )
    with pytest.raises(DataContractError) as ei2:
        svc._validate_fund_flow(unsorted, as_of=AS_OF, expected_interval=BarInterval.ONE_DAY)
    assert ei2.value.details.get("rule") == "sorted_unique"

    with pytest.raises(DataContractError) as ei3:
        svc._validate_fund_flow(
            ProviderSuccess(value=[_flow_point()], meta=_meta()),  # type: ignore[arg-type]
            as_of=AS_OF,
            expected_interval=BarInterval.ONE_DAY,
        )
    assert ei3.value.details.get("rule") == "type"


def test_validate_fund_flow_rejects_wrong_category_and_element() -> None:
    svc = _svc()
    bad_cat = ProviderSuccess(
        value=(),
        meta=_meta(DataCategory.MARKET_QUOTE),
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_fund_flow(bad_cat, as_of=AS_OF, expected_interval=BarInterval.ONE_DAY)
    assert ei.value.details.get("rule") == "category"

    bad_el = ProviderSuccess(value=("not-a-point",), meta=_meta())  # type: ignore[arg-type]
    with pytest.raises(DataContractError) as ei2:
        svc._validate_fund_flow(bad_el, as_of=AS_OF, expected_interval=BarInterval.ONE_MINUTE)
    assert ei2.value.details.get("rule") == "type"


def test_validate_northbound_order_incomplete_and_meta() -> None:
    svc = _svc()
    incomplete = NorthboundFlowPoint(
        trade_date=date(2024, 1, 15),
        channel="sh",
        net_buy_cny=None,
        buy_cny=None,
        sell_cny=None,
        disclosure_note="aggregate-only disclosure",
        source_vendor=VendorId.HKEX,
        reliability=ReliabilityLevel.HIGH,
        is_authoritative=True,
    )
    sz = NorthboundFlowPoint(
        trade_date=date(2024, 1, 15),
        channel="sz",
        net_buy_cny=None,
        buy_cny=None,
        sell_cny=None,
        disclosure_note="aggregate-only disclosure",
        source_vendor=VendorId.HKEX,
        reliability=ReliabilityLevel.HIGH,
        is_authoritative=True,
    )
    meta = ProviderResultMeta(
        vendor=VendorId.HKEX,
        category=DataCategory.CAPITAL,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=("NORTHBOUND_DISCLOSURE_INCOMPLETE",),
    )
    svc._validate_northbound(
        ProviderSuccess(value=(incomplete, sz), meta=meta),
        as_of=AS_OF,
        start=None,
        end=None,
    )

    # Missing disclosure_note when all numeric None.
    blank_note = NorthboundFlowPoint(
        trade_date=date(2024, 1, 15),
        channel="sh",
        net_buy_cny=None,
        buy_cny=None,
        sell_cny=None,
        disclosure_note=None,
        source_vendor=VendorId.HKEX,
        reliability=ReliabilityLevel.HIGH,
        is_authoritative=True,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_northbound(
            ProviderSuccess(value=(blank_note,), meta=meta),
            as_of=AS_OF,
            start=None,
            end=None,
        )
    assert ei.value.details.get("rule") == "incomplete_disclosure"

    # Missing incomplete meta warning.
    meta_no_warn = ProviderResultMeta(
        vendor=VendorId.HKEX,
        category=DataCategory.CAPITAL,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )
    with pytest.raises(DataContractError) as ei2:
        svc._validate_northbound(
            ProviderSuccess(value=(incomplete,), meta=meta_no_warn),
            as_of=AS_OF,
            start=None,
            end=None,
        )
    assert ei2.value.details.get("rule") == "incomplete_disclosure"

    # Unsorted channel order (sz before sh).
    with pytest.raises(DataContractError) as ei3:
        svc._validate_northbound(
            ProviderSuccess(value=(sz, incomplete), meta=meta),
            as_of=AS_OF,
            start=None,
            end=None,
        )
    assert ei3.value.details.get("rule") == "sorted_unique"


def test_validate_dragon_tiger_unique_sorted_and_request_match() -> None:
    svc = _svc()
    inst = _equity()
    r1 = DragonTigerRecord(
        trade_date=date(2024, 1, 15),
        instrument_id=inst.instrument_id,
        reason="a-reason",
        buy_total_cny=Decimal("10"),
        sell_total_cny=Decimal("3"),
        net_buy_cny=Decimal("7"),
        seats=(),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    r2 = DragonTigerRecord(
        trade_date=date(2024, 1, 15),
        instrument_id=inst.instrument_id,
        reason="b-reason",
        buy_total_cny=Decimal("5"),
        sell_total_cny=Decimal("1"),
        net_buy_cny=Decimal("4"),
        seats=(),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    svc._validate_dragon_tiger(
        ProviderSuccess(value=(r1, r2), meta=_meta()),
        trade_date=date(2024, 1, 15),
        instrument=inst,
    )
    with pytest.raises(DataContractError) as ei:
        svc._validate_dragon_tiger(
            ProviderSuccess(value=(r2, r1), meta=_meta()),
            trade_date=date(2024, 1, 15),
            instrument=inst,
        )
    assert ei.value.details.get("rule") == "sorted_unique"
    with pytest.raises(DataContractError) as ei2:
        svc._validate_dragon_tiger(
            ProviderSuccess(value=(r1, r1), meta=_meta()),
            trade_date=date(2024, 1, 15),
            instrument=inst,
        )
    assert ei2.value.details.get("rule") == "unique"
    with pytest.raises(DataContractError) as ei3:
        svc._validate_dragon_tiger(
            ProviderSuccess(value=(r1,), meta=_meta()),
            trade_date=date(2024, 1, 14),
            instrument=inst,
        )
    assert ei3.value.details.get("rule") == "trade_date"


def test_validate_margin_block_shareholder_order_and_identity() -> None:
    svc = _svc()
    m1 = MarginRecord(
        trade_date=date(2024, 1, 15),
        financing_balance_cny=Decimal("1"),
        financing_buy_cny=Decimal("1"),
        financing_repayment_cny=Decimal("1"),
        securities_lending_balance_cny=None,
        securities_lending_sell_shares=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    m2 = MarginRecord(
        trade_date=date(2024, 1, 14),
        financing_balance_cny=Decimal("1"),
        financing_buy_cny=Decimal("1"),
        financing_repayment_cny=Decimal("1"),
        securities_lending_balance_cny=None,
        securities_lending_sell_shares=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    svc._validate_margin(ProviderSuccess(value=(m1, m2), meta=_meta()), as_of=AS_OF)
    with pytest.raises(DataContractError):
        svc._validate_margin(ProviderSuccess(value=(m2, m1), meta=_meta()), as_of=AS_OF)

    b1 = BlockTradeRecord(
        trade_date=date(2024, 1, 15),
        price=Decimal("10"),
        volume_shares=100,
        amount_cny=Decimal("1000"),
        premium_percent=None,
        buyer_branch="A",
        seller_branch="B",
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    b2 = BlockTradeRecord(
        trade_date=date(2024, 1, 14),
        price=Decimal("9"),
        volume_shares=50,
        amount_cny=Decimal("450"),
        premium_percent=None,
        buyer_branch=None,
        seller_branch=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    svc._validate_block_trades(ProviderSuccess(value=(b1, b2), meta=_meta()), as_of=AS_OF)
    with pytest.raises(DataContractError) as ei:
        svc._validate_block_trades(ProviderSuccess(value=(b1, b1), meta=_meta()), as_of=AS_OF)
    assert ei.value.details.get("rule") == "unique"

    sh = ShareholderCountRecord(
        period_end=date(2023, 12, 31),
        published_at=datetime(2024, 1, 10, 0, 0, tzinfo=UTC),
        shareholder_count=1000,
        change_percent=None,
        average_holding_shares=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    svc._validate_shareholder(ProviderSuccess(value=(sh,), meta=_meta()), as_of=AS_OF, now=AS_OF)


def test_shareholder_unknown_published_at_historical_rejected() -> None:
    svc = _svc()
    unknown = ShareholderCountRecord(
        period_end=date(2023, 12, 31),
        published_at=None,
        shareholder_count=1000,
        change_percent=None,
        average_holding_shares=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    historical_as_of = AS_OF - timedelta(hours=2)
    with pytest.raises(DataContractError) as ei:
        svc._validate_shareholder(
            ProviderSuccess(value=(unknown,), meta=_meta()),
            as_of=historical_as_of,
            now=AS_OF,
        )
    assert ei.value.details.get("rule") == "historical_requires_published_at"

    # Current window allows unknown published_at.
    svc._validate_shareholder(
        ProviderSuccess(value=(unknown,), meta=_meta()),
        as_of=AS_OF - timedelta(seconds=60),
        now=AS_OF,
    )


def test_corporate_actions_unknown_published_at_historical_rejected() -> None:
    svc = _svc()
    unlock = UnlockRecord(
        unlock_date=date(2024, 1, 10),
        published_at=None,
        unlock_type="lockup",
        unlock_shares=100,
        tradable_shares=None,
        market_value_cny=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    success = ProviderSuccess(value=(unlock,), meta=_meta(DataCategory.CORPORATE_ACTIONS))
    with pytest.raises(DataContractError) as ei:
        svc._validate_corporate_actions(success, as_of=AS_OF - timedelta(hours=1), now=AS_OF)
    assert ei.value.details.get("rule") == "historical_requires_published_at"
    svc._validate_corporate_actions(success, as_of=AS_OF, now=AS_OF)


def test_validate_rejects_wrong_container_type_before_conversion() -> None:
    svc = _svc()
    with pytest.raises(DataContractError):
        svc._validate_margin(
            ProviderSuccess(value=[object()], meta=_meta()),  # type: ignore[arg-type]
            as_of=AS_OF,
        )
    with pytest.raises(DataContractError):
        svc._validate_chip(
            ProviderSuccess(value="not-chip", meta=_meta()),  # type: ignore[arg-type]
            instrument=_equity(),
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_required_failure_preserves_prior_and_fail_warnings() -> None:
    daily_w = WarningInfo(code="DAILY_OK_W", message="daily ok", details={})
    margin_w = WarningInfo(code="MARGIN_FAIL_W", message="margin fail", details={})
    router = _FakeRouter(
        {
            OP_DAILY_FLOW: _ok_result((_flow_point(),), result_warnings=(daily_w,)),
            "a_share.margin.v1": _fail_result("margin down", result_warnings=(margin_w,)),
        }
    )
    svc = _service(router)
    result = await svc.get(
        instrument=_equity(),
        metrics=(CapitalMetricType.DAILY_FLOW, CapitalMetricType.MARGIN),
        as_of=AS_OF,
    )
    assert result.ok is False
    codes = [w.code for w in result.warnings]
    assert "DAILY_OK_W" in codes
    assert "MARGIN_FAIL_W" in codes


@pytest.mark.asyncio
async def test_northbound_incomplete_meta_warning_surfaces() -> None:
    nb = (
        NorthboundFlowPoint(
            trade_date=date(2024, 1, 15),
            channel="sh",
            net_buy_cny=None,
            buy_cny=None,
            sell_cny=None,
            disclosure_note="aggregate-only",
            source_vendor=VendorId.HKEX,
            reliability=ReliabilityLevel.HIGH,
            is_authoritative=True,
        ),
    )
    meta = ProviderResultMeta(
        vendor=VendorId.HKEX,
        category=DataCategory.CAPITAL,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=("NORTHBOUND_DISCLOSURE_INCOMPLETE",),
    )
    router = _FakeRouter(
        {
            OP_NORTHBOUND: RouterExecutionResult(
                value=nb,
                ok=True,
                criticality=DataCriticality.CORE,
                meta=meta,
                attempts=(),
                warnings=(),
                error=None,
            )
        }
    )
    svc = _service(router)
    result = await svc.get(
        instrument=None,
        metrics=(CapitalMetricType.NORTHBOUND,),
        as_of=AS_OF,
    )
    assert result.ok is True
    assert any(w.code == "NORTHBOUND_DISCLOSURE_INCOMPLETE" for w in result.warnings)


@pytest.mark.asyncio
async def test_does_not_invent_low_reliability_for_high_data() -> None:
    nb = (
        NorthboundFlowPoint(
            trade_date=date(2024, 1, 15),
            channel="sh",
            net_buy_cny=None,
            buy_cny=None,
            sell_cny=None,
            disclosure_note="aggregate-only",
            source_vendor=VendorId.HKEX,
            reliability=ReliabilityLevel.HIGH,
            is_authoritative=True,
        ),
    )
    meta = ProviderResultMeta(
        vendor=VendorId.HKEX,
        category=DataCategory.CAPITAL,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=("NORTHBOUND_DISCLOSURE_INCOMPLETE", "UNRECOGNIZED_META"),
    )
    router = _FakeRouter(
        {
            OP_NORTHBOUND: RouterExecutionResult(
                value=nb,
                ok=True,
                criticality=DataCriticality.CORE,
                meta=meta,
                attempts=(),
                warnings=(),
                error=None,
            )
        }
    )
    svc = _service(router)
    result = await svc.get(
        instrument=None,
        metrics=(CapitalMetricType.NORTHBOUND,),
        as_of=AS_OF,
    )
    codes = [w.code for w in result.warnings]
    assert "LOW_RELIABILITY_MARKET_SIGNAL" not in codes
    assert "UNRECOGNIZED_META" not in codes
    assert "NORTHBOUND_DISCLOSURE_INCOMPLETE" in codes


@pytest.mark.asyncio
async def test_explicit_chip_required_fails_when_provider_unavailable() -> None:
    router = _FakeRouter({"a_share.chip_distribution.v2": _fail_result("chip upstream unverified")})
    svc = _service(router)
    result = await svc.get(
        instrument=_equity(),
        metrics=(CapitalMetricType.CHIP_DISTRIBUTION,),
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.data is None
    assert result.error is not None


@pytest.mark.asyncio
async def test_chip_derived_warning_reaches_final_capital_result() -> None:
    router = _FakeRouter(
        {
            "a_share.chip_distribution.v2": _ok_result(
                _chip_snapshot(), meta_warnings=("DERIVED_CHIP_DISTRIBUTION",)
            )
        }
    )
    result = await _service(router).get(
        instrument=_equity(),
        metrics=(CapitalMetricType.CHIP_DISTRIBUTION,),
        as_of=AS_OF,
    )
    assert result.ok is True
    assert [warning.code for warning in result.warnings] == ["DERIVED_CHIP_DISTRIBUTION"]


@pytest.mark.asyncio
async def test_chip_non_equity_is_rejected_before_router() -> None:
    router = _FakeRouter({})
    with pytest.raises(DataContractError) as exc:
        await _service(router).get(
            instrument=_equity(AssetType.ETF),
            metrics=(CapitalMetricType.CHIP_DISTRIBUTION,),
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "asset_support"
    assert router.calls == []


def test_current_window_seconds_constructor_rejects_negative() -> None:
    with pytest.raises(DataContractError):
        AShareCapitalService(
            router=_FakeRouter({}),  # type: ignore[arg-type]
            clock=_Clock(AS_OF),
            calendar=_Calendar(),
            intraday_flow_codec=intraday_flow_codec(),
            daily_flow_codec=daily_flow_codec(),
            northbound_codec=northbound_codec(),
            dragon_tiger_codec=dragon_tiger_codec(),
            margin_codec=margin_codec(),
            block_trades_codec=block_trades_codec(),
            shareholder_counts_codec=shareholder_counts_codec(),
            chip_distribution_codec=chip_distribution_codec(),
            corporate_actions_codec=corporate_actions_codec(),
            current_window_seconds=-1,
        )
