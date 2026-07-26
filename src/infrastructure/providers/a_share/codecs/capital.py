"""Capital and ownership cache codecs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

# E4a capital codecs (§18.3)
# ---------------------------------------------------------------------------
from domain.a_share.models import (  # noqa: E402
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    DragonTigerRecord,
    DragonTigerSeat,
    FundFlowPoint,
    MarginRecord,
    NorthboundFlowPoint,
    ShareholderCountRecord,
)
from domain.common.enums import (
    DataCategory,
)
from infrastructure.providers.a_share.codecs.base import (
    _ADJUSTMENT_BY_VALUE,
    _BAR_INTERVAL_BY_VALUE,
    _RELIABILITY_BY_VALUE,
    _VENDOR_BY_VALUE,
    AShareProviderCacheCodec,
    _contract_error,
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

CODEC_INTRADAY_FLOW: Final[str] = "a_share_intraday_flow.v1"
CODEC_DAILY_FLOW: Final[str] = "a_share_daily_flow.v1"
CODEC_NORTHBOUND: Final[str] = "a_share_northbound.v1"
CODEC_DRAGON_TIGER: Final[str] = "a_share_dragon_tiger.v1"
CODEC_MARGIN: Final[str] = "a_share_margin.v1"
CODEC_BLOCK_TRADES: Final[str] = "a_share_block_trades.v1"
CODEC_SHAREHOLDER_COUNTS: Final[str] = "a_share_shareholder_counts.v1"
CODEC_CHIP_DISTRIBUTION: Final[str] = "a_share_chip_distribution.v2"

E4A_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_INTRADAY_FLOW,
        CODEC_DAILY_FLOW,
        CODEC_NORTHBOUND,
        CODEC_DRAGON_TIGER,
        CODEC_MARGIN,
        CODEC_BLOCK_TRADES,
        CODEC_SHAREHOLDER_COUNTS,
        CODEC_CHIP_DISTRIBUTION,
    }
)

_FUND_FLOW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "occurred_at",
        "interval",
        "main_net_cny",
        "super_large_net_cny",
        "large_net_cny",
        "medium_net_cny",
        "small_net_cny",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_NORTHBOUND_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "channel",
        "net_buy_cny",
        "buy_cny",
        "sell_cny",
        "disclosure_note",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_SEAT_KEYS: Final[frozenset[str]] = frozenset(
    {"rank", "side", "branch_name", "amount_cny", "is_institution"}
)
_DRAGON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "instrument_id",
        "reason",
        "buy_total_cny",
        "sell_total_cny",
        "net_buy_cny",
        "seats",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_MARGIN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "financing_balance_cny",
        "financing_buy_cny",
        "financing_repayment_cny",
        "securities_lending_balance_cny",
        "securities_lending_sell_shares",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_BLOCK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "price",
        "volume_shares",
        "amount_cny",
        "premium_percent",
        "buyer_branch",
        "seller_branch",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_HOLDER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "period_end",
        "published_at",
        "shareholder_count",
        "change_percent",
        "average_holding_shares",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_BIN_KEYS: Final[frozenset[str]] = frozenset({"price_low", "price_high", "holding_ratio"})
_CHIP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "as_of",
        "bins",
        "profit_ratio",
        "average_cost",
        "concentration_90",
        "concentration_70",
        "source_vendor",
        "reliability",
        "is_authoritative",
        "calculation_method",
        "algorithm_version",
        "lookback_sessions",
        "input_adjustment",
        "bar_trade_date",
    }
)


def _encode_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise _contract_error(
            f"{field} must be bool", field=field, rule="bool_type", type=type(value).__name__
        )
    return value


def _decode_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise _contract_error(
            f"{field} must be bool", field=field, rule="bool_type", type=type(value).__name__
        )
    return value


def _encode_fund_flow_point(point: FundFlowPoint) -> dict[str, object]:
    if not isinstance(point, FundFlowPoint):
        raise _contract_error(
            "value element must be FundFlowPoint",
            field="value",
            rule="type",
            type=type(point).__name__,
        )
    return {
        "occurred_at": _encode_datetime(point.occurred_at, field="occurred_at"),
        "interval": _encode_enum(point.interval, field="interval", table=_BAR_INTERVAL_BY_VALUE),
        "main_net_cny": _encode_optional_decimal(point.main_net_cny, field="main_net_cny"),
        "super_large_net_cny": _encode_optional_decimal(
            point.super_large_net_cny, field="super_large_net_cny"
        ),
        "large_net_cny": _encode_optional_decimal(point.large_net_cny, field="large_net_cny"),
        "medium_net_cny": _encode_optional_decimal(point.medium_net_cny, field="medium_net_cny"),
        "small_net_cny": _encode_optional_decimal(point.small_net_cny, field="small_net_cny"),
        "source_vendor": _encode_enum(
            point.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            point.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(point.is_authoritative, field="is_authoritative"),
    }


def _decode_fund_flow_point(raw: object) -> FundFlowPoint:
    obj = _require_mapping(raw, field="value[]", required_keys=_FUND_FLOW_KEYS)
    return FundFlowPoint(
        occurred_at=_decode_datetime(obj["occurred_at"], field="occurred_at"),
        interval=_decode_enum(obj["interval"], field="interval", table=_BAR_INTERVAL_BY_VALUE),
        main_net_cny=_decode_optional_decimal(obj["main_net_cny"], field="main_net_cny"),
        super_large_net_cny=_decode_optional_decimal(
            obj["super_large_net_cny"], field="super_large_net_cny"
        ),
        large_net_cny=_decode_optional_decimal(obj["large_net_cny"], field="large_net_cny"),
        medium_net_cny=_decode_optional_decimal(obj["medium_net_cny"], field="medium_net_cny"),
        small_net_cny=_decode_optional_decimal(obj["small_net_cny"], field="small_net_cny"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_fund_flow(value: tuple[FundFlowPoint, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_fund_flow_point(p) for p in value]


def _decode_fund_flow(raw: object) -> tuple[FundFlowPoint, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_fund_flow_point(item) for item in raw)


def _encode_northbound_point(point: NorthboundFlowPoint) -> dict[str, object]:
    if not isinstance(point, NorthboundFlowPoint):
        raise _contract_error(
            "value element must be NorthboundFlowPoint",
            field="value",
            rule="type",
            type=type(point).__name__,
        )
    return {
        "trade_date": _encode_date(point.trade_date, field="trade_date"),
        "channel": _encode_str(point.channel, field="channel"),
        "net_buy_cny": _encode_optional_decimal(point.net_buy_cny, field="net_buy_cny"),
        "buy_cny": _encode_optional_decimal(point.buy_cny, field="buy_cny"),
        "sell_cny": _encode_optional_decimal(point.sell_cny, field="sell_cny"),
        "disclosure_note": _encode_optional_str(point.disclosure_note, field="disclosure_note"),
        "source_vendor": _encode_enum(
            point.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            point.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(point.is_authoritative, field="is_authoritative"),
    }


def _decode_northbound_point(raw: object) -> NorthboundFlowPoint:
    obj = _require_mapping(raw, field="value[]", required_keys=_NORTHBOUND_KEYS)
    return NorthboundFlowPoint(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        channel=_decode_str(obj["channel"], field="channel"),
        net_buy_cny=_decode_optional_decimal(obj["net_buy_cny"], field="net_buy_cny"),
        buy_cny=_decode_optional_decimal(obj["buy_cny"], field="buy_cny"),
        sell_cny=_decode_optional_decimal(obj["sell_cny"], field="sell_cny"),
        disclosure_note=_decode_optional_str(obj["disclosure_note"], field="disclosure_note"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_northbound(value: tuple[NorthboundFlowPoint, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_northbound_point(p) for p in value]


def _decode_northbound(raw: object) -> tuple[NorthboundFlowPoint, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_northbound_point(item) for item in raw)


def _encode_seat(seat: DragonTigerSeat) -> dict[str, object]:
    return {
        "rank": _encode_int(seat.rank, field="rank"),
        "side": _encode_str(seat.side, field="side"),
        "branch_name": _encode_str(seat.branch_name, field="branch_name"),
        "amount_cny": _encode_decimal(seat.amount_cny, field="amount_cny"),
        "is_institution": (
            None
            if seat.is_institution is None
            else _encode_bool(seat.is_institution, field="is_institution")
        ),
    }


def _decode_seat(raw: object) -> DragonTigerSeat:
    obj = _require_mapping(raw, field="seat", required_keys=_SEAT_KEYS)
    inst = obj["is_institution"]
    return DragonTigerSeat(
        rank=_decode_int(obj["rank"], field="rank"),
        side=_decode_str(obj["side"], field="side"),
        branch_name=_decode_str(obj["branch_name"], field="branch_name"),
        amount_cny=_decode_decimal(obj["amount_cny"], field="amount_cny"),
        is_institution=None if inst is None else _decode_bool(inst, field="is_institution"),
    )


def _encode_dragon(record: DragonTigerRecord) -> dict[str, object]:
    if not isinstance(record, DragonTigerRecord):
        raise _contract_error(
            "value element must be DragonTigerRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "trade_date": _encode_date(record.trade_date, field="trade_date"),
        "instrument_id": _encode_str(record.instrument_id, field="instrument_id"),
        "reason": _encode_str(record.reason, field="reason"),
        "buy_total_cny": _encode_decimal(record.buy_total_cny, field="buy_total_cny"),
        "sell_total_cny": _encode_decimal(record.sell_total_cny, field="sell_total_cny"),
        "net_buy_cny": _encode_decimal(record.net_buy_cny, field="net_buy_cny"),
        "seats": [_encode_seat(s) for s in record.seats],
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_dragon(raw: object) -> DragonTigerRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_DRAGON_KEYS)
    seats_raw = obj["seats"]
    if not isinstance(seats_raw, list):
        raise _contract_error("seats must be an array", field="seats", rule="type")
    return DragonTigerRecord(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        instrument_id=_decode_str(obj["instrument_id"], field="instrument_id"),
        reason=_decode_str(obj["reason"], field="reason"),
        buy_total_cny=_decode_decimal(obj["buy_total_cny"], field="buy_total_cny"),
        sell_total_cny=_decode_decimal(obj["sell_total_cny"], field="sell_total_cny"),
        net_buy_cny=_decode_decimal(obj["net_buy_cny"], field="net_buy_cny"),
        seats=tuple(_decode_seat(s) for s in seats_raw),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_dragons(value: tuple[DragonTigerRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_dragon(r) for r in value]


def _decode_dragons(raw: object) -> tuple[DragonTigerRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_dragon(item) for item in raw)


def _encode_margin(record: MarginRecord) -> dict[str, object]:
    if not isinstance(record, MarginRecord):
        raise _contract_error(
            "value element must be MarginRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "trade_date": _encode_date(record.trade_date, field="trade_date"),
        "financing_balance_cny": _encode_decimal(
            record.financing_balance_cny, field="financing_balance_cny"
        ),
        "financing_buy_cny": _encode_decimal(record.financing_buy_cny, field="financing_buy_cny"),
        "financing_repayment_cny": _encode_decimal(
            record.financing_repayment_cny, field="financing_repayment_cny"
        ),
        "securities_lending_balance_cny": _encode_optional_decimal(
            record.securities_lending_balance_cny,
            field="securities_lending_balance_cny",
        ),
        "securities_lending_sell_shares": _encode_optional_int(
            record.securities_lending_sell_shares,
            field="securities_lending_sell_shares",
        ),
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_margin(raw: object) -> MarginRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_MARGIN_KEYS)
    return MarginRecord(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        financing_balance_cny=_decode_decimal(
            obj["financing_balance_cny"], field="financing_balance_cny"
        ),
        financing_buy_cny=_decode_decimal(obj["financing_buy_cny"], field="financing_buy_cny"),
        financing_repayment_cny=_decode_decimal(
            obj["financing_repayment_cny"], field="financing_repayment_cny"
        ),
        securities_lending_balance_cny=_decode_optional_decimal(
            obj["securities_lending_balance_cny"],
            field="securities_lending_balance_cny",
        ),
        securities_lending_sell_shares=_decode_optional_int(
            obj["securities_lending_sell_shares"],
            field="securities_lending_sell_shares",
        ),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_margins(value: tuple[MarginRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_margin(r) for r in value]


def _decode_margins(raw: object) -> tuple[MarginRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_margin(item) for item in raw)


def _encode_block(record: BlockTradeRecord) -> dict[str, object]:
    if not isinstance(record, BlockTradeRecord):
        raise _contract_error(
            "value element must be BlockTradeRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "trade_date": _encode_date(record.trade_date, field="trade_date"),
        "price": _encode_decimal(record.price, field="price"),
        "volume_shares": _encode_int(record.volume_shares, field="volume_shares"),
        "amount_cny": _encode_decimal(record.amount_cny, field="amount_cny"),
        "premium_percent": _encode_optional_decimal(
            record.premium_percent, field="premium_percent"
        ),
        "buyer_branch": _encode_optional_str(record.buyer_branch, field="buyer_branch"),
        "seller_branch": _encode_optional_str(record.seller_branch, field="seller_branch"),
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_block(raw: object) -> BlockTradeRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_BLOCK_KEYS)
    return BlockTradeRecord(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        price=_decode_decimal(obj["price"], field="price"),
        volume_shares=_decode_int(obj["volume_shares"], field="volume_shares"),
        amount_cny=_decode_decimal(obj["amount_cny"], field="amount_cny"),
        premium_percent=_decode_optional_decimal(obj["premium_percent"], field="premium_percent"),
        buyer_branch=_decode_optional_str(obj["buyer_branch"], field="buyer_branch"),
        seller_branch=_decode_optional_str(obj["seller_branch"], field="seller_branch"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_blocks(value: tuple[BlockTradeRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_block(r) for r in value]


def _decode_blocks(raw: object) -> tuple[BlockTradeRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_block(item) for item in raw)


def _encode_holder(record: ShareholderCountRecord) -> dict[str, object]:
    if not isinstance(record, ShareholderCountRecord):
        raise _contract_error(
            "value element must be ShareholderCountRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "period_end": _encode_date(record.period_end, field="period_end"),
        "published_at": _encode_optional_datetime(record.published_at, field="published_at"),
        "shareholder_count": _encode_int(record.shareholder_count, field="shareholder_count"),
        "change_percent": _encode_optional_decimal(record.change_percent, field="change_percent"),
        "average_holding_shares": _encode_optional_decimal(
            record.average_holding_shares, field="average_holding_shares"
        ),
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_holder(raw: object) -> ShareholderCountRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_HOLDER_KEYS)
    return ShareholderCountRecord(
        period_end=_decode_date(obj["period_end"], field="period_end"),
        published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
        shareholder_count=_decode_int(obj["shareholder_count"], field="shareholder_count"),
        change_percent=_decode_optional_decimal(obj["change_percent"], field="change_percent"),
        average_holding_shares=_decode_optional_decimal(
            obj["average_holding_shares"], field="average_holding_shares"
        ),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_holders(value: tuple[ShareholderCountRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_holder(r) for r in value]


def _decode_holders(raw: object) -> tuple[ShareholderCountRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_holder(item) for item in raw)


def _encode_chip(value: ChipDistributionSnapshot) -> dict[str, object]:
    if not isinstance(value, ChipDistributionSnapshot):
        raise _contract_error(
            "value must be ChipDistributionSnapshot",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "as_of": _encode_datetime(value.as_of, field="as_of"),
        "bins": [
            {
                "price_low": _encode_decimal(b.price_low, field="price_low"),
                "price_high": _encode_decimal(b.price_high, field="price_high"),
                "holding_ratio": _encode_decimal(b.holding_ratio, field="holding_ratio"),
            }
            for b in value.bins
        ],
        "profit_ratio": _encode_optional_decimal(value.profit_ratio, field="profit_ratio"),
        "average_cost": _encode_optional_decimal(value.average_cost, field="average_cost"),
        "concentration_90": _encode_optional_decimal(
            value.concentration_90, field="concentration_90"
        ),
        "concentration_70": _encode_optional_decimal(
            value.concentration_70, field="concentration_70"
        ),
        "source_vendor": _encode_enum(
            value.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            value.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(value.is_authoritative, field="is_authoritative"),
        "calculation_method": _encode_str(value.calculation_method, field="calculation_method"),
        "algorithm_version": _encode_str(value.algorithm_version, field="algorithm_version"),
        "lookback_sessions": _encode_int(value.lookback_sessions, field="lookback_sessions"),
        "input_adjustment": _encode_enum(
            value.input_adjustment, field="input_adjustment", table=_ADJUSTMENT_BY_VALUE
        ),
        "bar_trade_date": _encode_date(value.bar_trade_date, field="bar_trade_date"),
    }


def _decode_chip(raw: object) -> ChipDistributionSnapshot:
    obj = _require_mapping(raw, field="value", required_keys=_CHIP_KEYS)
    bins_raw = obj["bins"]
    if not isinstance(bins_raw, list):
        raise _contract_error("bins must be an array", field="bins", rule="type")
    bins: list[ChipDistributionBin] = []
    for item in bins_raw:
        b = _require_mapping(item, field="bin", required_keys=_BIN_KEYS)
        bins.append(
            ChipDistributionBin(
                price_low=_decode_decimal(b["price_low"], field="price_low"),
                price_high=_decode_decimal(b["price_high"], field="price_high"),
                holding_ratio=_decode_decimal(b["holding_ratio"], field="holding_ratio"),
            )
        )
    return ChipDistributionSnapshot(
        as_of=_decode_datetime(obj["as_of"], field="as_of"),
        bins=tuple(bins),
        profit_ratio=_decode_optional_decimal(obj["profit_ratio"], field="profit_ratio"),
        average_cost=_decode_optional_decimal(obj["average_cost"], field="average_cost"),
        concentration_90=_decode_optional_decimal(
            obj["concentration_90"], field="concentration_90"
        ),
        concentration_70=_decode_optional_decimal(
            obj["concentration_70"], field="concentration_70"
        ),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
        calculation_method=_decode_str(obj["calculation_method"], field="calculation_method"),
        algorithm_version=_decode_str(obj["algorithm_version"], field="algorithm_version"),
        lookback_sessions=_decode_int(obj["lookback_sessions"], field="lookback_sessions"),
        input_adjustment=_decode_enum(
            obj["input_adjustment"], field="input_adjustment", table=_ADJUSTMENT_BY_VALUE
        ),
        bar_trade_date=_decode_date(obj["bar_trade_date"], field="bar_trade_date"),
    )


def intraday_flow_codec() -> AShareProviderCacheCodec[tuple[FundFlowPoint, ...]]:
    return AShareProviderCacheCodec(
        CODEC_INTRADAY_FLOW,
        _encode_fund_flow,
        _decode_fund_flow,
        expected_category=DataCategory.CAPITAL,
    )


def daily_flow_codec() -> AShareProviderCacheCodec[tuple[FundFlowPoint, ...]]:
    return AShareProviderCacheCodec(
        CODEC_DAILY_FLOW,
        _encode_fund_flow,
        _decode_fund_flow,
        expected_category=DataCategory.CAPITAL,
    )


def northbound_codec() -> AShareProviderCacheCodec[tuple[NorthboundFlowPoint, ...]]:
    return AShareProviderCacheCodec(
        CODEC_NORTHBOUND,
        _encode_northbound,
        _decode_northbound,
        expected_category=DataCategory.CAPITAL,
    )


def dragon_tiger_codec() -> AShareProviderCacheCodec[tuple[DragonTigerRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_DRAGON_TIGER,
        _encode_dragons,
        _decode_dragons,
        expected_category=DataCategory.CAPITAL,
    )


def margin_codec() -> AShareProviderCacheCodec[tuple[MarginRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_MARGIN,
        _encode_margins,
        _decode_margins,
        expected_category=DataCategory.CAPITAL,
    )


def block_trades_codec() -> AShareProviderCacheCodec[tuple[BlockTradeRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_BLOCK_TRADES,
        _encode_blocks,
        _decode_blocks,
        expected_category=DataCategory.CAPITAL,
    )


def shareholder_counts_codec() -> AShareProviderCacheCodec[tuple[ShareholderCountRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_SHAREHOLDER_COUNTS,
        _encode_holders,
        _decode_holders,
        expected_category=DataCategory.CAPITAL,
    )


def chip_distribution_codec() -> AShareProviderCacheCodec[ChipDistributionSnapshot]:
    return AShareProviderCacheCodec(
        CODEC_CHIP_DISTRIBUTION,
        _encode_chip,
        _decode_chip,
        expected_category=DataCategory.CAPITAL,
    )


E4A_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_INTRADAY_FLOW: intraday_flow_codec,  # type: ignore[dict-item]
    CODEC_DAILY_FLOW: daily_flow_codec,  # type: ignore[dict-item]
    CODEC_NORTHBOUND: northbound_codec,  # type: ignore[dict-item]
    CODEC_DRAGON_TIGER: dragon_tiger_codec,  # type: ignore[dict-item]
    CODEC_MARGIN: margin_codec,  # type: ignore[dict-item]
    CODEC_BLOCK_TRADES: block_trades_codec,  # type: ignore[dict-item]
    CODEC_SHAREHOLDER_COUNTS: shareholder_counts_codec,  # type: ignore[dict-item]
    CODEC_CHIP_DISTRIBUTION: chip_distribution_codec,  # type: ignore[dict-item]
}


# ---------------------------------------------------------------------------

__all__ = [
    "CODEC_BLOCK_TRADES",
    "CODEC_CHIP_DISTRIBUTION",
    "CODEC_DAILY_FLOW",
    "CODEC_DRAGON_TIGER",
    "CODEC_INTRADAY_FLOW",
    "CODEC_MARGIN",
    "CODEC_NORTHBOUND",
    "CODEC_SHAREHOLDER_COUNTS",
    "E4A_CODEC_IDS",
    "E4A_CODECS",
    "block_trades_codec",
    "chip_distribution_codec",
    "daily_flow_codec",
    "dragon_tiger_codec",
    "intraday_flow_codec",
    "margin_codec",
    "northbound_codec",
    "shareholder_counts_codec",
]
