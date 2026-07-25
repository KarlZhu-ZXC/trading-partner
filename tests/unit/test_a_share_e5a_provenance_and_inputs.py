"""E5a contract tests: retained component provenance and public input hardening."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from application.dto.a_share import (
    AShareGetEtfOptionSnapshotInput,
    AShareGetMarketStructureInput,
    AShareMarketStructureSnapshotDTO,
    ResearchSearchReportsInput,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    AShareComponentProvenanceDTO,
)
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.services.a_share_market_structure_service import (
    AShareMarketStructureResult,
)
from domain.a_share.enums import AShareComponentType, AShareMarketScope, BarInterval
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
from domain.common.errors import CalendarOutOfRange, DataContractError
from domain.instruments.models import Instrument

_NOW = datetime(2026, 7, 17, 8, tzinfo=UTC)


def _meta(*, warnings: tuple[str, ...] = ()) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
        category=DataCategory.MARKET_STRUCTURE,
        role=SourceRole.PRIMARY,
        as_of=_NOW,
        fetched_at=_NOW,
        freshness=Freshness.FRESH,
        session=TradingSession.CLOSED,
        latency_ms=0,
        cache_disposition=CacheDisposition.BYPASS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=warnings,
    )


def test_component_type_wire_inventory_is_stable() -> None:
    assert tuple(item.value for item in AShareComponentType) == (
        "quote",
        "fundamentals",
        "statements",
        "f10",
        "announcements",
        "news",
        "corporate_actions",
        "bars",
        "order_book",
        "ticks",
        "industries",
        "market_board",
        "intraday_flow",
        "daily_flow",
        "northbound",
        "dragon_tiger",
        "margin",
        "block_trade",
        "shareholder_count",
        "chip_distribution",
        "unlock",
        "dividend",
        "limit_context",
        "limit_reason_tags",
        "eastmoney_hot",
        "ths_hot",
        "concept_heat",
        "interactive_qa",
        "company_news",
        "market_news",
        "option_snapshot",
        "reports",
        "consensus",
        "industry_cycle",
        "company_operating_metrics",
    )


def test_derived_provenance_requires_and_only_allows_derived_warning() -> None:
    with pytest.raises(DataContractError):
        AShareComponentProvenance(
            component=AShareComponentType.CHIP_DISTRIBUTION,
            meta=_meta(),
            reliability=None,
            is_authoritative=None,
            is_derived=True,
        )


def _valid_provenance_payload() -> dict[str, object]:
    return {
        "component": "bars",
        "vendor": "eastmoney",
        "category": "market_structure",
        "role": "primary",
        "as_of": _NOW,
        "fetched_at": _NOW,
        "freshness": "fresh",
        "session": "closed",
        "cache_disposition": "bypass",
        "adjustment": None,
        "data_delay_seconds": None,
        "warnings": [],
        "reliability": None,
        "is_authoritative": None,
        "is_derived": False,
    }


def _assert_dto_error(
    payload: dict[str, object],
    *,
    location: tuple[str, ...],
    error_type: str,
    message_contains: str | None = None,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        AShareComponentProvenanceDTO.model_validate(payload)
    error = exc_info.value.errors()[0]
    assert error["loc"] == location
    assert error["type"] == error_type
    if message_contains is not None:
        assert message_contains in error["msg"]


def test_provenance_dto_exact_field_inventory() -> None:
    assert tuple(AShareComponentProvenanceDTO.model_fields) == (
        "component",
        "vendor",
        "category",
        "role",
        "as_of",
        "fetched_at",
        "freshness",
        "session",
        "cache_disposition",
        "adjustment",
        "data_delay_seconds",
        "warnings",
        "reliability",
        "is_authoritative",
        "is_derived",
    )


def test_provenance_dto_accepts_aware_datetimes() -> None:
    dto = AShareComponentProvenanceDTO.model_validate(_valid_provenance_payload())
    assert dto.as_of is _NOW
    assert dto.fetched_at is _NOW


@pytest.mark.parametrize("field", ["as_of", "fetched_at"])
def test_provenance_dto_rejects_naive_datetime(field: str) -> None:
    payload = _valid_provenance_payload()
    payload[field] = datetime(2026, 7, 17, 8)
    _assert_dto_error(
        payload,
        location=(field,),
        error_type="value_error",
        message_contains=f"{field} must be timezone-aware",
    )


def test_provenance_dto_rejects_bool_delay() -> None:
    payload = _valid_provenance_payload()
    payload["data_delay_seconds"] = True
    _assert_dto_error(payload, location=("data_delay_seconds",), error_type="value_error")


def test_provenance_dto_rejects_negative_delay() -> None:
    payload = _valid_provenance_payload()
    payload["data_delay_seconds"] = -1
    _assert_dto_error(
        payload,
        location=(),
        error_type="value_error",
        message_contains="data_delay_seconds must be nonnegative",
    )


def test_provenance_dto_rejects_unsafe_warning() -> None:
    payload = _valid_provenance_payload()
    payload["warnings"] = ["bad warning!"]
    _assert_dto_error(payload, location=("warnings",), error_type="value_error")


@pytest.mark.parametrize("field", ["is_authoritative", "is_derived"])
def test_provenance_dto_rejects_non_strict_boolean(field: str) -> None:
    payload = _valid_provenance_payload()
    payload[field] = 1
    _assert_dto_error(payload, location=(field,), error_type="bool_type")


def test_provenance_dto_rejects_forbidden_extra() -> None:
    payload = _valid_provenance_payload()
    payload["extra"] = "forbidden"
    _assert_dto_error(payload, location=("extra",), error_type="extra_forbidden")


def test_provenance_dto_rejects_low_reliability_without_warning() -> None:
    payload = _valid_provenance_payload()
    payload["reliability"] = "low"
    payload["is_authoritative"] = False
    _assert_dto_error(
        payload,
        location=(),
        error_type="value_error",
        message_contains="low reliability requires LOW_RELIABILITY_MARKET_SIGNAL",
    )


def test_provenance_domain_and_tuple_contract_hardening() -> None:
    from application.dto.a_share_provenance import validate_provenance_tuple

    with pytest.raises(DataContractError):
        AShareComponentProvenance(
            component=AShareComponentType.BARS,
            meta=_meta(),
            reliability=ReliabilityLevel.LOW,
            is_authoritative=False,
            is_derived=False,
        )
    low = AShareComponentProvenance(
        component=AShareComponentType.BARS,
        meta=_meta(warnings=("LOW_RELIABILITY_MARKET_SIGNAL",)),
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
        is_derived=False,
    )
    with pytest.raises(DataContractError):
        validate_provenance_tuple((low, low))
    order_book = AShareComponentProvenance(
        component=AShareComponentType.ORDER_BOOK,
        meta=_meta(),
        reliability=None,
        is_authoritative=None,
        is_derived=False,
    )
    with pytest.raises(DataContractError, match="order is invalid"):
        validate_provenance_tuple(
            (order_book, low),
            order=(AShareComponentType.BARS, AShareComponentType.ORDER_BOOK),
        )
    with pytest.raises(DataContractError):
        AShareComponentProvenance(
            component=AShareComponentType.BARS,
            meta=_meta(warnings=("DERIVED_CHIP_DISTRIBUTION",)),
            reliability=None,
            is_authoritative=None,
            is_derived=False,
        )
    payload = _valid_provenance_payload()
    payload["warnings"] = ["DERIVED_CHIP_DISTRIBUTION"]
    _assert_dto_error(payload, location=(), error_type="value_error")


def test_structure_result_requires_dto_provenance_identity() -> None:
    dto = AShareMarketStructureSnapshotDTO(
        scope=AShareMarketScope.MARKET,
        instrument_id=None,
        trade_date=None,
        as_of=_NOW,
        included_components=(AShareComponentType.MARKET_BOARD,),
        provenance=(),
    )
    with pytest.raises(DataContractError):
        AShareMarketStructureResult(ok=True, data=dto, warnings=(), error=None, provenance=())
    provenance = AShareComponentProvenance(
        component=AShareComponentType.MARKET_BOARD,
        meta=_meta(),
        reliability=None,
        is_authoritative=None,
        is_derived=False,
    )
    with pytest.raises(DataContractError):
        AShareMarketStructureResult(
            ok=True, data=dto, warnings=(), error=None, provenance=(provenance,)
        )


def test_e5a_input_hardening_happens_at_schema_edge() -> None:
    report = ResearchSearchReportsInput(text="  ", industry_code="  801010  ")
    assert report.text is None
    assert report.industry_code == "801010"
    assert ResearchSearchReportsInput(text=" " * 501, industry_code="801010").text is None
    assert (
        ResearchSearchReportsInput(text="x", industry_code=f"  {'a' * 64}  ").industry_code
        == "a" * 64
    )
    with pytest.raises(ValidationError):
        ResearchSearchReportsInput(text="x", industry_code=" " + "a" * 65)
    with pytest.raises(ValidationError):
        ResearchSearchReportsInput(text=" ", industry_code=" ")
    with pytest.raises(ValidationError):
        AShareGetEtfOptionSnapshotInput(
            underlying_instrument_id="etf:A_SHARE:510050.SH", strike_center="NaN"
        )
    with pytest.raises(ValidationError):
        AShareGetMarketStructureInput(
            instrument_id="equity:A_SHARE:600519.SH",
            include_bars=False,
            include_order_book=False,
            include_ticks=False,
            include_industries=False,
            include_market_board=False,
        )
    valid_dates = AShareGetMarketStructureInput(
        instrument_id="equity:A_SHARE:600519.SH", start="2026-07-16", end="2026-07-17"
    )
    assert valid_dates.start == date(2026, 7, 16)
    with pytest.raises(ValidationError):
        AShareGetMarketStructureInput(
            instrument_id="equity:A_SHARE:600519.SH", start=_NOW, end="2026-07-17"
        )
    with pytest.raises(ValidationError):
        AShareGetEtfOptionSnapshotInput(
            underlying_instrument_id="etf:A_SHARE:510050.SH", expiry="2026-07-17T00:00:00Z"
        )


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return _NOW


class _Calendar:
    def is_trading_day(self, day: date) -> bool:
        return day == _NOW.date()

    def previous_trading_day(self, day: date) -> date:
        return day - timedelta(days=1)


def _structure_service() -> tuple[object, _Clock]:
    from application.services.a_share_market_structure_service import (
        AShareMarketStructureService,
    )

    service = object.__new__(AShareMarketStructureService)
    clock = _Clock()
    service._clock = clock  # type: ignore[attr-defined]
    service._calendar = _Calendar()  # type: ignore[attr-defined]
    return service, clock


def _ok(value: object) -> RouterExecutionResult[object]:
    return RouterExecutionResult(
        value=value,
        ok=True,
        criticality=DataCriticality.CORE,
        meta=_meta(),
        attempts=(),
        warnings=(),
        error=None,
    )


def _failure() -> RouterExecutionResult[object]:
    return RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.CORE,
        meta=None,
        attempts=(),
        warnings=(),
        error=DataContractError("book failed"),
    )


@pytest.mark.asyncio
async def test_structure_get_single_clock_derives_date_and_allows_empty_industries() -> None:
    service, clock = _structure_service()
    captured: list[tuple[date, datetime]] = []

    async def industries(*, trade_date: date, limit: int, as_of: datetime, sampled_now: datetime):
        captured.append((trade_date, sampled_now))
        return _ok(())

    service._get_industry_performance = industries  # type: ignore[attr-defined]
    result = await service.get(  # type: ignore[attr-defined]
        scope=AShareMarketScope.INDUSTRY,
        instrument=None,
        trade_date=None,
        start=None,
        end=None,
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        include_bars=False,
        include_order_book=False,
        include_ticks=False,
        include_industries=True,
        include_market_board=False,
        industry_limit=20,
        tick_limit=10,
        as_of=_NOW,
    )
    assert clock.calls == 1
    assert captured == [(_NOW.date(), _NOW)]
    assert result.ok is True and result.data is not None
    assert result.data.industries == ()
    assert result.data.included_components == (AShareComponentType.INDUSTRIES,)
    assert tuple(item.component for item in result.provenance) == (AShareComponentType.INDUSTRIES,)


@pytest.mark.asyncio
async def test_structure_get_required_failure_retains_successful_sibling_in_order() -> None:
    service, _clock = _structure_service()
    instrument = Instrument(
        instrument_id="equity:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="test",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )

    async def bars(*args: object, **kwargs: object) -> RouterExecutionResult[object]:
        await asyncio.sleep(0.001)
        return _ok(())

    async def book(*args: object, **kwargs: object) -> RouterExecutionResult[object]:
        return _failure()

    service._get_bars = bars  # type: ignore[attr-defined]
    service._get_order_book = book  # type: ignore[attr-defined]
    result = await service.get(  # type: ignore[attr-defined]
        scope=AShareMarketScope.INSTRUMENT,
        instrument=instrument,
        trade_date=None,
        start=_NOW.date() - timedelta(days=1),
        end=_NOW.date(),
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        include_bars=True,
        include_order_book=True,
        include_ticks=False,
        include_industries=False,
        include_market_board=False,
        industry_limit=20,
        tick_limit=10,
        as_of=_NOW,
    )
    assert result.ok is False and result.error is not None
    assert str(result.error) == "book failed"
    assert tuple(item.component for item in result.provenance) == (AShareComponentType.BARS,)


@pytest.mark.asyncio
async def test_structure_get_scope_asset_and_calendar_preflight() -> None:
    service, _clock = _structure_service()
    base = dict(
        instrument=None,
        trade_date=None,
        start=None,
        end=None,
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        include_bars=False,
        include_order_book=False,
        include_ticks=False,
        include_industries=False,
        include_market_board=False,
        industry_limit=20,
        tick_limit=10,
        as_of=_NOW,
    )
    with pytest.raises(DataContractError, match="industry scope requires industries"):
        await service.get(  # type: ignore[attr-defined]
            scope=AShareMarketScope.INDUSTRY,
            **{**base, "include_market_board": True},
        )
    with pytest.raises(DataContractError, match="market scope requires market_board"):
        await service.get(  # type: ignore[attr-defined]
            scope=AShareMarketScope.MARKET,
            **{**base, "include_industries": True},
        )

    class _OutCalendar(_Calendar):
        def is_trading_day(self, day: date) -> bool:
            return False

        def previous_trading_day(self, day: date) -> date:
            raise CalendarOutOfRange("outside calendar")

    service._calendar = _OutCalendar()  # type: ignore[attr-defined]
    with pytest.raises(CalendarOutOfRange):
        await service.get(  # type: ignore[attr-defined]
            scope=AShareMarketScope.INDUSTRY, **{**base, "include_industries": True}
        )


@pytest.mark.asyncio
async def test_structure_get_sanitizes_unexpected_and_uses_frozen_failure_order() -> None:
    service, _clock = _structure_service()
    instrument = Instrument(
        instrument_id="equity:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="test",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )

    async def bars(*args: object, **kwargs: object) -> RouterExecutionResult[object]:
        await asyncio.sleep(0.001)
        raise RuntimeError("secret provider payload")

    async def book(*args: object, **kwargs: object) -> RouterExecutionResult[object]:
        return _failure()

    service._get_bars = bars  # type: ignore[attr-defined]
    service._get_order_book = book  # type: ignore[attr-defined]
    result = await service.get(  # type: ignore[attr-defined]
        scope=AShareMarketScope.INSTRUMENT,
        instrument=instrument,
        trade_date=None,
        start=_NOW.date() - timedelta(days=1),
        end=_NOW.date(),
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        include_bars=True,
        include_order_book=True,
        include_ticks=False,
        include_industries=False,
        include_market_board=False,
        industry_limit=20,
        tick_limit=10,
        as_of=_NOW,
    )
    assert result.ok is False and result.error is not None
    assert str(result.error) == "Unexpected A-share component failure"
    assert "secret provider payload" not in str(result.error)
