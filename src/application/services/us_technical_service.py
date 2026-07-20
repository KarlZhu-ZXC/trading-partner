"""Deterministic US technical indicator service (Phase 1F F3a).

Pure ``Decimal`` algorithms over routed daily bars. No infrastructure,
provider, or float math. Indicators that lack sufficient window are ``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from application.ports.clock import Clock
from application.services.us_market_data_service import USMarketDataService
from domain.common.enums import AdjustmentMethod, AssetType, Market
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    StaleMarketData,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.models import MarketBar, TechnicalIndicators
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USTechnicalSnapshot

_NEW_YORK = ZoneInfo("America/New_York")
_QUOTE_ASSET_TYPES = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_ALGORITHM_VERSION = "tp_technical_v1"
_SUPPORT_RESISTANCE_METHOD = "rolling_extrema_20_v1"
_LOOKBACK_MIN = 20
_LOOKBACK_MAX = 1000
_STALE_MAX_CALENDAR_DAYS = 4
_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")
_HUNDRED = Decimal("100")
_PRECISION = 50


def _require_strict_int(value: object, *, field: str) -> int:
    if type(value) is not int:
        raise DataContractError(
            f"{field} must be a strict int",
            details={"field": field, "rule": "int_type", "type": type(value).__name__},
        )
    return value


def _sma_latest(values: Sequence[Decimal], n: int) -> Decimal | None:
    if len(values) < n:
        return None
    window = values[-n:]
    return sum(window, _ZERO) / Decimal(n)


def _ema_series(values: Sequence[Decimal], n: int) -> list[Decimal | None]:
    """EMA-N: seed SMA of first N, then alpha=2/(N+1) recurrence. Sparse prefix None."""
    length = len(values)
    out: list[Decimal | None] = [None] * length
    if length < n:
        return out
    alpha = _TWO / Decimal(n + 1)
    prev = sum(values[:n], _ZERO) / Decimal(n)
    out[n - 1] = prev
    one_minus = _ONE - alpha
    for i in range(n, length):
        prev = alpha * values[i] + one_minus * prev
        out[i] = prev
    return out


def _ema_latest(values: Sequence[Decimal], n: int) -> Decimal | None:
    series = _ema_series(values, n)
    return series[-1] if series else None


def _macd_latest(
    closes: Sequence[Decimal],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_seq: list[Decimal] = []
    for i in range(len(closes)):
        e12, e26 = ema12[i], ema26[i]
        if e12 is not None and e26 is not None:
            macd_seq.append(e12 - e26)
    if not macd_seq:
        return None, None, None
    macd = macd_seq[-1]
    signal_series = _ema_series(macd_seq, 9)
    signal = signal_series[-1]
    if signal is None:
        return macd, None, None
    return macd, signal, macd - signal


def _rsi14_latest(closes: Sequence[Decimal]) -> Decimal | None:
    # Need 15 closes for 14 deltas seed, then Wilder recurrence to the end.
    if len(closes) < 15:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > _ZERO:
            gains.append(delta)
            losses.append(_ZERO)
        elif delta < _ZERO:
            gains.append(_ZERO)
            losses.append(-delta)
        else:
            gains.append(_ZERO)
            losses.append(_ZERO)
    # Seed: arithmetic mean of first 14 deltas (indices 0..13 of gain/loss lists).
    avg_gain = sum(gains[:14], _ZERO) / Decimal(14)
    avg_loss = sum(losses[:14], _ZERO) / Decimal(14)
    for i in range(14, len(gains)):
        avg_gain = ((avg_gain * Decimal(13)) + gains[i]) / Decimal(14)
        avg_loss = ((avg_loss * Decimal(13)) + losses[i]) / Decimal(14)
    if avg_loss == _ZERO and avg_gain == _ZERO:
        return None
    if avg_loss == _ZERO:
        return _HUNDRED
    rs = avg_gain / avg_loss
    return _HUNDRED - (_HUNDRED / (_ONE + rs))


def _bollinger20_latest(
    closes: Sequence[Decimal],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if len(closes) < 20:
        return None, None, None
    window = closes[-20:]
    mid = sum(window, _ZERO) / Decimal(20)
    var = sum((x - mid) ** 2 for x in window) / Decimal(20)
    # Decimal.sqrt requires non-negative finite; population variance is.
    sigma = var.sqrt()
    two_sigma = _TWO * sigma
    return mid, mid + two_sigma, mid - two_sigma


def _true_ranges(bars: Sequence[MarketBar]) -> list[Decimal]:
    if not bars:
        return []
    trs: list[Decimal] = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        bar = bars[i]
        prev_close = bars[i - 1].close
        hl = bar.high - bar.low
        hc = abs(bar.high - prev_close)
        lc = abs(bar.low - prev_close)
        trs.append(max(hl, hc, lc))
    return trs


def _atr14_latest(bars: Sequence[MarketBar]) -> Decimal | None:
    trs = _true_ranges(bars)
    if len(trs) < 14:
        return None
    atr = sum(trs[:14], _ZERO) / Decimal(14)
    for i in range(14, len(trs)):
        atr = ((atr * Decimal(13)) + trs[i]) / Decimal(14)
    return atr


def _vwma20_latest(bars: Sequence[MarketBar]) -> Decimal | None:
    if len(bars) < 20:
        return None
    window = bars[-20:]
    total_vol = sum((b.volume for b in window), _ZERO)
    if total_vol == _ZERO:
        return None
    weighted = sum((b.close * b.volume for b in window), _ZERO)
    return weighted / total_vol


def _mfi14_latest(bars: Sequence[MarketBar]) -> Decimal | None:
    # 15 bars → 14 typical transitions.
    if len(bars) < 15:
        return None
    typicals = [(b.high + b.low + b.close) / Decimal(3) for b in bars]
    pos_flows: list[Decimal] = []
    neg_flows: list[Decimal] = []
    for i in range(1, len(bars)):
        flow = typicals[i] * bars[i].volume
        if typicals[i] > typicals[i - 1]:
            pos_flows.append(flow)
            neg_flows.append(_ZERO)
        elif typicals[i] < typicals[i - 1]:
            pos_flows.append(_ZERO)
            neg_flows.append(flow)
        else:
            pos_flows.append(_ZERO)
            neg_flows.append(_ZERO)
    # Last 14 transitions.
    pos = sum(pos_flows[-14:], _ZERO)
    neg = sum(neg_flows[-14:], _ZERO)
    if pos == _ZERO and neg == _ZERO:
        return None
    if neg == _ZERO:
        return _HUNDRED
    ratio = pos / neg
    return _HUNDRED - (_HUNDRED / (_ONE + ratio))


def _support_resistance(
    bars: Sequence[MarketBar],
) -> tuple[Decimal | None, Decimal | None]:
    if len(bars) < 20:
        return None, None
    window = bars[-20:]
    support = min(b.low for b in window)
    resistance = max(b.high for b in window)
    return support, resistance


def compute_indicators(
    bars: Sequence[MarketBar],
) -> tuple[TechnicalIndicators, Decimal | None, Decimal | None]:
    """Compute all tp_technical_v1 fields from ascending session bars."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        closes = [b.close for b in bars]
        macd, signal, hist = _macd_latest(closes)
        mid, upper, lower = _bollinger20_latest(closes)
        support, resistance = _support_resistance(bars)
        indicators = TechnicalIndicators(
            ema_10=_ema_latest(closes, 10),
            sma_50=_sma_latest(closes, 50),
            sma_200=_sma_latest(closes, 200),
            rsi_14=_rsi14_latest(closes),
            macd=macd,
            macd_signal=signal,
            macd_histogram=hist,
            atr_14=_atr14_latest(bars),
            bollinger_mid=mid,
            bollinger_upper=upper,
            bollinger_lower=lower,
            vwma=_vwma20_latest(bars),
            mfi=_mfi14_latest(bars),
        )
        return indicators, support, resistance


class USTechnicalService:
    """F3a: deterministic technical snapshot from routed daily bars."""

    def __init__(self, data_service: USMarketDataService, clock: Clock) -> None:
        if data_service is None or clock is None:
            raise DataContractError(
                "data_service and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        self._data_service = data_service
        self._clock = clock

    def _require_us_tradable(self, instrument: Instrument) -> None:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.US:
            raise DataContractError(
                "instrument market must be US",
                details={"field": "instrument", "rule": "market"},
            )
        if instrument.asset_type not in _QUOTE_ASSET_TYPES:
            raise DataContractError(
                "instrument asset_type must be equity, etf, or index",
                details={
                    "field": "instrument",
                    "rule": "asset_type",
                    "asset_type": instrument.asset_type.value,
                },
            )

    def _require_as_of_not_future(self, as_of: datetime) -> None:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )

    def _require_lookback(self, lookback_sessions: object) -> int:
        n = _require_strict_int(lookback_sessions, field="lookback_sessions")
        if n < _LOOKBACK_MIN or n > _LOOKBACK_MAX:
            raise DataContractError(
                "lookback_sessions must be between 20 and 1000 inclusive",
                details={
                    "field": "lookback_sessions",
                    "rule": "range",
                    "min": _LOOKBACK_MIN,
                    "max": _LOOKBACK_MAX,
                },
            )
        return n

    @staticmethod
    def _assert_not_stale(*, bar_as_of: datetime, as_of: datetime) -> None:
        bar_day = bar_as_of.astimezone(_NEW_YORK).date()
        as_of_day = as_of.astimezone(_NEW_YORK).date()
        age_days = (as_of_day - bar_day).days
        if age_days > _STALE_MAX_CALENDAR_DAYS:
            raise StaleMarketData(
                "latest bar exceeds max calendar age for technicals",
                details={
                    "field": "bar_as_of",
                    "rule": "max_calendar_age_exceeded",
                },
            )

    def build_snapshot(
        self,
        instrument: Instrument,
        *,
        series: USBarSeries,
        as_of: datetime,
        lookback_sessions: int = 260,
    ) -> USTechnicalSnapshot:
        """Compute technicals from an already-fetched bar series (no provider I/O).

        Eligibility (as_of cutoff), lookback windowing, stale guard, and indicator
        math live here so coordinators can share one bars fetch with composites.
        """
        self._require_us_tradable(instrument)
        self._require_as_of_not_future(as_of)
        if not isinstance(series, USBarSeries):
            raise DataContractError(
                "bars value must be USBarSeries",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(series).__name__,
                },
            )
        if series.instrument_id != instrument.instrument_id:
            raise DataContractError(
                "bars instrument_id must match instrument",
                details={"field": "series.instrument_id", "rule": "same_instrument"},
            )
        if series.interval is not USBarInterval.ONE_DAY:
            raise DataContractError(
                "technical snapshot requires daily bars",
                details={"field": "series.interval", "rule": "daily_only"},
            )
        if series.adjustment is not AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED:
            raise DataContractError(
                "technical snapshot requires split-and-dividend-adjusted bars",
                details={
                    "field": "series.adjustment",
                    "rule": "split_and_dividend_adjusted_only",
                },
            )
        lookback = self._require_lookback(lookback_sessions)

        eligible = [b for b in series.bars if b.timestamp <= as_of]
        if not eligible:
            raise NoMarketData(
                "no bars available at or before as_of",
                details={"field": "bars", "rule": "nonempty"},
            )
        window = eligible[-lookback:]
        bar_as_of = window[-1].timestamp
        self._assert_not_stale(bar_as_of=bar_as_of, as_of=as_of)

        indicators, support, resistance = compute_indicators(window)
        return USTechnicalSnapshot(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            bar_as_of=bar_as_of,
            indicators=indicators,
            support=support,
            resistance=resistance,
            algorithm_version=_ALGORITHM_VERSION,
            historically_validated=False,
            support_resistance_method=_SUPPORT_RESISTANCE_METHOD,
        )

    async def get_snapshot(
        self,
        instrument: Instrument,
        *,
        as_of: datetime,
        lookback_sessions: int = 260,
    ) -> USTechnicalSnapshot:
        self._require_us_tradable(instrument)
        self._require_as_of_not_future(as_of)
        lookback = self._require_lookback(lookback_sessions)

        as_of_ny_date = as_of.astimezone(_NEW_YORK).date()
        start = as_of_ny_date - timedelta(days=lookback * 2)
        end = as_of_ny_date

        result = await self._data_service.get_bars(
            instrument,
            start=start,
            end=end,
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
            as_of=as_of,
        )
        if not result.ok:
            error = result.error
            if error is None:
                raise DataContractError(
                    "router failure missing typed error",
                    details={"field": "error", "rule": "required"},
                )
            if not isinstance(error, TradingPartnerError):
                raise DataContractError(
                    "router error must be TradingPartnerError",
                    details={"field": "error", "rule": "type"},
                )
            raise error

        series = result.value
        if not isinstance(series, USBarSeries):
            raise DataContractError(
                "bars value must be USBarSeries",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(series).__name__,
                },
            )

        return self.build_snapshot(
            instrument,
            series=series,
            as_of=as_of,
            lookback_sessions=lookback,
        )
