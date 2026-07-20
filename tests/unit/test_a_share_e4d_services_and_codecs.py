"""E4d v2 model/DTO/cache contract tests (no provider network)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from application.dto.a_share import ChipDistributionSnapshotDTO, SentimentSignalDTO
from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from application.services.a_share_capital_service import AShareCapitalService
from application.services.a_share_sentiment_service import AShareSentimentService
from domain.a_share.enums import SentimentSourceType
from domain.a_share.models import ChipDistributionBin, ChipDistributionSnapshot, SentimentSignal
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.codecs import chip_distribution_codec, sentiment_codec

NOW = datetime(2026, 7, 17, 7, tzinfo=UTC)


def _meta(category: DataCategory) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        data_delay_seconds=None,
        warnings=("DERIVED_CHIP_DISTRIBUTION",),
    )


def _chip() -> ChipDistributionSnapshot:
    return ChipDistributionSnapshot(
        as_of=NOW,
        bins=(ChipDistributionBin(Decimal("10"), Decimal("11"), Decimal("1")),),
        profit_ratio=Decimal("1"),
        average_cost=Decimal("10.5"),
        concentration_90=Decimal("0"),
        concentration_70=Decimal("0"),
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
        calculation_method="turnover_decay_uniform_range",
        algorithm_version="tp_chip_v1",
        lookback_sessions=120,
        input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        bar_trade_date=date(2026, 7, 17),
    )


def _instrument(asset_type: AssetType = AssetType.EQUITY) -> Instrument:
    return Instrument(
        instrument_id=f"{asset_type.value}:A_SHARE:600519.SH",
        market=Market.A_SHARE,
        symbol="600519.SH",
        name="test",
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=asset_type,
    )


class _Calendar:
    version = "test.v1"

    def is_trading_day(self, day: date) -> bool:
        return day == NOW.date()

    def previous_trading_day(self, day: date) -> date:
        return day - timedelta(days=1)

    def sessions_for(self, day: date):
        if not self.is_trading_day(day):
            return ()
        from domain.a_share.models import TradingSessionWindow

        return (
            TradingSessionWindow(
                session=TradingSession.REGULAR,
                start_at=NOW - timedelta(hours=5, minutes=30),
                end_at=NOW,
            ),
        )


def _entry(payload: str, category: DataCategory) -> CacheEntry:
    return CacheEntry(
        key="e4d",
        market=Market.A_SHARE,
        category=category,
        instrument_id="equity:A_SHARE:600519.SH",
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        freshness=Freshness.UNKNOWN,
        vendor=VendorId.EASTMONEY,
        payload_json=payload,
    )


def test_chip_v2_roundtrip_dto_and_exact_key_rejection() -> None:
    chip = _chip()
    assert ChipDistributionSnapshotDTO.from_domain(chip).algorithm_version == "tp_chip_v1"
    codec = chip_distribution_codec()
    encoded = codec.encode(ProviderSuccess(value=chip, meta=_meta(DataCategory.CAPITAL)))
    assert "a_share_chip_distribution.v2" in encoded
    assert codec.decode(_entry(encoded, DataCategory.CAPITAL)).value == chip
    bad = encoded.replace('"algorithm_version":"tp_chip_v1",', "")
    with pytest.raises(DataContractError):
        codec.decode(_entry(bad, DataCategory.CAPITAL))

    old_codec = encoded.replace(
        '"codec":"a_share_chip_distribution.v2"',
        '"codec":"a_share_chip_distribution.v1"',
    )
    with pytest.raises(DataContractError) as old_exc:
        codec.decode(_entry(old_codec, DataCategory.CAPITAL))
    assert old_exc.value.details.get("rule") == "codec_id"

    document = json.loads(encoded)
    document["value"]["unexpected"] = "drift"
    with pytest.raises(DataContractError):
        codec.decode(
            _entry(
                json.dumps(document, separators=(",", ":"), sort_keys=True),
                DataCategory.CAPITAL,
            )
        )


def test_chip_domain_fails_closed_for_provenance_and_sum() -> None:
    with pytest.raises(DataContractError):
        ChipDistributionSnapshot(
            as_of=NOW,
            bins=(ChipDistributionBin(Decimal("1"), Decimal("2"), Decimal("0.5")),),
            profit_ratio=Decimal("1"),
            average_cost=Decimal("1"),
            concentration_90=Decimal("0"),
            concentration_70=Decimal("0"),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.LOW,
            is_authoritative=False,
            calculation_method="turnover_decay_uniform_range",
            algorithm_version="tp_chip_v1",
            lookback_sessions=120,
            input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
            bar_trade_date=date(2026, 7, 17),
        )


def test_concept_signal_v2_roundtrip_preserves_source_identity_and_time() -> None:
    signal = SentimentSignal(
        source_type=SentimentSourceType.CONCEPT_HEAT,
        trade_date=date(2026, 7, 17),
        instrument_id=None,
        rank=1,
        rank_change=None,
        heat_value=Decimal("5"),
        concept_tags=("新能源",),
        label="新能源",
        source_vendor=VendorId.EASTMONEY,
        reliability=ReliabilityLevel.LOW,
        is_authoritative=False,
        source_item_id="BK123",
        observed_at=NOW,
    )
    assert SentimentSignalDTO.from_domain(signal).source_item_id == "BK123"
    codec = sentiment_codec()
    encoded = codec.encode(ProviderSuccess(value=(signal,), meta=_meta(DataCategory.SENTIMENT)))
    assert codec.decode(_entry(encoded, DataCategory.SENTIMENT)).value == (signal,)


def test_chip_service_rejects_adjustment_and_session_close_provenance_drift() -> None:
    service = object.__new__(AShareCapitalService)
    service._calendar = _Calendar()
    success = ProviderSuccess(value=_chip(), meta=_meta(DataCategory.CAPITAL))
    service._validate_chip(success, instrument=_instrument(), as_of=NOW)

    bad_meta = replace(success.meta, adjustment=None)
    with pytest.raises(DataContractError) as adjustment_exc:
        service._validate_chip(
            ProviderSuccess(value=_chip(), meta=bad_meta), instrument=_instrument(), as_of=NOW
        )
    assert adjustment_exc.value.details.get("rule") == "provenance"

    wrong_close = replace(_chip(), as_of=NOW - timedelta(minutes=1))
    with pytest.raises(DataContractError) as close_exc:
        service._validate_chip(
            ProviderSuccess(value=wrong_close, meta=success.meta),
            instrument=_instrument(),
            as_of=NOW,
        )
    assert close_exc.value.details.get("rule") == "session_close"

    wrong_day = replace(_chip(), bar_trade_date=date(2026, 7, 16))
    with pytest.raises(DataContractError) as day_exc:
        service._validate_chip(
            ProviderSuccess(value=wrong_day, meta=success.meta),
            instrument=_instrument(),
            as_of=NOW,
        )
    assert day_exc.value.details.get("rule") == "session_close"

    with pytest.raises(DataContractError) as asset_exc:
        service._validate_chip(success, instrument=_instrument(AssetType.ETF), as_of=NOW)
    assert asset_exc.value.details.get("rule") == "asset_support"


def _concept(
    *,
    item_id: str,
    label: str,
    observed_at: datetime,
    reliability: ReliabilityLevel = ReliabilityLevel.LOW,
) -> SentimentSignal:
    return SentimentSignal(
        source_type=SentimentSourceType.CONCEPT_HEAT,
        trade_date=date(2026, 7, 17),
        instrument_id=None,
        rank=1,
        rank_change=None,
        heat_value=Decimal("5"),
        concept_tags=(label,),
        label=label,
        source_vendor=VendorId.EASTMONEY,
        reliability=reliability,
        is_authoritative=False,
        source_item_id=item_id,
        observed_at=observed_at,
    )


def test_concept_service_enforces_low_reliability_cutoff_and_shared_observed_time() -> None:
    service = object.__new__(AShareSentimentService)
    first = _concept(item_id="BK1", label="one", observed_at=NOW)
    service._validate_sentiment(
        ProviderSuccess(value=(first,), meta=_meta(DataCategory.SENTIMENT)),
        source=SentimentSourceType.CONCEPT_HEAT,
        trade_date=NOW.date(),
        instrument=object(),  # validator requires only non-None request identity here
        as_of=NOW,
    )

    medium = _concept(
        item_id="BK1", label="one", observed_at=NOW, reliability=ReliabilityLevel.MEDIUM
    )
    with pytest.raises(DataContractError):
        service._validate_sentiment(
            ProviderSuccess(value=(medium,), meta=_meta(DataCategory.SENTIMENT)),
            source=SentimentSourceType.CONCEPT_HEAT,
            trade_date=NOW.date(),
            instrument=object(),
            as_of=NOW,
        )

    future = _concept(item_id="BK1", label="one", observed_at=NOW + timedelta(seconds=1))
    with pytest.raises(DataContractError) as future_exc:
        service._validate_sentiment(
            ProviderSuccess(value=(future,), meta=_meta(DataCategory.SENTIMENT)),
            source=SentimentSourceType.CONCEPT_HEAT,
            trade_date=NOW.date(),
            instrument=object(),
            as_of=NOW,
        )
    assert future_exc.value.details.get("rule") == "observed_time"

    second = replace(
        _concept(item_id="BK2", label="two", observed_at=NOW - timedelta(seconds=1)), rank=2
    )
    with pytest.raises(DataContractError) as shared_exc:
        service._validate_sentiment(
            ProviderSuccess(value=(first, second), meta=_meta(DataCategory.SENTIMENT)),
            source=SentimentSourceType.CONCEPT_HEAT,
            trade_date=NOW.date(),
            instrument=object(),
            as_of=NOW,
        )
    assert shared_exc.value.details.get("rule") == "observed_time"


def test_concept_service_rejects_missing_instrument_even_for_empty_success() -> None:
    service = object.__new__(AShareSentimentService)
    with pytest.raises(DataContractError) as exc:
        service._validate_sentiment(
            ProviderSuccess(value=(), meta=_meta(DataCategory.SENTIMENT)),
            source=SentimentSourceType.CONCEPT_HEAT,
            trade_date=NOW.date(),
            instrument=None,
            as_of=NOW,
        )
    assert exc.value.details.get("rule") == "required"
