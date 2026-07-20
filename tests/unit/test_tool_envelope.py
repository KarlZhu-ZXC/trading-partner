"""Tool Envelope invariant tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from application.dto.health import HealthStatusDTO
from application.dto.market import (
    InstrumentDTO,
    MarketBarDTO,
    TechnicalIndicatorsDTO,
    VerifiedMarketSnapshotDTO,
    decimal_to_wire_string,
)
from application.dto.tool_envelope import (
    ErrorInfo,
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    Freshness,
    HealthState,
    Market,
    SourceRole,
    TradingSession,
)

AS_OF = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
FETCHED = datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)


def _health_data() -> HealthStatusDTO:
    return HealthStatusDTO(
        status=HealthState.OK,
        app_name="tp",
        version="0.1.0",
        environment="test",
        database=HealthState.OK,
    )


def test_success_requires_data() -> None:
    with pytest.raises(ValidationError):
        ToolEnvelope[HealthStatusDTO](
            ok=True,
            request_id="req_1",
            market=None,
            as_of=AS_OF,
            fetched_at=FETCHED,
            freshness=Freshness.FRESH,
            sources=(),
            degraded=False,
            data=None,
            warnings=(),
            errors=(),
        )


def test_failure_requires_errors() -> None:
    with pytest.raises(ValidationError):
        ToolEnvelope[HealthStatusDTO](
            ok=False,
            request_id="req_1",
            market=None,
            as_of=AS_OF,
            fetched_at=FETCHED,
            freshness=Freshness.UNKNOWN,
            sources=(),
            degraded=True,
            data=None,
            warnings=(),
            errors=(),
        )


def test_degraded_requires_warning_or_error() -> None:
    with pytest.raises(ValidationError):
        ToolEnvelope.success(
            request_id="req_1",
            market=None,
            as_of=AS_OF,
            fetched_at=FETCHED,
            freshness=Freshness.FRESH,
            sources=(),
            data=_health_data(),
            degraded=True,
            warnings=(),
        )


def test_success_factory() -> None:
    env = ToolEnvelope.success(
        request_id="req_1",
        market=None,
        as_of=AS_OF,
        fetched_at=FETCHED,
        freshness=Freshness.FRESH,
        sources=(),
        data=_health_data(),
    )
    assert env.ok is True
    assert env.data is not None
    assert env.errors == ()


def test_failure_factory() -> None:
    env = ToolEnvelope[HealthStatusDTO].failure(
        request_id="req_1",
        market=Market.US,
        as_of=AS_OF,
        fetched_at=FETCHED,
        errors=[ErrorInfo(code="INVALID_INSTRUMENT", message="bad", retryable=False, details={})],
    )
    assert env.ok is False
    assert len(env.errors) == 1


def test_source_reference_discloses_provider_delay_seconds() -> None:
    source = SourceReference(
        name="yahoo",
        role=SourceRole.PRIMARY,
        retrieved_at=FETCHED,
        data_delay_seconds=900,
    )
    assert source.model_dump(mode="json")["data_delay_seconds"] == 900
    with pytest.raises(ValidationError):
        SourceReference(name="yahoo", role=SourceRole.PRIMARY, data_delay_seconds=-1)


def test_naive_datetime_rejected() -> None:
    with pytest.raises((ValidationError, Exception)):
        ToolEnvelope.success(
            request_id="req_1",
            market=None,
            as_of=datetime(2026, 7, 16, 12, 0),  # naive
            fetched_at=FETCHED,
            freshness=Freshness.FRESH,
            sources=(),
            data=_health_data(),
        )


def test_decimal_wire_preserves_scale() -> None:
    assert decimal_to_wire_string(Decimal("1500.00")) == "1500.00"
    assert decimal_to_wire_string(Decimal("169.50")) == "169.50"
    # Scientific notation expanded
    assert "E" not in decimal_to_wire_string(Decimal("1.5E+2")).upper()


def test_snapshot_dto_json_decimal_strings() -> None:
    dto = VerifiedMarketSnapshotDTO(
        instrument=InstrumentDTO(
            instrument_id="equity:US:NVDA",
            symbol="NVDA",
            name="NVIDIA Corporation",
            market=Market.US,
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            asset_type=AssetType.EQUITY,
        ),
        requested_as_of=AS_OF,
        latest_market_row=MarketBarDTO(
            timestamp=AS_OF,
            open=Decimal("170.00"),
            high=Decimal("175.00"),
            low=Decimal("168.00"),
            close=Decimal("173.00"),
            volume=Decimal("50000000"),
        ),
        indicators=TechnicalIndicatorsDTO(
            ema_10=None,
            sma_50=None,
            sma_200=None,
            rsi_14=None,
            macd=None,
            macd_signal=None,
            macd_histogram=None,
            atr_14=None,
            bollinger_mid=None,
            bollinger_upper=None,
            bollinger_lower=None,
            vwma=None,
            mfi=None,
        ),
        recent_closes=(Decimal("168.00"), Decimal("169.50")),
        adjustment=AdjustmentMethod.NONE,
        session=TradingSession.REGULAR,
        algorithm_version="mock-1.0.0",
    )
    payload = dto.model_dump(mode="json")
    assert payload["latest_market_row"]["close"] == "173.00"
    assert payload["recent_closes"] == ["168.00", "169.50"]
    assert payload["session"] == "regular"
    assert payload["adjustment"] == "none"


def test_degraded_with_warning_ok() -> None:
    env = ToolEnvelope.success(
        request_id="req_1",
        market=Market.US,
        as_of=AS_OF,
        fetched_at=FETCHED,
        freshness=Freshness.FRESH,
        sources=(),
        data=_health_data(),
        degraded=True,
        warnings=[WarningInfo(code="MOCK_DATA", message="mock", details={})],
    )
    assert env.degraded is True
