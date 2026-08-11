"""Phase 1F F1: compact US domain/DTO/settings contract tests."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetSnapshotInput,
    TechnicalGetSnapshotInput,
    USBarSeriesDTO,
    USGetSnapshotInput,
    USQuoteDTO,
)
from domain.common.enums import AdjustmentMethod, AppEnvironment, LogLevel, TradingSession
from domain.common.errors import DataContractError
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USQuote
from infrastructure.config.settings import AppSettings

NY = ZoneInfo("America/New_York")
QUOTE_AT = datetime(2026, 7, 17, 15, 30, tzinfo=NY)
BAR_TS = datetime(2026, 7, 17, 16, 0, tzinfo=NY)
INSTRUMENT = "equity:US:NVDA"
D = Decimal


def _base_settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": "sqlite:////tmp/tp-f1.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return AppSettings(_env_file=None, **base)  # type: ignore[call-arg]


def _valid_quote(**overrides: object) -> USQuote:
    fields: dict[str, object] = {
        "instrument_id": INSTRUMENT,
        "quote_at": QUOTE_AT,
        "session": TradingSession.REGULAR,
        "last": D("120.50"),
        "open": D("118.00"),
        "high": D("121.00"),
        "low": D("117.50"),
        "previous_close": D("119.00"),
        "volume": D("1000000"),
        "average_volume": D("900000"),
        "market_cap": D("3000000000000"),
        "beta": D("1.25"),
        "week_52_low": D("90.00"),
        "week_52_high": D("140.00"),
    }
    fields.update(overrides)
    return USQuote(**fields)  # type: ignore[arg-type]


def _valid_bar_series(**overrides: object) -> USBarSeries:
    bar = MarketBar(
        timestamp=BAR_TS,
        open=D("118.00"),
        high=D("121.00"),
        low=D("117.50"),
        close=D("120.50"),
        volume=D("1000000"),
    )
    fields: dict[str, object] = {
        "instrument_id": INSTRUMENT,
        "interval": USBarInterval.ONE_DAY,
        "adjustment": AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        "start": date(2026, 7, 17),
        "end": date(2026, 7, 17),
        "bars": (bar,),
    }
    fields.update(overrides)
    return USBarSeries(**fields)  # type: ignore[arg-type]


def test_valid_us_quote_and_bar_series() -> None:
    quote = _valid_quote()
    series = _valid_bar_series()
    assert quote.instrument_id == INSTRUMENT
    assert quote.last == D("120.50")
    assert series.interval is USBarInterval.ONE_DAY
    assert len(series.bars) == 1
    assert series.bars[0].close == D("120.50")


def test_output_dto_decimal_json_and_valid_inputs() -> None:
    quote_dto = USQuoteDTO.from_domain(_valid_quote())
    series_dto = USBarSeriesDTO.from_domain(_valid_bar_series())
    quote_json = quote_dto.model_dump(mode="json")
    series_json = series_dto.model_dump(mode="json")
    assert quote_json["last"] == "120.50"
    assert quote_json["display_price"] == "120.50"
    assert quote_json["price_basis"] == "last"
    assert quote_json["previous_close"] == "119.00"
    assert quote_json["previous_close_basis"] == "previous_completed_regular_session_close"
    assert quote_json["volume"] == "1000000"
    assert isinstance(quote_json["last"], str)

    future_dto = USQuoteDTO.from_domain(
        _valid_quote(instrument_id="future:US:GC=F")
    )
    assert future_dto.previous_close_basis == "previous_completed_daily_bar_close"
    assert future_dto.model_dump(mode="json")["previous_close_basis"] == (
        "previous_completed_daily_bar_close"
    )
    assert series_json["bars"][0]["close"] == "120.50"
    assert series_json["interval"] == "1d"

    snap = MarketGetSnapshotInput.model_validate(
        {"instrument_id": INSTRUMENT, "as_of": "2026-07-17T20:00:00+00:00"}
    )
    bars = MarketGetBarsInput.model_validate(
        {
            "instrument_id": "etf:US:QQQ",
            "start": "2026-07-01",
            "end": "2026-07-17",
        }
    )
    tech = TechnicalGetSnapshotInput.model_validate({"instrument_id": INSTRUMENT})
    composite = USGetSnapshotInput.model_validate(
        {"instrument_id": INSTRUMENT, "lookback_sessions": 260}
    )
    assert snap.instrument_id == INSTRUMENT
    assert bars.end >= bars.start
    assert bars.adjustment is None
    assert tech.lookback_sessions == 260
    assert composite.lookback_sessions == 260

    futures = MarketGetBarsInput.model_validate(
        {
            "instrument_id": "future:US:GC=F",
            "start": "2026-07-20",
            "end": "2026-07-21",
            "interval": "60m",
        }
    )
    assert futures.adjustment is None
    assert futures.interval is USBarInterval.SIXTY_MINUTES


def test_invalid_negative_last_and_ohlc_and_bar_range() -> None:
    with pytest.raises(DataContractError, match="nonnegative"):
        _valid_quote(last=D("-1"))
    with pytest.raises(DataContractError, match="high must be"):
        _valid_quote(high=D("100"), low=D("110"), open=D("105"), last=D("108"))
    with pytest.raises(DataContractError, match="end must be >= start"):
        _valid_bar_series(start=date(2026, 7, 18), end=date(2026, 7, 17))
    outside = MarketBar(
        timestamp=datetime(2026, 7, 20, 16, 0, tzinfo=NY),
        open=D("1"),
        high=D("1"),
        low=D("1"),
        close=D("1"),
        volume=D("0"),
    )
    with pytest.raises(DataContractError, match="inclusive"):
        _valid_bar_series(bars=(outside,))


@pytest.mark.parametrize(
    ("builder", "payload", "match"),
    [
        (
            MarketGetSnapshotInput.model_validate,
            {"instrument_id": "equity:A_SHARE:600519.SH"},
            "market must be one of",
        ),
        (
            MarketGetSnapshotInput.model_validate,
            {
                "instrument_id": INSTRUMENT,
                "as_of": datetime(2026, 7, 17, 12, 0),
            },
            "timezone-aware",
        ),
        (
            MarketGetBarsInput.model_validate,
            {
                "instrument_id": INSTRUMENT,
                "start": "2026-07-18",
                "end": "2026-07-17",
            },
            "end must be >= start",
        ),
        (
            TechnicalGetSnapshotInput.model_validate,
            {"instrument_id": INSTRUMENT, "lookback_sessions": 19},
            "lookback",
        ),
        (
            USGetSnapshotInput.model_validate,
            {"instrument_id": INSTRUMENT, "lookback_sessions": 1001},
            "lookback",
        ),
    ],
)
def test_input_rejects_non_us_naive_date_range_lookback(
    builder: object, payload: dict[str, object], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        builder(payload)  # type: ignore[operator]


def test_us_settings_defaults() -> None:
    s = _base_settings()
    assert s.yfinance_enabled is True
    assert s.alpha_vantage_enabled is True
    assert s.us_current_window_seconds == 300
    assert s.us_max_fresh_seconds == 30
    assert s.us_max_delayed_seconds == 900


@pytest.mark.parametrize(
    "field",
    [
        "us_current_window_seconds",
        "us_max_fresh_seconds",
        "us_max_delayed_seconds",
    ],
)
@pytest.mark.parametrize("value", [True, 1.0, -1])
def test_us_settings_strict_int_and_delayed_ge_fresh(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _base_settings(**{field: value})
    with pytest.raises(ValidationError, match="us_max_delayed_seconds"):
        _base_settings(us_max_fresh_seconds=31, us_max_delayed_seconds=30)
    boundary = _base_settings(
        us_current_window_seconds=0,
        us_max_fresh_seconds=0,
        us_max_delayed_seconds=0,
    )
    assert boundary.us_max_fresh_seconds == 0
    assert boundary.us_max_delayed_seconds == 0
