"""Shared SEC EDGAR infrastructure helpers (Phase 1G G2).

Public, infrastructure-only utilities for strict JSON, CIK padding, content-type
checks, and conservative filed-date visibility. Adapters may import these; they
must not be imported from domain/application.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from domain.common.enums import VendorId
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

JSON_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "application/json",
    "text/json",
    "text/plain",
    "*/*",
)

TICKERS_URL: Final[str] = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_PREFIX: Final[str] = "https://data.sec.gov/api/xbrl/companyfacts/"
SUBMISSIONS_PREFIX: Final[str] = "https://data.sec.gov/submissions/"
ARCHIVES_PREFIX: Final[str] = "https://www.sec.gov/Archives/edgar/data/"


def sec_contract(
    message: str,
    *,
    operation: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    details: dict[str, object] = {
        "vendor": VendorId.SEC_EDGAR.value,
        "operation": operation,
        "rule": rule,
    }
    details.update(extra)
    return DataContractError(message, details=details)


def reject_duplicate_object_pairs(
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


def reject_nonfinite_constant(name: str) -> None:
    raise DataContractError(
        "JSON non-finite constant is not allowed",
        details={"field": "json", "rule": "no_nan_infinity", "constant": name},
    )


def loads_sec_json_strict(body: bytes) -> Any:
    """Parse SEC JSON as UTF-8 with Decimal floats and no duplicate keys."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise DataContractError(
            "response body is not valid UTF-8",
            details={"field": "body", "rule": "encoding"},
        ) from None
    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_nonfinite_constant,
            object_pairs_hook=reject_duplicate_object_pairs,
        )
    except DataContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise DataContractError(
            "response body is not valid JSON",
            details={"field": "body", "rule": "json"},
        ) from None


def content_type_ok(headers: Mapping[str, str], allowed: Sequence[str]) -> bool:
    raw = headers.get("content-type") or headers.get("Content-Type")
    if not isinstance(raw, str) or not raw.strip():
        return False
    lowered = raw.split(";", 1)[0].strip().casefold()
    return any(token in lowered for token in allowed)


def pad_cik(value: object) -> str | None:
    """Normalize a CIK to a zero-padded 10-digit string, or None if invalid."""
    if type(value) is int and not isinstance(value, bool) and value >= 0:
        raw = str(value)
    elif isinstance(value, str) and value.strip().isdigit():
        raw = value.strip()
    else:
        return None
    if len(raw) > 10:
        return None
    return raw.zfill(10)


def filed_visibility_utc(filed_date: date) -> datetime:
    """Conservative visibility when SEC provides only a filed calendar date.

    Company Facts lacks acceptance timestamps; treat facts as first visible at
    the next UTC midnight after the filed date.
    """
    return datetime(
        filed_date.year, filed_date.month, filed_date.day, tzinfo=UTC
    ) + timedelta(days=1)


def raise_for_sec_http_status(status_code: int, *, operation: str) -> None:
    if status_code == 429:
        raise ProviderRateLimitError(
            "SEC EDGAR rate limited",
            details={
                "vendor": VendorId.SEC_EDGAR.value,
                "operation": operation,
                "error_type": "rate_limit",
                "status_class": "4xx",
            },
        )
    if status_code in {401, 403}:
        raise ProviderUnavailableError(
            "SEC EDGAR access blocked",
            details={
                "vendor": VendorId.SEC_EDGAR.value,
                "operation": operation,
                "error_type": "blocked",
                "status_class": "4xx",
            },
        )
    if status_code == 404:
        raise NoMarketData(
            "SEC EDGAR resource not found",
            details={
                "vendor": VendorId.SEC_EDGAR.value,
                "operation": operation,
                "error_type": "not_found",
                "status_class": "4xx",
            },
        )
    if status_code < 200 or status_code >= 300:
        raise ProviderUnavailableError(
            "SEC EDGAR HTTP failure",
            details={
                "vendor": VendorId.SEC_EDGAR.value,
                "operation": operation,
                "error_type": "http_status",
                "status_class": f"{status_code // 100}xx",
            },
        )
