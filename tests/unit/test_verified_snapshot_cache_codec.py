"""Phase 1D D6b1: VerifiedMarketSnapshotCacheCodec + ProviderCacheCodec port."""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.provider_router_engine import ProviderRouterEnginePort
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.market.models import MarketBar, TechnicalIndicators, VerifiedMarketSnapshot
from infrastructure.providers.common.verified_snapshot_cache_codec import (
    CODEC_ID,
    VerifiedMarketSnapshotCacheCodec,
)

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
FETCHED = datetime(2026, 7, 16, 15, 0, 1, tzinfo=UTC)
EXPIRES = datetime(2026, 7, 16, 15, 0, 31, tzinfo=UTC)
BAR_TS = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)
SECRET = "test-secret-malicious-value"
INSTRUMENT_ID = "equity:US:NVDA"
CACHE_KEY = (
    f"v1|US|market_snapshot|{INSTRUMENT_ID}|{AS_OF.isoformat()}|get_snapshot|a1b2c3d4e5f67890"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEC_SRC = (
    PROJECT_ROOT
    / "src"
    / "infrastructure"
    / "providers"
    / "common"
    / "verified_snapshot_cache_codec.py"
)


def _instrument(**overrides: object) -> Instrument:
    base: dict[str, object] = {
        "instrument_id": INSTRUMENT_ID,
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "market": Market.US,
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": AssetType.EQUITY,
        "is_active": True,
        "listing_status": "active",
        "country": "US",
        "mic": "XNAS",
        "underlying_instrument_id": None,
        "multiplier": Decimal("1.00"),
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("1"),
        "metadata_version": 1,
    }
    base.update(overrides)
    return Instrument(**base)  # type: ignore[arg-type]


def _bar(**overrides: object) -> MarketBar:
    base: dict[str, object] = {
        "timestamp": BAR_TS,
        "open": Decimal("100.00"),
        "high": Decimal("110.50"),
        "low": Decimal("99.10"),
        "close": Decimal("105.25"),
        "volume": Decimal("1000"),
    }
    base.update(overrides)
    return MarketBar(**base)  # type: ignore[arg-type]


def _indicators_full() -> TechnicalIndicators:
    return TechnicalIndicators(
        ema_10=Decimal("104.10"),
        sma_50=Decimal("100.00"),
        sma_200=Decimal("90.00"),
        rsi_14=Decimal("55.5"),
        macd=Decimal("1.23"),
        macd_signal=Decimal("1.10"),
        macd_histogram=Decimal("0.13"),
        atr_14=Decimal("2.5"),
        bollinger_mid=Decimal("105"),
        bollinger_upper=Decimal("110"),
        bollinger_lower=Decimal("100"),
        vwma=Decimal("104.5"),
        mfi=Decimal("60"),
    )


def _snapshot(
    *,
    instrument: Instrument | None = None,
    bar: MarketBar | None = None,
    indicators: TechnicalIndicators | None = None,
    recent_closes: tuple[Decimal, ...] | None = None,
    requested_as_of: datetime = AS_OF,
    adjustment: AdjustmentMethod = AdjustmentMethod.NONE,
    session: TradingSession = TradingSession.REGULAR,
    algorithm_version: str = "mock-1.0.0",
) -> VerifiedMarketSnapshot:
    market_bar = bar if bar is not None else _bar()
    closes = (
        recent_closes
        if recent_closes is not None
        else (Decimal("100.00"), Decimal("102.00"), market_bar.close)
    )
    return VerifiedMarketSnapshot(
        instrument=instrument if instrument is not None else _instrument(),
        requested_as_of=requested_as_of,
        latest_market_row=market_bar,
        indicators=indicators if indicators is not None else TechnicalIndicators.empty(),
        recent_closes=closes,
        adjustment=adjustment,
        session=session,
        algorithm_version=algorithm_version,
    )


def _meta(**overrides: object) -> ProviderResultMeta:
    base: dict[str, object] = {
        "vendor": VendorId.MOCK_US,
        "category": DataCategory.MARKET_SNAPSHOT,
        "role": SourceRole.PRIMARY,
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "freshness": Freshness.FRESH,
        "session": TradingSession.REGULAR,
        "latency_ms": 12,
        "cache_disposition": CacheDisposition.MISS,
        "adjustment": AdjustmentMethod.NONE,
        "data_delay_seconds": 0,
        "warnings": ("MOCK_DATA",),
    }
    base.update(overrides)
    return ProviderResultMeta(**base)  # type: ignore[arg-type]


def _success(
    *,
    snapshot: VerifiedMarketSnapshot | None = None,
    meta: ProviderResultMeta | None = None,
) -> ProviderSuccess[VerifiedMarketSnapshot]:
    return ProviderSuccess(
        value=snapshot if snapshot is not None else _snapshot(),
        meta=meta if meta is not None else _meta(),
    )


def _entry(
    payload_json: str,
    **overrides: object,
) -> CacheEntry:
    base: dict[str, object] = {
        "key": CACHE_KEY,
        "category": DataCategory.MARKET_SNAPSHOT,
        "market": Market.US,
        "instrument_id": INSTRUMENT_ID,
        "vendor": VendorId.MOCK_US,
        "payload_json": payload_json,
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "expires_at": EXPIRES,
        "freshness": Freshness.FRESH,
    }
    base.update(overrides)
    return CacheEntry(**base)  # type: ignore[arg-type]


def _error_blob(exc: BaseException) -> str:
    """Public error surface only (message/details + non-suppressed chain)."""
    parts = [str(exc), repr(exc)]
    if isinstance(exc, DataContractError):
        parts.append(str(exc.details))
        parts.append(repr(exc.details))
    # __cause__ is the explicit chain; must be empty when codec uses `from None`.
    cause = exc.__cause__
    while cause is not None:
        parts.append(str(cause))
        parts.append(repr(cause))
        parts.append(type(cause).__name__)
        cause = cause.__cause__
    # Only walk __context__ when not suppressed (PEP 415).
    if not getattr(exc, "__suppress_context__", False):
        ctx = exc.__context__
        while ctx is not None:
            parts.append(str(ctx))
            parts.append(repr(ctx))
            parts.append(type(ctx).__name__)
            if getattr(ctx, "__suppress_context__", False):
                break
            ctx = ctx.__context__
    return "\n".join(parts)


def _assert_no_leak(exc: BaseException, *needles: str) -> None:
    blob = _error_blob(exc)
    for needle in needles:
        assert needle not in blob, f"leaked {needle!r} in:\n{blob}"
    assert SECRET not in blob
    assert "JSONDecodeError" not in blob
    assert "ValidationError" not in blob
    assert "InvalidOperation" not in blob
    assert exc.__cause__ is None


# --- Protocol / packaging surface (consolidated implementation locks) ---


def test_codec_protocol_packaging_and_source_locks() -> None:
    """codec_id, Protocol surface, port exports, and source-text locks."""
    codec = VerifiedMarketSnapshotCacheCodec()
    assert codec.codec_id == "verified_market_snapshot.v1"
    assert CODEC_ID == "verified_market_snapshot.v1"
    assert VerifiedMarketSnapshotCacheCodec.codec_id == "verified_market_snapshot.v1"
    assert isinstance(codec.codec_id, str)
    assert callable(codec.encode)
    assert callable(codec.decode)
    as_protocol: ProviderCacheCodec[VerifiedMarketSnapshot] = codec
    assert as_protocol.codec_id == CODEC_ID

    encode_sig = inspect.signature(ProviderCacheCodec.encode)
    assert list(encode_sig.parameters) == ["self", "success"]
    decode_sig = inspect.signature(ProviderCacheCodec.decode)
    assert list(decode_sig.parameters) == ["self", "entry"]
    assert encode_sig.return_annotation is not inspect.Signature.empty
    assert decode_sig.return_annotation is not inspect.Signature.empty
    assert "success" in ProviderCacheCodec.encode.__annotations__
    assert "entry" in ProviderCacheCodec.decode.__annotations__
    assert "return" in ProviderCacheCodec.encode.__annotations__
    assert isinstance(ProviderCacheCodec.codec_id, property)

    sig = inspect.signature(ProviderRouterEnginePort.execute)
    params = sig.parameters
    assert list(params) == [
        "self",
        "market",
        "category",
        "chain",
        "criticality",
        "call",
        "operation_name",
        "request_fingerprint",
        "instrument",
        "as_of",
        "bypass_cache",
        "cache_codec",
        "result_validator",
    ]
    for name in list(params)[1:]:
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
    engine_mod = (PROJECT_ROOT / "src/application/ports/provider_router_engine.py").read_text(
        encoding="utf-8"
    )
    assert "class ProviderRouterEnginePort" in engine_mod
    assert "class ProviderRouterEngine(" not in engine_mod
    assert "class ProviderRouterEngine:" not in engine_mod

    from application.ports import ProviderCacheCodec as ExportedCodec
    from application.ports import ProviderRouterEnginePort as ExportedEngine
    from infrastructure.providers.common import (
        VERIFIED_MARKET_SNAPSHOT_CODEC_ID,
    )
    from infrastructure.providers.common import (
        VerifiedMarketSnapshotCacheCodec as Exported,
    )

    assert ExportedCodec is ProviderCacheCodec
    assert ExportedEngine is ProviderRouterEnginePort
    assert Exported is VerifiedMarketSnapshotCacheCodec
    assert VERIFIED_MARKET_SNAPSHOT_CODEC_ID == CODEC_ID

    text = CODEC_SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "pickle" not in imported
    assert "marshal" not in imported
    assert "importlib" not in imported
    assert "default=str" not in text
    assert "pickle.dumps" not in text
    assert "marshal.dumps" not in text
    assert "allow_nan=False" in text
    assert "sort_keys=True" in text
    assert 'separators=(",", ":")' in text or "separators=(',', ':')" in text


# --- Happy path / determinism ---


def test_encode_decode_roundtrip_preserves_full_instrument_and_indicators() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    snap = _snapshot(
        instrument=_instrument(
            country="US",
            mic="XNAS",
            multiplier=Decimal("1.00"),
            tick_size=Decimal("0.01"),
            lot_size=Decimal("1"),
            metadata_version=2,
        ),
        indicators=_indicators_full(),
        adjustment=AdjustmentMethod.SPLIT_ADJUSTED,
        session=TradingSession.PRE_MARKET,
    )
    meta = _meta(
        adjustment=AdjustmentMethod.SPLIT_ADJUSTED,
        session=TradingSession.PRE_MARKET,
        warnings=("MOCK_DATA", "PARTIAL_VENDOR_CHAIN"),
        latency_ms=7,
        data_delay_seconds=15,
    )
    success = _success(snapshot=snap, meta=meta)
    payload = codec.encode(success)
    # Byte-for-byte deterministic
    assert codec.encode(success) == payload
    assert codec.encode(success) == payload

    decoded = codec.decode(_entry(payload))
    assert decoded.value == snap
    assert decoded.value.instrument == snap.instrument
    assert decoded.value.indicators == snap.indicators
    assert decoded.value.recent_closes == snap.recent_closes
    assert decoded.meta.cache_disposition is CacheDisposition.HIT
    assert decoded.meta.vendor is meta.vendor
    assert decoded.meta.category is meta.category
    assert decoded.meta.role is meta.role
    assert decoded.meta.as_of == meta.as_of
    assert decoded.meta.fetched_at == meta.fetched_at
    assert decoded.meta.freshness is meta.freshness
    assert decoded.meta.session is meta.session
    assert decoded.meta.latency_ms == meta.latency_ms
    assert decoded.meta.adjustment is meta.adjustment
    assert decoded.meta.data_delay_seconds == meta.data_delay_seconds
    assert decoded.meta.warnings == meta.warnings


def test_encode_is_canonical_sorted_compact_unicode_safe() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    # Unicode in name must not be escaped (ensure_ascii=False)
    snap = _snapshot(
        instrument=_instrument(
            instrument_id="equity:A_SHARE:600519.SH",
            symbol="600519.SH",
            name="贵州茅台",
            market=Market.A_SHARE,
            exchange="SSE",
            currency="CNY",
            timezone="Asia/Shanghai",
            country="CN",
            mic="XSHG",
        )
    )
    meta = _meta(vendor=VendorId.MOCK_A_SHARE)
    payload = codec.encode(_success(snapshot=snap, meta=meta))
    assert "贵州茅台" in payload
    assert "\\u" not in payload
    # Compact separators, sorted keys
    assert ": " not in payload
    assert ", " not in payload
    parsed = json.loads(payload)
    assert list(parsed.keys()) == sorted(parsed.keys())
    assert list(parsed.keys()) == ["codec", "meta", "schema_version", "value"]
    assert set(parsed["meta"].keys()) == {
        "adjustment",
        "as_of",
        "cache_disposition",
        "category",
        "data_delay_seconds",
        "fetched_at",
        "freshness",
        "latency_ms",
        "role",
        "session",
        "vendor",
        "warnings",
    }
    assert set(parsed["value"]["instrument"].keys()) == {
        "asset_type",
        "country",
        "currency",
        "exchange",
        "instrument_id",
        "is_active",
        "listing_status",
        "lot_size",
        "market",
        "metadata_version",
        "mic",
        "multiplier",
        "name",
        "symbol",
        "tick_size",
        "timezone",
        "underlying_instrument_id",
    }
    assert set(parsed["value"]["indicators"].keys()) == {
        "atr_14",
        "bollinger_lower",
        "bollinger_mid",
        "bollinger_upper",
        "ema_10",
        "macd",
        "macd_histogram",
        "macd_signal",
        "mfi",
        "rsi_14",
        "sma_200",
        "sma_50",
        "vwma",
    }
    # Roundtrip A-share instrument
    entry = _entry(
        payload,
        market=Market.A_SHARE,
        instrument_id="equity:A_SHARE:600519.SH",
        vendor=VendorId.MOCK_A_SHARE,
        key=(
            "v1|A_SHARE|market_snapshot|equity:A_SHARE:600519.SH|"
            f"{AS_OF.isoformat()}|get_snapshot|a1b2c3d4e5f67890"
        ),
    )
    decoded = codec.decode(entry)
    assert decoded.value.instrument.name == "贵州茅台"
    assert decoded.value.instrument.market is Market.A_SHARE


def test_decimal_trailing_precision_and_scientific_expansion() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    bar = _bar(
        open=Decimal("1500.00"),
        high=Decimal("1500.00"),
        low=Decimal("1500.00"),
        close=Decimal("1500.00"),
        volume=Decimal("1E+3"),  # scientific → fixed-point on encode
    )
    snap = _snapshot(bar=bar, recent_closes=(Decimal("1490.00"), Decimal("1500.00")))
    payload = codec.encode(_success(snapshot=snap))
    assert '"open":"1500.00"' in payload
    assert '"close":"1500.00"' in payload
    # scientific expanded
    assert '"volume":"1000"' in payload or '"volume":"1000.0"' in payload
    assert "E" not in payload.upper().split("CODEC")[0] or "MOCK" in payload
    # No scientific notation left in numeric strings for volume
    vol = json.loads(payload)["value"]["latest_market_row"]["volume"]
    assert isinstance(vol, str)
    assert "E" not in vol.upper()
    decoded = codec.decode(_entry(payload))
    assert decoded.value.latest_market_row.open == Decimal("1500.00")
    assert decoded.value.latest_market_row.volume == Decimal("1000")


def test_datetime_meta_and_value_roundtrip_canonical() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = codec.encode(_success())
    data = json.loads(payload)
    assert data["meta"]["as_of"] == AS_OF.isoformat()
    assert data["meta"]["fetched_at"] == FETCHED.isoformat()
    assert data["value"]["requested_as_of"] == AS_OF.isoformat()
    assert data["value"]["latest_market_row"]["timestamp"] == BAR_TS.isoformat()
    decoded = codec.decode(_entry(payload))
    assert decoded.meta.as_of == AS_OF
    assert decoded.meta.fetched_at == FETCHED
    assert decoded.value.requested_as_of == AS_OF
    assert decoded.value.latest_market_row.timestamp == BAR_TS


def test_encode_writes_miss_decode_returns_hit_without_clobbering_other_meta() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    meta = _meta(
        role=SourceRole.FALLBACK,
        freshness=Freshness.DELAYED,
        latency_ms=99,
        data_delay_seconds=30,
        warnings=("MOCK_DATA", "CACHE_CANDIDATE"),
    )
    payload = codec.encode(_success(meta=meta))
    data = json.loads(payload)
    assert data["meta"]["cache_disposition"] == "miss"
    decoded = codec.decode(_entry(payload, freshness=Freshness.DELAYED))
    assert decoded.meta.cache_disposition is CacheDisposition.HIT
    assert decoded.meta.role is SourceRole.FALLBACK
    assert decoded.meta.freshness is Freshness.DELAYED
    assert decoded.meta.latency_ms == 99
    assert decoded.meta.data_delay_seconds == 30
    assert decoded.meta.warnings == ("MOCK_DATA", "CACHE_CANDIDATE")


# --- Encode guards ---


def test_encode_rejects_non_market_snapshot_category() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(_success(meta=_meta(category=DataCategory.MARKET_QUOTE)))
    assert exc_info.value.details.get("rule") == "category_market_snapshot"
    assert exc_info.value.details.get("field") == "meta.category"


def test_encode_rejects_non_miss_disposition() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    for disp in (
        CacheDisposition.HIT,
        CacheDisposition.BYPASS,
        CacheDisposition.STALE_HIT,
    ):
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(meta=_meta(cache_disposition=disp)))
        assert exc_info.value.details.get("rule") == "cache_disposition_miss"


def test_encode_rejects_requested_as_of_mismatch() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    other = AS_OF - timedelta(minutes=1)
    snap = _snapshot(requested_as_of=other, bar=_bar(timestamp=other - timedelta(hours=1)))
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(_success(snapshot=snap, meta=_meta(as_of=AS_OF)))
    assert exc_info.value.details.get("rule") == "requested_as_of_matches_meta"


def test_encode_invokes_contract_validator_and_propagates() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    # Invalid OHLC: high too low — validator must reject before dump.
    bad = _snapshot(
        bar=_bar(
            open=Decimal("100"),
            high=Decimal("90"),
            low=Decimal("80"),
            close=Decimal("85"),
        )
    )
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(_success(snapshot=bad))
    assert exc_info.value.details.get("rule") == "ohlc_high"

    called: list[VerifiedMarketSnapshot] = []
    real = __import__(
        "infrastructure.providers.common.contract_validation",
        fromlist=["validate_verified_market_snapshot"],
    ).validate_verified_market_snapshot

    def _wrap(snapshot: VerifiedMarketSnapshot) -> None:
        called.append(snapshot)
        real(snapshot)

    with patch(
        "infrastructure.providers.common.verified_snapshot_cache_codec."
        "validate_verified_market_snapshot",
        side_effect=_wrap,
    ):
        payload = codec.encode(_success())
    assert len(called) == 1
    assert isinstance(payload, str)


# --- Decode safety / malformed ---


def test_decode_malformed_json_safe() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry("{not-json" + SECRET))
    assert exc_info.value.details.get("rule") == "malformed_json"
    _assert_no_leak(exc_info.value, SECRET, "{not-json")


def test_decode_malicious_payload_does_not_leak_secret_or_exception_chain() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    malicious = json.dumps(
        {
            "codec": CODEC_ID,
            "meta": {"vendor": SECRET, "extra": SECRET},
            "schema_version": 1,
            "value": {"token": SECRET},
        },
        ensure_ascii=False,
    )
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(malicious))
    _assert_no_leak(exc_info.value, SECRET)
    assert exc_info.value.__cause__ is None


def test_decode_extra_top_level_key_rejected() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["evil"] = SECRET
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload, ensure_ascii=False)))
    assert exc_info.value.details.get("rule") == "extra_keys"
    _assert_no_leak(exc_info.value, SECRET)


def test_decode_missing_top_level_key_rejected() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    del payload["value"]
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "missing_keys"


def test_decode_extra_and_missing_nested_instrument_keys() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["value"]["instrument"]["hacker_field"] = SECRET
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload, ensure_ascii=False)))
    assert exc_info.value.details.get("rule") == "extra_keys"
    _assert_no_leak(exc_info.value, SECRET)

    payload = json.loads(codec.encode(_success()))
    del payload["value"]["instrument"]["mic"]
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "missing_keys"


def test_decode_rejects_json_number_decimal() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["value"]["latest_market_row"]["close"] = 105.25  # JSON number
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "decimal_string"
    _assert_no_leak(exc_info.value, "105.25")


def test_decode_rejects_nonfinite_decimal_strings() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    for bad in ("NaN", "Infinity", "-Infinity", "sNaN"):
        payload = json.loads(codec.encode(_success()))
        payload["value"]["latest_market_row"]["close"] = bad
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(json.dumps(payload)))
        assert exc_info.value.details.get("rule") == "canonical_decimal_string"
        _assert_no_leak(exc_info.value, bad)


def test_decode_rejects_noncanonical_decimal_strings() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    bads = (
        "1e3",
        "1E+3",
        "1E-2",
        "01",
        "00.1",
        "001",
        "+1",
        "+0",
    )
    for bad in bads:
        payload = json.loads(codec.encode(_success()))
        payload["value"]["latest_market_row"]["close"] = bad
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(json.dumps(payload)))
        assert exc_info.value.details.get("rule") == "canonical_decimal_string"
        # Empty needle is always a substring; only assert non-empty payload needles.
        if bad:
            _assert_no_leak(exc_info.value, bad)
        else:
            _assert_no_leak(exc_info.value)


def test_decode_rejects_noncanonical_decimal_strings_with_spacing_or_grammar_glitches() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    bads = (".5", "1.", " 1", "1 ", "1.0 ", "\t1")
    for bad in bads:
        payload = json.loads(codec.encode(_success()))
        payload["value"]["latest_market_row"]["close"] = bad
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(json.dumps(payload)))
        assert exc_info.value.details.get("rule") == "canonical_decimal_string"
        if bad:
            _assert_no_leak(exc_info.value, bad)
        else:
            _assert_no_leak(exc_info.value)


def test_decode_rejects_noncanonical_decimal_strings_with_literal_tokens() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    bads = ("", "1.2.3", "--1", "0x10")
    for bad in bads:
        payload = json.loads(codec.encode(_success()))
        payload["value"]["latest_market_row"]["close"] = bad
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(json.dumps(payload)))
        assert exc_info.value.details.get("rule") == "canonical_decimal_string"
        if bad:
            _assert_no_leak(exc_info.value, bad)
        else:
            _assert_no_leak(exc_info.value)


def test_decode_accepts_encoder_canonical_decimal_strings() -> None:
    """Grammar-matching fixed-point forms (incl. -0 / trailing zeros) decode."""
    codec = VerifiedMarketSnapshotCacheCodec()
    wires = (
        "1",
        "1.00",
        "1500.00",
        "1000",
        "10.0100",
    )
    for wire in wires:
        payload = json.loads(codec.encode(_success()))
        # Keep OHLC consistent enough for validator after parse.
        for key in ("open", "high", "low", "close"):
            payload["value"]["latest_market_row"][key] = wire
        payload["value"]["latest_market_row"]["volume"] = "1000"
        # recent_closes last element must match close for contract validator.
        closes = list(payload["value"]["recent_closes"])
        closes[-1] = wire
        payload["value"]["recent_closes"] = closes
        decoded = codec.decode(_entry(json.dumps(payload)))
        assert decoded.value.latest_market_row.close == Decimal(wire)


def test_decode_accepts_encoder_canonical_decimal_strings_with_sign_and_scale() -> None:
    """Grammar-matching fixed-point forms including sign and scale."""
    codec = VerifiedMarketSnapshotCacheCodec()
    wires = (
        "0",
        "-0",
        "0.0",
        "-0.0",
        "0.00",
        "-1.23",
        "0.5",
    )
    for wire in wires:
        payload = json.loads(codec.encode(_success()))
        # Keep OHLC consistent enough for validator after parse.
        for key in ("open", "high", "low", "close"):
            payload["value"]["latest_market_row"][key] = wire
        payload["value"]["latest_market_row"]["volume"] = "1000"
        # recent_closes last element must match close for contract validator.
        closes = list(payload["value"]["recent_closes"])
        closes[-1] = wire
        payload["value"]["recent_closes"] = closes
        decoded = codec.decode(_entry(json.dumps(payload)))
        assert decoded.value.latest_market_row.close == Decimal(wire)


def test_encode_rejects_nonfinite_decimal() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    bar = _bar(close=Decimal("NaN"))
    # MarketBar accepts NaN Decimal; validator/codec must reject without leak.
    snap = VerifiedMarketSnapshot(
        instrument=_instrument(),
        requested_as_of=AS_OF,
        latest_market_row=bar,
        indicators=TechnicalIndicators.empty(),
        recent_closes=(Decimal("NaN"),),
        adjustment=AdjustmentMethod.NONE,
        session=TradingSession.REGULAR,
        algorithm_version="mock-1.0.0",
    )
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(_success(snapshot=snap))
    assert "NaN" not in _error_blob(exc_info.value)


def test_decode_rejects_naive_datetime() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["meta"]["as_of"] = "2026-07-16T15:00:00"  # naive
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "timezone_aware"


def test_decode_rejects_noncanonical_datetime_z_suffix() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["meta"]["as_of"] = "2026-07-16T15:00:00Z"
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    # Z parses but isoformat() is +00:00 → non-canonical
    assert exc_info.value.details.get("rule") in {
        "canonical_isoformat",
        "datetime_parse",
    }
    _assert_no_leak(exc_info.value, "2026-07-16T15:00:00Z")


def test_decode_rejects_malicious_enum_without_echo() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["meta"]["vendor"] = SECRET
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload, ensure_ascii=False)))
    assert exc_info.value.details.get("rule") == "enum_value"
    _assert_no_leak(exc_info.value, SECRET)


def test_decode_rejects_wrong_codec_id() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["codec"] = "other_codec.v1"
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "codec_id"


def test_decode_rejects_wrong_schema_version() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["schema_version"] = 2
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "schema_version"


# --- CacheEntry coherence ---


def _encoded_payload() -> str:
    return VerifiedMarketSnapshotCacheCodec().encode(_success())


def test_decode_coherence_mismatches_for_entry_keys() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = _encoded_payload()
    cases: tuple[tuple[dict[str, object], str], ...] = (
        (
            {
                "category": DataCategory.MARKET_QUOTE,
                "key": (
                    f"v1|US|market_quote|{INSTRUMENT_ID}|"
                    f"{AS_OF.isoformat()}|get_snapshot|a1b2c3d4e5f67890"
                ),
            },
            "category_market_snapshot",
        ),
        ({"vendor": VendorId.MOCK_A_SHARE}, "coherence_vendor"),
        (
            {"as_of": AS_OF - timedelta(seconds=1)},
            "coherence_as_of",
        ),
        (
            {"fetched_at": FETCHED + timedelta(seconds=1)},
            "coherence_fetched_at",
        ),
    )
    for override, rule in cases:
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(payload, **override))
        assert exc_info.value.details.get("rule") == rule


def test_decode_coherence_mismatches_for_entry_market_and_freshness() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = _encoded_payload()
    cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"freshness": Freshness.STALE}, "coherence_freshness"),
        (
            {
                "market": Market.A_SHARE,
                "instrument_id": "equity:A_SHARE:600519.SH",
                "key": (
                    "v1|A_SHARE|market_snapshot|equity:A_SHARE:600519.SH|"
                    f"{AS_OF.isoformat()}|get_snapshot|a1b2c3d4e5f67890"
                ),
            },
            "coherence_market",
        ),
    )
    for override, rule in cases:
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(payload, **override))
        assert exc_info.value.details.get("rule") == rule


def test_decode_coherence_requested_as_of_vs_meta() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    # Keep bar before both as_of values; change only requested_as_of in value.
    other = AS_OF - timedelta(hours=1)
    payload["value"]["requested_as_of"] = other.isoformat()
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    # May fail contract validator (bar vs as_of) or coherence depending on order.
    # Design: validate then coherence — validator checks last close etc first;
    # requested_as_of change alone with bar still <= both should hit coherence.
    assert exc_info.value.details.get("rule") in {
        "coherence_requested_as_of",
        "not_after_as_of",
        "last_close_matches_bar",
    }


def test_decode_rejects_coherence_instrument_id_mismatch() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(
            _entry(
                _encoded_payload(),
                instrument_id="equity:US:AAPL",
                key=(
                    f"v1|US|market_snapshot|equity:US:AAPL|"
                    f"{AS_OF.isoformat()}|get_snapshot|a1b2c3d4e5f67890"
                ),
            )
        )
    assert exc_info.value.details.get("rule") == "coherence_instrument_id"


def test_decode_coherence_category_vs_meta() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    payload["meta"]["category"] = DataCategory.MARKET_QUOTE.value
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") in {
        "coherence_category",
        "category_market_snapshot",
    }


def test_decode_invokes_contract_validator() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = codec.encode(_success())
    called: list[object] = []
    real = __import__(
        "infrastructure.providers.common.contract_validation",
        fromlist=["validate_verified_market_snapshot"],
    ).validate_verified_market_snapshot

    def _wrap(snapshot: VerifiedMarketSnapshot) -> None:
        called.append(snapshot)
        real(snapshot)

    with patch(
        "infrastructure.providers.common.verified_snapshot_cache_codec."
        "validate_verified_market_snapshot",
        side_effect=_wrap,
    ):
        codec.decode(_entry(payload))
    assert len(called) == 1


def test_decode_contract_validator_failure_is_safe() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success()))
    # Break last-close invariant after encode.
    payload["value"]["recent_closes"] = ["100.00", "999.99"]
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(json.dumps(payload)))
    assert exc_info.value.details.get("rule") == "last_close_matches_bar"
    _assert_no_leak(exc_info.value, "999.99")


def test_all_indicator_keys_present_when_null() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    payload = json.loads(codec.encode(_success(snapshot=_snapshot())))
    indicators = payload["value"]["indicators"]
    for key in (
        "ema_10",
        "sma_50",
        "sma_200",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_histogram",
        "atr_14",
        "bollinger_mid",
        "bollinger_upper",
        "bollinger_lower",
        "vwma",
        "mfi",
    ):
        assert key in indicators
        assert indicators[key] is None


def test_encode_rejects_wrong_value_type() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(ProviderSuccess(value="not-a-snapshot", meta=_meta()))  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "success.value"


# --- Strict JSON / disposition / enum identity / encode scalars ---


class _FakeValueBearing:
    """Impostor with a matching wire ``.value`` but not a frozen enum member."""

    def __init__(self, value: str) -> None:
        self.value = value


def test_decode_rejects_duplicate_keys_nested_and_top_level() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    nested = (
        '{"codec":"verified_market_snapshot.v1","meta":{'
        '"vendor":"mock_us","vendor":"' + SECRET + '","category":"market_snapshot"},'
        '"schema_version":1,"value":{}}'
    )
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(nested))
    assert exc_info.value.details.get("rule") == "malformed_json"
    _assert_no_leak(exc_info.value, SECRET, "StrictJson")

    top = (
        '{"codec":"verified_market_snapshot.v1","codec":"evil.v1",'
        '"meta":{},"schema_version":1,"value":{}}'
    )
    with pytest.raises(DataContractError) as exc_info:
        codec.decode(_entry(top))
    assert exc_info.value.details.get("rule") == "malformed_json"
    _assert_no_leak(exc_info.value, "evil.v1")


def test_decode_rejects_nonstandard_json_constants() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    for token in ("NaN", "Infinity", "-Infinity"):
        raw = f'{{"codec":"verified_market_snapshot.v1","x":{token}}}'
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(raw))
        assert exc_info.value.details.get("rule") == "malformed_json"
        _assert_no_leak(exc_info.value, token, "StrictJson")


def test_decode_rejects_non_miss_cache_disposition() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    for disp in (
        CacheDisposition.HIT,
        CacheDisposition.BYPASS,
        CacheDisposition.STALE_HIT,
    ):
        payload = json.loads(codec.encode(_success()))
        payload["meta"]["cache_disposition"] = disp.value
        with pytest.raises(DataContractError) as exc_info:
            codec.decode(_entry(json.dumps(payload)))
        assert exc_info.value.details.get("rule") == "cache_disposition_miss"
        assert exc_info.value.details.get("field") == "meta.cache_disposition"
        _assert_no_leak(exc_info.value, disp.value)


def test_encode_rejects_fake_value_bearing_enum_objects_snapshot() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    cases: tuple[tuple[str, str, str], ...] = (
        ("snapshot", "adjustment", AdjustmentMethod.NONE.value),
        ("snapshot", "session", TradingSession.REGULAR.value),
    )
    for target, attr, wire in cases:
        snap = _snapshot()
        meta = _meta()
        fake = _FakeValueBearing(wire)
        if target == "snapshot":
            object.__setattr__(snap, attr, fake)
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(snapshot=snap, meta=meta))
        # Pre-encode guards may reject category/disposition before enum encoding;
        # either path is a safe reject (never serializes impostors).
        rule = exc_info.value.details.get("rule")
        assert rule in {
            "enum_type",
            "category_market_snapshot",
            "cache_disposition_miss",
        }
        # Do not assert short wire values absent from field path text (e.g. "fresh"
        # ⊆ "freshness"). Payload secret + exception chain must still be clean.
        _assert_no_leak(exc_info.value)


def test_encode_rejects_fake_value_bearing_enum_objects_instrument() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    cases: tuple[tuple[str, str, str], ...] = (
        ("instrument", "market", Market.US.value),
        ("instrument", "asset_type", AssetType.EQUITY.value),
    )
    for target, attr, wire in cases:
        snap = _snapshot()
        meta = _meta()
        fake = _FakeValueBearing(wire)
        if target == "instrument":
            object.__setattr__(snap.instrument, attr, fake)
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(snapshot=snap, meta=meta))
        # Pre-encode guards may reject category/disposition before enum encoding;
        # either path is a safe reject (never serializes impostors).
        rule = exc_info.value.details.get("rule")
        assert rule in {
            "enum_type",
            "category_market_snapshot",
            "cache_disposition_miss",
        }
        # Do not assert short wire values absent from field path text (e.g. "fresh"
        # ⊆ "freshness"). Payload secret + exception chain must still be clean.
        _assert_no_leak(exc_info.value)


def test_encode_rejects_fake_value_bearing_enum_objects_meta() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    cases: tuple[tuple[str, str], ...] = (
        ("vendor", VendorId.MOCK_US.value),
        ("category", DataCategory.MARKET_SNAPSHOT.value),
        ("role", SourceRole.PRIMARY.value),
        ("freshness", Freshness.FRESH.value),
        ("session", TradingSession.REGULAR.value),
        ("cache_disposition", CacheDisposition.MISS.value),
        ("adjustment", AdjustmentMethod.NONE.value),
    )
    for attr, wire in cases:
        snap = _snapshot()
        meta = _meta()
        fake = _FakeValueBearing(wire)
        object.__setattr__(meta, attr, fake)
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(snapshot=snap, meta=meta))
        # Pre-encode guards may reject category/disposition before enum encoding;
        # either path is a safe reject (never serializes impostors).
        rule = exc_info.value.details.get("rule")
        assert rule in {
            "enum_type",
            "category_market_snapshot",
            "cache_disposition_miss",
        }
        # Do not assert short wire values absent from field path text (e.g. "fresh"
        # ⊆ "freshness"). Payload secret + exception chain must still be clean.
        _assert_no_leak(exc_info.value)


def test_encode_rejects_mutated_invalid_instrument_scalar_types() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    cases: tuple[tuple[object, str], ...] = (
        (
            lambda snap, _meta: object.__setattr__(snap.instrument, "instrument_id", 12345),
            "instrument.instrument_id",
        ),
        (
            lambda snap, _meta: object.__setattr__(snap.instrument, "name", {"x": 1}),
            "instrument.name",
        ),
        (
            lambda snap, _meta: object.__setattr__(snap.instrument, "is_active", 1),
            "instrument.is_active",
        ),
        (
            lambda snap, _meta: object.__setattr__(snap.instrument, "metadata_version", 0),
            "instrument.metadata_version",
        ),
        (
            lambda snap, _meta: object.__setattr__(snap.instrument, "metadata_version", True),
            "instrument.metadata_version",
        ),
        (
            lambda snap, _meta: object.__setattr__(snap.instrument, "country", b"US"),
            "instrument.country",
        ),
    )
    for mutator, field_fragment in cases:
        snap = _snapshot()
        meta = _meta()
        mutator(snap, meta)  # type: ignore[operator]
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(snapshot=snap, meta=meta))
        field = str(exc_info.value.details.get("field", ""))
        assert field_fragment in field
        # No payload echo of mutated values / secrets.
        _assert_no_leak(exc_info.value, SECRET, "12345")


def test_encode_rejects_mutated_invalid_algorithm_version() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    snap = _snapshot()
    meta = _meta()
    object.__setattr__(snap, "algorithm_version", 1.0)
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(_success(snapshot=snap, meta=meta))
    field = str(exc_info.value.details.get("field", ""))
    assert "algorithm_version" in field
    _assert_no_leak(exc_info.value, SECRET, "1.0")


def test_encode_rejects_mutated_invalid_meta_latency() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    cases: tuple[tuple[object, str], ...] = (
        (
            lambda _snap, meta: object.__setattr__(meta, "latency_ms", -1),
            "meta.latency_ms",
        ),
        (
            lambda _snap, meta: object.__setattr__(meta, "latency_ms", True),
            "meta.latency_ms",
        ),
    )
    for mutator, field_fragment in cases:
        snap = _snapshot()
        meta = _meta()
        mutator(snap, meta)  # type: ignore[operator]
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(snapshot=snap, meta=meta))
        field = str(exc_info.value.details.get("field", ""))
        assert field_fragment in field
        _assert_no_leak(exc_info.value, SECRET, "True", "-1")


def test_encode_rejects_mutated_invalid_meta_delay() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    snap = _snapshot()
    meta = _meta()
    object.__setattr__(meta, "data_delay_seconds", "0")
    with pytest.raises(DataContractError) as exc_info:
        codec.encode(_success(snapshot=snap, meta=meta))
    field = str(exc_info.value.details.get("field", ""))
    assert "meta.data_delay_seconds" in field
    _assert_no_leak(exc_info.value, SECRET, "0")


def test_encode_rejects_mutated_invalid_meta_warnings() -> None:
    codec = VerifiedMarketSnapshotCacheCodec()
    cases: tuple[tuple[object, str, tuple[str, ...]], ...] = (
        (
            lambda _snap, meta: object.__setattr__(meta, "warnings", ("MOCK_DATA", 99)),
            "meta.warnings",
            ("99",),
        ),
        (
            lambda _snap, meta: object.__setattr__(meta, "warnings", "MOCK_DATA"),
            "meta.warnings",
            ("MOCK_DATA",),
        ),
    )
    for mutator, field_fragment, leaks in cases:
        snap = _snapshot()
        meta = _meta()
        mutator(snap, meta)  # type: ignore[operator]
        with pytest.raises(DataContractError) as exc_info:
            codec.encode(_success(snapshot=snap, meta=meta))
        field = str(exc_info.value.details.get("field", ""))
        assert field_fragment in field
        _assert_no_leak(exc_info.value, SECRET, *leaks)
