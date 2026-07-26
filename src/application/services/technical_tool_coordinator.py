"""Cross-market Phase 2D technical-analysis and chart coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import (
    ProviderResultMeta,
    RouterExecutionResult,
)
from application.dto.technical import (
    TechnicalAnalysisDTO,
    TechnicalAnalysisInput,
    TechnicalChartInput,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.ports.technical_chart_renderer import TechnicalChartRenderer
from application.ports.technical_indicator_engine import TechnicalIndicatorEngine
from application.services.a_share_market_structure_service import AShareMarketStructureService
from application.services.commodity_spot_service import CommoditySpotService
from application.services.instrument_master_service import InstrumentMasterService
from application.services.us_market_data_service import USMarketDataService
from domain.a_share.enums import BarInterval
from domain.a_share.models import AShareBar
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    DataCriticality,
    Freshness,
    Market,
    SourceRole,
)
from domain.common.errors import DataContractError, NoMarketData, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.cross_asset.enums import OfferSide
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.technical.models import TechnicalAnalysis, TechnicalTimeframe
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries

_NEW_YORK = ZoneInfo("America/New_York")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class TechnicalChartArtifact:
    envelope: ToolEnvelope[TechnicalAnalysisDTO]
    png: bytes | None


def _weekly_bars(daily: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
    groups: list[list[MarketBar]] = []
    key: tuple[int, int] | None = None
    for bar in daily:
        iso = bar.timestamp.isocalendar()
        current = (iso.year, iso.week)
        if current != key:
            groups.append([])
            key = current
        groups[-1].append(bar)
    return tuple(
        MarketBar(
            timestamp=group[-1].timestamp,
            open=group[0].open,
            high=max(bar.high for bar in group),
            low=min(bar.low for bar in group),
            close=group[-1].close,
            volume=sum((bar.volume for bar in group), Decimal("0")),
        )
        for group in groups
    )


def _source(meta: ProviderResultMeta | None) -> tuple[SourceReference, ...]:
    if meta is None:
        return ()
    return (
        SourceReference(
            name=meta.vendor.value,
            role=meta.role,
            retrieved_at=meta.fetched_at,
            data_delay_seconds=meta.data_delay_seconds,
        ),
    )


def _warnings(result: RouterExecutionResult[object]) -> tuple[WarningInfo, ...]:
    merged = list(result.warnings)
    seen = {warning.code for warning in merged}
    if result.meta is not None:
        for code in result.meta.warnings:
            if code not in seen:
                seen.add(code)
                messages = {
                    "FUTURES_CONTRACT_NOT_SPOT": (
                        "Technical levels use an exchange-traded futures proxy, not "
                        "the exact OTC spot instrument."
                    ),
                    "CONTINUOUS_FUTURES_ROLL_RISK": (
                        "The continuous front-month future can shift at contract roll."
                    ),
                }
                merged.append(
                    WarningInfo(
                        code=code,
                        message=messages.get(code, "Provider supplied this warning code."),
                    )
                )
        if result.meta.role is SourceRole.FALLBACK and "FALLBACK_SOURCE" not in seen:
            merged.append(
                WarningInfo(
                    code="FALLBACK_SOURCE",
                    message="The configured fallback market-data source was used.",
                )
            )
        if (
            result.meta.freshness is not Freshness.FRESH
            and "TECHNICAL_DATA_NOT_FRESH" not in seen
        ):
            merged.append(
                WarningInfo(
                    code="TECHNICAL_DATA_NOT_FRESH",
                    message="Technical analysis uses delayed, stale, or unknown-freshness bars.",
                    details={"freshness": result.meta.freshness.value},
                )
            )
    return tuple(merged)


class TechnicalToolCoordinator:
    def __init__(
        self,
        *,
        instrument_master: InstrumentMasterService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        us_data_service: USMarketDataService,
        a_share_data_service: AShareMarketStructureService,
        indicator_engine: TechnicalIndicatorEngine,
        chart_renderer: TechnicalChartRenderer,
        commodity_spot_service: CommoditySpotService | None = None,
    ) -> None:
        self._instrument_master = instrument_master
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._us_data_service = us_data_service
        self._a_share_data_service = a_share_data_service
        self._indicator_engine = indicator_engine
        self._chart_renderer = chart_renderer
        self._commodity_spot = commodity_spot_service

    async def get_snapshot(
        self, request: TechnicalAnalysisInput
    ) -> ToolEnvelope[TechnicalAnalysisDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        as_of = request.as_of or self._clock.now()
        try:
            instrument = self._instrument_master.get(request.instrument_id)
            result, bars, basis = await self._load_daily_bars(
                instrument, as_of=as_of, lookback=request.lookback_sessions
            )
            if not result.ok or result.meta is None:
                return self._provider_failure(request_id, as_of, instrument.market, result)
            timeframes = self._analyze_intervals(bars, request.intervals)
            analysis = TechnicalAnalysis(
                instrument_id=instrument.instrument_id,
                market=instrument.market,
                as_of=as_of,
                timeframes=timeframes,
                price_basis=basis,
            )
            warnings = _warnings(result)
            return ToolEnvelope.success(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=result.meta.fetched_at,
                freshness=result.meta.freshness,
                sources=_source(result.meta),
                data=TechnicalAnalysisDTO.from_domain(analysis),
                degraded=result.meta.freshness is not Freshness.FRESH or bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return self._exception_failure(request_id, as_of, exc)

    async def render_chart(self, request: TechnicalChartInput) -> TechnicalChartArtifact:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        as_of = request.as_of or self._clock.now()
        try:
            instrument = self._instrument_master.get(request.instrument_id)
            result, daily, basis = await self._load_daily_bars(
                instrument, as_of=as_of, lookback=request.lookback_sessions
            )
            if not result.ok or result.meta is None:
                envelope = self._provider_failure(request_id, as_of, instrument.market, result)
                return TechnicalChartArtifact(envelope=envelope, png=None)
            bars = daily if request.interval == "1d" else _weekly_bars(daily)
            analysis = self._indicator_engine.analyze(bars, interval=request.interval)
            domain = TechnicalAnalysis(
                instrument_id=instrument.instrument_id,
                market=instrument.market,
                as_of=as_of,
                timeframes=(analysis,),
                price_basis=basis,
            )
            warnings = _warnings(result)
            envelope = ToolEnvelope.success(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=result.meta.fetched_at,
                freshness=result.meta.freshness,
                sources=_source(result.meta),
                data=TechnicalAnalysisDTO.from_domain(domain),
                degraded=result.meta.freshness is not Freshness.FRESH or bool(warnings),
                warnings=warnings,
            )
            png = self._chart_renderer.render(
                instrument_id=instrument.instrument_id,
                bars=bars,
                analysis=analysis,
            )
            return TechnicalChartArtifact(envelope=envelope, png=png)
        except Exception as exc:  # noqa: BLE001
            return TechnicalChartArtifact(
                envelope=self._exception_failure(request_id, as_of, exc), png=None
            )

    def _analyze_intervals(
        self, daily: tuple[MarketBar, ...], intervals: tuple[str, ...]
    ) -> tuple[TechnicalTimeframe, ...]:
        out: list[TechnicalTimeframe] = []
        for interval in intervals:
            bars = daily if interval == "1d" else _weekly_bars(daily)
            out.append(self._indicator_engine.analyze(bars, interval=interval))
        return tuple(out)

    async def _load_daily_bars(
        self, instrument: Instrument, *, as_of: datetime, lookback: int
    ) -> tuple[RouterExecutionResult[object], tuple[MarketBar, ...], str]:
        if instrument.market is Market.DCE:
            error = NoMarketData(
                "DCE futures have no OHLCV path in Phase 3A; "
                "technical analysis is unavailable",
                details={
                    "code": "DCE_OHLCV_UNAVAILABLE",
                    "instrument_id": instrument.instrument_id,
                },
            )
            return (
                RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=DataCriticality.CORE,
                    meta=None,
                    attempts=(),
                    warnings=(),
                    error=error,
                ),
                (),
                "unavailable",
            )
        if instrument.market in {Market.US, Market.CME}:
            day = as_of.astimezone(_NEW_YORK).date()
            is_future = instrument.asset_type is AssetType.FUTURE
            adjustment = (
                AdjustmentMethod.NONE
                if is_future
                else AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
            )
            if instrument.market is Market.CME:
                basis = "unadjusted_specific_futures_close"
            elif is_future:
                basis = "unadjusted_front_month_continuous_futures_close"
            else:
                basis = "split_and_dividend_adjusted_daily_close"
            us_result = await self._us_data_service.get_bars(
                instrument,
                start=day - timedelta(days=lookback * 2),
                end=day,
                interval=USBarInterval.ONE_DAY,
                adjustment=adjustment,
                as_of=as_of,
            )
            if not us_result.ok or not isinstance(us_result.value, USBarSeries):
                return us_result, (), basis
            return (
                us_result,
                tuple(us_result.value.bars[-lookback:]),
                basis,
            )
        if instrument.market is Market.OTC and instrument.asset_type in {
            AssetType.COMMODITY_SPOT,
            AssetType.CFD,
        }:
            return await self._load_otc_daily_bars(
                instrument, as_of=as_of, lookback=lookback
            )
        if instrument.market is Market.A_SHARE:
            day = as_of.astimezone(_SHANGHAI).date()
            a_result = await self._a_share_data_service.get_bars(
                instrument,
                start=day - timedelta(days=lookback * 2),
                end=day,
                interval=BarInterval.ONE_DAY,
                adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
                as_of=as_of,
            )
            if not a_result.ok or not isinstance(a_result.value, tuple):
                return a_result, (), "forward_adjusted_daily_close"
            a_bars = a_result.value[-lookback:]
            bars = tuple(
                MarketBar(
                    timestamp=bar.end_at,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=Decimal(bar.volume_shares),
                )
                for bar in a_bars
                if isinstance(bar, AShareBar)
            )
            return a_result, bars, "forward_adjusted_daily_close"
        raise DataContractError(
            "technical analysis supports A_SHARE, US, CME, and OTC markets",
            details={
                "market": instrument.market.value,
                "asset_type": instrument.asset_type.value,
            },
        )

    async def _load_otc_daily_bars(
        self, instrument: Instrument, *, as_of: datetime, lookback: int
    ) -> tuple[RouterExecutionResult[object], tuple[MarketBar, ...], str]:
        basis = "unadjusted_otc_broker_daily_close"
        if self._commodity_spot is None:
            error = NoMarketData(
                "commodity spot service is not configured",
                details={"code": "PROVIDER_NOT_CONFIGURED"},
            )
            return (
                RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=DataCriticality.CORE,
                    meta=None,
                    attempts=(),
                    warnings=(),
                    error=error,
                ),
                (),
                basis,
            )
        day = as_of.astimezone(_NEW_YORK).date()
        result = await self._commodity_spot.get_bars(
            instrument,
            start=day - timedelta(days=lookback * 2),
            end=day,
            interval=USBarInterval.ONE_DAY,
            as_of=as_of,
            offer_side=OfferSide.BID,
            adjustment=AdjustmentMethod.NONE,
        )
        if not result.ok or result.data is None:
            return (
                RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=DataCriticality.CORE,
                    meta=None,
                    attempts=(),
                    warnings=result.warnings,
                    error=result.error
                    or NoMarketData(
                        "commodity spot bars unavailable",
                        details={"code": "NO_MARKET_DATA"},
                    ),
                ),
                (),
                basis,
            )
        bars = tuple(
            MarketBar(
                timestamp=bar.timestamp,
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=Decimal(str(bar.volume)),
            )
            for bar in result.data.bars[-lookback:]
        )
        meta = result.meta
        if meta is None:
            return (
                RouterExecutionResult(
                    value=None,
                    ok=False,
                    criticality=DataCriticality.CORE,
                    meta=None,
                    attempts=(),
                    warnings=result.warnings,
                    error=DataContractError(
                        "commodity spot provider omitted result metadata",
                        details={"code": "DATA_CONTRACT_ERROR"},
                    ),
                ),
                (),
                basis,
            )
        return (
            RouterExecutionResult(
                value=result.data,
                ok=True,
                criticality=DataCriticality.CORE,
                meta=meta,
                attempts=(),
                warnings=result.warnings,
                error=None,
            ),
            bars,
            basis,
        )

    def _provider_failure(
        self,
        request_id: str,
        as_of: datetime,
        market: Market,
        result: RouterExecutionResult[object],
    ) -> ToolEnvelope[TechnicalAnalysisDTO]:
        error = result.error or DataContractError("market-data provider returned no bars")
        return ToolEnvelope.failure(
            request_id=request_id,
            market=market,
            as_of=as_of,
            fetched_at=result.meta.fetched_at if result.meta else self._clock.now(),
            freshness=result.meta.freshness if result.meta else Freshness.UNKNOWN,
            sources=_source(result.meta),
            errors=[
                to_error_info(error, self._secret_redactor)
                if isinstance(error, TradingPartnerError)
                else to_error_info_from_exception(error, self._secret_redactor)
            ],
            warnings=_warnings(result),
        )

    def _exception_failure(
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[TechnicalAnalysisDTO]:
        error = (
            to_error_info(exc, self._secret_redactor)
            if isinstance(exc, TradingPartnerError)
            else to_error_info_from_exception(exc, self._secret_redactor)
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=self._clock.now(),
            errors=[error],
        )
