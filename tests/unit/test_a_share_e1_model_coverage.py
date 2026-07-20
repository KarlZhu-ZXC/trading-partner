"""Phase 1E E1: exhaustive domain model + DTO coverage and field inventory."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.dto import a_share as a_share_dto
from domain.a_share import enums as a_share_enums
from domain.a_share import models as a_share_models
from domain.a_share.enums import (
    BarInterval,
    FinancialStatementType,
    LimitPoolType,
    OptionType,
    SentimentSourceType,
    TickDirection,
)
from domain.a_share.models import (
    AnalystReportItem,
    AnnouncementItem,
    AShareBar,
    AShareQuote,
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    ConsensusEstimate,
    DividendRecord,
    DragonTigerRecord,
    DragonTigerSeat,
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    FundFlowPoint,
    IndustryPerformanceRow,
    InteractiveQAItem,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    MarginRecord,
    MarketBoardSnapshot,
    NewsItem,
    NorthboundFlowPoint,
    OptionGreeks,
    OrderBookLevel,
    SentimentSignal,
    ShareholderCountRecord,
    TradeTick,
    TradingSessionWindow,
    UnlockRecord,
)
from domain.common.enums import (
    AdjustmentMethod,
    ReliabilityLevel,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2024, 1, 10, 10, 0, tzinfo=SH)
DAY = date(2024, 1, 10)
EQUITY = "equity:A_SHARE:600519.SH"
ETF = "etf:A_SHARE:510050.SH"
INDEX = "index:A_SHARE:000001.SH"
OPTION = "option:A_SHARE:510050C2403M00300"
US_EQUITY = "equity:US:NVDA"

# Design §4 / §17 frozen field inventory (name → field names).
DESIGN_FIELD_INVENTORY: dict[str, frozenset[str]] = {
    "AShareQuote": frozenset(
        {
            "instrument_id",
            "quote_at",
            "session",
            "last",
            "open",
            "high",
            "low",
            "previous_close",
            "change",
            "change_percent",
            "volume_shares",
            "turnover_amount_cny",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "total_market_cap_cny",
            "float_market_cap_cny",
            "limit_up_price",
            "limit_down_price",
        }
    ),
    "OrderBookLevel": frozenset(
        {"level", "bid_price", "bid_volume_shares", "ask_price", "ask_volume_shares"}
    ),
    "TradeTick": frozenset({"occurred_at", "price", "volume_shares", "direction"}),
    "AShareBar": frozenset(
        {
            "start_at",
            "end_at",
            "interval",
            "open",
            "high",
            "low",
            "close",
            "volume_shares",
            "turnover_amount_cny",
            "adjustment",
        }
    ),
    "FundamentalMetric": frozenset({"name", "value", "unit", "period_end", "published_at"}),
    "FinancialStatementLine": frozenset(
        {
            "statement_type",
            "period_end",
            "published_at",
            "item_code",
            "item_name",
            "value",
            "unit",
        }
    ),
    "F10Section": frozenset({"section", "title", "body", "as_of"}),
    "AnalystReportItem": frozenset(
        {
            "report_key",
            "title",
            "institution",
            "analyst_names",
            "published_at",
            "rating",
            "target_price",
            "eps_forecasts",
            "source_url",
            "pdf_url",
        }
    ),
    "ConsensusEstimate": frozenset(
        {"fiscal_year", "metric", "mean", "high", "low", "institution_count"}
    ),
    "AnnouncementItem": frozenset(
        {
            "announcement_key",
            "title",
            "published_at",
            "category",
            "source_url",
            "pdf_url",
        }
    ),
    "NewsItem": frozenset(
        {"news_key", "title", "summary", "published_at", "source_name", "source_url"}
    ),
    "InteractiveQAItem": frozenset(
        {"qa_key", "question", "asked_at", "answer", "answered_at", "source_url"}
    ),
    "IndustryPerformanceRow": frozenset(
        {
            "industry_code",
            "industry_name",
            "trade_date",
            "change_percent",
            "advancing_count",
            "declining_count",
            "unchanged_count",
            "leading_instrument_id",
            "leading_change_percent",
            "turnover_amount_cny",
        }
    ),
    "MarketBoardSnapshot": frozenset(
        {
            "trade_date",
            "advancing_count",
            "declining_count",
            "unchanged_count",
            "limit_up_count",
            "limit_down_count",
            "broken_limit_count",
            "total_turnover_cny",
            "median_change_percent",
            "industries",
        }
    ),
    "FundFlowPoint": frozenset(
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
    ),
    "NorthboundFlowPoint": frozenset(
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
    ),
    "DragonTigerSeat": frozenset({"rank", "side", "branch_name", "amount_cny", "is_institution"}),
    "DragonTigerRecord": frozenset(
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
    ),
    "MarginRecord": frozenset(
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
    ),
    "BlockTradeRecord": frozenset(
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
    ),
    "ShareholderCountRecord": frozenset(
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
    ),
    "ChipDistributionBin": frozenset({"price_low", "price_high", "holding_ratio"}),
    "ChipDistributionSnapshot": frozenset(
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
    ),
    "UnlockRecord": frozenset(
        {
            "unlock_date",
            "published_at",
            "unlock_type",
            "unlock_shares",
            "tradable_shares",
            "market_value_cny",
            "source_vendor",
            "reliability",
            "is_authoritative",
        }
    ),
    "DividendRecord": frozenset(
        {
            "fiscal_year",
            "plan_status",
            "ex_date",
            "cash_per_share",
            "bonus_shares_per_share",
            "transfer_shares_per_share",
            "published_at",
            "source_vendor",
            "reliability",
            "is_authoritative",
        }
    ),
    "LimitPoolEntry": frozenset(
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
    ),
    "LimitUpContext": frozenset(
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
    ),
    "LimitUpLadderRung": frozenset(
        {"consecutive_limit_count", "instrument_count", "instrument_ids"}
    ),
    "SentimentSignal": frozenset(
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
    ),
    "EtfOptionContract": frozenset(
        {
            "instrument_id",
            "underlying_instrument_id",
            "option_type",
            "expiry",
            "strike",
            "multiplier",
        }
    ),
    "EtfOptionQuote": frozenset(
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
    ),
    "OptionGreeks": frozenset(
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
    ),
    "EtfOptionSnapshot": frozenset({"underlying_instrument_id", "expiry", "quotes", "greeks"}),
    "TradingSessionWindow": frozenset({"session", "start_at", "end_at"}),
}


def _vendor_kwargs() -> dict[str, object]:
    return {
        "source_vendor": VendorId.EASTMONEY,
        "reliability": ReliabilityLevel.MEDIUM,
        "is_authoritative": False,
    }


def _quote(**overrides: object) -> AShareQuote:
    base: dict[str, object] = {
        "instrument_id": EQUITY,
        "quote_at": NOW,
        "session": TradingSession.REGULAR,
        "last": Decimal("100"),
        "open": Decimal("99"),
        "high": Decimal("101"),
        "low": Decimal("98"),
        "previous_close": Decimal("99"),
        "change": Decimal("1"),
        "change_percent": Decimal("1.01"),
        "volume_shares": 1000,
        "turnover_amount_cny": Decimal("100000"),
        "turnover_rate": Decimal("0.01"),
        "pe_ttm": Decimal("20"),
        "pb": Decimal("5"),
        "total_market_cap_cny": Decimal("1e12"),
        "float_market_cap_cny": Decimal("1e12"),
        "limit_up_price": Decimal("110"),
        "limit_down_price": Decimal("90"),
    }
    base.update(overrides)
    return AShareQuote(**base)  # type: ignore[arg-type]


def _contract(**overrides: object) -> EtfOptionContract:
    base: dict[str, object] = {
        "instrument_id": OPTION,
        "underlying_instrument_id": ETF,
        "option_type": OptionType.CALL,
        "expiry": date(2024, 3, 27),
        "strike": Decimal("3.0"),
        "multiplier": Decimal("10000"),
    }
    base.update(overrides)
    return EtfOptionContract(**base)  # type: ignore[arg-type]


def _all_domain_instances() -> dict[str, object]:
    seat = DragonTigerSeat(
        rank=1,
        side="buy",
        branch_name="branch-a",
        amount_cny=Decimal("100"),
        is_institution=False,
    )
    bin_row = ChipDistributionBin(
        price_low=Decimal("10"),
        price_high=Decimal("11"),
        holding_ratio=Decimal("1"),
    )
    estimate = ConsensusEstimate(
        fiscal_year=2024,
        metric="eps",
        mean=Decimal("1.2"),
        high=Decimal("1.5"),
        low=Decimal("1.0"),
        institution_count=10,
    )
    contract = _contract()
    option_quote = EtfOptionQuote(
        contract=contract,
        quote_at=NOW,
        last=Decimal("0.2"),
        bid_prices=(Decimal("0.19"),),
        bid_volumes=(10,),
        ask_prices=(Decimal("0.21"),),
        ask_volumes=(12,),
        volume_contracts=100,
        open_interest=50,
    )
    greeks = OptionGreeks(
        contract_instrument_id=OPTION,
        as_of=NOW,
        delta=Decimal("0.5"),
        gamma=Decimal("0.1"),
        theta=Decimal("-0.01"),
        vega=Decimal("0.2"),
        implied_volatility=Decimal("0.3"),
        theoretical_value=Decimal("0.2"),
        source_provided=True,
    )
    industry = IndustryPerformanceRow(
        industry_code="801780",
        industry_name="banks",
        trade_date=DAY,
        change_percent=Decimal("1.2"),
        advancing_count=10,
        declining_count=5,
        unchanged_count=1,
        leading_instrument_id=EQUITY,
        leading_change_percent=Decimal("3.0"),
        turnover_amount_cny=Decimal("1e9"),
    )
    entry = LimitPoolEntry(
        pool_type=LimitPoolType.LIMIT_UP,
        trade_date=DAY,
        instrument_id=EQUITY,
        name="Moutai",
        last=Decimal("1700"),
        change_percent=Decimal("10"),
        consecutive_limit_count=2,
        days_and_boards="2/2",
        first_seal_at=NOW,
        last_seal_at=NOW + timedelta(minutes=30),
        seal_amount_cny=Decimal("1e8"),
        broken_count=0,
        industry="liquor",
        reason_tags=("earnings",),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
    )
    rung = LimitUpLadderRung(
        consecutive_limit_count=2,
        instrument_count=1,
        instrument_ids=(EQUITY,),
    )
    return {
        "AShareQuote": _quote(),
        "OrderBookLevel": OrderBookLevel(
            level=1,
            bid_price=Decimal("10"),
            bid_volume_shares=100,
            ask_price=Decimal("10.1"),
            ask_volume_shares=50,
        ),
        "TradeTick": TradeTick(
            occurred_at=NOW,
            price=Decimal("100"),
            volume_shares=10,
            direction=TickDirection.BUY,
        ),
        "AShareBar": AShareBar(
            start_at=NOW,
            end_at=NOW + timedelta(hours=1),
            interval=BarInterval.ONE_DAY,
            open=Decimal("10"),
            high=Decimal("12"),
            low=Decimal("9"),
            close=Decimal("11"),
            volume_shares=1000,
            turnover_amount_cny=Decimal("11000"),
            adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        ),
        "FundamentalMetric": FundamentalMetric(
            name="roe",
            value=Decimal("0.2"),
            unit="ratio",
            period_end=DAY,
            published_at=NOW,
        ),
        "FinancialStatementLine": FinancialStatementLine(
            statement_type=FinancialStatementType.INCOME_STATEMENT,
            period_end=DAY,
            published_at=NOW,
            item_code="rev",
            item_name="revenue",
            value=Decimal("1e9"),
            unit="CNY",
        ),
        "F10Section": F10Section(
            section="company",
            title="Overview",
            body="body",
            as_of=NOW,
        ),
        "ConsensusEstimate": estimate,
        "AnalystReportItem": AnalystReportItem(
            report_key="r1",
            title="note",
            institution="broker",
            analyst_names=("a",),
            published_at=NOW,
            rating="buy",
            target_price=Decimal("2000"),
            eps_forecasts=(estimate,),
            source_url="https://example.invalid/r",
            pdf_url=None,
        ),
        "AnnouncementItem": AnnouncementItem(
            announcement_key="a1",
            title="ann",
            published_at=NOW,
            category="periodic",
            source_url="https://example.invalid/a",
            pdf_url=None,
        ),
        "NewsItem": NewsItem(
            news_key="n1",
            title="news",
            summary="s",
            published_at=NOW,
            source_name="cls",
            source_url=None,
        ),
        "InteractiveQAItem": InteractiveQAItem(
            qa_key="q1",
            question="q?",
            asked_at=NOW,
            answer="a",
            answered_at=NOW + timedelta(hours=1),
            source_url=None,
        ),
        "IndustryPerformanceRow": industry,
        "MarketBoardSnapshot": MarketBoardSnapshot(
            trade_date=DAY,
            advancing_count=100,
            declining_count=50,
            unchanged_count=10,
            limit_up_count=5,
            limit_down_count=1,
            broken_limit_count=2,
            total_turnover_cny=Decimal("1e12"),
            median_change_percent=Decimal("0.5"),
            industries=(industry,),
        ),
        "FundFlowPoint": FundFlowPoint(
            occurred_at=NOW,
            interval=BarInterval.ONE_DAY,
            main_net_cny=Decimal("1"),
            super_large_net_cny=None,
            large_net_cny=None,
            medium_net_cny=None,
            small_net_cny=None,
            **_vendor_kwargs(),
        ),
        "NorthboundFlowPoint": NorthboundFlowPoint(
            trade_date=DAY,
            channel="sh",
            net_buy_cny=Decimal("1"),
            buy_cny=Decimal("2"),
            sell_cny=Decimal("1"),
            disclosure_note=None,
            **_vendor_kwargs(),
        ),
        "DragonTigerSeat": seat,
        "DragonTigerRecord": DragonTigerRecord(
            trade_date=DAY,
            instrument_id=EQUITY,
            reason="limit up",
            buy_total_cny=Decimal("100"),
            sell_total_cny=Decimal("40"),
            net_buy_cny=Decimal("60"),
            seats=(seat,),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=True,
        ),
        "MarginRecord": MarginRecord(
            trade_date=DAY,
            financing_balance_cny=Decimal("1e9"),
            financing_buy_cny=Decimal("1e8"),
            financing_repayment_cny=Decimal("5e7"),
            securities_lending_balance_cny=None,
            securities_lending_sell_shares=None,
            **_vendor_kwargs(),
        ),
        "BlockTradeRecord": BlockTradeRecord(
            trade_date=DAY,
            price=Decimal("100"),
            volume_shares=1000,
            amount_cny=Decimal("100000"),
            premium_percent=Decimal("-1"),
            buyer_branch=None,
            seller_branch=None,
            **_vendor_kwargs(),
        ),
        "ShareholderCountRecord": ShareholderCountRecord(
            period_end=DAY,
            published_at=NOW,
            shareholder_count=100_000,
            change_percent=Decimal("-1"),
            average_holding_shares=Decimal("1000"),
            **_vendor_kwargs(),
        ),
        "ChipDistributionBin": bin_row,
        "ChipDistributionSnapshot": ChipDistributionSnapshot(
            as_of=NOW,
            bins=(bin_row,),
            profit_ratio=Decimal("0.5"),
            average_cost=Decimal("10.5"),
            concentration_90=Decimal("0.9"),
            concentration_70=Decimal("0.7"),
            calculation_method="turnover_decay_uniform_range",
            algorithm_version="tp_chip_v1",
            lookback_sessions=120,
            input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
            bar_trade_date=DAY,
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.LOW,
            is_authoritative=False,
        ),
        "UnlockRecord": UnlockRecord(
            unlock_date=DAY,
            published_at=NOW,
            unlock_type="restricted",
            unlock_shares=1_000_000,
            tradable_shares=1_000_000,
            market_value_cny=Decimal("1e9"),
            **_vendor_kwargs(),
        ),
        "DividendRecord": DividendRecord(
            fiscal_year=2023,
            plan_status="implemented",
            ex_date=DAY,
            cash_per_share=Decimal("1.5"),
            bonus_shares_per_share=None,
            transfer_shares_per_share=None,
            published_at=NOW,
            **_vendor_kwargs(),
        ),
        "LimitPoolEntry": entry,
        "LimitUpLadderRung": rung,
        "LimitUpContext": LimitUpContext(
            trade_date=DAY,
            entries=(entry,),
            limit_up_count=5,
            limit_down_count=1,
            broken_limit_count=2,
            broken_rate=Decimal("0.1"),
            max_consecutive_count=5,
            promotion_rate=Decimal("0.3"),
            ladder=(rung,),
        ),
        "SentimentSignal": SentimentSignal(
            source_type=SentimentSourceType.THS_HOT,
            trade_date=DAY,
            instrument_id=EQUITY,
            rank=1,
            rank_change=0,
            heat_value=Decimal("99"),
            concept_tags=("ai",),
            label="hot",
            source_vendor=VendorId.THS,
            reliability=ReliabilityLevel.LOW,
            is_authoritative=False,
        ),
        "EtfOptionContract": contract,
        "EtfOptionQuote": option_quote,
        "OptionGreeks": greeks,
        "EtfOptionSnapshot": EtfOptionSnapshot(
            underlying_instrument_id=ETF,
            expiry=date(2024, 3, 27),
            quotes=(option_quote,),
            greeks=(greeks,),
        ),
        "TradingSessionWindow": TradingSessionWindow(
            session=TradingSession.REGULAR,
            start_at=NOW,
            end_at=NOW + timedelta(hours=2),
        ),
    }


def test_every_frozen_domain_model_instantiated_once() -> None:
    instances = _all_domain_instances()
    module_classes = {
        name: obj
        for name, obj in vars(a_share_models).items()
        if isinstance(obj, type) and is_dataclass(obj) and obj.__module__ == a_share_models.__name__
    }
    assert set(instances) == set(module_classes)
    for name, instance in instances.items():
        assert isinstance(instance, module_classes[name])


def test_design_field_inventory_matches_dataclasses() -> None:
    module_classes = {
        name: obj
        for name, obj in vars(a_share_models).items()
        if isinstance(obj, type) and is_dataclass(obj) and obj.__module__ == a_share_models.__name__
    }
    assert set(DESIGN_FIELD_INVENTORY) == set(module_classes)
    for name, expected in DESIGN_FIELD_INVENTORY.items():
        actual = {f.name for f in fields(module_classes[name])}
        assert actual == expected, f"{name}: {actual ^ expected}"


def test_every_output_dto_from_domain_path() -> None:
    instances = _all_domain_instances()
    # DTO classes with from_domain (exclude composite product DTO).
    dto_pairs: list[tuple[type, str]] = []
    for name, obj in vars(a_share_dto).items():
        if not (isinstance(obj, type) and name.endswith("DTO")):
            continue
        if name == "AShareCompositeSnapshotDTO":
            continue
        from_domain = getattr(obj, "from_domain", None)
        if from_domain is None:
            continue
        domain_name = name[: -len("DTO")]
        dto_pairs.append((obj, domain_name))

    assert dto_pairs, "expected output DTOs with from_domain"
    missing = [dn for _, dn in dto_pairs if dn not in instances]
    assert not missing, f"missing domain fixtures for DTOs: {missing}"

    for dto_cls, domain_name in dto_pairs:
        domain_obj = instances[domain_name]
        kwargs = {"provenance": ()} if dto_cls.__name__ == "EtfOptionSnapshotDTO" else {}
        dto = dto_cls.from_domain(domain_obj, **kwargs)
        assert isinstance(dto, dto_cls)


def test_sentiment_signal_rejects_authoritative() -> None:
    with pytest.raises(DataContractError, match="is_authoritative|authoritative"):
        SentimentSignal(
            source_type=SentimentSourceType.THS_HOT,
            trade_date=DAY,
            instrument_id=EQUITY,
            rank=1,
            rank_change=None,
            heat_value=None,
            concept_tags=(),
            label=None,
            source_vendor=VendorId.THS,
            reliability=ReliabilityLevel.LOW,
            is_authoritative=True,
        )


def test_a_share_identity_us_and_wrong_assets() -> None:
    # Quote: US market rejected; OPTION rejected; ETF/INDEX allowed.
    with pytest.raises(DataContractError, match="A_SHARE|market"):
        _quote(instrument_id=US_EQUITY)
    with pytest.raises(DataContractError, match="asset type"):
        _quote(instrument_id=OPTION)
    _quote(instrument_id=ETF)
    _quote(instrument_id=INDEX)

    # Dragon tiger / limit pool: equity only.
    with pytest.raises(DataContractError, match="asset type|A_SHARE"):
        DragonTigerRecord(
            trade_date=DAY,
            instrument_id=ETF,
            reason="x",
            buy_total_cny=Decimal("1"),
            sell_total_cny=Decimal("0"),
            net_buy_cny=Decimal("1"),
            seats=(),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=True,
        )
    with pytest.raises(DataContractError, match="asset type|A_SHARE"):
        LimitPoolEntry(
            pool_type=LimitPoolType.LIMIT_UP,
            trade_date=DAY,
            instrument_id=US_EQUITY,
            name="x",
            last=Decimal("1"),
            change_percent=Decimal("1"),
            consecutive_limit_count=None,
            days_and_boards=None,
            first_seal_at=None,
            last_seal_at=None,
            seal_amount_cny=None,
            broken_count=None,
            industry=None,
            reason_tags=(),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
        )

    # Option contract identity.
    with pytest.raises(DataContractError, match="asset type"):
        _contract(instrument_id=EQUITY)
    with pytest.raises(DataContractError, match="asset type"):
        _contract(underlying_instrument_id=EQUITY)
    with pytest.raises(DataContractError, match="A_SHARE|market"):
        _contract(underlying_instrument_id="etf:US:SPY")

    # Snapshot underlying ETF + expiry match.
    contract = _contract(expiry=date(2024, 3, 27))
    quote = EtfOptionQuote(
        contract=contract,
        quote_at=NOW,
        last=None,
        bid_prices=(),
        bid_volumes=(),
        ask_prices=(),
        ask_volumes=(),
        volume_contracts=None,
        open_interest=None,
    )
    with pytest.raises(DataContractError, match="asset type"):
        EtfOptionSnapshot(
            underlying_instrument_id=EQUITY,
            expiry=date(2024, 3, 27),
            quotes=(quote,),
            greeks=(),
        )
    with pytest.raises(DataContractError, match="expiry"):
        EtfOptionSnapshot(
            underlying_instrument_id=ETF,
            expiry=date(2024, 4, 24),
            quotes=(quote,),
            greeks=(),
        )


def test_family_representative_validation_failures() -> None:
    # Market structure family: float + naive datetime + range.
    with pytest.raises(DataContractError, match="float"):
        _quote(last=1.5)  # type: ignore[arg-type]
    with pytest.raises(DataContractError, match="timezone-aware|aware"):
        _quote(quote_at=datetime(2024, 1, 10, 10, 0))
    with pytest.raises(DataContractError, match="high"):
        _quote(high=Decimal("90"), low=Decimal("100"), last=Decimal("95"))

    # Bar OHLC family range.
    with pytest.raises(DataContractError, match="high|low"):
        AShareBar(
            start_at=NOW,
            end_at=NOW + timedelta(hours=1),
            interval=BarInterval.ONE_DAY,
            open=Decimal("10"),
            high=Decimal("9"),
            low=Decimal("8"),
            close=Decimal("9"),
            volume_shares=1,
            turnover_amount_cny=None,
            adjustment=AdjustmentMethod.NONE,
        )

    # Order book uniqueness / range.
    with pytest.raises(DataContractError, match="1..5"):
        OrderBookLevel(
            level=0,
            bid_price=None,
            bid_volume_shares=None,
            ask_price=None,
            ask_volume_shares=None,
        )

    # Capital family: ratio + net consistency.
    with pytest.raises(DataContractError, match=r"\[0, 1\]"):
        ChipDistributionBin(
            price_low=Decimal("1"),
            price_high=Decimal("2"),
            holding_ratio=Decimal("2"),
        )
    with pytest.raises(DataContractError, match="net_buy"):
        DragonTigerRecord(
            trade_date=DAY,
            instrument_id=EQUITY,
            reason="x",
            buy_total_cny=Decimal("10"),
            sell_total_cny=Decimal("1"),
            net_buy_cny=Decimal("0"),
            seats=(),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=False,
        )

    # Research family: consensus whitelist.
    with pytest.raises(DataContractError, match="eps|revenue|net_income"):
        ConsensusEstimate(
            fiscal_year=2024,
            metric="ebitda",
            mean=None,
            high=None,
            low=None,
            institution_count=None,
        )

    # Limit family uniqueness.
    entry = LimitPoolEntry(
        pool_type=LimitPoolType.LIMIT_UP,
        trade_date=DAY,
        instrument_id=EQUITY,
        name="x",
        last=Decimal("1"),
        change_percent=Decimal("1"),
        consecutive_limit_count=None,
        days_and_boards=None,
        first_seal_at=None,
        last_seal_at=None,
        seal_amount_cny=None,
        broken_count=None,
        industry=None,
        reason_tags=(),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
    )
    with pytest.raises(DataContractError, match="unique"):
        LimitUpContext(
            trade_date=DAY,
            entries=(entry, entry),
            limit_up_count=1,
            limit_down_count=0,
            broken_limit_count=0,
            broken_rate=None,
            max_consecutive_count=None,
            promotion_rate=None,
            ladder=(),
        )

    # Options family: positive strike + source_provided.
    with pytest.raises(DataContractError, match="positive"):
        _contract(strike=Decimal("0"))
    with pytest.raises(DataContractError, match="source_provided"):
        OptionGreeks(
            contract_instrument_id=OPTION,
            as_of=NOW,
            delta=None,
            gamma=None,
            theta=None,
            vega=None,
            implied_volatility=None,
            theoretical_value=None,
            source_provided=False,
        )

    # Calendar window range.
    with pytest.raises(DataContractError, match="end_at"):
        TradingSessionWindow(
            session=TradingSession.REGULAR,
            start_at=NOW,
            end_at=NOW,
        )


def test_enums_module_export_sanity() -> None:
    assert a_share_enums.CapitalMetricType.DIVIDEND.value == "dividend"
    assert a_share_enums.OptionType.PUT.value == "put"
