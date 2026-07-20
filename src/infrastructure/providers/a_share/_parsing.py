"""Shared A-share adapter parsing helpers (infrastructure only).

Decimal conversion never routes through binary float. JSON numbers use
``parse_float=Decimal`` so digits come from the JSON text. Duplicate object
keys are rejected at every nesting level. NaN/Infinity JSON constants are
rejected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from domain.common.enums import AssetType, Market, TradingSession
from domain.common.errors import DataContractError, NoMarketData
from domain.common.values import build_instrument_id, parse_instrument_id
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic

SHANGHAI = ZoneInfo("Asia/Shanghai")

_BLANK_NUMERIC = frozenset({"", "-", "--", "None", "null", "NULL", "nan", "NaN"})

# A-share public quote volume unit: 1 lot (手) = 100 shares for EQUITY/ETF.
_LOTS_TO_SHARES: int = 100
_YI_TO_CNY: Decimal = Decimal("100000000")  # 1 亿 = 1e8


def require_a_share_instrument(instrument: Instrument) -> tuple[str, str]:
    """Return ``(code6, exchange_suffix)`` for an A-share instrument.

    ``code6`` is the numeric code (e.g. ``600519``); suffix is ``SH``/``SZ``/``BJ``.
    """
    if not isinstance(instrument, Instrument):
        raise DataContractError(
            "instrument must be Instrument",
            details={"field": "instrument", "rule": "type"},
        )
    if instrument.market is not Market.A_SHARE:
        raise DataContractError(
            "instrument market must be A_SHARE",
            details={"field": "instrument", "rule": "market"},
        )
    _asset, market, symbol = parse_instrument_id(instrument.instrument_id)
    if market is not Market.A_SHARE:
        raise DataContractError(
            "instrument_id market must be A_SHARE",
            details={"field": "instrument_id", "rule": "market"},
        )
    symbol_u = symbol.upper()
    if "." in symbol_u:
        code, suffix = symbol_u.rsplit(".", 1)
    else:
        code, suffix = symbol_u, ""
    if not code.isdigit() or len(code) > 6:
        raise DataContractError(
            "A-share symbol code must be numeric",
            details={"field": "symbol", "rule": "code"},
        )
    code6 = code.zfill(6)
    if suffix not in {"SH", "SZ", "BJ"}:
        exchange = instrument.exchange.upper()
        if exchange in {"SSE", "XSHG"}:
            suffix = "SH"
        elif exchange in {"SZSE", "XSHE"}:
            suffix = "SZ"
        elif exchange in {"BSE", "XBEI"}:
            suffix = "BJ"
        else:
            raise DataContractError(
                "unable to map A-share exchange suffix",
                details={"field": "symbol", "rule": "exchange_suffix"},
            )
    return code6, suffix


def tencent_symbol(code6: str, suffix: str) -> str:
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[suffix]
    return f"{prefix}{code6}"


def eastmoney_secid(code6: str, suffix: str) -> str:
    # 1 = SSE, 0 = SZSE/BSE (Eastmoney convention for A-share boards).
    market_no = "1" if suffix == "SH" else "0"
    return f"{market_no}.{code6}"


def instrument_id_from_code(
    code6: str, suffix: str, *, asset: AssetType = AssetType.EQUITY
) -> str:
    return build_instrument_id(asset, Market.A_SHARE, f"{code6}.{suffix}")


def lots_to_shares(
    lots: int,
    *,
    field: str,
    asset_type: AssetType,
) -> int | None:
    """Convert provider volume lots (手) to domain volume_shares.

    Frozen unit matrix (E2):
    - EQUITY / ETF: lots * 100 with checked nonnegative integer arithmetic
    - INDEX: volume is not share-lot semantics — return None (do not invent shares)
    - other asset types: fail closed
    """
    if not isinstance(lots, int) or isinstance(lots, bool):
        raise DataContractError(
            f"{field} lots must be int",
            details={"field": field, "rule": "lots_type"},
        )
    if lots < 0:
        raise DataContractError(
            f"{field} lots must be nonnegative",
            details={"field": field, "rule": "lots_nonnegative"},
        )
    if asset_type in {AssetType.EQUITY, AssetType.ETF}:
        try:
            return lots * _LOTS_TO_SHARES
        except OverflowError as exc:
            raise DataContractError(
                f"{field} lots overflow when converting to shares",
                details={"field": field, "rule": "lots_overflow"},
            ) from exc
    if asset_type is AssetType.INDEX:
        return None
    raise DataContractError(
        f"{field} volume unit conversion unsupported for asset type",
        details={
            "field": field,
            "rule": "volume_unit_unsupported",
            "asset_type": asset_type.value,
        },
    )


def yi_to_cny(value: Decimal, *, field: str) -> Decimal:
    """Convert market-cap unit of 100 million CNY (亿) to CNY."""
    if type(value) is not Decimal or not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal in 亿 units",
            details={"field": field, "rule": "yi_unit"},
        )
    return value * _YI_TO_CNY


def decimal_from_text(raw: object, *, field: str) -> Decimal | None:
    """Parse a vendor numeric field to Decimal without float intermediates."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise DataContractError(
            f"{field} must not be bool",
            details={"field": field, "rule": "no_bool"},
        )
    if isinstance(raw, float):
        raise DataContractError(
            f"{field} must not be float",
            details={"field": field, "rule": "no_float"},
        )
    if type(raw) is Decimal:
        if not raw.is_finite():
            raise DataContractError(
                f"{field} must be finite",
                details={"field": field, "rule": "finite"},
            )
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text in _BLANK_NUMERIC:
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        text = text.replace(",", "")
        if text in _BLANK_NUMERIC:
            return None
        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError):
            raise DataContractError(
                f"{field} is not a valid decimal",
                details={"field": field, "rule": "decimal_parse"},
            ) from None
        if not value.is_finite():
            raise DataContractError(
                f"{field} must be finite",
                details={"field": field, "rule": "finite"},
            )
        return value
    raise DataContractError(
        f"{field} has unsupported numeric type",
        details={"field": field, "rule": "type", "type": type(raw).__name__},
    )


def require_decimal(raw: object, *, field: str) -> Decimal:
    value = decimal_from_text(raw, field=field)
    if value is None:
        raise DataContractError(
            f"{field} is required",
            details={"field": field, "rule": "required"},
        )
    return value


def int_from_text(raw: object, *, field: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise DataContractError(
            f"{field} must not be bool",
            details={"field": field, "rule": "no_bool"},
        )
    if isinstance(raw, float):
        raise DataContractError(
            f"{field} must not be float",
            details={"field": field, "rule": "no_float"},
        )
    if isinstance(raw, int):
        return raw
    if type(raw) is Decimal:
        if not raw.is_finite() or raw != raw.to_integral_value():
            raise DataContractError(
                f"{field} must be an integral Decimal",
                details={"field": field, "rule": "integral"},
            )
        return int(raw)
    if isinstance(raw, str):
        text = raw.strip().replace(",", "")
        if text in _BLANK_NUMERIC:
            return None
        if "." in text:
            dec = decimal_from_text(text, field=field)
            if dec is None:
                return None
            if dec != dec.to_integral_value():
                raise DataContractError(
                    f"{field} must be integral",
                    details={"field": field, "rule": "integral"},
                )
            return int(dec)
        try:
            return int(text)
        except ValueError:
            raise DataContractError(
                f"{field} is not a valid int",
                details={"field": field, "rule": "int_parse"},
            ) from None
    raise DataContractError(
        f"{field} has unsupported int type",
        details={"field": field, "rule": "type", "type": type(raw).__name__},
    )


def require_int(raw: object, *, field: str) -> int:
    value = int_from_text(raw, field=field)
    if value is None:
        raise DataContractError(
            f"{field} is required",
            details={"field": field, "rule": "required"},
        )
    return value


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """object_pairs_hook: reject duplicate keys at this object nesting level."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DataContractError(
                "JSON object contains duplicate keys",
                details={"field": "json", "rule": "duplicate_key", "key": key},
            )
        out[key] = value
    return out


def _reject_nonfinite_constant(name: str) -> None:
    raise DataContractError(
        "JSON non-finite constant is not allowed",
        details={"field": "json", "rule": "no_nan_infinity", "constant": name},
    )


def loads_json_decimal(body: bytes, *, encoding: str = "utf-8") -> Any:
    """Parse JSON with Decimal floats, no NaN/Infinity, no duplicate keys."""
    try:
        text = body.decode(encoding)
    except UnicodeDecodeError:
        raise DataContractError(
            "response body is not valid text",
            details={"field": "body", "rule": "encoding"},
        ) from None
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
    except (TypeError, ValueError, json.JSONDecodeError):
        raise DataContractError(
            "response body is not valid JSON",
            details={"field": "body", "rule": "json"},
        ) from None


# Declared Content-Type charset → Python codec name (fail closed).
_DECLARED_CHARSET_CODECS: Mapping[str, str] = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "gbk": "gbk",
    "gb2312": "gbk",
    "gb18030": "gb18030",
}


def declared_text_encoding(headers: object) -> str:
    """Return a safe Python codec from Content-Type ``charset=`` (required).

    Accepts only the closed charset set used by A-share vendors (UTF-8 / GBK
    family). Missing, empty, or unknown charset is contract drift — never guess
    and never fall back to a silent default.
    """
    if not isinstance(headers, dict) and not hasattr(headers, "get"):
        raise DataContractError(
            "response headers missing Content-Type charset",
            details={"field": "headers", "rule": "encoding"},
        )
    try:
        getter = getattr(headers, "get", None)
        if getter is None:
            raise DataContractError(
                "response headers missing Content-Type charset",
                details={"field": "headers", "rule": "encoding"},
            )
        raw = getter("content-type") or getter("Content-Type")
    except DataContractError:
        raise
    except Exception:
        raise DataContractError(
            "response headers missing Content-Type charset",
            details={"field": "headers", "rule": "encoding"},
        ) from None
    if not isinstance(raw, str) or not raw.strip():
        raise DataContractError(
            "response headers missing Content-Type charset",
            details={"field": "content-type", "rule": "encoding"},
        )
    charset: str | None = None
    parts = raw.split(";")
    for part in parts[1:]:
        token = part.strip()
        if not token:
            continue
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key.strip().casefold() != "charset":
            continue
        charset = value.strip().strip('"').strip("'").casefold()
        break
    if not charset:
        raise DataContractError(
            "response Content-Type is missing charset",
            details={"field": "content-type", "rule": "encoding"},
        )
    codec = _DECLARED_CHARSET_CODECS.get(charset)
    if codec is None:
        raise DataContractError(
            "response Content-Type charset is not supported",
            details={"field": "content-type", "rule": "encoding"},
        )
    return codec


def loads_json_decimal_declared(body: bytes, headers: object) -> Any:
    """Decode body with declared charset, then Decimal-safe JSON parse."""
    encoding = declared_text_encoding(headers)
    return loads_json_decimal(body, encoding=encoding)


def decode_text(body: bytes, *, encodings: tuple[str, ...] = ("utf-8", "gbk")) -> str:
    for enc in encodings:
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DataContractError(
        "response body could not be decoded",
        details={"field": "body", "rule": "encoding"},
    )


def combine_shanghai_date_time(day: date, hhmmss: str) -> datetime:
    """Combine local date + HH:MM:SS (or HHMMSS) into Asia/Shanghai aware datetime."""
    text = hhmmss.strip()
    if re.fullmatch(r"\d{6}", text):
        hour = int(text[0:2])
        minute = int(text[2:4])
        second = int(text[4:6])
    elif re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", text):
        parts = text.split(":")
        hour, minute, second = int(parts[0]), int(parts[1]), int(parts[2])
    elif re.fullmatch(r"\d{1,2}:\d{2}", text):
        parts = text.split(":")
        hour, minute, second = int(parts[0]), int(parts[1]), 0
    else:
        raise DataContractError(
            "time text is not a recognized HH:MM:SS form",
            details={"field": "time", "rule": "time_format"},
        )
    return datetime(
        day.year, day.month, day.day, hour, minute, second, tzinfo=SHANGHAI
    )


def parse_shanghai_date(text: str) -> date:
    cleaned = text.strip().replace("/", "-")
    # Eastmoney datacenter often emits ``YYYY-MM-DD 00:00:00``; take the date part.
    if " " in cleaned:
        cleaned = cleaned.split(" ", 1)[0].strip()
    if "T" in cleaned:
        cleaned = cleaned.split("T", 1)[0].strip()
    if re.fullmatch(r"\d{8}", cleaned):
        return date(int(cleaned[0:4]), int(cleaned[4:6]), int(cleaned[6:8]))
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        raise DataContractError(
            "date text is not a valid ISO date",
            details={"field": "date", "rule": "date_parse"},
        ) from None


def parse_tencent_timestamp(text: str) -> datetime:
    """Parse Tencent field-30 ``YYYYMMDDHHMMSS`` into Asia/Shanghai datetime.

    Missing/malformed timestamps are contract drift — never substitute as_of.
    """
    cleaned = text.strip()
    if not re.fullmatch(r"\d{14}", cleaned):
        raise DataContractError(
            "Tencent quote timestamp must be YYYYMMDDHHMMSS",
            details={"field": "quote_at", "rule": "contract_drift"},
        )
    year = int(cleaned[0:4])
    month = int(cleaned[4:6])
    day = int(cleaned[6:8])
    hour = int(cleaned[8:10])
    minute = int(cleaned[10:12])
    second = int(cleaned[12:14])
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=SHANGHAI)
    except ValueError:
        raise DataContractError(
            "Tencent quote timestamp is not a valid datetime",
            details={"field": "quote_at", "rule": "contract_drift"},
        ) from None


def parse_tencent_composite_turnover(text: str, *, field: str) -> Decimal | None:
    """Parse field-35 composite ``last/volume_lots/exact_turnover_cny``."""
    cleaned = text.strip()
    if not cleaned or cleaned in _BLANK_NUMERIC:
        return None
    parts = cleaned.split("/")
    if len(parts) < 3:
        raise DataContractError(
            f"{field} composite must be last/volume/turnover",
            details={"field": field, "rule": "composite_shape"},
        )
    return require_decimal(parts[2], field=field)


def require_exact_date(value: object, *, field: str) -> date:
    """Require a real ``date`` (not datetime subclass)."""
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date (not datetime)",
            details={"field": field, "rule": "exact_date_type"},
        )
    return value


def session_for(as_of: datetime) -> TradingSession:
    return infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")


def raise_no_data(*, vendor: str, operation: str) -> None:
    raise NoMarketData(
        "provider returned no market data",
        details={"vendor": vendor, "operation": operation},
    )


def status_is_rate_limit(status_code: int) -> bool:
    return status_code == 429


def status_is_blocked(status_code: int) -> bool:
    return status_code in {401, 403}


def content_type_matches(headers: object, *, allowed_substrings: tuple[str, ...]) -> bool:
    """Return True if Content-Type contains any allowed substring (casefold)."""
    if not isinstance(headers, dict) and not hasattr(headers, "get"):
        return False
    raw = None
    try:
        getter = getattr(headers, "get", None)
        if getter is None:
            return False
        raw = getter("content-type") or getter("Content-Type")
    except Exception:
        return False
    if not isinstance(raw, str) or not raw.strip():
        return False
    lower = raw.casefold()
    return any(token.casefold() in lower for token in allowed_substrings)


def first_day_of_month(day: date) -> date:
    return date(day.year, day.month, 1)


def week_period_start(end_day: date) -> date:
    """Monday of the ISO week containing ``end_day`` (calendar date, not trading)."""
    return end_day - timedelta(days=end_day.weekday())


def parse_shanghai_datetime(text: object, *, field: str) -> datetime | None:
    """Parse common vendor datetime strings into Asia/Shanghai aware datetimes.

    Accepted:
    - ISO-8601 (with or without timezone; naive treated as Shanghai)
    - ``YYYY-MM-DD HH:MM:SS`` / ``YYYY/MM/DD HH:MM:SS``
    - ``YYYY-MM-DD`` / ``YYYYMMDD`` (midnight Shanghai)
    - Unix epoch seconds (int/str/Decimal integral)
    Blank / ``-`` → None.
    """
    if text is None:
        return None
    if isinstance(text, datetime):
        if text.tzinfo is None or text.utcoffset() is None:
            return text.replace(tzinfo=SHANGHAI)
        return text
    if isinstance(text, bool):
        raise DataContractError(
            f"{field} must not be bool",
            details={"field": field, "rule": "no_bool"},
        )
    if isinstance(text, float):
        raise DataContractError(
            f"{field} must not be float",
            details={"field": field, "rule": "no_float"},
        )
    if isinstance(text, int) or type(text) is Decimal:
        if type(text) is Decimal:
            if not text.is_finite() or text != text.to_integral_value():
                raise DataContractError(
                    f"{field} epoch must be integral",
                    details={"field": field, "rule": "epoch_integral"},
                )
            epoch = int(text)
        else:
            epoch = int(text)
        if epoch <= 0:
            return None
        # Milliseconds vs seconds heuristic for A-share vendor epochs.
        if epoch > 10_000_000_000:
            epoch = epoch // 1000
        return datetime.fromtimestamp(epoch, tz=SHANGHAI)
    if not isinstance(text, str):
        raise DataContractError(
            f"{field} has unsupported datetime type",
            details={"field": field, "rule": "type", "type": type(text).__name__},
        )
    cleaned = text.strip()
    if cleaned in _BLANK_NUMERIC:
        return None
    if re.fullmatch(r"\d{10,13}", cleaned):
        return parse_shanghai_datetime(int(cleaned), field=field)
    if re.fullmatch(r"\d{8}", cleaned):
        day = parse_shanghai_date(cleaned)
        return datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=SHANGHAI)
    normalized = cleaned.replace("/", "-")
    # "YYYY-MM-DD HH:MM:SS" → ISO-ish
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", normalized):
        normalized = normalized.replace(" ", "T")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            day = date.fromisoformat(normalized)
            return datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=SHANGHAI)
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise DataContractError(
            f"{field} is not a recognized datetime",
            details={"field": field, "rule": "datetime_parse"},
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=SHANGHAI)
    return parsed


def sanitize_public_url(raw: object, *, field: str) -> str | None:
    """Validate a provider URL: http/https only, no credentials/fragments/secrets.

    Rejects non-http(s) schemes, userinfo, fragments, and query keys that look
    like secrets. Returns None for blank input.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DataContractError(
            f"{field} must be a string URL",
            details={"field": field, "rule": "url_type"},
        )
    text = raw.strip()
    if not text or text in _BLANK_NUMERIC:
        return None
    if len(text) > 2000:
        raise DataContractError(
            f"{field} exceeds max URL length",
            details={"field": field, "rule": "url_length"},
        )
    from urllib.parse import parse_qsl, urlsplit

    try:
        parts = urlsplit(text)
    except ValueError:
        raise DataContractError(
            f"{field} is not a valid URL",
            details={"field": field, "rule": "url_parse"},
        ) from None
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise DataContractError(
            f"{field} scheme must be http or https",
            details={"field": field, "rule": "url_scheme"},
        )
    if parts.username is not None or parts.password is not None:
        raise DataContractError(
            f"{field} must not embed credentials",
            details={"field": field, "rule": "url_credentials"},
        )
    if parts.fragment:
        raise DataContractError(
            f"{field} must not contain a fragment",
            details={"field": field, "rule": "url_fragment"},
        )
    host = (parts.hostname or "").casefold()
    if not host:
        raise DataContractError(
            f"{field} must include a host",
            details={"field": field, "rule": "url_host"},
        )
    secret_keys = {
        "token",
        "access_token",
        "apikey",
        "api_key",
        "key",
        "secret",
        "password",
        "auth",
        "authorization",
        "cookie",
        "session",
        "sign",
        "signature",
    }
    for qk, _qv in parse_qsl(parts.query, keep_blank_values=True):
        if qk.casefold() in secret_keys:
            raise DataContractError(
                f"{field} query must not contain secrets",
                details={"field": field, "rule": "url_secret_query"},
            )
    return text


def require_nonnegative_exact_int(value: object, *, field: str) -> int:
    """Validate nonnegative exact ``int`` (reject bool subclass)."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataContractError(
            f"{field} must be a nonnegative exact int",
            details={"field": field, "rule": "nonnegative"},
        )
    return value


def is_effective_current_as_of(
    as_of: datetime, now: datetime, *, window_seconds: int = 300
) -> bool:
    """Whether ``as_of`` is within the current-query window of ``now``."""
    if as_of > now:
        return False
    return (now - as_of).total_seconds() <= window_seconds


def publication_cutoff_keep(
    published_at: datetime | None,
    *,
    as_of: datetime,
    now: datetime,
    current_window_seconds: int = 300,
) -> tuple[bool, bool]:
    """Decide whether a published record is kept under §18.2 cutoff rules.

    Returns ``(keep, unknown_excluded)``.
    - ``published_at is None``: keep only for effective current query; historical
      queries exclude with ``unknown_excluded=True``.
    - ``published_at > as_of``: exclude (future leak).
    - else keep.
    Never treats unknown publication as ``fetched_at``.
    """
    if published_at is None:
        if is_effective_current_as_of(
            as_of, now, window_seconds=current_window_seconds
        ):
            return True, False
        return False, True
    if published_at > as_of:
        return False, False
    return True, False
