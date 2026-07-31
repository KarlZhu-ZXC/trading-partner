"""Pure instrument symbol normalization (no I/O).

Phase 1D design §5.4. Returns normalization candidates; does not consult Master.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from domain.common.enums import AssetType, Market
from domain.common.errors import InvalidInstrument

# Full-width ASCII digits / letters → half-width (NFKC covers this).
_A_SHARE_PREFIX_EXCHANGE: dict[str, tuple[str, str]] = {
    # code-prefix → (exchange, canonical_suffix)
    "60": ("SSE", ".SH"),
    "68": ("SSE", ".SH"),
    "00": ("SZSE", ".SZ"),
    "30": ("SZSE", ".SZ"),
    "83": ("BSE", ".BJ"),
    "87": ("BSE", ".BJ"),
    "43": ("BSE", ".BJ"),
}

# Explicit suffix / prefix tokens → (exchange, canonical_suffix)
_A_SHARE_EXCHANGE_TOKENS: dict[str, tuple[str, str]] = {
    "SH": ("SSE", ".SH"),
    "SSE": ("SSE", ".SH"),
    "SZ": ("SZSE", ".SZ"),
    "SZSE": ("SZSE", ".SZ"),
    "BJ": ("BSE", ".BJ"),
    "BSE": ("BSE", ".BJ"),
}

_US_EXCHANGE_SUFFIXES = frozenset(
    {
        "NASDAQ",
        "NYSE",
        "ARCA",
        "CBOE",
        "AMEX",
        "BATS",
        "IEX",
        "OTC",
        "NYSEARCA",
    }
)

# Root (1–6 alnum/.) + YYMMDD + C/P + 8-digit strike*1000
_OCC_RE = re.compile(r"^([A-Z0-9.]{1,6})(\d{6})([CP])(\d{8})$")

_US_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]+$")
_YAHOO_CONTINUOUS_FUTURE_RE = re.compile(r"^[A-Z0-9]{1,8}=F$")
_KR_SECURITY_RE = re.compile(r"^(\d{6})(?:\.(KS|KQ))?$")
_KR_INDEXES = frozenset({"KS11", "KQ11", "KS200"})

# A-share option-style contract codes (exchange native), e.g. 10007601.SH
_A_SHARE_OPTION_CODE_RE = re.compile(r"^(\d{6,10})(\.(?:SH|SZ|BJ))?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class NormalizedSymbol:
    market: Market
    asset_type_hint: AssetType | None
    canonical_candidate: str
    local_code: str | None
    exchange_hint: str | None
    display_symbol: str
    warnings: tuple[str, ...]


def normalize_symbol_input(
    market: Market,
    raw: str,
    *,
    asset_type_hint: AssetType | None = None,
) -> NormalizedSymbol:
    """Return a normalization candidate; no I/O.

    Raises:
        InvalidInstrument: empty input or market-specific format failure.
    """
    if not isinstance(raw, str):
        raise InvalidInstrument(
            "symbol input must be a string",
            details={"raw_type": type(raw).__name__, "market": market.value},
        )
    # NFKC: full-width digits/letters → half-width; strip outer whitespace
    cleaned = unicodedata.normalize("NFKC", raw).strip()
    if not cleaned:
        raise InvalidInstrument(
            "symbol input must be non-empty",
            details={"raw": raw, "market": market.value},
        )

    if market is Market.A_SHARE:
        return _normalize_a_share(cleaned, raw=raw, asset_type_hint=asset_type_hint)
    if market is Market.US:
        return _normalize_us(cleaned, raw=raw, asset_type_hint=asset_type_hint)
    if market is Market.KR:
        return _normalize_kr(cleaned, raw=raw, asset_type_hint=asset_type_hint)
    raise InvalidInstrument(
        "unsupported market for symbol normalization",
        details={"market": market.value, "raw": raw},
    )


def _normalize_a_share(
    cleaned: str,
    *,
    raw: str,
    asset_type_hint: AssetType | None,
) -> NormalizedSymbol:
    warnings: list[str] = []
    # Collapse internal whitespace for compact forms like "600519 .SH"
    compact = re.sub(r"\s+", "", cleaned)
    upper = compact.upper()

    if asset_type_hint is AssetType.OPTION:
        return _normalize_a_share_option(upper, raw=raw, asset_type_hint=asset_type_hint)

    exchange_hint: str | None = None
    code: str | None = None
    suffix: str | None = None

    # Prefix form: SH600519 / SZ000001 / BJ830000 / SSE600519
    prefix_match = re.fullmatch(
        r"(SH|SSE|SZ|SZSE|BJ|BSE)(\d{6})",
        upper,
    )
    if prefix_match is not None:
        token, code = prefix_match.group(1), prefix_match.group(2)
        exchange_hint, suffix = _A_SHARE_EXCHANGE_TOKENS[token]
    else:
        # Suffix forms: 600519.SH / 600519.SSE / 600519SH / 600519SSE
        suffix_match = re.fullmatch(
            r"(\d{6})(?:\.?(SH|SSE|SZ|SZSE|BJ|BSE))?",
            upper,
        )
        if suffix_match is None:
            raise InvalidInstrument(
                "A-share symbol could not be normalized",
                details={"raw": raw, "market": Market.A_SHARE.value, "reason": "format"},
            )
        code = suffix_match.group(1)
        token = suffix_match.group(2)
        if token is not None:
            exchange_hint, suffix = _A_SHARE_EXCHANGE_TOKENS[token]

    assert code is not None
    local_code = code

    if suffix is None:
        inferred = _A_SHARE_PREFIX_EXCHANGE.get(code[:2])
        if inferred is None:
            # Do not invent exchange; leave bare local code for alias / ambiguity path.
            warnings.append("a_share_exchange_unresolved")
            return NormalizedSymbol(
                market=Market.A_SHARE,
                asset_type_hint=asset_type_hint,
                canonical_candidate=local_code,
                local_code=local_code,
                exchange_hint=None,
                display_symbol=local_code,
                warnings=tuple(warnings),
            )
        exchange_hint, suffix = inferred

    canonical = f"{local_code}{suffix}"
    return NormalizedSymbol(
        market=Market.A_SHARE,
        asset_type_hint=asset_type_hint,
        canonical_candidate=canonical,
        local_code=local_code,
        exchange_hint=exchange_hint,
        display_symbol=canonical,
        warnings=tuple(warnings),
    )


def _normalize_a_share_option(
    upper: str,
    *,
    raw: str,
    asset_type_hint: AssetType | None,
) -> NormalizedSymbol:
    """A-share option: exchange contract code, e.g. 10007601.SH."""
    match = _A_SHARE_OPTION_CODE_RE.fullmatch(upper)
    if match is None:
        raise InvalidInstrument(
            "A-share option symbol could not be normalized",
            details={
                "raw": raw,
                "market": Market.A_SHARE.value,
                "reason": "option_format",
            },
        )
    code = match.group(1)
    token_part = match.group(2)
    if token_part is not None:
        token = token_part.lstrip(".").upper()
        exchange_hint, suffix = _A_SHARE_EXCHANGE_TOKENS[token]
        canonical = f"{code}{suffix}"
    else:
        # Bare contract number: keep as candidate; exchange from Master later.
        exchange_hint = None
        canonical = code
    return NormalizedSymbol(
        market=Market.A_SHARE,
        asset_type_hint=asset_type_hint,
        canonical_candidate=canonical,
        local_code=code,
        exchange_hint=exchange_hint,
        display_symbol=canonical,
        warnings=(),
    )


def _normalize_us(
    cleaned: str,
    *,
    raw: str,
    asset_type_hint: AssetType | None,
) -> NormalizedSymbol:
    upper = cleaned.upper()
    if upper.startswith("$"):
        upper = upper[1:]
    if not upper:
        raise InvalidInstrument(
            "US symbol must be non-empty after stripping '$'",
            details={"raw": raw, "market": Market.US.value},
        )

    warnings: list[str] = []

    if asset_type_hint is AssetType.FUTURE:
        compact = upper.replace(" ", "")
        if not _YAHOO_CONTINUOUS_FUTURE_RE.fullmatch(compact):
            raise InvalidInstrument(
                "US continuous future symbol must use Yahoo ROOT=F form",
                details={
                    "raw": raw,
                    "market": Market.US.value,
                    "reason": "future_format",
                },
            )
        return NormalizedSymbol(
            market=Market.US,
            asset_type_hint=AssetType.FUTURE,
            canonical_candidate=compact,
            local_code=compact.removesuffix("=F"),
            exchange_hint="COMEX",
            display_symbol=compact,
            warnings=("continuous_future_roll_risk",),
        )

    # Index caret form: keep underlying ticker as candidate; Master owns canonical (e.g. SPX).
    if upper.startswith("^"):
        if any(ch.isspace() for ch in upper):
            raise InvalidInstrument(
                "US symbol must not contain embedded whitespace",
                details={
                    "raw": raw,
                    "market": Market.US.value,
                    "reason": "embedded_whitespace",
                },
            )
        body = upper[1:]
        if not body or not _US_SYMBOL_RE.fullmatch(body):
            raise InvalidInstrument(
                "US index symbol is invalid",
                details={"raw": raw, "market": Market.US.value, "reason": "index_format"},
            )
        warnings.append("us_index_caret_stripped;canonical_from_master")
        return NormalizedSymbol(
            market=Market.US,
            asset_type_hint=asset_type_hint or AssetType.INDEX,
            canonical_candidate=body,
            local_code=body,
            exchange_hint="INDEX",
            display_symbol=f"^{body}",
            warnings=tuple(warnings),
        )

    # OCC option path (design §5.4.5): remove spaces/separators before validating.
    # Hinted OPTION always takes this path; unhinted input may auto-detect OCC shape.
    occ_candidate = upper.replace("-", "").replace("/", "").replace(" ", "")
    if asset_type_hint is AssetType.OPTION or _looks_like_occ(occ_candidate):
        return _normalize_us_occ(occ_candidate, raw=raw, asset_type_hint=asset_type_hint)

    # Non-option US equity/ETF/index symbols reject embedded whitespace (e.g. "BRK B").
    if any(ch.isspace() for ch in cleaned):
        raise InvalidInstrument(
            "US symbol must not contain embedded whitespace",
            details={"raw": raw, "market": Market.US.value, "reason": "embedded_whitespace"},
        )

    # Class share separators: BRK/B, BRK-B → BRK.B (frozen canonical uses '.')
    normalized = upper.replace("/", ".")
    hyphen_class = re.fullmatch(r"([A-Z0-9.]+)-([A-Z])", normalized)
    if hyphen_class is not None:
        normalized = f"{hyphen_class.group(1)}.{hyphen_class.group(2)}"

    exchange_hint: str | None = None
    # Strip mistaken exchange suffix: NVDA.NASDAQ → NVDA
    if "." in normalized:
        head, tail = normalized.rsplit(".", 1)
        if tail in _US_EXCHANGE_SUFFIXES and head:
            exchange_hint = tail
            normalized = head

    if not _US_SYMBOL_RE.fullmatch(normalized):
        raise InvalidInstrument(
            "US symbol contains invalid characters",
            details={
                "raw": raw,
                "market": Market.US.value,
                "reason": "charset",
                "normalized": normalized,
            },
        )

    return NormalizedSymbol(
        market=Market.US,
        asset_type_hint=asset_type_hint,
        canonical_candidate=normalized,
        local_code=normalized,
        exchange_hint=exchange_hint,
        display_symbol=normalized,
        warnings=tuple(warnings),
    )


def _normalize_kr(
    cleaned: str,
    *,
    raw: str,
    asset_type_hint: AssetType | None,
) -> NormalizedSymbol:
    compact = re.sub(r"\s+", "", cleaned).upper()
    if compact.startswith("^"):
        compact = compact[1:]
    if compact in _KR_INDEXES:
        if asset_type_hint not in {None, AssetType.INDEX}:
            raise InvalidInstrument(
                "Korean index symbol conflicts with asset type hint",
                details={"raw": raw, "market": Market.KR.value, "reason": "asset_type"},
            )
        exchange = "KOSDAQ" if compact == "KQ11" else "KOSPI"
        return NormalizedSymbol(
            market=Market.KR,
            asset_type_hint=AssetType.INDEX,
            canonical_candidate=compact,
            local_code=compact,
            exchange_hint=exchange,
            display_symbol=f"^{compact}",
            warnings=(),
        )

    match = _KR_SECURITY_RE.fullmatch(compact)
    if match is None:
        raise InvalidInstrument(
            "Korean security symbol must be a six-digit code or supported index",
            details={"raw": raw, "market": Market.KR.value, "reason": "format"},
        )
    code, suffix = match.groups()
    security_exchange = {"KS": "KOSPI", "KQ": "KOSDAQ"}.get(suffix)
    return NormalizedSymbol(
        market=Market.KR,
        asset_type_hint=asset_type_hint,
        canonical_candidate=code,
        local_code=code,
        exchange_hint=security_exchange,
        display_symbol=f"{code}.{suffix}" if suffix is not None else code,
        warnings=() if suffix is not None else ("kr_exchange_resolved_by_directory",),
    )


def _looks_like_occ(s: str) -> bool:
    return _OCC_RE.fullmatch(s) is not None


def _normalize_us_occ(
    occ: str,
    *,
    raw: str,
    asset_type_hint: AssetType | None,
) -> NormalizedSymbol:
    match = _OCC_RE.fullmatch(occ)
    if match is None:
        raise InvalidInstrument(
            "US option OCC symbol is invalid",
            details={
                "raw": raw,
                "market": Market.US.value,
                "reason": "occ_format",
            },
        )
    root, yymmdd, _cp, _strike = match.groups()
    try:
        yy = int(yymmdd[0:2])
        mm = int(yymmdd[2:4])
        dd = int(yymmdd[4:6])
        # OCC uses 2-digit year; interpret as 2000–2099 for format validation.
        date(2000 + yy, mm, dd)
    except ValueError as exc:
        raise InvalidInstrument(
            "US option OCC expiry date segment is invalid",
            details={
                "raw": raw,
                "market": Market.US.value,
                "reason": "occ_format",
                "yymmdd": yymmdd,
            },
        ) from exc

    return NormalizedSymbol(
        market=Market.US,
        asset_type_hint=asset_type_hint or AssetType.OPTION,
        canonical_candidate=occ,
        local_code=root,
        exchange_hint=None,
        display_symbol=occ,
        warnings=(),
    )
