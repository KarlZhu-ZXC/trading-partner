"""Cross-market Phase 2D technical-analysis and chart coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
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
from application.services.instrument_master_service import InstrumentMasterService
from application.services.us_market_data_service import USMarketDataService
from domain.a_share.enums import BarInterval
from domain.a_share.models import AShareBar
from domain.common.enums import AdjustmentMethod, Freshness, Market, SourceRole
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix
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
                merged.append(
                    WarningInfo(code=code, message="Provider supplied this warning code.")
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
    ) -> None:
        self._instrument_master = instrument_master
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._us_data_service = us_data_service
        self._a_share_data_service = a_share_data_service
        self._indicator_engine = indicator_engine
        self._chart_renderer = chart_renderer

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
        if instrument.market is Market.US:
            day = as_of.astimezone(_NEW_YORK).date()
            us_result = await self._us_data_service.get_bars(
                instrument,
                start=day - timedelta(days=lookback * 2),
                end=day,
                interval=USBarInterval.ONE_DAY,
                adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
                as_of=as_of,
            )
            if not us_result.ok or not isinstance(us_result.value, USBarSeries):
                return us_result, (), "split_and_dividend_adjusted_daily_close"
            return (
                us_result,
                tuple(us_result.value.bars[-lookback:]),
                "split_and_dividend_adjusted_daily_close",
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
        raise DataContractError("technical analysis supports A_SHARE and US markets")

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
