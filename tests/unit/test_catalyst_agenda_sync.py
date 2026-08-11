from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from application.dto.catalyst_agenda_sync import CatalystAgendaSyncInput
from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.dto.provider_state import CacheEntry
from application.ports.catalyst_agenda_scope_reader import (
    AgendaScopeEntry,
    AgendaScopeSnapshot,
)
from application.ports.http_transport import HttpResponse
from application.services.catalyst_agenda_sync_service import CatalystAgendaSyncService
from conftest import FixedClock, SequentialIdGenerator
from domain.catalyst_agenda.calendar import (
    CatalystAgendaSyncReceipt,
    CatalystCalendarBatch,
    CatalystCalendarCandidate,
)
from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaScopeReason,
    AgendaSyncProviderStatus,
    AgendaSyncStatus,
)
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import ProviderNotConfigured, ProviderUnavailableError
from domain.instruments.models import Instrument
from infrastructure.providers.us.catalyst_calendar_codecs import catalyst_calendar_codec
from infrastructure.providers.us.fred import FredMacroAdapter
from infrastructure.providers.us.yahoo_finance_research import YahooFinanceResearchAdapter
from infrastructure.providers.us.yfinance_calendar_client import (
    YahooCalendarPayload,
    YahooSplitCalendarRow,
)

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def _instrument(symbol: str = "NVDA") -> Instrument:
    return Instrument(
        instrument_id=f"equity:US:{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


class _UnusedTransport:
    async def send(self, _request: object) -> HttpResponse:
        raise AssertionError("transport must not be used")


class _YahooCalendar:
    def __init__(self, payload: YahooCalendarPayload | Exception) -> None:
        self.payload = payload

    async def get_calendar(self, *_args: object, **_kwargs: object) -> YahooCalendarPayload:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.mark.asyncio
async def test_yahoo_calendar_normalizes_current_company_events() -> None:
    payload = YahooCalendarPayload(
        earnings_dates=(date(2026, 8, 20), date(2026, 8, 21)),
        ex_dividend_dates=(date(2026, 8, 18),),
        dividend_dates=(),
        splits=(YahooSplitCalendarRow("NVDA", date(2026, 8, 25)),),
    )
    adapter = YahooFinanceResearchAdapter(
        _UnusedTransport(),
        clock=FixedClock(NOW),
        calendar_client=_YahooCalendar(payload),
    )

    result = await adapter.get_catalyst_calendar(
        _instrument(),
        start=date(2026, 8, 9),
        end=date(2026, 9, 8),
        as_of=NOW,
    )

    assert result.meta.category is DataCategory.CORPORATE_ACTIONS
    assert {item.kind for item in result.value.candidates} == {
        AgendaItemKind.EARNINGS,
        AgendaItemKind.DIVIDEND,
        AgendaItemKind.CORPORATE_ACTION,
    }
    earnings = next(
        item for item in result.value.candidates if item.kind is AgendaItemKind.EARNINGS
    )
    assert earnings.date_certainty is AgendaDateCertainty.RANGE
    assert all(not item.historical_vintage for item in result.value.candidates)


@pytest.mark.asyncio
async def test_yahoo_failure_is_not_normalized_as_an_empty_calendar() -> None:
    adapter = YahooFinanceResearchAdapter(
        _UnusedTransport(),
        clock=FixedClock(NOW),
        calendar_client=_YahooCalendar(ProviderUnavailableError("unavailable")),
    )
    with pytest.raises(ProviderUnavailableError):
        await adapter.get_catalyst_calendar(
            _instrument(),
            start=NOW.date(),
            end=(NOW + timedelta(days=30)).date(),
            as_of=NOW,
        )


class _FredTransport:
    def __init__(self) -> None:
        self.request: object | None = None

    async def send(self, request: object) -> HttpResponse:
        self.request = request
        return HttpResponse(
            200,
            {"content-type": "application/json"},
            json.dumps(
                {
                    "release_dates": [
                        {
                            "release_id": 10,
                            "release_name": "Consumer Price Index",
                            "date": "2026-08-12",
                        }
                    ]
                }
            ).encode(),
        )


@pytest.mark.asyncio
async def test_fred_calendar_uses_future_date_semantics_and_global_scope() -> None:
    transport = _FredTransport()
    adapter = FredMacroAdapter(
        transport,
        api_key="test-key",
        clock=FixedClock(NOW),
    )

    result = await adapter.get_catalyst_calendar(
        None,
        start=NOW.date(),
        end=(NOW + timedelta(days=30)).date(),
        as_of=NOW,
        release_ids=(10,),
    )

    assert result.meta.category is DataCategory.MACRO
    candidate = result.value.candidates[0]
    assert candidate.instrument_id is None
    assert candidate.historical_vintage is True
    assert candidate.kind is AgendaItemKind.MACRO_RELEASE
    request = transport.request
    assert request is not None
    assert request.params["release_id"] == "10"  # type: ignore[union-attr]
    assert request.params["include_release_dates_with_no_data"] == "true"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_fred_calendar_without_key_is_typed_unavailable() -> None:
    adapter = FredMacroAdapter(_UnusedTransport(), api_key=None, clock=FixedClock(NOW))
    with pytest.raises(ProviderNotConfigured):
        await adapter.get_catalyst_calendar(
            None,
            start=NOW.date(),
            end=(NOW + timedelta(days=30)).date(),
            as_of=NOW,
            release_ids=(10,),
        )


def _candidate(
    vendor: VendorId,
    *,
    instrument_id: str | None,
    event_day: date,
    release_id: int = 10,
) -> CatalystCalendarCandidate:
    timezone = UTC
    kind = AgendaItemKind.MACRO_RELEASE if vendor is VendorId.FRED else AgendaItemKind.EARNINGS
    key = (
        f"fred:{release_id}:{event_day.year}-{event_day.month:02d}:1"
        if vendor is VendorId.FRED
        else f"{instrument_id}:earnings:2026Q3"
    )
    return CatalystCalendarCandidate(
        vendor=vendor,
        instrument_id=instrument_id,
        kind=kind,
        title="CPI" if vendor is VendorId.FRED else "Earnings",
        fiscal_period=f"{event_day.year}-{event_day.month:02d}",
        upstream_event_key=key,
        window_start=datetime.combine(event_day, datetime.min.time(), tzinfo=timezone),
        window_end=datetime.combine(event_day, datetime.max.time(), tzinfo=timezone),
        timezone="UTC",
        date_certainty=AgendaDateCertainty.CONFIRMED,
        source_reference=None,
        source_visible_at=NOW,
        last_verified_at=NOW,
        historical_vintage=vendor is VendorId.FRED,
    )


def _meta(vendor: VendorId) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=vendor,
        category=(
            DataCategory.MACRO
            if vendor is VendorId.FRED
            else DataCategory.CORPORATE_ACTIONS
        ),
        role=SourceRole.PRIMARY,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )


class _Router:
    def __init__(self, *, fail_yahoo: bool = False, empty_yahoo: bool = False) -> None:
        self.fail_yahoo = fail_yahoo
        self.empty_yahoo = empty_yahoo
        self.fred_day = date(2026, 8, 12)
        self.calls: list[tuple[DataCategory, str | None]] = []

    async def execute(self, **kwargs: object) -> RouterExecutionResult[CatalystCalendarBatch]:
        category = kwargs["category"]
        instrument = kwargs["instrument"]
        instrument_id = instrument.instrument_id if isinstance(instrument, Instrument) else None
        expected_category = (
            DataCategory.CORPORATE_ACTIONS
            if instrument_id is not None
            else DataCategory.MACRO
        )
        assert category is expected_category
        self.calls.append((category, instrument_id))
        vendor = VendorId.YFINANCE if instrument_id else VendorId.FRED
        if self.fail_yahoo and vendor is VendorId.YFINANCE:
            return RouterExecutionResult(
                value=None,
                ok=False,
                criticality=DataCriticality.CORE,
                meta=None,
                attempts=(),
                warnings=(),
                error=ProviderUnavailableError("unavailable"),
            )
        candidates = ()
        if vendor is VendorId.FRED:
            candidates = (_candidate(vendor, instrument_id=None, event_day=self.fred_day),)
        elif not self.empty_yahoo:
            candidates = (
                _candidate(vendor, instrument_id=instrument_id, event_day=date(2026, 8, 20)),
            )
        batch = CatalystCalendarBatch(vendor, NOW.date(), date(2026, 9, 8), candidates)
        return RouterExecutionResult(
            value=batch,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=_meta(vendor),
            attempts=(),
            warnings=(),
            error=None,
        )


class _AgendaRepository:
    def __init__(self) -> None:
        self.values: list[object] = []
        self.logical: dict[str, str] = {}

    def get_current_by_logical_key(self, key: str) -> object | None:
        item_id = self.logical.get(key)
        return self.get_current(item_id) if item_id else None

    def get_current(self, item_id: str | None) -> object | None:
        matches = [value for value in self.values if value.agenda_item_id == item_id]
        return max(matches, key=lambda value: value.version) if matches else None

    def append_initial(self, identity: object, value: object) -> object:
        self.logical[identity.logical_key] = identity.agenda_item_id
        self.values.append(value)
        return value

    def append_version(self, value: object, *, expected_version: int) -> object:
        assert self.get_current(value.agenda_item_id).version == expected_version
        self.values.append(value)
        return value

    def list_visible(self, *, as_of: datetime) -> tuple[object, ...]:
        return tuple(value for value in self.values if value.recorded_at <= as_of)


class _ReceiptRepository:
    def __init__(self) -> None:
        self.values: list[CatalystAgendaSyncReceipt] = []

    def get_by_idempotency_key(self, key: str) -> CatalystAgendaSyncReceipt | None:
        return next((value for value in self.values if value.idempotency_key == key), None)

    def append(self, receipt: CatalystAgendaSyncReceipt) -> CatalystAgendaSyncReceipt:
        self.values.append(receipt)
        return receipt

    def latest(self) -> CatalystAgendaSyncReceipt | None:
        return self.values[-1] if self.values else None

    def list_since(
        self, _since: datetime, *, limit: int = 20
    ) -> tuple[CatalystAgendaSyncReceipt, ...]:
        return tuple(reversed(self.values[-limit:]))


class _Scope:
    def __init__(self, instrument_ids: tuple[str, ...]) -> None:
        self.instrument_ids = instrument_ids

    def read_current(self) -> AgendaScopeSnapshot:
        return AgendaScopeSnapshot(
            tuple(
                AgendaScopeEntry(value, None, (AgendaScopeReason.WATCHLIST,))
                for value in self.instrument_ids
            )
        )


class _Uow:
    def __init__(self, instruments: dict[str, Instrument]) -> None:
        self.instruments = SimpleNamespace(get_by_id=instruments.get)

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _service(
    router: _Router,
    agenda: _AgendaRepository,
    receipts: _ReceiptRepository,
    *,
    durable: tuple[str, ...] = (),
) -> CatalystAgendaSyncService:
    instruments = {value: _instrument(value.rsplit(":", 1)[-1]) for value in durable}
    instruments["equity:US:NVDA"] = _instrument("NVDA")
    instruments["equity:US:AAPL"] = _instrument("AAPL")
    return CatalystAgendaSyncService(
        router=router,  # type: ignore[arg-type]
        agenda_repository=agenda,  # type: ignore[arg-type]
        sync_repository=receipts,
        scope_reader=_Scope(durable),  # type: ignore[arg-type]
        instrument_uow_factory=lambda: _Uow(instruments),  # type: ignore[arg-type]
        calendar_codec=catalyst_calendar_codec(),
        clock=FixedClock(NOW),
        id_generator=SequentialIdGenerator(),
    )


@pytest.mark.asyncio
async def test_sync_merges_explicit_and_durable_scope_and_skips_unconfigured_fred() -> None:
    router, agenda, receipts = _Router(), _AgendaRepository(), _ReceiptRepository()
    service = _service(
        router,
        agenda,
        receipts,
        durable=("equity:US:AAPL",),
    )

    result = await service.sync(
        CatalystAgendaSyncInput(
            instrument_ids=("equity:US:NVDA",),
            idempotency_key="scope-union",
        )
    )

    assert result.scope_count == 2
    assert {item[1] for item in router.calls} == {"equity:US:AAPL", "equity:US:NVDA"}
    assert result.status == AgendaSyncStatus.PARTIAL
    assert any(
        item.vendor == VendorId.FRED and item.status == AgendaSyncProviderStatus.SKIPPED
        for item in result.provider_results
    )


@pytest.mark.asyncio
async def test_sync_persists_fred_as_global_provider_agenda() -> None:
    router, agenda, receipts = _Router(), _AgendaRepository(), _ReceiptRepository()
    service = _service(router, agenda, receipts)

    result = await service.sync(
        CatalystAgendaSyncInput(fred_release_ids=(10,), idempotency_key="fred-global")
    )

    assert result.status == AgendaSyncStatus.COMPLETE
    version = agenda.values[0]
    assert version.instrument_id is None and version.subject_id is None
    assert version.authorization_note == f"provider_sync:{result.receipt_id}"
    assert version.historical_vintage is True


@pytest.mark.asyncio
async def test_sync_keeps_empty_success_distinct_from_provider_failure() -> None:
    empty = await _service(
        _Router(empty_yahoo=True),
        _AgendaRepository(),
        _ReceiptRepository(),
        durable=("equity:US:NVDA",),
    ).sync(CatalystAgendaSyncInput(idempotency_key="empty"))
    failed = await _service(
        _Router(fail_yahoo=True),
        _AgendaRepository(),
        _ReceiptRepository(),
        durable=("equity:US:NVDA",),
    ).sync(CatalystAgendaSyncInput(idempotency_key="failed"))

    assert empty.succeeded_scope_count == 1 and empty.failed_scope_count == 0
    assert failed.succeeded_scope_count == 0 and failed.failed_scope_count == 1
    assert failed.status == AgendaSyncStatus.FAILED


@pytest.mark.asyncio
async def test_sync_revises_date_drift_without_creating_a_second_identity() -> None:
    router, agenda, receipts = _Router(), _AgendaRepository(), _ReceiptRepository()
    service = _service(router, agenda, receipts)
    await service.sync(CatalystAgendaSyncInput(fred_release_ids=(10,), idempotency_key="first"))
    router.fred_day = date(2026, 8, 13)

    second = await service.sync(
        CatalystAgendaSyncInput(fred_release_ids=(10,), idempotency_key="second")
    )

    assert second.revised_count == 1
    assert second.date_drift_count == 1
    assert second.appended_count == 0
    assert len({value.agenda_item_id for value in agenda.values}) == 1


def test_calendar_codec_round_trip_preserves_vintage_and_certainty() -> None:
    batch = CatalystCalendarBatch(
        VendorId.FRED,
        NOW.date(),
        date(2026, 9, 8),
        (_candidate(VendorId.FRED, instrument_id=None, event_day=date(2026, 8, 12)),),
    )
    codec = catalyst_calendar_codec()
    success = ProviderSuccess(batch, _meta(VendorId.FRED))
    payload = codec.encode(success)
    entry = CacheEntry(
        key="calendar",
        category=DataCategory.MACRO,
        market=Market.US,
        instrument_id=None,
        vendor=VendorId.FRED,
        payload_json=payload,
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        freshness=Freshness.FRESH,
    )

    decoded = codec.decode(entry)

    assert decoded.value == batch
