"""FRED/ALFRED macro context adapter with historical vintage cutoffs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.catalyst_agenda.calendar import CatalystCalendarBatch, CatalystCalendarCandidate
from domain.catalyst_agenda.enums import AgendaDateCertainty, AgendaItemKind
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_context.models import (
    USMacroContext,
    USMacroObservation,
    USMacroSeriesSnapshot,
)
from infrastructure.system.clock import SystemClock

_BASE: Final[str] = "https://api.stlouisfed.org/fred"
_CHICAGO: Final = ZoneInfo("America/Chicago")


def _contract(message: str, *, rule: str) -> DataContractError:
    return DataContractError(
        message,
        details={"vendor": VendorId.FRED.value, "operation": "macro", "rule": rule},
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _contract("FRED JSON contains duplicate keys", rule="duplicate_key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise _contract("FRED JSON contains a non-finite value", rule="nonfinite")


def _loads(body: bytes) -> object:
    try:
        return json.loads(
            body.decode("utf-8"),
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_constant,
            object_pairs_hook=_pairs,
        )
    except DataContractError:
        raise
    except (UnicodeDecodeError, ValueError, TypeError):
        raise _contract("FRED response is not valid JSON", rule="json") from None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _day(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise _contract(f"FRED {field} is invalid", rule=field)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _contract(f"FRED {field} is invalid", rule=field) from None


def _number(value: object) -> Decimal | None:
    if value is None or value == "" or value == ".":
        return None
    if type(value) is Decimal:
        return value if value.is_finite() else None
    if type(value) is int:
        return Decimal(value)
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            raise _contract("FRED observation value is invalid", rule="value") from None
        return parsed if parsed.is_finite() else None
    raise _contract("FRED observation value is invalid", rule="value")


class FredMacroAdapter:
    """Read-only FRED provider; requested date is also the ALFRED vintage date."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_key: str | None,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be positive", details={"field": "timeout_seconds"}
            )
        self._transport = transport
        self._api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout = float(timeout_seconds)

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.FRED

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category in {
            DataCategory.MACRO,
        }

    def is_configured(self) -> bool:
        return self._enabled and self._api_key is not None

    def _require_as_of(self, as_of: datetime) -> None:
        require_aware_datetime(as_of, field_name="as_of")
        if as_of > self._clock.now():
            raise DataContractError("as_of must not be in the future", details={"field": "as_of"})
        if not self.is_configured():
            raise ProviderNotConfigured(
                "FRED adapter is not configured", details={"vendor": self.vendor_id.value}
            )

    async def _get(
        self, path: str, params: Mapping[str, str]
    ) -> tuple[Mapping[str, object], datetime]:
        assert self._api_key is not None
        wire = dict(params)
        wire.update({"api_key": self._api_key, "file_type": "json"})
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=f"{_BASE}/{path}",
                params=wire,
                headers={"Accept": "application/json"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        fetched_at = self._clock.now()
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "FRED rate limited", details={"vendor": self.vendor_id.value}
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError(
                "FRED HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "status_class": f"{response.status_code // 100}xx",
                },
            )
        content_type = response.headers.get("content-type") or response.headers.get("Content-Type")
        if not isinstance(content_type, str) or "json" not in content_type.casefold():
            raise _contract("FRED response Content-Type is invalid", rule="content_type")
        payload = _loads(response.body)
        if not isinstance(payload, Mapping):
            raise _contract("FRED payload has invalid shape", rule="contract_drift")
        return payload, fetched_at

    async def _series(
        self,
        series_id: str,
        *,
        start: date,
        cutoff: date,
    ) -> tuple[USMacroSeriesSnapshot, datetime]:
        vintage = cutoff.isoformat()
        meta_payload, _ = await self._get(
            "series",
            {
                "series_id": series_id,
                "realtime_start": vintage,
                "realtime_end": vintage,
            },
        )
        observation_payload, fetched_at = await self._get(
            "series/observations",
            {
                "series_id": series_id,
                "observation_start": start.isoformat(),
                "observation_end": cutoff.isoformat(),
                "realtime_start": vintage,
                "realtime_end": vintage,
                "sort_order": "asc",
            },
        )
        meta_rows = meta_payload.get("seriess")
        if (
            not isinstance(meta_rows, list)
            or not meta_rows
            or not isinstance(meta_rows[0], Mapping)
        ):
            raise _contract("FRED series metadata is unavailable", rule="series_metadata")
        meta = meta_rows[0]
        rows = observation_payload.get("observations")
        if not isinstance(rows, list):
            raise _contract("FRED observations have invalid shape", rule="observations")
        observations: list[USMacroObservation] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise _contract("FRED observation has invalid shape", rule="observation")
            value = _number(row.get("value"))
            if value is None:
                continue
            observation_day = _day(row.get("date"), field="date")
            vintage_day = _day(row.get("realtime_start"), field="realtime_start")
            if observation_day > cutoff or vintage_day > cutoff:
                continue
            observations.append(USMacroObservation(observation_day, vintage_day, value))
        points = tuple(observations)
        latest = points[-1].value if points else None
        change = latest - points[0].value if latest is not None else None
        last_updated = self._parse_last_updated(meta.get("last_updated"))
        return (
            USMacroSeriesSnapshot(
                series_id=series_id,
                title=(_text(meta.get("title")) or series_id)[:256],
                unit=(_text(meta.get("units_short") or meta.get("units")) or "unknown")[:128],
                frequency=(
                    _text(meta.get("frequency_short") or meta.get("frequency")) or "unknown"
                )[:128],
                last_updated=last_updated,
                observations=points,
                latest_value=latest,
                window_change=change,
            ),
            fetched_at,
        )

    @staticmethod
    def _parse_last_updated(value: object) -> datetime | None:
        text = _text(value)
        if text is None:
            return None
        for fmt in ("%Y-%m-%d %I:%M:%S %p %Z", "%Y-%m-%d %I:%M %p %Z"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=_CHICAGO)
            except ValueError:
                continue
        return None

    async def get_macro_context(
        self,
        *,
        series_ids: tuple[str, ...],
        lookback_days: int,
        as_of: datetime,
    ) -> ProviderSuccess[USMacroContext]:
        self._require_as_of(as_of)
        cutoff = as_of.date()
        start = cutoff - timedelta(days=lookback_days)
        results = await asyncio.gather(
            *(self._series(series_id, start=start, cutoff=cutoff) for series_id in series_ids)
        )
        series = tuple(item[0] for item in results)
        fetched_at = max((item[1] for item in results), default=self._clock.now())
        context = USMacroContext(as_of, series, False, ())
        meta = ProviderResultMeta(
            vendor=self.vendor_id,
            category=DataCategory.MACRO,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            session=TradingSession.UNKNOWN,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=(),
        )
        return ProviderSuccess(context, meta)

    async def get_catalyst_calendar(
        self,
        instrument: Instrument | None,
        *,
        start: date,
        end: date,
        as_of: datetime,
        release_ids: tuple[int, ...] = (),
    ) -> ProviderSuccess[CatalystCalendarBatch]:
        """Return official FRED release dates visible at the requested vintage."""
        if instrument is not None:
            raise DataContractError("FRED catalyst calendar is macro-only")
        if len(release_ids) != 1 or release_ids[0] < 1:
            raise DataContractError("FRED catalyst calendar requires one positive release_id")
        self._require_as_of(as_of)
        if end < start:
            raise DataContractError("calendar end must be >= start")
        vintage = as_of.date().isoformat()
        payload, fetched_at = await self._get(
            "release/dates",
            {
                "release_id": str(release_ids[0]),
                "realtime_start": vintage,
                "realtime_end": vintage,
                "include_release_dates_with_no_data": "true",
                "order_by": "release_date",
                "sort_order": "desc",
                "limit": "1000",
            },
        )
        rows = payload.get("release_dates")
        if not isinstance(rows, list):
            raise _contract("FRED release dates have invalid shape", rule="release_dates")
        parsed: list[tuple[int, str, date]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise _contract("FRED release date has invalid shape", rule="release_date")
            release_id = row.get("release_id")
            release_name = _text(row.get("release_name"))
            release_day = _day(row.get("date"), field="date")
            if type(release_id) is not int or release_name is None:
                raise _contract("FRED release date identity is invalid", rule="release_identity")
            if start <= release_day <= end:
                parsed.append((release_id, release_name, release_day))
        parsed.sort(key=lambda item: (item[0], item[2], item[1]))
        occurrence: dict[tuple[int, int, int], int] = {}
        candidates: list[CatalystCalendarCandidate] = []
        for release_id, release_name, release_day in parsed:
            bucket = (release_id, release_day.year, release_day.month)
            ordinal = occurrence.get(bucket, 0) + 1
            occurrence[bucket] = ordinal
            candidates.append(
                CatalystCalendarCandidate(
                    vendor=self.vendor_id,
                    instrument_id=None,
                    kind=AgendaItemKind.MACRO_RELEASE,
                    title=release_name[:300],
                    fiscal_period=f"{release_day.year}-{release_day.month:02d}",
                    upstream_event_key=(
                        f"fred:{release_id}:{release_day.year}-{release_day.month:02d}:{ordinal}"
                    ),
                    window_start=datetime.combine(release_day, time.min, tzinfo=_CHICAGO),
                    window_end=datetime.combine(release_day, time.max, tzinfo=_CHICAGO),
                    timezone="America/Chicago",
                    date_certainty=AgendaDateCertainty.CONFIRMED,
                    source_reference=f"https://fred.stlouisfed.org/release?rid={release_id}",
                    source_visible_at=as_of,
                    last_verified_at=fetched_at,
                    historical_vintage=True,
                )
            )
        batch = CatalystCalendarBatch(
            vendor=self.vendor_id,
            start=start,
            end=end,
            candidates=tuple(candidates),
            limitation_codes=("FRED_RELEASE_DATE_NOT_EXACT_PUBLICATION_TIME",),
        )
        return ProviderSuccess(
            batch,
            ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.MACRO,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.FRESH,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=None,
                data_delay_seconds=None,
                warnings=batch.limitation_codes,
            ),
        )
