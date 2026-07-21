"""Provider cache key construction and validation (pure domain).

Fingerprint and full key validation reject free-text, query-like, and
secret-shaped material. Rejected key / fingerprint values must never appear
in error messages or details.

Depends only on domain.common enums / errors / time / values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

from domain.common.enums import DataCategory, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id

# Design v1.12: fingerprint is exactly operation_name|16 lowercase hex.
_OPERATION_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_FINGERPRINT_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}\|[0-9a-f]{16}$"
)

# Cache key segment safety: public identities use asset/market/symbol chars only.
# Rejects pipe/query/whitespace pollution of the v1|{...}|{instrument_id}|... key.
_CACHE_INSTRUMENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:=\-]+$")

# v1 key is exactly 7 pipe-separated segments once fingerprint's single pipe
# is included: v1|market|category|instrument|as_of|operation|hash16
_CACHE_KEY_SEGMENT_COUNT = 7

# Safe segment maps: never construct Market/DataCategory from raw segments
# (constructor ValueError messages embed the rejected value and must not become
# __context__/__cause__ of the public DataContractError).
_MARKET_BY_VALUE: dict[str, Market] = {m.value: m for m in Market}
_CATEGORY_BY_VALUE: dict[str, DataCategory] = {c.value: c for c in DataCategory}


@dataclass(frozen=True, slots=True)
class ParsedCacheKey:
    """Coherent fields extracted from a valid v1 provider cache key."""

    market: Market
    category: DataCategory
    instrument_id: str | None
    as_of: datetime
    fingerprint: str


def _reject_fingerprint() -> NoReturn:
    """Raise without echoing the rejected fingerprint value."""
    raise DataContractError(
        "fingerprint must be <operation_name>|<16 lowercase hex> "
        "where operation_name matches "
        "^[A-Za-z][A-Za-z0-9_.:-]{0,127}$ and hash matches ^[0-9a-f]{16}$",
        details={"field": "fingerprint"},
    )


def _reject_cache_key() -> NoReturn:
    """Raise without echoing the rejected key value."""
    raise DataContractError(
        "cache key must be a valid v1 key",
        details={"field": "key"},
    )


def validate_cache_instrument_id(
    instrument_id: str | None, market: Market
) -> None:
    """Validate optional cache instrument_id against parse_instrument_id + market.

    Invalid values and market mismatches raise DataContractError without
    echoing the raw instrument_id (may contain pipes, query strings, secrets).
    """
    if instrument_id is None:
        return
    if not isinstance(instrument_id, str) or not instrument_id.strip():
        raise DataContractError(
            "instrument_id must be None or a non-empty string",
            details={"field": "instrument_id"},
        )
    # Reject free-text pollution before parse so query/secret-shaped values never
    # leak via parse_instrument_id details even when asset:market:symbol-shaped.
    if not _CACHE_INSTRUMENT_ID_RE.fullmatch(instrument_id):
        raise DataContractError(
            "instrument_id must be a valid public identity",
            details={"field": "instrument_id"},
        )
    parse_failed = False
    parsed_market: Market | None = None
    try:
        _asset, parsed_market, _symbol = parse_instrument_id(instrument_id)
    except DataContractError:
        # parse_instrument_id may include the raw id in details — drop it.
        parse_failed = True
    if parse_failed:
        raise DataContractError(
            "instrument_id must be a valid public identity",
            details={"field": "instrument_id"},
        )
    assert parsed_market is not None
    if parsed_market != market:
        raise DataContractError(
            "instrument_id market must equal market",
            details={
                "field": "instrument_id",
                "parsed_market": parsed_market.value,
                "market": market.value,
            },
        )


def require_valid_fingerprint(fingerprint: str) -> str:
    """Validate fingerprint grammar; never echo rejected values."""
    if not isinstance(fingerprint, str):
        raise DataContractError(
            "fingerprint must be a string",
            details={"field": "fingerprint", "type": type(fingerprint).__name__},
        )
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        _reject_fingerprint()
    # Defensive split: exactly one pipe, both sides already matched by RE.
    operation_name, hash_prefix = fingerprint.split("|", 1)
    if not _OPERATION_NAME_RE.fullmatch(operation_name) or not _HASH_RE.fullmatch(
        hash_prefix
    ):
        _reject_fingerprint()
    return fingerprint


def build_cache_key(
    market: Market,
    category: DataCategory,
    instrument_id: str | None,
    as_of: datetime,
    fingerprint: str,
) -> str:
    """Build the v1 cache key: ``v1|{market}|{category}|{id or '-'}|{as_of}|{fp}``.

    Raises DataContractError on naive ``as_of``, invalid fingerprint, or invalid
    instrument identity without echoing rejected fingerprint / instrument_id
    (may contain secret material).
    """
    require_aware_datetime(as_of, field_name="as_of")
    require_valid_fingerprint(fingerprint)
    validate_cache_instrument_id(instrument_id, market)
    instrument_part = "-" if instrument_id is None else instrument_id
    return (
        f"v1|{market.value}|{category.value}|{instrument_part}|"
        f"{as_of.isoformat()}|{fingerprint}"
    )


def parse_cache_key(key: str) -> ParsedCacheKey:
    """Parse and validate a v1 cache key.

    A valid key has version ``v1`` and exactly coherent market, category,
    instrument, as_of, and fingerprint segments. Never echoes the rejected key.
    """
    if not isinstance(key, str) or not key:
        _reject_cache_key()

    parts = key.split("|")
    if len(parts) != _CACHE_KEY_SEGMENT_COUNT:
        _reject_cache_key()

    version, market_s, category_s, instrument_s, as_of_s, operation_s, hash_s = parts
    if version != "v1":
        _reject_cache_key()

    market = _MARKET_BY_VALUE.get(market_s)
    if market is None:
        _reject_cache_key()

    category = _CATEGORY_BY_VALUE.get(category_s)
    if category is None:
        _reject_cache_key()

    if instrument_s == "-":
        instrument_id: str | None = None
    else:
        instrument_id = instrument_s
        instrument_invalid = False
        try:
            validate_cache_instrument_id(instrument_id, market)
        except DataContractError:
            # Do not re-raise here: caught errors must not become __context__.
            instrument_invalid = True
        if instrument_invalid:
            _reject_cache_key()

    fingerprint = f"{operation_s}|{hash_s}"
    fingerprint_invalid = False
    try:
        require_valid_fingerprint(fingerprint)
    except DataContractError:
        fingerprint_invalid = True
    if fingerprint_invalid:
        _reject_cache_key()

    as_of: datetime | None = None
    as_of_parse_failed = False
    try:
        as_of = datetime.fromisoformat(as_of_s)
    except ValueError:
        # fromisoformat embeds the raw segment; raise outside this except.
        as_of_parse_failed = True
    if as_of_parse_failed or as_of is None:
        _reject_cache_key()

    as_of_invalid = False
    try:
        require_aware_datetime(as_of, field_name="as_of")
    except DataContractError:
        as_of_invalid = True
    if as_of_invalid:
        _reject_cache_key()

    # Exact canonical form: rebuild must equal the original key string.
    instrument_part = "-" if instrument_id is None else instrument_id
    rebuilt = (
        f"v1|{market.value}|{category.value}|{instrument_part}|"
        f"{as_of.isoformat()}|{fingerprint}"
    )
    if rebuilt != key:
        _reject_cache_key()

    return ParsedCacheKey(
        market=market,
        category=category,
        instrument_id=instrument_id,
        as_of=as_of,
        fingerprint=fingerprint,
    )


def require_cache_key_matches_fields(
    key: str,
    *,
    entry_key: str,
    market: Market,
    category: DataCategory,
    instrument_id: str | None,
    as_of: datetime,
) -> ParsedCacheKey:
    """Require ``key`` is a valid v1 key equal to ``entry_key`` with matching fields.

    Parsed market, category, instrument_id, and as_of must equal the provided
    primitives. Never echoes the rejected key.
    """
    # Syntactic validity first so secret-shaped arbitrary keys never echo.
    parsed = parse_cache_key(key)
    if key != entry_key:
        raise DataContractError(
            "key must match entry.key",
            details={"field": "key"},
        )
    if (
        parsed.market != market
        or parsed.category != category
        or parsed.instrument_id != instrument_id
        or parsed.as_of != as_of
    ):
        raise DataContractError(
            "cache key fields must match entry market, category, "
            "instrument_id, and as_of",
            details={"field": "key"},
        )
    return parsed
