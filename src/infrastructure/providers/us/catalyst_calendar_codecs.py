"""Typed cache codec for normalized Catalyst Calendar batches."""

from __future__ import annotations

from datetime import date, datetime

from domain.catalyst_agenda.calendar import CatalystCalendarBatch, CatalystCalendarCandidate
from domain.catalyst_agenda.enums import AgendaDateCertainty, AgendaItemKind
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from infrastructure.providers.us.codecs import USProviderCacheCodec


def _encode(value: CatalystCalendarBatch) -> dict[str, object]:
    if not isinstance(value, CatalystCalendarBatch):
        raise DataContractError("calendar cache value must be CatalystCalendarBatch")
    return {
        "vendor": value.vendor.value,
        "start": value.start.isoformat(),
        "end": value.end.isoformat(),
        "limitation_codes": list(value.limitation_codes),
        "candidates": [
            {
                "vendor": item.vendor.value,
                "instrument_id": item.instrument_id,
                "kind": item.kind.value,
                "title": item.title,
                "fiscal_period": item.fiscal_period,
                "upstream_event_key": item.upstream_event_key,
                "window_start": item.window_start.isoformat(),
                "window_end": item.window_end.isoformat(),
                "timezone": item.timezone,
                "date_certainty": item.date_certainty.value,
                "source_reference": item.source_reference,
                "source_visible_at": item.source_visible_at.isoformat(),
                "last_verified_at": item.last_verified_at.isoformat(),
                "historical_vintage": item.historical_vintage,
            }
            for item in value.candidates
        ],
    }


def _decode(raw: object) -> CatalystCalendarBatch:
    try:
        if not isinstance(raw, dict) or set(raw) != {
            "vendor",
            "start",
            "end",
            "limitation_codes",
            "candidates",
        }:
            raise ValueError
        candidates_raw = raw["candidates"]
        limitations_raw = raw["limitation_codes"]
        if not isinstance(candidates_raw, list) or not isinstance(limitations_raw, list):
            raise ValueError
        candidates: list[CatalystCalendarCandidate] = []
        expected = {
            "vendor",
            "instrument_id",
            "kind",
            "title",
            "fiscal_period",
            "upstream_event_key",
            "window_start",
            "window_end",
            "timezone",
            "date_certainty",
            "source_reference",
            "source_visible_at",
            "last_verified_at",
            "historical_vintage",
        }
        for item in candidates_raw:
            if not isinstance(item, dict) or set(item) != expected:
                raise ValueError
            candidates.append(
                CatalystCalendarCandidate(
                    vendor=VendorId(item["vendor"]),
                    instrument_id=item["instrument_id"],
                    kind=AgendaItemKind(item["kind"]),
                    title=item["title"],
                    fiscal_period=item["fiscal_period"],
                    upstream_event_key=item["upstream_event_key"],
                    window_start=datetime.fromisoformat(item["window_start"]),
                    window_end=datetime.fromisoformat(item["window_end"]),
                    timezone=item["timezone"],
                    date_certainty=AgendaDateCertainty(item["date_certainty"]),
                    source_reference=item["source_reference"],
                    source_visible_at=datetime.fromisoformat(item["source_visible_at"]),
                    last_verified_at=datetime.fromisoformat(item["last_verified_at"]),
                    historical_vintage=item["historical_vintage"],
                )
            )
        return CatalystCalendarBatch(
            vendor=VendorId(raw["vendor"]),
            start=date.fromisoformat(raw["start"]),
            end=date.fromisoformat(raw["end"]),
            candidates=tuple(candidates),
            limitation_codes=tuple(limitations_raw),
        )
    except DataContractError:
        raise
    except (KeyError, TypeError, ValueError):
        raise DataContractError("calendar cache value failed schema validation") from None


def catalyst_calendar_codec() -> USProviderCacheCodec[CatalystCalendarBatch]:
    return USProviderCacheCodec(
        codec_id="catalyst.calendar.v1",
        encode_value=_encode,
        decode_value=_decode,
    )
