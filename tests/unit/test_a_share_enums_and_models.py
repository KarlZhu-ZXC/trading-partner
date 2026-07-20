"""Phase 1E E1: A-share enum wire preservation and domain model validation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from domain.a_share.enums import (
    AShareMarketScope,
    AShareSnapshotDetail,
    BarInterval,
    CapitalMetricType,
    FinancialStatementType,
    LimitPoolType,
    OptionType,
    SentimentSourceType,
    TickDirection,
)
from domain.a_share.models import (
    AShareBar,
    AShareQuote,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    ConsensusEstimate,
    DragonTigerRecord,
    DragonTigerSeat,
    EtfOptionContract,
    FundFlowPoint,
    OrderBookLevel,
    TradeTick,
    validate_order_book_levels,
)
from domain.common.enums import (
    AdjustmentMethod,
    DataCategory,
    ReliabilityLevel,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2024, 1, 10, 10, 0, tzinfo=SH)
INSTRUMENT = "equity:A_SHARE:600519.SH"


def test_adjustment_method_append_only_wire_values() -> None:
    assert AdjustmentMethod.NONE.value == "none"
    assert AdjustmentMethod.SPLIT_ADJUSTED.value == "split_adjusted"
    assert AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED.value == "split_and_dividend_adjusted"
    assert AdjustmentMethod.FORWARD_ADJUSTED.value == "forward_adjusted"
    assert AdjustmentMethod.BACKWARD_ADJUSTED.value == "backward_adjusted"


def test_vendor_id_phase1e_append_only_wire_values() -> None:
    expected = {
        VendorId.SINA: "sina",
        VendorId.CNINFO: "cninfo",
        VendorId.THS: "ths",
        VendorId.CLS: "cls",
        VendorId.SSE: "sse",
        VendorId.SZSE: "szse",
        VendorId.HKEX: "hkex",
        VendorId.IWENCAI: "iwencai",
        VendorId.EASTMONEY: "eastmoney",
        VendorId.TENCENT: "tencent",
        VendorId.A_SHARE_FALLBACK: "a_share_fallback",
    }
    for member, wire in expected.items():
        assert member.value == wire
        assert VendorId(wire) is member


def test_data_category_phase1e_append_only_wire_values() -> None:
    expected = {
        DataCategory.MARKET_STRUCTURE: "market_structure",
        DataCategory.RESEARCH_REPORTS: "research_reports",
        DataCategory.INTERACTIVE_QA: "interactive_qa",
        DataCategory.CORPORATE_ACTIONS: "corporate_actions",
    }
    for member, wire in expected.items():
        assert member.value == wire
        assert DataCategory(wire) is member


def test_a_share_business_enum_wire_values() -> None:
    assert AShareSnapshotDetail.SUMMARY.value == "summary"
    assert AShareSnapshotDetail.FULL.value == "full"
    assert AShareMarketScope.INSTRUMENT.value == "instrument"
    assert BarInterval.ONE_DAY.value == "1d"
    assert BarInterval.ONE_MONTH.value == "1mo"
    assert TickDirection.BUY.value == "buy"
    assert FinancialStatementType.BALANCE_SHEET.value == "balance_sheet"
    assert CapitalMetricType.DAILY_FLOW.value == "daily_flow"
    assert CapitalMetricType.DRAGON_TIGER.value == "dragon_tiger"
    assert LimitPoolType.BROKEN_LIMIT.value == "broken_limit"
    assert SentimentSourceType.THS_HOT.value == "ths_hot"
    assert OptionType.CALL.value == "call"
    # Frozen capital metric enum order for default merge.
    assert [m.value for m in CapitalMetricType] == [
        "intraday_flow",
        "daily_flow",
        "northbound",
        "dragon_tiger",
        "margin",
        "block_trade",
        "shareholder_count",
        "chip_distribution",
        "unlock",
        "dividend",
    ]


def test_ashare_quote_rejects_float_and_naive_time() -> None:
    with pytest.raises(DataContractError, match="float"):
        AShareQuote(
            instrument_id=INSTRUMENT,
            quote_at=NOW,
            session=TradingSession.REGULAR,
            last=1.5,  # type: ignore[arg-type]
            open=None,
            high=None,
            low=None,
            previous_close=None,
            change=None,
            change_percent=None,
            volume_shares=None,
            turnover_amount_cny=None,
            turnover_rate=None,
            pe_ttm=None,
            pb=None,
            total_market_cap_cny=None,
            float_market_cap_cny=None,
            limit_up_price=None,
            limit_down_price=None,
        )
    with pytest.raises(DataContractError, match="timezone-aware"):
        AShareQuote(
            instrument_id=INSTRUMENT,
            quote_at=datetime(2024, 1, 10, 10, 0),
            session=TradingSession.REGULAR,
            last=Decimal("100"),
            open=None,
            high=None,
            low=None,
            previous_close=None,
            change=None,
            change_percent=None,
            volume_shares=None,
            turnover_amount_cny=None,
            turnover_rate=None,
            pe_ttm=None,
            pb=None,
            total_market_cap_cny=None,
            float_market_cap_cny=None,
            limit_up_price=None,
            limit_down_price=None,
        )


def test_order_book_level_range_and_uniqueness() -> None:
    with pytest.raises(DataContractError, match="1..5"):
        OrderBookLevel(
            level=0,
            bid_price=None,
            bid_volume_shares=None,
            ask_price=None,
            ask_volume_shares=None,
        )
    levels = (
        OrderBookLevel(
            level=1,
            bid_price=Decimal("10"),
            bid_volume_shares=100,
            ask_price=Decimal("10.1"),
            ask_volume_shares=50,
        ),
        OrderBookLevel(
            level=2,
            bid_price=Decimal("9.9"),
            bid_volume_shares=200,
            ask_price=Decimal("10.2"),
            ask_volume_shares=60,
        ),
    )
    validate_order_book_levels(levels)
    with pytest.raises(DataContractError, match="unique"):
        validate_order_book_levels((levels[0], levels[0]))


def test_ashare_bar_ohlc_and_adjustment() -> None:
    bar = AShareBar(
        start_at=datetime(2024, 1, 10, 0, 0, tzinfo=SH),
        end_at=datetime(2024, 1, 10, 15, 0, tzinfo=SH),
        interval=BarInterval.ONE_DAY,
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal("11"),
        volume_shares=1000,
        turnover_amount_cny=Decimal("11000"),
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
    )
    assert bar.adjustment is AdjustmentMethod.FORWARD_ADJUSTED
    with pytest.raises(DataContractError, match="high"):
        AShareBar(
            start_at=bar.start_at,
            end_at=bar.end_at,
            interval=BarInterval.ONE_DAY,
            open=Decimal("10"),
            high=Decimal("9"),
            low=Decimal("8"),
            close=Decimal("9.5"),
            volume_shares=1,
            turnover_amount_cny=None,
            adjustment=AdjustmentMethod.NONE,
        )


def test_trade_tick_and_fund_flow() -> None:
    tick = TradeTick(
        occurred_at=NOW,
        price=Decimal("100.5"),
        volume_shares=100,
        direction=TickDirection.BUY,
    )
    assert tick.direction is TickDirection.BUY
    flow = FundFlowPoint(
        occurred_at=NOW,
        interval=BarInterval.ONE_DAY,
        main_net_cny=Decimal("1"),
        super_large_net_cny=None,
        large_net_cny=None,
        medium_net_cny=None,
        small_net_cny=None,
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.MEDIUM,
        is_authoritative=False,
    )
    assert flow.source_vendor is VendorId.EASTMONEY


def test_dragon_tiger_net_consistency() -> None:
    with pytest.raises(DataContractError, match="net_buy"):
        DragonTigerRecord(
            trade_date=date(2024, 1, 10),
            instrument_id=INSTRUMENT,
            reason="limit up",
            buy_total_cny=Decimal("100"),
            sell_total_cny=Decimal("40"),
            net_buy_cny=Decimal("50"),
            seats=(
                DragonTigerSeat(
                    rank=1,
                    side="buy",
                    branch_name="branch",
                    amount_cny=Decimal("100"),
                    is_institution=False,
                ),
            ),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=True,
        )


def test_chip_bins_order_and_ratio() -> None:
    with pytest.raises(DataContractError, match=r"\[0, 1\]"):
        ChipDistributionBin(
            price_low=Decimal("10"),
            price_high=Decimal("11"),
            holding_ratio=Decimal("1.5"),
        )
    ChipDistributionSnapshot(
        as_of=NOW,
        bins=(
            ChipDistributionBin(
                price_low=Decimal("10"),
                price_high=Decimal("11"),
                holding_ratio=Decimal("0.4"),
            ),
            ChipDistributionBin(
                price_low=Decimal("11"),
                price_high=Decimal("12"),
                holding_ratio=Decimal("0.6"),
            ),
        ),
        profit_ratio=Decimal("0.5"),
        average_cost=Decimal("10.5"),
        concentration_90=Decimal("0.9"),
        concentration_70=Decimal("0.7"),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
        calculation_method="turnover_decay_uniform_range",
        algorithm_version="tp_chip_v1",
        lookback_sessions=120,
        input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        bar_trade_date=NOW.date(),
    )


def test_consensus_metric_whitelist() -> None:
    with pytest.raises(DataContractError, match="eps|revenue|net_income"):
        ConsensusEstimate(
            fiscal_year=2024,
            metric="ebitda",
            mean=None,
            high=None,
            low=None,
            institution_count=None,
        )


def test_option_contract_positive_strike() -> None:
    EtfOptionContract(
        instrument_id="option:A_SHARE:510050C2403M00300",
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        option_type=OptionType.CALL,
        expiry=date(2024, 3, 27),
        strike=Decimal("3.0"),
        multiplier=Decimal("10000"),
    )
    with pytest.raises(DataContractError, match="positive"):
        EtfOptionContract(
            instrument_id="option:A_SHARE:510050C2403M00300",
            underlying_instrument_id="etf:A_SHARE:510050.SH",
            option_type=OptionType.CALL,
            expiry=date(2024, 3, 27),
            strike=Decimal("0"),
            multiplier=None,
        )


def test_quote_valid_construct() -> None:
    quote = AShareQuote(
        instrument_id=INSTRUMENT,
        quote_at=NOW,
        session=TradingSession.REGULAR,
        last=Decimal("1700.00"),
        open=Decimal("1690.00"),
        high=Decimal("1710.00"),
        low=Decimal("1680.00"),
        previous_close=Decimal("1685.00"),
        change=Decimal("15.00"),
        change_percent=Decimal("0.89"),
        volume_shares=1_000_000,
        turnover_amount_cny=Decimal("1700000000"),
        turnover_rate=Decimal("0.01"),
        pe_ttm=Decimal("30"),
        pb=Decimal("8"),
        total_market_cap_cny=Decimal("2e12"),
        float_market_cap_cny=Decimal("2e12"),
        limit_up_price=Decimal("1853.50"),
        limit_down_price=Decimal("1516.50"),
    )
    assert quote.last == Decimal("1700.00")
