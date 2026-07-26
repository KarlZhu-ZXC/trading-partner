"""ETF-option cache codecs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Final

# E4c ETF option snapshot codec (§18.3)
# ---------------------------------------------------------------------------
from domain.a_share.enums import OptionType  # noqa: E402
from domain.a_share.models import (  # noqa: E402
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    OptionGreeks,
)
from domain.common.enums import (
    DataCategory,
)
from infrastructure.providers.a_share.codecs.base import (
    AShareProviderCacheCodec,
    _contract_error,
    _decode_bool,
    _decode_date,
    _decode_datetime,
    _decode_decimal,
    _decode_enum,
    _decode_optional_decimal,
    _decode_optional_int,
    _decode_str,
    _encode_bool,
    _encode_date,
    _encode_datetime,
    _encode_decimal,
    _encode_enum,
    _encode_optional_decimal,
    _encode_optional_int,
    _encode_str,
    _require_mapping,
)

CODEC_OPTION_SNAPSHOT: Final[str] = "a_share_option_snapshot.v1"

E4C_CODEC_IDS: Final[frozenset[str]] = frozenset({CODEC_OPTION_SNAPSHOT})

_OPTION_TYPE_BY_VALUE: Final[Mapping[str, OptionType]] = {m.value: m for m in OptionType}

_OPTION_CONTRACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "instrument_id",
        "underlying_instrument_id",
        "option_type",
        "expiry",
        "strike",
        "multiplier",
    }
)
_OPTION_QUOTE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract",
        "quote_at",
        "last",
        "bid_prices",
        "bid_volumes",
        "ask_prices",
        "ask_volumes",
        "volume_contracts",
        "open_interest",
    }
)
_OPTION_GREEKS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract_instrument_id",
        "as_of",
        "delta",
        "gamma",
        "theta",
        "vega",
        "implied_volatility",
        "theoretical_value",
        "source_provided",
    }
)
_OPTION_SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "underlying_instrument_id",
        "expiry",
        "quotes",
        "greeks",
    }
)


def _encode_decimal_tuple(values: tuple[Decimal, ...], *, field: str) -> list[str]:
    if not isinstance(values, tuple):
        raise _contract_error(f"{field} must be a tuple", field=field, rule="type")
    return [_encode_decimal(v, field=f"{field}[{i}]") for i, v in enumerate(values)]


def _decode_decimal_tuple(raw: object, *, field: str) -> tuple[Decimal, ...]:
    if not isinstance(raw, list):
        raise _contract_error(f"{field} must be an array", field=field, rule="type")
    return tuple(_decode_decimal(item, field=f"{field}[{idx}]") for idx, item in enumerate(raw))


def _encode_int_tuple(values: tuple[int, ...], *, field: str) -> list[int]:
    if not isinstance(values, tuple):
        raise _contract_error(f"{field} must be a tuple", field=field, rule="type")
    out: list[int] = []
    for idx, item in enumerate(values):
        if type(item) is not int or isinstance(item, bool):
            raise _contract_error(
                f"{field}[{idx}] must be int",
                field=f"{field}[{idx}]",
                rule="int_type",
            )
        out.append(item)
    return out


def _decode_int_tuple(raw: object, *, field: str) -> tuple[int, ...]:
    if not isinstance(raw, list):
        raise _contract_error(f"{field} must be an array", field=field, rule="type")
    out: list[int] = []
    for idx, item in enumerate(raw):
        if type(item) is not int or isinstance(item, bool):
            raise _contract_error(
                f"{field}[{idx}] must be int",
                field=f"{field}[{idx}]",
                rule="int_type",
            )
        out.append(item)
    return tuple(out)


def _encode_contract(contract: EtfOptionContract) -> dict[str, object]:
    if not isinstance(contract, EtfOptionContract):
        raise _contract_error(
            "contract must be EtfOptionContract",
            field="contract",
            rule="type",
            type=type(contract).__name__,
        )
    return {
        "instrument_id": _encode_str(contract.instrument_id, field="instrument_id"),
        "underlying_instrument_id": _encode_str(
            contract.underlying_instrument_id, field="underlying_instrument_id"
        ),
        "option_type": _encode_enum(
            contract.option_type, field="option_type", table=_OPTION_TYPE_BY_VALUE
        ),
        "expiry": _encode_date(contract.expiry, field="expiry"),
        "strike": _encode_decimal(contract.strike, field="strike"),
        "multiplier": _encode_optional_decimal(contract.multiplier, field="multiplier"),
    }


def _decode_contract(raw: object) -> EtfOptionContract:
    obj = _require_mapping(raw, field="contract", required_keys=_OPTION_CONTRACT_KEYS)
    return EtfOptionContract(
        instrument_id=_decode_str(obj["instrument_id"], field="instrument_id"),
        underlying_instrument_id=_decode_str(
            obj["underlying_instrument_id"], field="underlying_instrument_id"
        ),
        option_type=_decode_enum(
            obj["option_type"], field="option_type", table=_OPTION_TYPE_BY_VALUE
        ),
        expiry=_decode_date(obj["expiry"], field="expiry"),
        strike=_decode_decimal(obj["strike"], field="strike"),
        multiplier=_decode_optional_decimal(obj["multiplier"], field="multiplier"),
    )


def _encode_option_quote(quote: EtfOptionQuote) -> dict[str, object]:
    if not isinstance(quote, EtfOptionQuote):
        raise _contract_error(
            "quotes element must be EtfOptionQuote",
            field="quotes",
            rule="type",
            type=type(quote).__name__,
        )
    return {
        "contract": _encode_contract(quote.contract),
        "quote_at": _encode_datetime(quote.quote_at, field="quote_at"),
        "last": _encode_optional_decimal(quote.last, field="last"),
        "bid_prices": _encode_decimal_tuple(quote.bid_prices, field="bid_prices"),
        "bid_volumes": _encode_int_tuple(quote.bid_volumes, field="bid_volumes"),
        "ask_prices": _encode_decimal_tuple(quote.ask_prices, field="ask_prices"),
        "ask_volumes": _encode_int_tuple(quote.ask_volumes, field="ask_volumes"),
        "volume_contracts": _encode_optional_int(quote.volume_contracts, field="volume_contracts"),
        "open_interest": _encode_optional_int(quote.open_interest, field="open_interest"),
    }


def _decode_option_quote(raw: object) -> EtfOptionQuote:
    obj = _require_mapping(raw, field="quotes[]", required_keys=_OPTION_QUOTE_KEYS)
    return EtfOptionQuote(
        contract=_decode_contract(obj["contract"]),
        quote_at=_decode_datetime(obj["quote_at"], field="quote_at"),
        last=_decode_optional_decimal(obj["last"], field="last"),
        bid_prices=_decode_decimal_tuple(obj["bid_prices"], field="bid_prices"),
        bid_volumes=_decode_int_tuple(obj["bid_volumes"], field="bid_volumes"),
        ask_prices=_decode_decimal_tuple(obj["ask_prices"], field="ask_prices"),
        ask_volumes=_decode_int_tuple(obj["ask_volumes"], field="ask_volumes"),
        volume_contracts=_decode_optional_int(obj["volume_contracts"], field="volume_contracts"),
        open_interest=_decode_optional_int(obj["open_interest"], field="open_interest"),
    )


def _encode_greeks(greeks: OptionGreeks) -> dict[str, object]:
    if not isinstance(greeks, OptionGreeks):
        raise _contract_error(
            "greeks element must be OptionGreeks",
            field="greeks",
            rule="type",
            type=type(greeks).__name__,
        )
    return {
        "contract_instrument_id": _encode_str(
            greeks.contract_instrument_id, field="contract_instrument_id"
        ),
        "as_of": _encode_datetime(greeks.as_of, field="as_of"),
        "delta": _encode_optional_decimal(greeks.delta, field="delta"),
        "gamma": _encode_optional_decimal(greeks.gamma, field="gamma"),
        "theta": _encode_optional_decimal(greeks.theta, field="theta"),
        "vega": _encode_optional_decimal(greeks.vega, field="vega"),
        "implied_volatility": _encode_optional_decimal(
            greeks.implied_volatility, field="implied_volatility"
        ),
        "theoretical_value": _encode_optional_decimal(
            greeks.theoretical_value, field="theoretical_value"
        ),
        "source_provided": _encode_bool(greeks.source_provided, field="source_provided"),
    }


def _decode_greeks(raw: object) -> OptionGreeks:
    obj = _require_mapping(raw, field="greeks[]", required_keys=_OPTION_GREEKS_KEYS)
    return OptionGreeks(
        contract_instrument_id=_decode_str(
            obj["contract_instrument_id"], field="contract_instrument_id"
        ),
        as_of=_decode_datetime(obj["as_of"], field="as_of"),
        delta=_decode_optional_decimal(obj["delta"], field="delta"),
        gamma=_decode_optional_decimal(obj["gamma"], field="gamma"),
        theta=_decode_optional_decimal(obj["theta"], field="theta"),
        vega=_decode_optional_decimal(obj["vega"], field="vega"),
        implied_volatility=_decode_optional_decimal(
            obj["implied_volatility"], field="implied_volatility"
        ),
        theoretical_value=_decode_optional_decimal(
            obj["theoretical_value"], field="theoretical_value"
        ),
        source_provided=_decode_bool(obj["source_provided"], field="source_provided"),
    )


def _encode_option_snapshot(value: EtfOptionSnapshot) -> dict[str, object]:
    if not isinstance(value, EtfOptionSnapshot):
        raise _contract_error(
            "value must be EtfOptionSnapshot",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "underlying_instrument_id": _encode_str(
            value.underlying_instrument_id, field="underlying_instrument_id"
        ),
        "expiry": (None if value.expiry is None else _encode_date(value.expiry, field="expiry")),
        "quotes": [_encode_option_quote(q) for q in value.quotes],
        "greeks": [_encode_greeks(g) for g in value.greeks],
    }


def _decode_option_snapshot(raw: object) -> EtfOptionSnapshot:
    obj = _require_mapping(raw, field="value", required_keys=_OPTION_SNAPSHOT_KEYS)
    quotes_raw = obj["quotes"]
    greeks_raw = obj["greeks"]
    if not isinstance(quotes_raw, list):
        raise _contract_error("quotes must be an array", field="quotes", rule="type")
    if not isinstance(greeks_raw, list):
        raise _contract_error("greeks must be an array", field="greeks", rule="type")
    expiry_raw = obj["expiry"]
    expiry = None if expiry_raw is None else _decode_date(expiry_raw, field="expiry")
    return EtfOptionSnapshot(
        underlying_instrument_id=_decode_str(
            obj["underlying_instrument_id"], field="underlying_instrument_id"
        ),
        expiry=expiry,
        quotes=tuple(_decode_option_quote(q) for q in quotes_raw),
        greeks=tuple(_decode_greeks(g) for g in greeks_raw),
    )


def option_snapshot_codec() -> AShareProviderCacheCodec[EtfOptionSnapshot]:
    return AShareProviderCacheCodec(
        CODEC_OPTION_SNAPSHOT,
        _encode_option_snapshot,
        _decode_option_snapshot,
        expected_category=DataCategory.OPTIONS,
    )


E4C_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_OPTION_SNAPSHOT: option_snapshot_codec,  # type: ignore[dict-item]
}

__all__ = [
    "CODEC_OPTION_SNAPSHOT",
    "E4C_CODEC_IDS",
    "E4C_CODECS",
    "option_snapshot_codec",
]
