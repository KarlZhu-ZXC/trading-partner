"""Codecs for CME Group public CmeWS JSON payloads.

Provider-raw structures stay in infrastructure. Codecs are strict and fail
closed: malformed rows raise ``DataContractError``; empty successful payloads
become typed unavailability at the client layer (never fabricated values).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from domain.common.errors import DataContractError
from domain.cross_asset.cme_identity import (
    contract_code_from_parts,
    contract_month_from_month_code,
)
from domain.cross_asset.enums import SettlementStatus

_NY = ZoneInfo("America/New_York")
_MONTH_NAME_TO_NUM: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_MONTH_CODE_RE = re.compile(r"^[FGHJKMNQUVXZ]$")
# e.g. "DEC 26", "DEC26", "Dec 2026"
_MONTH_LABEL_RE = re.compile(
    r"^(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"[\s\-/]?(?P<year>\d{2}|\d{4})$",
    re.IGNORECASE,
)
_SLASH_DATE_RE = re.compile(r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})$")


@dataclass(frozen=True, slots=True)
class CmeCalendarRow:
    """One product-calendar row from CME public ProductCalendar."""

    contract_month: str
    contract_code: str
    last_trade_date: date | None
    first_notice_date: date | None
    settlement_date: date | None
    expiration_date: date | None


@dataclass(frozen=True, slots=True)
class CmeSettlementRow:
    """One settlement/volume/OI row from CME public Settlements endpoint."""

    contract_month: str
    contract_code: str
    settlement: Decimal | None
    session_volume: Decimal | None
    open_interest: Decimal | None
    settlement_status: SettlementStatus


@dataclass(frozen=True, slots=True)
class CmeSettlementDocument:
    trade_date: date
    published_at: datetime
    rows: tuple[CmeSettlementRow, ...]
    is_final: bool


@dataclass(frozen=True, slots=True)
class CmeDelayedQuoteRow:
    """One delayed quote row from CME public Quotes endpoint."""

    contract_month: str
    contract_code: str
    last: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    volume: Decimal | None
    open_interest: Decimal | None
    quote_at: datetime | None


def _contract(
    message: str,
    *,
    operation: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    details: dict[str, object] = {
        "vendor": "cme_public",
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


def loads_cme_json(body: bytes, *, operation: str) -> Any:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _contract(
            "CME response body is not valid UTF-8",
            operation=operation,
            rule="encoding",
        ) from exc
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
            "CME response body is not valid JSON",
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
        if not compact or compact in {"-", "--", "N/A", "n/a"}:
            raise _contract(
                f"{field} is blank",
                operation=operation,
                rule="blank_decimal",
                field=field,
            )
        # CME sometimes suffixes settlements with "A"/"B" for amended prints.
        compact = re.sub(r"[A-Za-z]+$", "", compact)
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
    if isinstance(value, str) and value.strip() in {"-", "--", "N/A", "n/a"}:
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


def _parse_slash_date(value: object, *, field: str, operation: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    match = _SLASH_DATE_RE.fullmatch(text)
    if match is None:
        # ISO fallback.
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise _contract(
                f"{field} is not a parseable date",
                operation=operation,
                rule="date_parse",
                field=field,
            ) from exc
    return date(
        int(match.group("y")),
        int(match.group("m")),
        int(match.group("d")),
    )


def _parse_month_label(
    label: object,
    *,
    month_code: object | None,
    year_hint: object | None,
    root: str,
    operation: str,
) -> tuple[str, str]:
    """Return (contract_month YYYY-MM, contract_code e.g. GCZ26)."""
    code: str | None = None
    year: int | None = None

    if isinstance(month_code, str) and _MONTH_CODE_RE.fullmatch(month_code.strip().upper()):
        code = month_code.strip().upper()
    if type(year_hint) is int and not isinstance(year_hint, bool):
        year = year_hint if year_hint > 100 else 2000 + year_hint
    elif isinstance(year_hint, str) and year_hint.strip().isdigit():
        raw = int(year_hint.strip())
        year = raw if raw > 100 else 2000 + raw

    if isinstance(label, str) and label.strip():
        text = label.strip().upper().replace(".", " ")
        match = _MONTH_LABEL_RE.fullmatch(text.replace("  ", " "))
        if match is not None:
            mon = match.group("mon").upper()
            year_raw = match.group("year")
            month_num = _MONTH_NAME_TO_NUM[mon]
            year_val = int(year_raw)
            if year_val < 100:
                year_val = 2000 + year_val
            # Prefer explicit month code when present; else map from name.
            if code is None:
                # Invert month number to CME code via identity helper.
                from domain.cross_asset.cme_identity import month_code_from_contract_month

                code = month_code_from_contract_month(f"{year_val:04d}-{month_num:02d}")
            year = year_val

    if code is None or year is None:
        raise _contract(
            "settlement/calendar row missing contract month identity",
            operation=operation,
            rule="contract_month_identity",
        )
    contract_month = contract_month_from_month_code(code, year)
    contract_code = contract_code_from_parts(root, contract_month)
    return contract_month, contract_code


def _ny_session_close(day: date) -> datetime:
    return datetime.combine(day, time(17, 0), tzinfo=_NY)


def decode_product_calendar(
    payload: object,
    *,
    root: str,
    operation: str = "product_calendar",
) -> tuple[CmeCalendarRow, ...]:
    """Decode CME ProductCalendar Future JSON into calendar rows."""
    if not isinstance(payload, dict):
        raise _contract(
            "product calendar payload must be an object",
            operation=operation,
            rule="payload_type",
        )
    # Live CmeWS shapes vary: monthGroups / calendarEntries / empty.
    groups = payload.get("monthGroups")
    if groups is None:
        groups = payload.get("calendarEntries")
    if groups is None:
        # Some responses nest under "results".
        results = payload.get("results")
        if isinstance(results, dict):
            groups = results.get("monthGroups") or results.get("calendarEntries")
        elif isinstance(results, list):
            groups = results
    if groups is None:
        # Empty object is a typed empty chain, not a codec error.
        if not payload:
            return ()
        raise _contract(
            "product calendar missing monthGroups/calendarEntries",
            operation=operation,
            rule="missing_groups",
        )
    if not isinstance(groups, list):
        raise _contract(
            "product calendar groups must be a list",
            operation=operation,
            rule="groups_type",
        )

    rows: list[CmeCalendarRow] = []
    for idx, item in enumerate(groups):
        if not isinstance(item, dict):
            raise _contract(
                "product calendar row must be an object",
                operation=operation,
                rule="row_type",
                index=idx,
            )
        # Skip option/spread rows when product type markers are present.
        product_type = item.get("productType") or item.get("type") or item.get("optionType")
        if isinstance(product_type, str) and product_type.strip().upper() in {
            "OOF",
            "OPTION",
            "SPREAD",
            "BUNDLE",
        }:
            continue
        label = (
            item.get("expirationMonth")
            or item.get("month")
            or item.get("contractMonth")
            or item.get("monthYear")
        )
        month_code = item.get("monthCode") or item.get("contractCode")
        # contractCode sometimes is full "GCZ26" — keep only single-letter codes.
        if (
            isinstance(month_code, str)
            and len(month_code.strip()) > 1
            and not _MONTH_CODE_RE.fullmatch(month_code.strip().upper())
        ):
            month_code = item.get("monthCode")
        year_hint = item.get("year") or item.get("contractYear")
        try:
            contract_month, contract_code = _parse_month_label(
                label,
                month_code=month_code if isinstance(month_code, str) else None,
                year_hint=year_hint,
                root=root,
                operation=operation,
            )
        except DataContractError:
            # Outright filter: skip unparseable non-futures rows rather than fail
            # the whole chain when markers are absent.
            continue

        last_trade = _parse_slash_date(
            item.get("lastTrade") or item.get("lastTradeDate") or item.get("ltd"),
            field="lastTrade",
            operation=operation,
        )
        first_notice = _parse_slash_date(
            item.get("firstNotice") or item.get("firstNoticeDate") or item.get("fnd"),
            field="firstNotice",
            operation=operation,
        )
        settlement = _parse_slash_date(
            item.get("settlement") or item.get("settlementDate") or item.get("finalSettlement"),
            field="settlementDate",
            operation=operation,
        )
        expiration = _parse_slash_date(
            item.get("expiration") or item.get("expirationDate") or item.get("lastDelivery"),
            field="expirationDate",
            operation=operation,
        )
        if expiration is None:
            expiration = last_trade
        rows.append(
            CmeCalendarRow(
                contract_month=contract_month,
                contract_code=contract_code,
                last_trade_date=last_trade,
                first_notice_date=first_notice,
                settlement_date=settlement,
                expiration_date=expiration,
            )
        )
    # Stable order by contract month then code.
    rows.sort(key=lambda r: (r.contract_month, r.contract_code))
    return tuple(rows)


def decode_settlements(
    payload: object,
    *,
    root: str,
    trade_date: date,
    operation: str = "settlements",
) -> CmeSettlementDocument:
    """Decode CME Settlements Future JSON into settlement/VOI rows."""
    if not isinstance(payload, dict):
        raise _contract(
            "settlements payload must be an object",
            operation=operation,
            rule="payload_type",
        )
    settlements = payload.get("settlements")
    if settlements is None and isinstance(payload.get("empty"), bool) and payload["empty"]:
        return CmeSettlementDocument(
            trade_date=trade_date,
            published_at=_ny_session_close(trade_date).astimezone(UTC),
            rows=(),
            is_final=False,
        )
    if not isinstance(settlements, list):
        raise _contract(
            "settlements list missing",
            operation=operation,
            rule="missing_settlements",
        )

    # updateTime / reportDate are best-effort publication timestamps.
    published_at = _ny_session_close(trade_date).astimezone(UTC)
    update_raw = payload.get("updateTime") or payload.get("reportDate")
    if isinstance(update_raw, str) and update_raw.strip():
        # Common form: "04:30:00 PM CT" — keep session-close when unparseable.
        pass
    is_final = False
    status_raw = payload.get("status") or payload.get("settlementStatus")
    if isinstance(status_raw, str) and "final" in status_raw.casefold():
        is_final = True

    rows: list[CmeSettlementRow] = []
    for idx, item in enumerate(settlements):
        if not isinstance(item, dict):
            raise _contract(
                "settlement row must be an object",
                operation=operation,
                rule="row_type",
                index=idx,
            )
        # Filter option/spread markers (strike alone is not decisive for futures).
        if item.get("optionType") is not None:
            continue
        label = item.get("month") or item.get("expirationMonth") or item.get("monthYear")
        month_code = item.get("monthCode")
        year_hint = item.get("year")
        try:
            contract_month, contract_code = _parse_month_label(
                label,
                month_code=month_code,
                year_hint=year_hint,
                root=root,
                operation=operation,
            )
        except DataContractError:
            continue

        settle = _optional_decimal(
            item.get("settle") or item.get("settlement") or item.get("settlePrice"),
            field="settle",
            operation=operation,
        )
        volume = _optional_decimal(
            item.get("volume") or item.get("totalVolume") or item.get("globexVolume"),
            field="volume",
            operation=operation,
        )
        open_interest = _optional_decimal(
            item.get("openInterest") or item.get("openInterestTotal") or item.get("oi"),
            field="openInterest",
            operation=operation,
        )
        row_status = SettlementStatus.FINAL if is_final else SettlementStatus.PRELIMINARY
        row_status_raw = item.get("settlementStatus")
        if isinstance(row_status_raw, str):
            lowered = row_status_raw.casefold()
            if "final" in lowered:
                row_status = SettlementStatus.FINAL
            elif "prelim" in lowered:
                row_status = SettlementStatus.PRELIMINARY
        rows.append(
            CmeSettlementRow(
                contract_month=contract_month,
                contract_code=contract_code,
                settlement=settle,
                session_volume=volume,
                open_interest=open_interest,
                settlement_status=row_status,
            )
        )
    rows.sort(key=lambda r: (r.contract_month, r.contract_code))
    return CmeSettlementDocument(
        trade_date=trade_date,
        published_at=published_at,
        rows=tuple(rows),
        is_final=is_final,
    )


def decode_delayed_quotes(
    payload: object,
    *,
    root: str,
    as_of: datetime,
    operation: str = "delayed_quotes",
) -> tuple[CmeDelayedQuoteRow, ...]:
    """Decode CME delayed Quotes Future JSON."""
    if not isinstance(payload, dict):
        raise _contract(
            "quotes payload must be an object",
            operation=operation,
            rule="payload_type",
        )
    quotes = payload.get("quotes") or payload.get("quote")
    if quotes is None:
        return ()
    if isinstance(quotes, dict):
        quotes = [quotes]
    if not isinstance(quotes, list):
        raise _contract(
            "quotes must be a list",
            operation=operation,
            rule="quotes_type",
        )

    rows: list[CmeDelayedQuoteRow] = []
    for idx, item in enumerate(quotes):
        if not isinstance(item, dict):
            raise _contract(
                "quote row must be an object",
                operation=operation,
                rule="row_type",
                index=idx,
            )
        if item.get("optionType") is not None:
            continue
        label = (
            item.get("expirationMonth")
            or item.get("month")
            or item.get("expirationDate")
            or item.get("contractCode")
        )
        month_code = item.get("monthCode")
        year_hint = item.get("year")
        try:
            contract_month, contract_code = _parse_month_label(
                label,
                month_code=month_code,
                year_hint=year_hint,
                root=root,
                operation=operation,
            )
        except DataContractError:
            continue
        last = _optional_decimal(
            item.get("last") or item.get("lastPrice") or item.get("priorSettle"),
            field="last",
            operation=operation,
        )
        bid = _optional_decimal(item.get("bid"), field="bid", operation=operation)
        ask = _optional_decimal(item.get("ask"), field="ask", operation=operation)
        volume = _optional_decimal(item.get("volume"), field="volume", operation=operation)
        open_interest = _optional_decimal(
            item.get("openInterest"), field="openInterest", operation=operation
        )
        quote_at: datetime | None = None
        updated = item.get("updated") or item.get("lastTradeDate") or item.get("quoteTime")
        if isinstance(updated, str) and updated.strip().isdigit():
            # Epoch millis or seconds — accept seconds-like values only.
            raw = int(updated.strip())
            if raw > 10_000_000_000:
                raw //= 1000
            quote_at = datetime.fromtimestamp(raw, tz=UTC)
        rows.append(
            CmeDelayedQuoteRow(
                contract_month=contract_month,
                contract_code=contract_code,
                last=last,
                bid=bid,
                ask=ask,
                volume=volume,
                open_interest=open_interest,
                quote_at=quote_at if quote_at is not None and quote_at <= as_of else None,
            )
        )
    rows.sort(key=lambda r: (r.contract_month, r.contract_code))
    return tuple(rows)
