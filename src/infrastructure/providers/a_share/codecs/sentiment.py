"""Limit, sentiment, and interactive-QA cache codecs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

# E4b limit / sentiment / interactive QA codecs (§18.3)
# ---------------------------------------------------------------------------
from domain.a_share.enums import LimitPoolType, SentimentSourceType  # noqa: E402
from domain.a_share.models import (  # noqa: E402
    InteractiveQAItem,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    SentimentSignal,
)
from domain.common.enums import (
    DataCategory,
)
from infrastructure.providers.a_share.codecs.base import (
    _RELIABILITY_BY_VALUE,
    _VENDOR_BY_VALUE,
    AShareProviderCacheCodec,
    _contract_error,
    _decode_bool,
    _decode_date,
    _decode_datetime,
    _decode_decimal,
    _decode_enum,
    _decode_int,
    _decode_optional_datetime,
    _decode_optional_decimal,
    _decode_optional_int,
    _decode_optional_str,
    _decode_str,
    _encode_bool,
    _encode_date,
    _encode_datetime,
    _encode_decimal,
    _encode_enum,
    _encode_int,
    _encode_optional_datetime,
    _encode_optional_decimal,
    _encode_optional_int,
    _encode_optional_str,
    _encode_str,
    _require_mapping,
)

CODEC_LIMIT_CONTEXT: Final[str] = "a_share_limit_context.v1"
CODEC_SENTIMENT: Final[str] = "a_share_sentiment.v2"
CODEC_INTERACTIVE_QA: Final[str] = "a_share_interactive_qa.v1"

E4B_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_LIMIT_CONTEXT,
        CODEC_SENTIMENT,
        CODEC_INTERACTIVE_QA,
    }
)

_LIMIT_POOL_TYPE_BY_VALUE: Final[Mapping[str, LimitPoolType]] = {m.value: m for m in LimitPoolType}
_SENTIMENT_SOURCE_BY_VALUE: Final[Mapping[str, SentimentSourceType]] = {
    m.value: m for m in SentimentSourceType
}

_LIMIT_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "pool_type",
        "trade_date",
        "instrument_id",
        "name",
        "last",
        "change_percent",
        "consecutive_limit_count",
        "days_and_boards",
        "first_seal_at",
        "last_seal_at",
        "seal_amount_cny",
        "broken_count",
        "industry",
        "reason_tags",
        "source_vendor",
        "reliability",
    }
)
_LADDER_KEYS: Final[frozenset[str]] = frozenset(
    {"consecutive_limit_count", "instrument_count", "instrument_ids"}
)
_LIMIT_CONTEXT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "entries",
        "limit_up_count",
        "limit_down_count",
        "broken_limit_count",
        "broken_rate",
        "max_consecutive_count",
        "promotion_rate",
        "ladder",
    }
)
_SENTIMENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_type",
        "trade_date",
        "instrument_id",
        "rank",
        "rank_change",
        "heat_value",
        "concept_tags",
        "label",
        "source_vendor",
        "reliability",
        "is_authoritative",
        "source_item_id",
        "observed_at",
    }
)
_QA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "qa_key",
        "question",
        "asked_at",
        "answer",
        "answered_at",
        "source_url",
    }
)


def _encode_str_tuple(values: tuple[str, ...], *, field: str) -> list[str]:
    if not isinstance(values, tuple):
        raise _contract_error(f"{field} must be a tuple", field=field, rule="type")
    out: list[str] = []
    for idx, item in enumerate(values):
        out.append(_encode_str(item, field=f"{field}[{idx}]"))
    return out


def _decode_str_tuple(raw: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _contract_error(f"{field} must be an array", field=field, rule="type")
    return tuple(_decode_str(item, field=f"{field}[{idx}]") for idx, item in enumerate(raw))


def _encode_limit_entry(entry: LimitPoolEntry) -> dict[str, object]:
    if not isinstance(entry, LimitPoolEntry):
        raise _contract_error(
            "value element must be LimitPoolEntry",
            field="value",
            rule="type",
            type=type(entry).__name__,
        )
    return {
        "pool_type": _encode_enum(
            entry.pool_type, field="pool_type", table=_LIMIT_POOL_TYPE_BY_VALUE
        ),
        "trade_date": _encode_date(entry.trade_date, field="trade_date"),
        "instrument_id": _encode_str(entry.instrument_id, field="instrument_id"),
        "name": _encode_str(entry.name, field="name"),
        "last": _encode_decimal(entry.last, field="last"),
        "change_percent": _encode_decimal(entry.change_percent, field="change_percent"),
        "consecutive_limit_count": _encode_optional_int(
            entry.consecutive_limit_count, field="consecutive_limit_count"
        ),
        "days_and_boards": _encode_optional_str(entry.days_and_boards, field="days_and_boards"),
        "first_seal_at": _encode_optional_datetime(entry.first_seal_at, field="first_seal_at"),
        "last_seal_at": _encode_optional_datetime(entry.last_seal_at, field="last_seal_at"),
        "seal_amount_cny": _encode_optional_decimal(entry.seal_amount_cny, field="seal_amount_cny"),
        "broken_count": _encode_optional_int(entry.broken_count, field="broken_count"),
        "industry": _encode_optional_str(entry.industry, field="industry"),
        "reason_tags": _encode_str_tuple(entry.reason_tags, field="reason_tags"),
        "source_vendor": _encode_enum(
            entry.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            entry.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
    }


def _decode_limit_entry(raw: object) -> LimitPoolEntry:
    obj = _require_mapping(raw, field="value[]", required_keys=_LIMIT_ENTRY_KEYS)
    return LimitPoolEntry(
        pool_type=_decode_enum(
            obj["pool_type"], field="pool_type", table=_LIMIT_POOL_TYPE_BY_VALUE
        ),
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        instrument_id=_decode_str(obj["instrument_id"], field="instrument_id"),
        name=_decode_str(obj["name"], field="name"),
        last=_decode_decimal(obj["last"], field="last"),
        change_percent=_decode_decimal(obj["change_percent"], field="change_percent"),
        consecutive_limit_count=_decode_optional_int(
            obj["consecutive_limit_count"], field="consecutive_limit_count"
        ),
        days_and_boards=_decode_optional_str(obj["days_and_boards"], field="days_and_boards"),
        first_seal_at=_decode_optional_datetime(obj["first_seal_at"], field="first_seal_at"),
        last_seal_at=_decode_optional_datetime(obj["last_seal_at"], field="last_seal_at"),
        seal_amount_cny=_decode_optional_decimal(obj["seal_amount_cny"], field="seal_amount_cny"),
        broken_count=_decode_optional_int(obj["broken_count"], field="broken_count"),
        industry=_decode_optional_str(obj["industry"], field="industry"),
        reason_tags=_decode_str_tuple(obj["reason_tags"], field="reason_tags"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
    )


def _encode_ladder(rung: LimitUpLadderRung) -> dict[str, object]:
    if not isinstance(rung, LimitUpLadderRung):
        raise _contract_error(
            "ladder element must be LimitUpLadderRung",
            field="ladder",
            rule="type",
            type=type(rung).__name__,
        )
    return {
        "consecutive_limit_count": _encode_int(
            rung.consecutive_limit_count, field="consecutive_limit_count"
        ),
        "instrument_count": _encode_int(rung.instrument_count, field="instrument_count"),
        "instrument_ids": _encode_str_tuple(rung.instrument_ids, field="instrument_ids"),
    }


def _decode_ladder(raw: object) -> LimitUpLadderRung:
    obj = _require_mapping(raw, field="ladder[]", required_keys=_LADDER_KEYS)
    return LimitUpLadderRung(
        consecutive_limit_count=_decode_int(
            obj["consecutive_limit_count"], field="consecutive_limit_count"
        ),
        instrument_count=_decode_int(obj["instrument_count"], field="instrument_count"),
        instrument_ids=_decode_str_tuple(obj["instrument_ids"], field="instrument_ids"),
    )


def _encode_limit_context(value: LimitUpContext) -> dict[str, object]:
    if not isinstance(value, LimitUpContext):
        raise _contract_error(
            "value must be LimitUpContext",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "trade_date": _encode_date(value.trade_date, field="trade_date"),
        "entries": [_encode_limit_entry(e) for e in value.entries],
        "limit_up_count": _encode_int(value.limit_up_count, field="limit_up_count"),
        "limit_down_count": _encode_int(value.limit_down_count, field="limit_down_count"),
        "broken_limit_count": _encode_int(value.broken_limit_count, field="broken_limit_count"),
        "broken_rate": _encode_optional_decimal(value.broken_rate, field="broken_rate"),
        "max_consecutive_count": _encode_optional_int(
            value.max_consecutive_count, field="max_consecutive_count"
        ),
        "promotion_rate": _encode_optional_decimal(value.promotion_rate, field="promotion_rate"),
        "ladder": [_encode_ladder(r) for r in value.ladder],
    }


def _decode_limit_context(raw: object) -> LimitUpContext:
    obj = _require_mapping(raw, field="value", required_keys=_LIMIT_CONTEXT_KEYS)
    entries_raw = obj["entries"]
    ladder_raw = obj["ladder"]
    if not isinstance(entries_raw, list):
        raise _contract_error("entries must be an array", field="entries", rule="type")
    if not isinstance(ladder_raw, list):
        raise _contract_error("ladder must be an array", field="ladder", rule="type")
    return LimitUpContext(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        entries=tuple(_decode_limit_entry(e) for e in entries_raw),
        limit_up_count=_decode_int(obj["limit_up_count"], field="limit_up_count"),
        limit_down_count=_decode_int(obj["limit_down_count"], field="limit_down_count"),
        broken_limit_count=_decode_int(obj["broken_limit_count"], field="broken_limit_count"),
        broken_rate=_decode_optional_decimal(obj["broken_rate"], field="broken_rate"),
        max_consecutive_count=_decode_optional_int(
            obj["max_consecutive_count"], field="max_consecutive_count"
        ),
        promotion_rate=_decode_optional_decimal(obj["promotion_rate"], field="promotion_rate"),
        ladder=tuple(_decode_ladder(r) for r in ladder_raw),
    )


def _encode_sentiment_signal(signal: SentimentSignal) -> dict[str, object]:
    if not isinstance(signal, SentimentSignal):
        raise _contract_error(
            "value element must be SentimentSignal",
            field="value",
            rule="type",
            type=type(signal).__name__,
        )
    return {
        "source_type": _encode_enum(
            signal.source_type, field="source_type", table=_SENTIMENT_SOURCE_BY_VALUE
        ),
        "trade_date": _encode_date(signal.trade_date, field="trade_date"),
        "instrument_id": _encode_optional_str(signal.instrument_id, field="instrument_id"),
        "rank": _encode_optional_int(signal.rank, field="rank"),
        "rank_change": _encode_optional_int(signal.rank_change, field="rank_change"),
        "heat_value": _encode_optional_decimal(signal.heat_value, field="heat_value"),
        "concept_tags": _encode_str_tuple(signal.concept_tags, field="concept_tags"),
        "label": _encode_optional_str(signal.label, field="label"),
        "source_vendor": _encode_enum(
            signal.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            signal.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(signal.is_authoritative, field="is_authoritative"),
        "source_item_id": _encode_optional_str(signal.source_item_id, field="source_item_id"),
        "observed_at": _encode_optional_datetime(signal.observed_at, field="observed_at"),
    }


def _decode_sentiment_signal(raw: object) -> SentimentSignal:
    obj = _require_mapping(raw, field="value[]", required_keys=_SENTIMENT_KEYS)
    return SentimentSignal(
        source_type=_decode_enum(
            obj["source_type"], field="source_type", table=_SENTIMENT_SOURCE_BY_VALUE
        ),
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        instrument_id=_decode_optional_str(obj["instrument_id"], field="instrument_id"),
        rank=_decode_optional_int(obj["rank"], field="rank"),
        rank_change=_decode_optional_int(obj["rank_change"], field="rank_change"),
        heat_value=_decode_optional_decimal(obj["heat_value"], field="heat_value"),
        concept_tags=_decode_str_tuple(obj["concept_tags"], field="concept_tags"),
        label=_decode_optional_str(obj["label"], field="label"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
        source_item_id=_decode_optional_str(obj["source_item_id"], field="source_item_id"),
        observed_at=_decode_optional_datetime(obj["observed_at"], field="observed_at"),
    )


def _encode_sentiment(value: tuple[SentimentSignal, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_sentiment_signal(s) for s in value]


def _decode_sentiment(raw: object) -> tuple[SentimentSignal, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_sentiment_signal(item) for item in raw)


def _encode_qa(item: InteractiveQAItem) -> dict[str, object]:
    if not isinstance(item, InteractiveQAItem):
        raise _contract_error(
            "value element must be InteractiveQAItem",
            field="value",
            rule="type",
            type=type(item).__name__,
        )
    return {
        "qa_key": _encode_str(item.qa_key, field="qa_key"),
        "question": _encode_str(item.question, field="question"),
        "asked_at": _encode_optional_datetime(item.asked_at, field="asked_at"),
        "answer": _encode_str(item.answer, field="answer"),
        "answered_at": _encode_datetime(item.answered_at, field="answered_at"),
        "source_url": _encode_optional_str(item.source_url, field="source_url"),
    }


def _decode_qa(raw: object) -> InteractiveQAItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_QA_KEYS)
    return InteractiveQAItem(
        qa_key=_decode_str(obj["qa_key"], field="qa_key"),
        question=_decode_str(obj["question"], field="question"),
        asked_at=_decode_optional_datetime(obj["asked_at"], field="asked_at"),
        answer=_decode_str(obj["answer"], field="answer"),
        answered_at=_decode_datetime(obj["answered_at"], field="answered_at"),
        source_url=_decode_optional_str(obj["source_url"], field="source_url"),
    )


def _encode_qa_tuple(value: tuple[InteractiveQAItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_qa(item) for item in value]


def _decode_qa_tuple(raw: object) -> tuple[InteractiveQAItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_qa(item) for item in raw)


def limit_context_codec() -> AShareProviderCacheCodec[LimitUpContext]:
    return AShareProviderCacheCodec(
        CODEC_LIMIT_CONTEXT,
        _encode_limit_context,
        _decode_limit_context,
        expected_category=DataCategory.LIMIT_UP,
    )


def sentiment_codec() -> AShareProviderCacheCodec[tuple[SentimentSignal, ...]]:
    return AShareProviderCacheCodec(
        CODEC_SENTIMENT,
        _encode_sentiment,
        _decode_sentiment,
        expected_category=DataCategory.SENTIMENT,
    )


def interactive_qa_codec() -> AShareProviderCacheCodec[tuple[InteractiveQAItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_INTERACTIVE_QA,
        _encode_qa_tuple,
        _decode_qa_tuple,
        expected_category=DataCategory.INTERACTIVE_QA,
    )


E4B_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_LIMIT_CONTEXT: limit_context_codec,  # type: ignore[dict-item]
    CODEC_SENTIMENT: sentiment_codec,  # type: ignore[dict-item]
    CODEC_INTERACTIVE_QA: interactive_qa_codec,  # type: ignore[dict-item]
}


# ---------------------------------------------------------------------------

__all__ = [
    "CODEC_INTERACTIVE_QA",
    "CODEC_LIMIT_CONTEXT",
    "CODEC_SENTIMENT",
    "E4B_CODEC_IDS",
    "E4B_CODECS",
    "interactive_qa_codec",
    "limit_context_codec",
    "sentiment_codec",
]
