"""Compact unit tests for AShareToolCoordinator (Phase 1E E5c)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.a_share import (
    AShareCompositeSnapshotDTO,
    AShareGetSnapshotInput,
    AShareQuoteDTO,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    AShareComponentProvenanceDTO,
)
from application.dto.provider_routing import ProviderResultMeta
from application.dto.tool_envelope import WarningInfo
from application.services.a_share_snapshot_service import AShareSnapshotResult
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.instrument_access_service import InstrumentAccessService
from conftest import FixedClock, SequentialIdGenerator
from domain.a_share.enums import AShareComponentType, AShareSnapshotDetail
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import InvalidInstrument, ProviderUnavailableError
from domain.instruments.models import Instrument
from infrastructure.system.redactor import DefaultSecretRedactor

_NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
_INSTRUMENT_ID = "equity:A_SHARE:600519.SH"
_INSTRUMENT = Instrument(
    instrument_id=_INSTRUMENT_ID,
    symbol="600519.SH",
    name="Kweichow Moutai",
    market=Market.A_SHARE,
    exchange="SSE",
    currency="CNY",
    timezone="Asia/Shanghai",
    asset_type=AssetType.EQUITY,
)


def _meta(
    *,
    vendor: VendorId = VendorId.EASTMONEY,
    role: SourceRole = SourceRole.PRIMARY,
    fetched_at: datetime | None = None,
    freshness: Freshness = Freshness.FRESH,
    warnings: tuple[str, ...] = (),
    category: DataCategory = DataCategory.MARKET_SNAPSHOT,
) -> ProviderResultMeta:
    at = fetched_at if fetched_at is not None else _NOW
    return ProviderResultMeta(
        vendor=vendor,
        category=category,
        role=role,
        as_of=_NOW,
        fetched_at=at,
        freshness=freshness,
        session=TradingSession.REGULAR,
        latency_ms=0,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=warnings,
    )


def _prov(
    component: AShareComponentType,
    meta: ProviderResultMeta,
    *,
    reliability: ReliabilityLevel | None = ReliabilityLevel.HIGH,
    is_authoritative: bool | None = True,
    is_derived: bool = False,
) -> AShareComponentProvenance:
    return AShareComponentProvenance(
        component=component,
        meta=meta,
        reliability=reliability,
        is_authoritative=is_authoritative,
        is_derived=is_derived,
    )


def _quote_dto() -> AShareQuoteDTO:
    return AShareQuoteDTO(
        instrument_id=_INSTRUMENT_ID,
        quote_at=_NOW,
        session=TradingSession.REGULAR,
        last=Decimal("1800.00"),
        open=Decimal("1790.00"),
        high=Decimal("1810.00"),
        low=Decimal("1785.00"),
        previous_close=Decimal("1795.00"),
        change=Decimal("5.00"),
        change_percent=Decimal("0.28"),
        volume_shares=1_000_000,
        turnover_amount_cny=Decimal("1800000000.00"),
        turnover_rate=None,
        pe_ttm=None,
        pb=None,
        total_market_cap_cny=None,
        float_market_cap_cny=None,
        limit_up_price=None,
        limit_down_price=None,
    )


def _snapshot_dto(
    provenance: tuple[AShareComponentProvenance, ...],
) -> AShareCompositeSnapshotDTO:
    return AShareCompositeSnapshotDTO(
        instrument_id=_INSTRUMENT_ID,
        detail=AShareSnapshotDetail.SUMMARY,
        as_of=_NOW,
        quote=_quote_dto(),
        provenance=tuple(AShareComponentProvenanceDTO.from_result(item) for item in provenance),
    )


def _coordinator(
    *,
    instrument_master: MagicMock | None = None,
    instrument_resolver: MagicMock | None = None,
    snapshot_service: MagicMock | None = None,
    clock: FixedClock | None = None,
    ids: SequentialIdGenerator | None = None,
) -> tuple[AShareToolCoordinator, MagicMock, MagicMock, FixedClock, SequentialIdGenerator]:
    master = instrument_master or MagicMock()
    master.get.return_value = _INSTRUMENT
    snap = snapshot_service or MagicMock()
    snap.get_snapshot = AsyncMock()
    clock = clock or FixedClock(_NOW)
    ids = ids or SequentialIdGenerator()
    coord = AShareToolCoordinator(
        instrument_access=InstrumentAccessService(master, instrument_resolver or MagicMock()),
        clock=clock,
        id_generator=ids,
        secret_redactor=DefaultSecretRedactor(),
        snapshot_service=snap,
        market_structure_service=MagicMock(),
        capital_service=MagicMock(),
        limit_up_service=MagicMock(),
        sentiment_service=MagicMock(),
        etf_option_service=MagicMock(),
        industry_cycle_service=MagicMock(),
        company_operating_metrics_service=MagicMock(),
        report_search_service=MagicMock(),
    )
    return coord, master, snap, clock, ids


@pytest.mark.asyncio
async def test_get_snapshot_clean_success() -> None:
    """Primary/fresh provenance → ok, not degraded, empty warnings."""
    provenance = (
        _prov(
            AShareComponentType.QUOTE,
            _meta(vendor=VendorId.TENCENT, role=SourceRole.PRIMARY),
        ),
    )
    result = AShareSnapshotResult(
        ok=True,
        data=_snapshot_dto(provenance),
        warnings=(),
        error=None,
        provenance=provenance,
    )
    coord, master, snap, _clock, _ids = _coordinator()
    snap.get_snapshot.return_value = result

    envelope = await coord.get_snapshot(
        AShareGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=_NOW)
    )

    assert envelope.ok is True
    assert envelope.degraded is False
    assert envelope.market == Market.A_SHARE
    assert envelope.as_of == _NOW
    assert envelope.freshness == Freshness.FRESH
    assert envelope.fetched_at == _NOW
    assert envelope.warnings == ()
    assert len(envelope.sources) == 1
    assert envelope.sources[0].name == VendorId.TENCENT.value
    assert envelope.sources[0].role == SourceRole.PRIMARY
    assert envelope.data is not None
    assert envelope.data.instrument_id == _INSTRUMENT_ID
    assert envelope.data.quote.previous_close_basis == (
        "previous_completed_regular_session_close"
    )
    master.get.assert_called_once_with(_INSTRUMENT_ID)
    snap.get_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_provenance_degraded_aggregation_dedupe_worst_freshness() -> None:
    """Dedupe sources, worst freshness, synthesize standard warning once."""
    t0 = _NOW
    t1 = _NOW + timedelta(seconds=10)
    t2 = _NOW + timedelta(seconds=20)
    # Same (eastmoney, primary) twice → one source with max retrieved_at.
    # Fallback + delayed + stale → FALLBACK + DELAYED + STALE (first occurrence).
    provenance = (
        _prov(
            AShareComponentType.QUOTE,
            _meta(
                vendor=VendorId.EASTMONEY,
                role=SourceRole.PRIMARY,
                fetched_at=t0,
                freshness=Freshness.FRESH,
            ),
        ),
        _prov(
            AShareComponentType.FUNDAMENTALS,
            _meta(
                vendor=VendorId.EASTMONEY,
                role=SourceRole.PRIMARY,
                fetched_at=t2,
                freshness=Freshness.DELAYED,
            ),
        ),
        _prov(
            AShareComponentType.NEWS,
            _meta(
                vendor=VendorId.CLS,
                role=SourceRole.FALLBACK,
                fetched_at=t1,
                freshness=Freshness.STALE,
            ),
        ),
    )
    product_warning = WarningInfo(
        code="OPTIONAL_COMPONENT_FAILED",
        message="optional news path used fallback",
        details={"component": "news"},
    )
    result = AShareSnapshotResult(
        ok=True,
        data=_snapshot_dto(provenance),
        warnings=(product_warning,),
        error=None,
        provenance=provenance,
    )
    coord, _master, snap, _clock, _ids = _coordinator()
    snap.get_snapshot.return_value = result

    envelope = await coord.get_snapshot(
        AShareGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=_NOW)
    )

    assert envelope.ok is True
    assert envelope.degraded is True
    assert envelope.freshness == Freshness.STALE
    assert envelope.fetched_at == t2
    assert [s.name for s in envelope.sources] == [
        VendorId.EASTMONEY.value,
        VendorId.CLS.value,
    ]
    assert envelope.sources[0].role == SourceRole.PRIMARY
    assert envelope.sources[0].retrieved_at == t2  # max of eastmoney/primary
    assert envelope.sources[1].role == SourceRole.FALLBACK
    assert envelope.sources[1].retrieved_at == t1
    # Product warning first; synthesized codes append without overwriting.
    codes = [w.code for w in envelope.warnings]
    assert codes[0] == "OPTIONAL_COMPONENT_FAILED"
    assert "FALLBACK_A_SHARE_SOURCE" in codes
    assert "DELAYED_A_SHARE_DATA" in codes
    assert "STALE_A_SHARE_DATA" in codes
    assert codes.count("FALLBACK_A_SHARE_SOURCE") == 1
    assert codes.count("DELAYED_A_SHARE_DATA") == 1
    assert codes.count("STALE_A_SHARE_DATA") == 1


@pytest.mark.asyncio
async def test_required_failure_retains_successful_source() -> None:
    """ok=false keeps successful provenance sources and redacts the error."""
    success_at = _NOW + timedelta(seconds=5)
    provenance = (
        _prov(
            AShareComponentType.QUOTE,
            _meta(
                vendor=VendorId.TENCENT,
                role=SourceRole.PRIMARY,
                fetched_at=success_at,
                freshness=Freshness.FRESH,
            ),
        ),
    )
    error = ProviderUnavailableError(
        "quote chain exhausted with secret=test-secret-value",
        details={"token": "test-secret-value", "vendor": "eastmoney"},
        retryable=True,
    )
    result = AShareSnapshotResult(
        ok=False,
        data=None,
        warnings=(),
        error=error,
        provenance=provenance,
    )
    coord, _master, snap, _clock, _ids = _coordinator()
    snap.get_snapshot.return_value = result

    envelope = await coord.get_snapshot(
        AShareGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=_NOW)
    )

    assert envelope.ok is False
    assert envelope.degraded is True
    assert envelope.data is None
    assert envelope.freshness == Freshness.FRESH
    assert envelope.fetched_at == success_at
    assert len(envelope.sources) == 1
    assert envelope.sources[0].name == VendorId.TENCENT.value
    assert envelope.sources[0].retrieved_at == success_at
    assert len(envelope.errors) == 1
    assert envelope.errors[0].code == error.code
    assert envelope.errors[0].retryable is True
    assert "test-secret-value" not in envelope.errors[0].message
    assert "test-secret-value" not in str(envelope.errors[0].details)


@pytest.mark.asyncio
async def test_clock_request_id_and_instrument_resolution() -> None:
    """Omitted as_of samples clock once; request_id once; master miss → failure."""
    clock = FixedClock(_NOW)
    ids = SequentialIdGenerator()
    master = MagicMock()
    master.get.side_effect = InvalidInstrument(
        "instrument not found",
        details={"instrument_id": _INSTRUMENT_ID},
    )
    resolver = MagicMock()
    resolver.resolve_dynamic = AsyncMock(
        side_effect=InvalidInstrument(
            "instrument not found",
            details={"instrument_id": _INSTRUMENT_ID},
        )
    )
    coord, _master, snap, _clock, _ids = _coordinator(
        instrument_master=master,
        instrument_resolver=resolver,
        clock=clock,
        ids=ids,
    )

    envelope = await coord.get_snapshot(
        AShareGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=None)
    )

    assert envelope.ok is False
    assert envelope.as_of == _NOW  # effective as_of from single clock sample
    assert envelope.request_id.startswith("req_")
    assert envelope.market == Market.A_SHARE
    assert envelope.freshness == Freshness.UNKNOWN
    assert envelope.sources == ()
    assert envelope.errors[0].code == "INVALID_INSTRUMENT"
    master.get.assert_called_once_with(_INSTRUMENT_ID)
    snap.get_snapshot.assert_not_awaited()

    # Second call with explicit as_of must not consume another as_of sample path;
    # request_id still advances once per call.
    master.get.side_effect = None
    master.get.return_value = _INSTRUMENT
    provenance = (_prov(AShareComponentType.QUOTE, _meta(fetched_at=_NOW - timedelta(hours=1))),)
    snap.get_snapshot.return_value = AShareSnapshotResult(
        ok=True,
        data=_snapshot_dto(provenance),
        warnings=(),
        error=None,
        provenance=provenance,
    )
    explicit_as_of = _NOW - timedelta(minutes=30)
    envelope2 = await coord.get_snapshot(
        AShareGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=explicit_as_of)
    )
    assert envelope2.ok is True
    assert envelope2.as_of == explicit_as_of
    assert envelope2.request_id != envelope.request_id
    assert envelope2.request_id.startswith("req_")
    # Product received resolved instrument + coordinator effective as_of.
    call_args = snap.get_snapshot.await_args
    assert call_args is not None
    assert call_args.args[0] is _INSTRUMENT
    assert call_args.args[1] == explicit_as_of
