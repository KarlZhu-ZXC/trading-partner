"""E4c option snapshot codec, fingerprint, validator, and service unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from application.dto.a_share_provenance import provenance_dtos
from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.dto.tool_envelope import WarningInfo
from application.services.a_share_etf_option_service import (
    OP_OPTION_SNAPSHOT,
    AShareEtfOptionResult,
    AShareEtfOptionService,
)
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import OPTIONS_POLICY
from domain.a_share.enums import OptionType
from domain.a_share.models import (
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    OptionGreeks,
)
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.codecs import (
    CODEC_OPTION_SNAPSHOT,
    E4C_CODEC_IDS,
    option_snapshot_codec,
)

AS_OF = datetime(2026, 7, 17, 7, 0, tzinfo=UTC)
EXPIRY = date(2026, 7, 22)


def _etf() -> Instrument:
    return Instrument(
        instrument_id="etf:A_SHARE:510050.SH",
        symbol="510050.SH",
        name="50ETF",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.ETF,
    )


def _meta(
    *,
    vendor: VendorId = VendorId.SINA,
    category: DataCategory = DataCategory.OPTIONS,
    as_of: datetime | None = None,
    warnings: tuple[str, ...] = (),
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=vendor,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=as_of or AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.DELAYED,
        session=TradingSession.CLOSED,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=300,
        warnings=warnings,
    )


def _contract(
    cid: str,
    *,
    otype: OptionType = OptionType.CALL,
    strike: Decimal = Decimal("3.0"),
    expiry: date = EXPIRY,
    multiplier: Decimal | None = None,
) -> EtfOptionContract:
    return EtfOptionContract(
        instrument_id=f"option:A_SHARE:{cid}",
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        option_type=otype,
        expiry=expiry,
        strike=strike,
        multiplier=multiplier,
    )


def _quote(
    cid: str,
    *,
    otype: OptionType = OptionType.CALL,
    strike: Decimal = Decimal("3.0"),
    quote_at: datetime | None = None,
    expiry: date = EXPIRY,
    last: Decimal | None = Decimal("0.12"),
    multiplier: Decimal | None = None,
) -> EtfOptionQuote:
    return EtfOptionQuote(
        contract=_contract(cid, otype=otype, strike=strike, expiry=expiry, multiplier=multiplier),
        quote_at=quote_at or AS_OF,
        last=last,
        bid_prices=(Decimal("0.11"),),
        bid_volumes=(10,),
        ask_prices=(Decimal("0.13"),),
        ask_volumes=(12,),
        volume_contracts=50,
        open_interest=100,
    )


def _greek(
    cid: str,
    *,
    as_of: datetime | None = None,
    delta: Decimal | None = Decimal("0.55"),
    gamma: Decimal | None = Decimal("0.02"),
    theta: Decimal | None = Decimal("-0.01"),
    vega: Decimal | None = Decimal("0.12"),
    iv: Decimal | None = Decimal("0.18"),
    theoretical: Decimal | None = Decimal("0.125"),
) -> OptionGreeks:
    return OptionGreeks(
        contract_instrument_id=f"option:A_SHARE:{cid}",
        as_of=as_of or AS_OF,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_volatility=iv,
        theoretical_value=theoretical,
        source_provided=True,
    )


def _snapshot() -> EtfOptionSnapshot:
    q_call = _quote("10007601", otype=OptionType.CALL)
    q_put = _quote("10007602", otype=OptionType.PUT)
    return EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(q_call, q_put),
        greeks=(_greek("10007601"), _greek("10007602")),
    )


def test_e4c_codec_ids() -> None:
    assert CODEC_OPTION_SNAPSHOT in E4C_CODEC_IDS
    assert CODEC_OPTION_SNAPSHOT == "a_share_option_snapshot.v1"


def _cache_entry(
    payload: str,
    *,
    category: DataCategory = DataCategory.OPTIONS,
    vendor: VendorId = VendorId.SINA,
) -> Any:
    from application.dto.provider_state import CacheEntry

    return CacheEntry(
        key="v1|A_SHARE|e4c|fp",
        market=Market.A_SHARE,
        category=category,
        instrument_id="etf:A_SHARE:510050.SH",
        as_of=AS_OF,
        fetched_at=AS_OF,
        expires_at=AS_OF + timedelta(hours=1),
        freshness=Freshness.DELAYED,
        vendor=vendor,
        payload_json=payload,
    )


def test_option_codec_roundtrip() -> None:
    codec = option_snapshot_codec()
    success = ProviderSuccess(value=_snapshot(), meta=_meta())
    encoded = codec.encode(success)
    assert "pickle" not in encoded
    decoded = codec.decode(_cache_entry(encoded))
    assert decoded.value == success.value
    assert decoded.meta.vendor is VendorId.SINA
    assert decoded.meta.category is DataCategory.OPTIONS


def test_option_codec_rejects_malicious_extra_keys() -> None:
    codec = option_snapshot_codec()
    success = ProviderSuccess(value=_snapshot(), meta=_meta())
    import json

    payload = json.loads(codec.encode(success))
    payload["value"]["evil"] = True
    with pytest.raises(DataContractError):
        codec.decode(_cache_entry(json.dumps(payload)))


def test_option_codec_rejects_wrong_category_on_decode() -> None:
    codec = option_snapshot_codec()
    success = ProviderSuccess(
        value=_snapshot(),
        meta=_meta(category=DataCategory.CAPITAL),
    )
    encoded = codec.encode(success)
    with pytest.raises(DataContractError) as exc:
        codec.decode(_cache_entry(encoded, category=DataCategory.CAPITAL, vendor=VendorId.SINA))
    assert exc.value.details.get("rule") in {"category", "coherence_category"}


def test_option_fingerprint_canonical() -> None:
    fp = build_a_share_fingerprint(
        OP_OPTION_SNAPSHOT,
        "etf:A_SHARE:510050.SH",
        {
            "expiry": "2026-07-22",
            "strike_center": "3.0",
            "strike_count_each_side": "0",
        },
        AS_OF,
    )
    assert fp.startswith("v1|a_share.option_snapshot.v1|etf:A_SHARE:510050.SH|")
    assert "expiry=2026-07-22" in fp
    assert "strike_center=3.0" in fp
    assert "strike_count_each_side=0" in fp


class _FakeRouter:
    def __init__(self, result: RouterExecutionResult[Any]) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> RouterExecutionResult[Any]:
        self.calls.append(kwargs)
        validator = kwargs.get("result_validator")
        if validator is not None and self._result.ok and self._result.value is not None:
            success = ProviderSuccess(
                value=self._result.value,
                meta=self._result.meta or _meta(),
            )
            validator(success)
        return self._result


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _ok_result(value: Any, *, meta: ProviderResultMeta | None = None) -> RouterExecutionResult[Any]:
    return RouterExecutionResult(
        ok=True,
        value=value,
        error=None,
        meta=meta or _meta(),
        warnings=(),
        attempts=(),
        criticality=DataCriticality.CORE,
    )


def _err_result(
    error: Exception | None = None,
    *,
    warnings: tuple[WarningInfo, ...] = (),
) -> RouterExecutionResult[Any]:
    return RouterExecutionResult(
        ok=False,
        value=None,
        error=error or DataContractError("boom", details={"rule": "fail"}),
        meta=None,
        warnings=warnings,
        attempts=(),
        criticality=DataCriticality.CORE,
    )


def _service(router: _FakeRouter) -> AShareEtfOptionService:
    return AShareEtfOptionService(
        router=router,  # type: ignore[arg-type]
        clock=_FixedClock(AS_OF),
        option_snapshot_codec=option_snapshot_codec(),
    )


@pytest.mark.asyncio
async def test_option_service_success() -> None:
    snap = _snapshot()
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    result = await service.get(
        _etf(),
        expiry=EXPIRY,
        strike_center=Decimal("3.0"),
        strike_count_each_side=0,
        as_of=AS_OF,
    )
    assert isinstance(result, AShareEtfOptionResult)
    assert result.ok is True
    assert result.data is not None
    assert tuple(item.component.value for item in result.provenance) == ("option_snapshot",)
    assert result.provenance[0].meta is router._result.meta
    assert result.data.provenance[0].vendor is result.provenance[0].meta.vendor
    assert result.data.provenance == provenance_dtos(result.provenance)
    assert result.data.underlying_instrument_id == "etf:A_SHARE:510050.SH"
    assert result.data.expiry == EXPIRY
    assert len(result.data.quotes) == 2
    assert router.calls[0]["tool_policy"] is OPTIONS_POLICY
    assert router.calls[0]["operation_name"] == OP_OPTION_SNAPSHOT
    assert router.calls[0]["category"] is DataCategory.OPTIONS
    assert DataCategory.OPTIONS in OPTIONS_POLICY.required_categories
    assert DataCategory.OPTIONS not in OPTIONS_POLICY.optional_categories


@pytest.mark.asyncio
async def test_option_service_validator_rejects_wrong_vendor() -> None:
    snap = _snapshot()
    router = _FakeRouter(_ok_result(snap, meta=_meta(vendor=VendorId.EASTMONEY)))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "meta_vendor"


@pytest.mark.asyncio
async def test_option_service_validator_rejects_empty_quotes() -> None:
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(),
        greeks=(),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "non_empty"


@pytest.mark.asyncio
async def test_option_service_validator_rejects_missing_greek_join() -> None:
    q_call = _quote("10007601", otype=OptionType.CALL)
    q_put = _quote("10007602", otype=OptionType.PUT)
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(q_call, q_put),
        greeks=(_greek("10007601"), _greek("99999999")),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "join"


@pytest.mark.asyncio
async def test_option_service_required_failure() -> None:
    router = _FakeRouter(
        _err_result(warnings=(WarningInfo(code="PROVIDER_FAILED", message="failed"),))
    )
    service = _service(router)
    result = await service.get(
        _etf(),
        expiry=EXPIRY,
        strike_center=Decimal("3.0"),
        strike_count_each_side=0,
        as_of=AS_OF,
    )
    assert result.ok is False
    assert result.data is None
    assert result.error is not None
    assert result.provenance == ()


@pytest.mark.asyncio
async def test_option_service_rejects_future_as_of() -> None:
    service = _service(_FakeRouter(_err_result()))
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF + timedelta(minutes=1),
        )
    assert exc.value.details.get("rule") == "not_future"


@pytest.mark.asyncio
async def test_option_service_rejects_non_etf() -> None:
    service = _service(_FakeRouter(_err_result()))
    equity = Instrument(
        instrument_id="equity:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="test",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )
    with pytest.raises(DataContractError) as exc:
        await service.get(
            equity,
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "asset_type"


@pytest.mark.asyncio
async def test_option_service_validator_rejects_future_quote_at() -> None:
    future_q = _quote(
        "10007601",
        quote_at=AS_OF + timedelta(hours=1),
    )
    q_put = _quote("10007602", otype=OptionType.PUT)
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(future_q, q_put),
        greeks=(_greek("10007601"), _greek("10007602")),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "as_of_cutoff"


@pytest.mark.asyncio
async def test_malicious_wrong_meta_as_of() -> None:
    snap = _snapshot()
    wrong_meta = _meta(as_of=AS_OF - timedelta(seconds=1))
    router = _FakeRouter(_ok_result(snap, meta=wrong_meta))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "meta_as_of"


@pytest.mark.asyncio
async def test_malicious_expiry_none_mixed_local_quote_dates() -> None:
    # expiry=None path still requires one local quote date and exact date expiry.
    q_call = _quote(
        "10007601",
        otype=OptionType.CALL,
        quote_at=AS_OF,
    )
    q_put = _quote(
        "10007602",
        otype=OptionType.PUT,
        quote_at=AS_OF - timedelta(days=1),
    )
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(q_call, q_put),
        greeks=(_greek("10007601"), _greek("10007602")),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=None,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "single_local_date"


@pytest.mark.asyncio
async def test_malicious_one_sided_strike() -> None:
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(_quote("10007601", otype=OptionType.CALL),),
        greeks=(_greek("10007601"),),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "strike_sides"


@pytest.mark.asyncio
async def test_malicious_nonnumeric_instrument_id() -> None:
    # Domain allows option:A_SHARE:510050C... form; service must reject non-digit ids.
    bad = EtfOptionContract(
        instrument_id="option:A_SHARE:510050C2607M03000",
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        option_type=OptionType.CALL,
        expiry=EXPIRY,
        strike=Decimal("3.0"),
        multiplier=None,
    )
    q_call = EtfOptionQuote(
        contract=bad,
        quote_at=AS_OF,
        last=Decimal("0.12"),
        bid_prices=(Decimal("0.11"),),
        bid_volumes=(10,),
        ask_prices=(Decimal("0.13"),),
        ask_volumes=(12,),
        volume_contracts=50,
        open_interest=100,
    )
    q_put = _quote("10007602", otype=OptionType.PUT)
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(q_call, q_put),
        greeks=(
            OptionGreeks(
                contract_instrument_id="option:A_SHARE:510050C2607M03000",
                as_of=AS_OF,
                delta=Decimal("0.5"),
                gamma=Decimal("0.01"),
                theta=Decimal("-0.01"),
                vega=Decimal("0.1"),
                implied_volatility=Decimal("0.2"),
                theoretical_value=Decimal("0.1"),
                source_provided=True,
            ),
            _greek("10007602"),
        ),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "identity"


@pytest.mark.asyncio
async def test_malicious_multiplier_not_none() -> None:
    q_call = _quote("10007601", otype=OptionType.CALL, multiplier=Decimal("10000"))
    q_put = _quote("10007602", otype=OptionType.PUT)
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(q_call, q_put),
        greeks=(_greek("10007601"), _greek("10007602")),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "multiplier_none"


@pytest.mark.asyncio
async def test_malicious_wrong_greek_as_of() -> None:
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(
            _quote("10007601", otype=OptionType.CALL),
            _quote("10007602", otype=OptionType.PUT),
        ),
        greeks=(
            _greek("10007601", as_of=AS_OF - timedelta(seconds=30)),
            _greek("10007602"),
        ),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "as_of_exact"


@pytest.mark.asyncio
async def test_malicious_greek_delta_out_of_range() -> None:
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(
            _quote("10007601", otype=OptionType.CALL),
            _quote("10007602", otype=OptionType.PUT),
        ),
        greeks=(
            _greek("10007601", delta=Decimal("1.5")),
            _greek("10007602"),
        ),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "delta_range"


@pytest.mark.asyncio
async def test_malicious_negative_gamma() -> None:
    snap = EtfOptionSnapshot(
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=EXPIRY,
        quotes=(
            _quote("10007601", otype=OptionType.CALL),
            _quote("10007602", otype=OptionType.PUT),
        ),
        greeks=(
            _greek("10007601", gamma=Decimal("-0.01")),
            _greek("10007602"),
        ),
    )
    router = _FakeRouter(_ok_result(snap))
    service = _service(router)
    with pytest.raises(DataContractError) as exc:
        await service.get(
            _etf(),
            expiry=EXPIRY,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "nonnegative"


def test_service_rejects_optional_options_policy() -> None:
    bad = ToolDataPolicy(
        tool_name="evil.options",
        required_categories=(),
        optional_categories=(DataCategory.OPTIONS,),
        category_chain_overrides={},
    )
    with pytest.raises(DataContractError) as exc:
        AShareEtfOptionService(
            router=_FakeRouter(_err_result()),  # type: ignore[arg-type]
            clock=_FixedClock(AS_OF),
            option_snapshot_codec=option_snapshot_codec(),
            tool_policy=bad,
        )
    assert exc.value.details.get("rule") in {
        "options_required",
        "options_not_optional",
    }
