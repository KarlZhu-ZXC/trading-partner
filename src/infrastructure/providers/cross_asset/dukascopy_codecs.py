"""Codecs for Dukascopy Jetta and legacy Trading Tools JSON payloads.

Provider-raw structures stay in infrastructure. Codecs are strict and fail
closed: malformed rows raise ``DataContractError``; empty successful payloads
become typed unavailability at the client layer (never fabricated values).

The primary codec follows the current ``dukascopy-node`` Jetta columnar/delta
format.  Legacy Trading Tools codecs remain for an optional key-backed fallback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from domain.common.errors import DataContractError
from domain.cross_asset.enums import OfferSide
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval

_VENDOR = "dukascopy"
_JSONP_RE = re.compile(r"^\s*(?:/\*.*?\*/\s*)?[A-Za-z_$][\w$]*\s*\((.*)\)\s*;?\s*$", re.DOTALL)

# Only intervals with a verified native Dukascopy historical timeFrame mapping.
_INTERVAL_TO_TIMEFRAME: dict[USBarInterval, str] = {
    USBarInterval.ONE_MINUTE: "1min",
    USBarInterval.SIXTY_MINUTES: "1hour",
    USBarInterval.ONE_DAY: "1day",
}
_TIMEFRAME_TO_INTERVAL: dict[str, USBarInterval] = {
    value: key for key, value in _INTERVAL_TO_TIMEFRAME.items()
}

# Seeded Trading Partner identities → Dukascopy instrument codes.
_INSTRUMENT_TO_DUKA: dict[str, str] = {
    "commodity_spot:OTC:XAUUSD": "XAU/USD",
    "commodity_spot:OTC:XAGUSD": "XAG/USD",
    "cfd:OTC:COPPER_CMD_USD": "COPPER.CMD/USD",
    "cfd:OTC:LIGHT_CMD_USD": "LIGHT.CMD/USD",
}
_DUKA_TO_INSTRUMENT: dict[str, str] = {v: k for k, v in _INSTRUMENT_TO_DUKA.items()}

_INSTRUMENT_TO_JETTA: dict[str, str] = {
    "commodity_spot:OTC:XAUUSD": "XAU-USD",
    "commodity_spot:OTC:XAGUSD": "XAG-USD",
    "cfd:OTC:COPPER_CMD_USD": "COPPER.CMD-USD",
    "cfd:OTC:LIGHT_CMD_USD": "LIGHT.CMD-USD",
}

_SUPPORTED_OFFER_SIDES = frozenset({OfferSide.BID, OfferSide.ASK})
_MAX_HISTORICAL_COUNT = 5000


def supported_bar_intervals() -> frozenset[USBarInterval]:
    return frozenset(_INTERVAL_TO_TIMEFRAME)


def dukascopy_instrument_code(instrument_id: str) -> str:
    code = _INSTRUMENT_TO_DUKA.get(instrument_id)
    if code is None:
        raise DataContractError(
            "instrument is not supported by the Dukascopy free adapter",
            details={
                "vendor": _VENDOR,
                "field": "instrument_id",
                "rule": "supported_instruments",
            },
        )
    return code


def dukascopy_jetta_instrument_code(instrument_id: str) -> str:
    code = _INSTRUMENT_TO_JETTA.get(instrument_id)
    if code is None:
        raise DataContractError(
            "instrument is not supported by the Dukascopy Jetta adapter",
            details={
                "vendor": _VENDOR,
                "field": "instrument_id",
                "rule": "supported_instruments",
            },
        )
    return code


def supported_jetta_instrument_codes() -> tuple[str, ...]:
    return tuple(_INSTRUMENT_TO_JETTA.values())


def instrument_id_for_dukascopy_code(code: str) -> str | None:
    return _DUKA_TO_INSTRUMENT.get(code.strip())


def timeframe_for_interval(interval: USBarInterval) -> str:
    mapped = _INTERVAL_TO_TIMEFRAME.get(interval)
    if mapped is None:
        raise DataContractError(
            "bar interval is not verified for Dukascopy historicalPrices",
            details={
                "vendor": _VENDOR,
                "field": "interval",
                "rule": "verified_timeframe_only",
                "interval": interval.value if isinstance(interval, USBarInterval) else None,
                "supported": sorted(i.value for i in _INTERVAL_TO_TIMEFRAME),
            },
        )
    return mapped


def interval_for_timeframe(timeframe: str) -> USBarInterval:
    mapped = _TIMEFRAME_TO_INTERVAL.get(timeframe)
    if mapped is None:
        raise DataContractError(
            "unknown Dukascopy timeFrame",
            details={
                "vendor": _VENDOR,
                "field": "timeFrame",
                "rule": "verified_timeframe_only",
            },
        )
    return mapped


def require_offer_side(offer_side: OfferSide) -> OfferSide:
    if offer_side not in _SUPPORTED_OFFER_SIDES:
        raise DataContractError(
            "offer_side must be B or A",
            details={"vendor": _VENDOR, "field": "offer_side", "rule": "enum"},
        )
    return offer_side


def clamp_historical_count(count: int) -> int:
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise DataContractError(
            "historical count must be a positive int",
            details={"vendor": _VENDOR, "field": "count", "rule": "positive"},
        )
    return min(count, _MAX_HISTORICAL_COUNT)


@dataclass(frozen=True, slots=True)
class DukascopyInstrumentRow:
    code: str
    instrument_id: int | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class DukascopyQuoteRow:
    instrument_code: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    quote_at: datetime | None


def _contract(
    message: str,
    *,
    operation: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    details: dict[str, object] = {
        "vendor": _VENDOR,
        "operation": operation,
        "rule": rule,
    }
    details.update(extra)
    return DataContractError(message, details=details)


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise DataContractError(
                "JSON object contains duplicate keys",
                details={"field": "json", "rule": "duplicate_key"},
            )
        out[key] = value
    return out


def _reject_nonfinite_constant(name: str) -> None:
    raise DataContractError(
        "JSON non-finite constant is not allowed",
        details={"field": "json", "rule": "no_nan_infinity", "constant": name},
    )


def _strip_jsonp(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return stripped
    if stripped[0] in "{[":
        return stripped
    match = _JSONP_RE.fullmatch(stripped)
    if match is not None:
        return match.group(1).strip()
    return stripped


def loads_dukascopy_json(body: bytes, *, operation: str) -> Any:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _contract(
            "Dukascopy response body is not valid UTF-8",
            operation=operation,
            rule="encoding",
        ) from exc
    text = _strip_jsonp(text)
    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_nonfinite_constant,
            object_pairs_hook=_reject_duplicate_object_pairs,
        )
    except DataContractError:
        raise
    except (ValueError, TypeError) as exc:
        raise _contract(
            "Dukascopy response is not valid JSON",
            operation=operation,
            rule="json",
        ) from exc


def _decimal(value: object, *, field: str, operation: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, float):
        raise _contract(
            f"Dukascopy {field} must not be binary float",
            operation=operation,
            rule="no_float",
            field=field,
        )
    if type(value) is Decimal:
        if not value.is_finite():
            raise _contract(
                f"Dukascopy {field} must be finite",
                operation=operation,
                rule="finite",
                field=field,
            )
        return value
    if type(value) is int and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise _contract(
                f"Dukascopy {field} is not a decimal",
                operation=operation,
                rule="decimal",
                field=field,
            ) from exc
        if not parsed.is_finite():
            raise _contract(
                f"Dukascopy {field} must be finite",
                operation=operation,
                rule="finite",
                field=field,
            )
        return parsed
    raise _contract(
        f"Dukascopy {field} has unsupported type",
        operation=operation,
        rule="type",
        field=field,
    )


def _require_decimal(value: object, *, field: str, operation: str) -> Decimal:
    number = _decimal(value, field=field, operation=operation)
    if number is None:
        raise _contract(
            f"Dukascopy {field} is required",
            operation=operation,
            rule="required",
            field=field,
        )
    return number


def _epoch_ms_to_datetime(value: object, *, field: str, operation: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, float):
        raise _contract(
            f"Dukascopy {field} must not be binary float",
            operation=operation,
            rule="no_float",
            field=field,
        )
    if type(value) is Decimal:
        if not value.is_finite() or value != value.to_integral_value():
            raise _contract(
                f"Dukascopy {field} must be an integer epoch ms",
                operation=operation,
                rule="epoch_ms",
                field=field,
            )
        millis = int(value)
    elif type(value) is int and not isinstance(value, bool):
        millis = value
    elif isinstance(value, str) and value.strip().isdigit():
        millis = int(value.strip())
    else:
        raise _contract(
            f"Dukascopy {field} must be epoch milliseconds",
            operation=operation,
            rule="epoch_ms",
            field=field,
        )
    # Accept seconds only if clearly too small for modern ms timestamps.
    if 0 < millis < 10_000_000_000:
        millis *= 1000
    if millis < 0:
        raise _contract(
            f"Dukascopy {field} must be nonnegative",
            operation=operation,
            rule="epoch_ms",
            field=field,
        )
    try:
        return datetime.fromtimestamp(millis / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise _contract(
            f"Dukascopy {field} is out of range",
            operation=operation,
            rule="epoch_ms",
            field=field,
        ) from exc


def _mapping_get(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in row:
            return row[key]
    lower = {str(k).casefold(): v for k, v in row.items()}
    for key in keys:
        if key.casefold() in lower:
            return lower[key.casefold()]
    return None


def decode_instrument_list(payload: object) -> tuple[DukascopyInstrumentRow, ...]:
    operation = "instrumentList"
    rows_raw: Sequence[object]
    if isinstance(payload, list):
        rows_raw = payload
    elif isinstance(payload, Mapping):
        instruments = payload.get("instruments")
        if not isinstance(instruments, list):
            raise _contract(
                "instrumentList payload must be a list or instruments object",
                operation=operation,
                rule="shape",
            )
        rows_raw = instruments
    else:
        raise _contract(
            "instrumentList payload must be a list or object",
            operation=operation,
            rule="shape",
        )

    out: list[DukascopyInstrumentRow] = []
    seen: set[str] = set()
    for idx, item in enumerate(rows_raw):
        if not isinstance(item, Mapping):
            if isinstance(item, str) and item.strip():
                code = item.strip()
                name = None
            else:
                raise _contract(
                    "instrumentList row is malformed",
                    operation=operation,
                    rule="row_shape",
                    index=idx,
                )
        else:
            raw_code = _mapping_get(item, "name", "instrument", "symbol", "code")
            if not isinstance(raw_code, str) or not raw_code.strip():
                raise _contract(
                    "instrumentList row missing instrument code",
                    operation=operation,
                    rule="instrument_code",
                    index=idx,
                )
            code = raw_code.strip()
            raw_name = _mapping_get(item, "description", "title", "name")
            name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
        instrument_id: int | None = None
        if isinstance(item, Mapping):
            raw_id = _mapping_get(item, "id")
            if raw_id is not None and raw_id != "":
                if type(raw_id) is int and not isinstance(raw_id, bool):
                    instrument_id = raw_id
                elif isinstance(raw_id, str) and raw_id.strip().isdigit():
                    instrument_id = int(raw_id.strip())
                else:
                    raise _contract(
                        "instrumentList id must be a positive integer",
                        operation=operation,
                        rule="instrument_id",
                        index=idx,
                    )
                if instrument_id < 1:
                    raise _contract(
                        "instrumentList id must be a positive integer",
                        operation=operation,
                        rule="instrument_id",
                        index=idx,
                    )
        if code in seen:
            raise _contract(
                "instrumentList contains duplicate instrument codes",
                operation=operation,
                rule="unique",
                index=idx,
            )
        seen.add(code)
        out.append(
            DukascopyInstrumentRow(
                code=code,
                instrument_id=instrument_id,
                name=name,
            )
        )
    return tuple(out)


def decode_current_prices(payload: object) -> tuple[DukascopyQuoteRow, ...]:
    operation = "currentPrices"
    rows_raw: Sequence[object]
    if isinstance(payload, list):
        rows_raw = payload
    elif isinstance(payload, Mapping):
        # Either {instrument: {...}} map or {"prices":[...]} / {"quotes":[...]}
        for key in ("prices", "quotes", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows_raw = candidate
                break
        else:
            rows_raw = []
            for key, value in payload.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                if isinstance(value, Mapping):
                    merged = dict(value)
                    merged.setdefault("instrument", key)
                    rows_raw = [*rows_raw, merged]
            if not rows_raw:
                raise _contract(
                    "currentPrices payload has unknown shape",
                    operation=operation,
                    rule="shape",
                )
    else:
        raise _contract(
            "currentPrices payload must be a list or object",
            operation=operation,
            rule="shape",
        )

    out: list[DukascopyQuoteRow] = []
    for idx, item in enumerate(rows_raw):
        if not isinstance(item, Mapping):
            raise _contract(
                "currentPrices row is malformed",
                operation=operation,
                rule="row_shape",
                index=idx,
            )
        raw_code = _mapping_get(item, "instrument", "name", "symbol", "code")
        if not isinstance(raw_code, str) or not raw_code.strip():
            raise _contract(
                "currentPrices row missing instrument code",
                operation=operation,
                rule="instrument_code",
                index=idx,
            )
        bid = _decimal(
            _mapping_get(item, "bid", "bidPrice", "b"),
            field="bid",
            operation=operation,
        )
        ask = _decimal(
            _mapping_get(item, "ask", "askPrice", "a"),
            field="ask",
            operation=operation,
        )
        last = _decimal(
            _mapping_get(item, "last", "lastPrice", "price", "mid"),
            field="last",
            operation=operation,
        )
        if bid is None and ask is None and last is None:
            raise _contract(
                "currentPrices row has no price fields",
                operation=operation,
                rule="price_required",
                index=idx,
            )
        if bid is not None and ask is not None and bid > ask:
            raise _contract(
                "currentPrices bid must be <= ask",
                operation=operation,
                rule="bid_ask_order",
                index=idx,
            )
        quote_at = _epoch_ms_to_datetime(
            _mapping_get(item, "timestamp", "time", "updated", "t"),
            field="timestamp",
            operation=operation,
        )
        out.append(
            DukascopyQuoteRow(
                instrument_code=raw_code.strip(),
                bid=bid,
                ask=ask,
                last=last,
                quote_at=quote_at,
            )
        )
    return tuple(out)


def _bar_from_parts(
    *,
    timestamp: datetime,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
    index: int,
    operation: str,
) -> MarketBar:
    if high < low:
        raise _contract(
            "historicalPrices high must be >= low",
            operation=operation,
            rule="ohlc",
            index=index,
        )
    if high < open_ or high < close:
        raise _contract(
            "historicalPrices high must cover open/close",
            operation=operation,
            rule="ohlc",
            index=index,
        )
    if low > open_ or low > close:
        raise _contract(
            "historicalPrices low must cover open/close",
            operation=operation,
            rule="ohlc",
            index=index,
        )
    if volume < 0:
        raise _contract(
            "historicalPrices volume must be nonnegative",
            operation=operation,
            rule="volume",
            index=index,
        )
    return MarketBar(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _decode_bar_object(item: Mapping[str, object], *, index: int) -> MarketBar:
    operation = "historicalPrices"
    ts = _epoch_ms_to_datetime(
        _mapping_get(item, "timestamp", "time", "t", "date"),
        field="timestamp",
        operation=operation,
    )
    if ts is None:
        raise _contract(
            "historicalPrices bar missing timestamp",
            operation=operation,
            rule="timestamp",
            index=index,
        )
    open_ = _require_decimal(
        _mapping_get(item, "open", "o", "Open"),
        field="open",
        operation=operation,
    )
    high = _require_decimal(
        _mapping_get(item, "high", "h", "High"),
        field="high",
        operation=operation,
    )
    low = _require_decimal(
        _mapping_get(item, "low", "l", "Low"),
        field="low",
        operation=operation,
    )
    close = _require_decimal(
        _mapping_get(item, "close", "c", "Close"),
        field="close",
        operation=operation,
    )
    volume = _require_decimal(
        _mapping_get(item, "volume", "vol", "v", "Volume"),
        field="volume",
        operation=operation,
    )
    return _bar_from_parts(
        timestamp=ts,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        index=index,
        operation=operation,
    )


def _decode_bar_sequence(item: Sequence[object], *, index: int) -> MarketBar:
    operation = "historicalPrices"
    if len(item) < 6:
        raise _contract(
            "historicalPrices tuple bar must have 6 fields",
            operation=operation,
            rule="tuple_shape",
            index=index,
        )
    ts = _epoch_ms_to_datetime(item[0], field="timestamp", operation=operation)
    if ts is None:
        raise _contract(
            "historicalPrices bar missing timestamp",
            operation=operation,
            rule="timestamp",
            index=index,
        )
    open_ = _require_decimal(item[1], field="open", operation=operation)
    high = _require_decimal(item[2], field="high", operation=operation)
    low = _require_decimal(item[3], field="low", operation=operation)
    close = _require_decimal(item[4], field="close", operation=operation)
    volume = _require_decimal(item[5], field="volume", operation=operation)
    return _bar_from_parts(
        timestamp=ts,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        index=index,
        operation=operation,
    )


def _decode_columnar(payload: Mapping[str, object]) -> tuple[MarketBar, ...]:
    operation = "historicalPrices"
    times = _mapping_get(payload, "times", "timestamps", "t")
    opens = _mapping_get(payload, "opens", "open", "o")
    highs = _mapping_get(payload, "highs", "high", "h")
    lows = _mapping_get(payload, "lows", "low", "l")
    closes = _mapping_get(payload, "closes", "close", "c")
    volumes = _mapping_get(payload, "volumes", "volume", "v")
    columns = (times, opens, highs, lows, closes, volumes)
    if any(not isinstance(col, list) for col in columns):
        raise _contract(
            "historicalPrices columnar payload is incomplete",
            operation=operation,
            rule="columnar_shape",
        )
    assert isinstance(times, list)
    assert isinstance(opens, list)
    assert isinstance(highs, list)
    assert isinstance(lows, list)
    assert isinstance(closes, list)
    assert isinstance(volumes, list)
    lengths = {len(times), len(opens), len(highs), len(lows), len(closes), len(volumes)}
    if len(lengths) != 1:
        raise _contract(
            "historicalPrices columnar arrays must share length",
            operation=operation,
            rule="columnar_length",
        )
    bars: list[MarketBar] = []
    for idx in range(len(times)):
        bars.append(
            _decode_bar_sequence(
                (times[idx], opens[idx], highs[idx], lows[idx], closes[idx], volumes[idx]),
                index=idx,
            )
        )
    return tuple(bars)


def decode_historical_prices(payload: object) -> tuple[MarketBar, ...]:
    operation = "historicalPrices"
    rows_raw: Sequence[object]
    if isinstance(payload, list):
        rows_raw = payload
    elif isinstance(payload, Mapping):
        for key in ("candles", "bars", "prices", "data", "values"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows_raw = candidate
                break
        else:
            # Column-oriented free-API shape.
            return _decode_columnar(payload)
    else:
        raise _contract(
            "historicalPrices payload must be a list or object",
            operation=operation,
            rule="shape",
        )

    bars: list[MarketBar] = []
    for idx, item in enumerate(rows_raw):
        if isinstance(item, Mapping):
            bars.append(_decode_bar_object(item, index=idx))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            bars.append(_decode_bar_sequence(item, index=idx))
        else:
            raise _contract(
                "historicalPrices bar is malformed",
                operation=operation,
                rule="row_shape",
                index=idx,
            )
    if len(bars) > _MAX_HISTORICAL_COUNT:
        raise _contract(
            "historicalPrices exceeded max count",
            operation=operation,
            rule="max_count",
            max_count=_MAX_HISTORICAL_COUNT,
        )
    # Ensure ascending order without inventing values.
    prev: datetime | None = None
    for idx, bar in enumerate(bars):
        if prev is not None and bar.timestamp <= prev:
            raise _contract(
                "historicalPrices timestamps must be strictly ascending",
                operation=operation,
                rule="strict_order",
                index=idx,
            )
        prev = bar.timestamp
    return tuple(bars)


def _jetta_integer(value: object, *, field: str, operation: str) -> int:
    if type(value) is int and not isinstance(value, bool):
        return value
    if type(value) is Decimal and value.is_finite() and value == value.to_integral_value():
        return int(value)
    raise _contract(
        f"Dukascopy Jetta {field} must be an integer",
        operation=operation,
        rule="integer",
        field=field,
    )


def _jetta_column(
    payload: Mapping[str, object],
    key: str,
    *,
    expected_length: int,
    operation: str,
) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list) or len(value) != expected_length:
        raise _contract(
            "Dukascopy Jetta column lengths must match",
            operation=operation,
            rule="columnar_length",
            field=key,
        )
    return value


def decode_jetta_candles(payload: object) -> tuple[MarketBar, ...]:
    """Decode Jetta's base-plus-delta candle representation.

    Jetta expresses timestamp deltas in units of ``shift`` and price deltas in
    integer units of ``multiplier``.  Unlike the presentation layer in
    ``dukascopy-node``, this provider does not synthesize flat candles for gaps.
    """

    operation = "jetta_candles"
    if not isinstance(payload, Mapping):
        raise _contract(
            "Dukascopy Jetta candle payload must be an object",
            operation=operation,
            rule="shape",
        )
    times_raw = payload.get("times")
    if not isinstance(times_raw, list):
        raise _contract(
            "Dukascopy Jetta candle payload is missing times",
            operation=operation,
            rule="columnar_shape",
        )
    length = len(times_raw)
    opens = _jetta_column(payload, "opens", expected_length=length, operation=operation)
    highs = _jetta_column(payload, "highs", expected_length=length, operation=operation)
    lows = _jetta_column(payload, "lows", expected_length=length, operation=operation)
    closes = _jetta_column(payload, "closes", expected_length=length, operation=operation)
    volumes = _jetta_column(payload, "volumes", expected_length=length, operation=operation)
    if length == 0:
        return ()

    timestamp_ms = _jetta_integer(payload.get("timestamp"), field="timestamp", operation=operation)
    shift_ms = _jetta_integer(payload.get("shift"), field="shift", operation=operation)
    if timestamp_ms < 0 or shift_ms <= 0:
        raise _contract(
            "Dukascopy Jetta timestamp/shift is invalid",
            operation=operation,
            rule="time_base",
        )
    multiplier = _require_decimal(
        payload.get("multiplier"), field="multiplier", operation=operation
    )
    if multiplier <= 0:
        raise _contract(
            "Dukascopy Jetta multiplier must be positive",
            operation=operation,
            rule="positive",
            field="multiplier",
        )

    def base_units(field: str) -> int:
        value = _require_decimal(payload.get(field), field=field, operation=operation)
        return int((value / multiplier).to_integral_value(rounding=ROUND_HALF_UP))

    open_units = base_units("open")
    high_units = base_units("high")
    low_units = base_units("low")
    close_units = base_units("close")
    bars: list[MarketBar] = []
    for idx in range(length):
        time_delta = _jetta_integer(times_raw[idx], field="times", operation=operation)
        if time_delta < 0:
            raise _contract(
                "Dukascopy Jetta time deltas must be nonnegative",
                operation=operation,
                rule="time_delta",
                index=idx,
            )
        timestamp_ms += time_delta * shift_ms
        open_units += _jetta_integer(opens[idx], field="opens", operation=operation)
        high_units += _jetta_integer(highs[idx], field="highs", operation=operation)
        low_units += _jetta_integer(lows[idx], field="lows", operation=operation)
        close_units += _jetta_integer(closes[idx], field="closes", operation=operation)
        try:
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise _contract(
                "Dukascopy Jetta timestamp is out of range",
                operation=operation,
                rule="epoch_ms",
                index=idx,
            ) from exc
        volume = _require_decimal(volumes[idx], field="volumes", operation=operation)
        bars.append(
            _bar_from_parts(
                timestamp=timestamp,
                open_=Decimal(open_units) * multiplier,
                high=Decimal(high_units) * multiplier,
                low=Decimal(low_units) * multiplier,
                close=Decimal(close_units) * multiplier,
                volume=volume,
                index=idx,
                operation=operation,
            )
        )

    previous: datetime | None = None
    for idx, bar in enumerate(bars):
        if previous is not None and bar.timestamp <= previous:
            raise _contract(
                "Dukascopy Jetta timestamps must be strictly ascending",
                operation=operation,
                rule="strict_order",
                index=idx,
            )
        previous = bar.timestamp
    return tuple(bars)


__all__ = [
    "DukascopyInstrumentRow",
    "DukascopyQuoteRow",
    "clamp_historical_count",
    "decode_current_prices",
    "decode_historical_prices",
    "decode_instrument_list",
    "dukascopy_instrument_code",
    "decode_jetta_candles",
    "dukascopy_jetta_instrument_code",
    "instrument_id_for_dukascopy_code",
    "interval_for_timeframe",
    "loads_dukascopy_json",
    "require_offer_side",
    "supported_bar_intervals",
    "supported_jetta_instrument_codes",
    "timeframe_for_interval",
]
