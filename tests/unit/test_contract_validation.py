"""Phase 1D D6a: validate_verified_market_snapshot contract checks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from domain.common.enums import AdjustmentMethod, AssetType, Market, TradingSession
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)
from domain.market.validation import validate_verified_market_snapshot

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
BAR_TS = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _bar(
    *,
    open_: str = "100",
    high: str = "110",
    low: str = "90",
    close: str = "105",
    volume: str = "1000",
    timestamp: datetime = BAR_TS,
) -> MarketBar:
    return MarketBar(
        timestamp=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _snapshot(
    *,
    bar: MarketBar | None = None,
    recent_closes: tuple[Decimal, ...] | None = None,
    algorithm_version: str = "mock-1.0.0",
    requested_as_of: datetime = AS_OF,
) -> VerifiedMarketSnapshot:
    market_bar = bar if bar is not None else _bar()
    closes = (
        recent_closes
        if recent_closes is not None
        else (Decimal("100"), Decimal("102"), market_bar.close)
    )
    return VerifiedMarketSnapshot(
        instrument=_instrument(),
        requested_as_of=requested_as_of,
        latest_market_row=market_bar,
        indicators=TechnicalIndicators.empty(),
        recent_closes=closes,
        adjustment=AdjustmentMethod.NONE,
        session=TradingSession.REGULAR,
        algorithm_version=algorithm_version,
    )


def test_valid_snapshot_passes() -> None:
    validate_verified_market_snapshot(_snapshot())


def test_rejects_empty_recent_closes() -> None:
    snap = _snapshot(recent_closes=())
    with pytest.raises(DataContractError, match="non-empty") as exc_info:
        validate_verified_market_snapshot(snap)
    assert exc_info.value.details.get("rule") == "non_empty"


def test_rejects_last_close_mismatch_without_echoing_prices() -> None:
    snap = _snapshot(
        bar=_bar(close="105"),
        recent_closes=(Decimal("100"), Decimal("999.99")),
    )
    with pytest.raises(DataContractError, match="last element") as exc_info:
        validate_verified_market_snapshot(snap)
    blob = str(exc_info.value) + str(exc_info.value.details)
    assert "999.99" not in blob
    assert "105" not in blob or "field" in blob  # field names ok; price values not
    # Stronger: details must not contain Decimal price strings
    assert "999" not in blob
    assert exc_info.value.details.get("rule") == "last_close_matches_bar"


def test_rejects_invalid_ohlc_high() -> None:
    # high < max(open, close, low)
    snap = _snapshot(bar=_bar(open_="100", high="99", low="90", close="105"))
    with pytest.raises(DataContractError, match="high") as exc_info:
        validate_verified_market_snapshot(snap)
    assert exc_info.value.details.get("rule") == "ohlc_high"
    assert "99" not in str(exc_info.value.details)


def test_rejects_invalid_ohlc_low() -> None:
    snap = _snapshot(bar=_bar(open_="100", high="110", low="106", close="105"))
    with pytest.raises(DataContractError, match="low") as exc_info:
        validate_verified_market_snapshot(snap)
    assert exc_info.value.details.get("rule") == "ohlc_low"


def test_rejects_negative_volume() -> None:
    snap = _snapshot(bar=_bar(volume="-1"))
    with pytest.raises(DataContractError, match="volume") as exc_info:
        validate_verified_market_snapshot(snap)
    assert exc_info.value.details.get("rule") == "volume_nonnegative"


def test_rejects_future_bar_relative_to_as_of() -> None:
    future = AS_OF + timedelta(minutes=1)
    snap = _snapshot(bar=_bar(timestamp=future), recent_closes=(Decimal("105"),))
    # rebuild with matching close
    bar = _bar(timestamp=future, close="105")
    snap = _snapshot(bar=bar, recent_closes=(Decimal("105"),))
    with pytest.raises(DataContractError, match="requested_as_of") as exc_info:
        validate_verified_market_snapshot(snap)
    assert exc_info.value.details.get("rule") == "not_after_as_of"


def test_accepts_bar_timestamp_equal_to_as_of() -> None:
    bar = _bar(timestamp=AS_OF, close="105")
    validate_verified_market_snapshot(
        _snapshot(bar=bar, recent_closes=(Decimal("105"),), requested_as_of=AS_OF)
    )


def test_rejects_blank_algorithm_version() -> None:
    for bad in ("", "   ", "\t"):
        snap = _snapshot(algorithm_version=bad)
        with pytest.raises(DataContractError, match="algorithm_version") as exc_info:
            validate_verified_market_snapshot(snap)
        assert exc_info.value.details.get("rule") == "non_blank"
        # Never echo the rejected algorithm_version payload.
        assert "algorithm_version" not in {
            k for k in exc_info.value.details if k not in {"field", "rule"}
        }
        assert bad.strip() == "" or bad not in repr(exc_info.value.details)


def test_rejects_non_decimal_numeric_via_object_setattr() -> None:
    """Explicit validation must not rely only on dataclass construction."""
    snap = _snapshot()
    # Bypass MarketBar construction type expectations.
    object.__setattr__(snap.latest_market_row, "close", 105.5)  # float, not Decimal
    with pytest.raises(DataContractError, match="Decimal") as exc_info:
        validate_verified_market_snapshot(snap)
    blob = str(exc_info.value) + str(exc_info.value.details)
    assert "105.5" not in blob
    assert exc_info.value.details.get("rule") == "decimal_type"


def test_rejects_int_and_bool_as_decimal() -> None:
    for bad in (105, True, False):
        snap = _snapshot()
        object.__setattr__(snap.latest_market_row, "volume", bad)
        with pytest.raises(DataContractError) as exc_info:
            validate_verified_market_snapshot(snap)
        assert "volume" in str(exc_info.value.details.get("field", ""))
        assert str(bad) not in str(exc_info.value.details) or bad is True
        # bool True details type is 'bool' — value itself must not appear as payload
        assert "sk-" not in str(exc_info.value.details)


def test_rejects_non_decimal_in_recent_closes() -> None:
    snap = _snapshot()
    object.__setattr__(snap, "recent_closes", (Decimal("100"), 1.23, Decimal("105")))
    with pytest.raises(DataContractError, match="Decimal") as exc_info:
        validate_verified_market_snapshot(snap)
    assert "1.23" not in str(exc_info.value) + str(exc_info.value.details)


_NON_FINITE_DECIMALS = (
    Decimal("NaN"),
    Decimal("sNaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
)
_BAR_DECIMAL_FIELDS = ("open", "high", "low", "close", "volume")


def test_rejects_non_finite_decimals() -> None:
    for field in _BAR_DECIMAL_FIELDS:
        for special in _NON_FINITE_DECIMALS:
            snap = _snapshot()
            object.__setattr__(snap.latest_market_row, field, special)
            if field != "close":
                object.__setattr__(
                    snap, "recent_closes", (Decimal("100"), snap.latest_market_row.close)
                )
            else:
                object.__setattr__(snap, "recent_closes", (Decimal("105"),))
            with pytest.raises(DataContractError) as exc_info:
                validate_verified_market_snapshot(snap)
            assert exc_info.value.details.get("rule") == "finite_decimal"
            assert exc_info.value.details.get("field") == f"latest_market_row.{field}"
            blob = str(exc_info.value) + repr(exc_info.value.details) + repr(exc_info.value)
            assert "NaN" not in blob
            assert "Infinity" not in blob
            assert "sNaN" not in blob
            assert "InvalidOperation" not in blob

    for special in _NON_FINITE_DECIMALS:
        bar = _bar(close="105")
        snap = _snapshot(bar=bar, recent_closes=(Decimal("100"), special, Decimal("105")))
        # Bypass tuple construction path if needed (already Decimal specials).
        with pytest.raises(DataContractError) as exc_info:
            validate_verified_market_snapshot(snap)
        assert exc_info.value.details.get("rule") == "finite_decimal"
        assert exc_info.value.details.get("field") == "recent_closes[1]"
        blob = str(exc_info.value) + repr(exc_info.value.details) + repr(exc_info.value)
        assert "NaN" not in blob
        assert "Infinity" not in blob
        assert "sNaN" not in blob
        assert "InvalidOperation" not in blob


def test_rejects_wrong_snapshot_type() -> None:
    with pytest.raises(DataContractError, match="VerifiedMarketSnapshot"):
        validate_verified_market_snapshot(object())  # type: ignore[arg-type]


def test_rejects_malicious_payload_fields_not_leaked() -> None:
    """Poison values in bar must never appear in DataContractError text/details."""
    poison_price = "999999.123456789-LEAK"
    snap = _snapshot()
    # Invalid OHLC with poison decimals constructed normally
    bar = MarketBar(
        timestamp=BAR_TS,
        open=Decimal("100"),
        high=Decimal("50"),  # invalid high
        low=Decimal("90"),
        close=Decimal(poison_price.split("-")[0]),
        volume=Decimal("1"),
    )
    snap = _snapshot(bar=bar, recent_closes=(bar.close,))
    with pytest.raises(DataContractError) as exc_info:
        validate_verified_market_snapshot(snap)
    blob = str(exc_info.value) + repr(exc_info.value.details)
    assert "LEAK" not in blob
    assert poison_price not in blob
