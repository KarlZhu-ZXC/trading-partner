"""Codecs for DCE official publicweb JSON payloads (LH only).

Provider-raw structures stay in infrastructure. Codecs are strict and fail
closed: malformed futures rows raise ``DataContractError``; option/summary
rows are rejected; empty successful LH payloads become typed unavailability
at the client layer (never fabricated values).

Discovered request shapes (runtime calls DCE directly; no AKShare dependency):

* ``POST .../tradepara/contractInfo``
* ``POST .../dailystat/dayQuotes``
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from domain.common.errors import DataContractError, InvalidInstrument
from domain.cross_asset.dce_identity import (
    DCE_LH_CONTRACT_MONTHS,
    DCE_LH_ROOT,
    normalize_dce_contract_id,
    parse_dce_lh_contract_code,
)
from domain.cross_asset.enums import SettlementStatus

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_YYYYMMDD_RE = re.compile(r"^\d{8}$")
_SUMMARY_MARKERS = ("小计", "总计", "合计", "subtotal", "total")
_MAX_JSON_BYTES = 8_000_000


@dataclass(frozen=True, slots=True)
class DceContractInfoRow:
    """One outright futures contract lifecycle row from DCE contractInfo."""

    contract_code: str
    contract_month: str
    start_trade_date: date | None
    last_trade_date: date | None
    delivery_date: date | None


@dataclass(frozen=True, slots=True)
class DceDayQuoteRow:
    """One EOD settlement/volume/OI (+ optional OHLC) row from dayQuotes."""

    contract_code: str
    contract_month: str
    settlement: Decimal | None
    session_volume: Decimal | None
    open_interest: Decimal | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    settlement_status: SettlementStatus


@dataclass(frozen=True, slots=True)
class DceDayQuotesDocument:
    trade_date: date
    published_at: datetime
    rows: tuple[DceDayQuoteRow, ...]


def _contract(
    message: str,
    *,
    operation: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    details: dict[str, object] = {
        "vendor": "dce_official",
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


def loads_dce_json(body: bytes, *, operation: str) -> Any:
    if not isinstance(body, (bytes, bytearray)):
        raise _contract(
            "DCE response body must be bytes",
            operation=operation,
            rule="body_type",
        )
    if len(body) > _MAX_JSON_BYTES:
        raise _contract(
            "DCE response body exceeds maximum JSON size",
            operation=operation,
            rule="body_size",
            max_bytes=_MAX_JSON_BYTES,
        )
    if len(body) == 0:
        raise _contract(
            "DCE response body is empty",
            operation=operation,
            rule="empty_body",
        )
    try:
        text = bytes(body).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _contract(
            "DCE response body is not valid UTF-8",
            operation=operation,
            rule="encoding",
        ) from exc
    # Hard-fail HTML anti-bot / captcha pages that leak through as 200.
    stripped = text.lstrip()[:64].casefold()
    if stripped.startswith("<!doctype") or stripped.startswith("<html"):
        raise _contract(
            "DCE response body is HTML, not JSON",
            operation=operation,
            rule="html_body",
        )
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
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _contract(
            "DCE response body is not valid JSON",
            operation=operation,
            rule="json",
        ) from exc


def _as_decimal(value: object, *, field: str, operation: str) -> Decimal:
    if value is None:
        raise _contract(
            f"{field} is required",
            operation=operation,
            rule="required",
            field=field,
        )
    if type(value) is Decimal:
        if not value.is_finite():
            raise _contract(
                f"{field} must be finite",
                operation=operation,
                rule="finite_decimal",
                field=field,
            )
        return value
    if type(value) is int and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str):
        compact = value.strip().replace(",", "").replace("'", "")
        if not compact or compact in {"-", "--", "N/A", "n/a", "—", "－"}:
            raise _contract(
                f"{field} is blank",
                operation=operation,
                rule="blank_decimal",
                field=field,
            )
        try:
            parsed = Decimal(compact)
        except (InvalidOperation, ValueError) as exc:
            raise _contract(
                f"{field} is not a valid decimal",
                operation=operation,
                rule="decimal_parse",
                field=field,
            ) from exc
        if not parsed.is_finite():
            raise _contract(
                f"{field} must be finite",
                operation=operation,
                rule="finite_decimal",
                field=field,
            )
        return parsed
    raise _contract(
        f"{field} has unsupported type",
        operation=operation,
        rule="decimal_type",
        field=field,
        type=type(value).__name__,
    )


def _optional_decimal(value: object, *, field: str, operation: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str) and value.strip() in {
        "-",
        "--",
        "N/A",
        "n/a",
        "—",
        "－",
    }:
        return None
    number = _as_decimal(value, field=field, operation=operation)
    if number < 0:
        raise _contract(
            f"{field} must be nonnegative",
            operation=operation,
            rule="nonnegative",
            field=field,
        )
    return number


def _parse_yyyymmdd(value: object, *, field: str, operation: str) -> date | None:
    if value is None:
        return None
    if type(value) is int and not isinstance(value, bool):
        text = f"{value:08d}"
    elif isinstance(value, str):
        text = value.strip()
        if not text or text in {"-", "--"}:
            return None
        # Allow ISO dates as defensive fallback.
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            try:
                return date.fromisoformat(text[:10])
            except ValueError as exc:
                raise _contract(
                    f"{field} is not a parseable date",
                    operation=operation,
                    rule="date_parse",
                    field=field,
                ) from exc
    else:
        raise _contract(
            f"{field} has unsupported date type",
            operation=operation,
            rule="date_type",
            field=field,
            type=type(value).__name__,
        )
    if not _YYYYMMDD_RE.fullmatch(text):
        raise _contract(
            f"{field} must use YYYYMMDD",
            operation=operation,
            rule="date_format",
            field=field,
        )
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError as exc:
        raise _contract(
            f"{field} is not a valid calendar date",
            operation=operation,
            rule="date_value",
            field=field,
        ) from exc


def _is_summary_row(row: dict[str, object]) -> bool:
    for key in ("variety", "varietyName", "contractId", "contract"):
        raw = row.get(key)
        if isinstance(raw, str):
            text = raw.strip()
            lowered = text.casefold()
            if any(marker in text for marker in _SUMMARY_MARKERS[:3]):
                return True
            if any(marker in lowered for marker in _SUMMARY_MARKERS[3:]):
                return True
    return False


def _is_option_row(row: dict[str, object]) -> bool:
    trade_type = row.get("tradeType")
    if trade_type is not None:
        text = str(trade_type).strip()
        # DCE futures tradeType is "1"; options use other codes.
        if text and text not in {"1", "1.0"}:
            return True
    contract_id = row.get("contractId") or row.get("contract")
    if isinstance(contract_id, str):
        compact = contract_id.strip().upper()
        # Futures are LHYYMM only; options carry strike series markers.
        if (
            ("-" in compact or "C" in compact[2:] or "P" in compact[2:])
            and not re.fullmatch(r"LH\d{4}", compact)
        ):
            return True
    option_series = row.get("optionSeries")
    return isinstance(option_series, str) and bool(option_series.strip())


def _is_lh_variety_row(row: dict[str, object]) -> bool:
    variety_order = row.get("varietyOrder") or row.get("varietyId")
    if isinstance(variety_order, str) and variety_order.strip().upper() == DCE_LH_ROOT:
        return True
    if isinstance(variety_order, str) and variety_order.strip().casefold() == "lh":
        return True
    contract_id = row.get("contractId") or row.get("contract")
    if isinstance(contract_id, str) and contract_id.strip().upper().startswith(
        DCE_LH_ROOT
    ):
        return True
    variety = row.get("variety") or row.get("varietyName")
    return (
        isinstance(variety, str)
        and "生猪" in variety
        and not _is_summary_row(row)
    )


def _extract_data_list(payload: object, *, operation: str) -> list[object]:
    if not isinstance(payload, dict):
        raise _contract(
            "DCE payload must be an object",
            operation=operation,
            rule="payload_type",
        )
    # Some envelopes use success/code flags.
    success = payload.get("success")
    if success is False or success == 0 or success == "false":
        raise _contract(
            "DCE payload reports unsuccessful response",
            operation=operation,
            rule="success_flag",
        )
    data = payload.get("data")
    if data is None:
        # Empty object → empty list (typed no-data at client).
        if not payload or set(payload.keys()) <= {"success", "code", "msg", "message"}:
            return []
        raise _contract(
            "DCE payload missing data array",
            operation=operation,
            rule="missing_data",
        )
    if not isinstance(data, list):
        raise _contract(
            "DCE payload data must be a list",
            operation=operation,
            rule="data_type",
        )
    return data


def decode_contract_info(
    payload: object,
    *,
    operation: str = "contract_info",
) -> tuple[DceContractInfoRow, ...]:
    """Decode DCE contractInfo JSON into LH outright contract rows."""
    data = _extract_data_list(payload, operation=operation)
    rows: list[DceContractInfoRow] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise _contract(
                "contractInfo row must be an object",
                operation=operation,
                rule="row_type",
                index=idx,
            )
        if _is_summary_row(item) or _is_option_row(item):
            continue
        if not _is_lh_variety_row(item):
            continue
        raw_id = item.get("contractId") or item.get("contract")
        symbol = normalize_dce_contract_id(raw_id)
        if symbol is None:
            # LH variety but malformed identity — fail closed.
            raise _contract(
                "contractInfo LH row has malformed contractId",
                operation=operation,
                rule="contract_id",
                index=idx,
            )
        try:
            code = parse_dce_lh_contract_code(symbol)
        except InvalidInstrument as exc:
            raise _contract(
                "contractInfo LH row failed identity validation",
                operation=operation,
                rule="contract_id",
                index=idx,
            ) from exc
        if code.month not in DCE_LH_CONTRACT_MONTHS:
            raise _contract(
                "contractInfo LH row has disallowed contract month",
                operation=operation,
                rule="contract_months",
                index=idx,
                month=code.month,
            )
        start_trade = _parse_yyyymmdd(
            item.get("startTradeDate"),
            field="startTradeDate",
            operation=operation,
        )
        last_trade = _parse_yyyymmdd(
            item.get("endTradeDate"),
            field="endTradeDate",
            operation=operation,
        )
        delivery = _parse_yyyymmdd(
            item.get("endDeliveryDate"),
            field="endDeliveryDate",
            operation=operation,
        )
        rows.append(
            DceContractInfoRow(
                contract_code=code.symbol,
                contract_month=code.contract_month,
                start_trade_date=start_trade,
                last_trade_date=last_trade,
                delivery_date=delivery,
            )
        )
    rows.sort(key=lambda r: (r.contract_month, r.contract_code))
    return tuple(rows)


def _shanghai_eod(day: date) -> datetime:
    # Day-session close used as publication/as_of cutoff for EOD statistics.
    return datetime.combine(day, time(15, 0), tzinfo=_SHANGHAI)


def decode_day_quotes(
    payload: object,
    *,
    trade_date: date,
    operation: str = "day_quotes",
) -> DceDayQuotesDocument:
    """Decode DCE dayQuotes JSON into LH EOD settlement/volume/OI rows."""
    if type(trade_date) is not date:
        raise _contract(
            "trade_date must be a date",
            operation=operation,
            rule="trade_date_type",
        )
    data = _extract_data_list(payload, operation=operation)
    rows: list[DceDayQuoteRow] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise _contract(
                "dayQuotes row must be an object",
                operation=operation,
                rule="row_type",
                index=idx,
            )
        if _is_summary_row(item) or _is_option_row(item):
            continue
        if not _is_lh_variety_row(item):
            continue
        raw_id = item.get("contractId") or item.get("contract")
        symbol = normalize_dce_contract_id(raw_id)
        if symbol is None:
            raise _contract(
                "dayQuotes LH row has malformed contractId",
                operation=operation,
                rule="contract_id",
                index=idx,
            )
        try:
            code = parse_dce_lh_contract_code(symbol)
        except InvalidInstrument as exc:
            raise _contract(
                "dayQuotes LH row failed identity validation",
                operation=operation,
                rule="contract_id",
                index=idx,
            ) from exc
        # DCE wire typo: "volumn" is the official volume field name.
        volume_raw = item.get("volumn")
        if volume_raw is None:
            volume_raw = item.get("volume")
        settlement = _optional_decimal(
            item.get("clearPrice") if item.get("clearPrice") is not None else item.get("settle"),
            field="clearPrice",
            operation=operation,
        )
        session_volume = _optional_decimal(
            volume_raw, field="volumn", operation=operation
        )
        open_interest = _optional_decimal(
            item.get("openInterest"), field="openInterest", operation=operation
        )
        open_px = _optional_decimal(item.get("open"), field="open", operation=operation)
        high_px = _optional_decimal(item.get("high"), field="high", operation=operation)
        low_px = _optional_decimal(item.get("low"), field="low", operation=operation)
        close_px = _optional_decimal(
            item.get("close"), field="close", operation=operation
        )
        rows.append(
            DceDayQuoteRow(
                contract_code=code.symbol,
                contract_month=code.contract_month,
                settlement=settlement,
                session_volume=session_volume,
                open_interest=open_interest,
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                settlement_status=SettlementStatus.FINAL,
            )
        )
    rows.sort(key=lambda r: (r.contract_month, r.contract_code))
    return DceDayQuotesDocument(
        trade_date=trade_date,
        published_at=_shanghai_eod(trade_date),
        rows=tuple(rows),
    )
