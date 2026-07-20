"""Phase 1D D6a: provider routing DTO invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from application.dto.provider_routing import (
    ProviderAttemptRecord,
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.dto.tool_envelope import WarningInfo
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    ProviderAttemptOutcome,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError, NoMarketData, ProviderNotConfigured

AS_OF = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
FETCHED = datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)


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
        "data_delay_seconds": None,
        "warnings": (),
    }
    base.update(overrides)
    return ProviderResultMeta(**base)  # type: ignore[arg-type]


# --- ProviderResultMeta ---


def test_provider_result_meta_accepts_valid_warning_payload() -> None:
    meta = _meta(latency_ms=0, data_delay_seconds=0, warnings=("MOCK_DATA",))
    assert meta.vendor is VendorId.MOCK_US
    assert meta.latency_ms == 0
    assert meta.warnings == ("MOCK_DATA",)

    meta = _meta(warnings=("MOCK_DATA", "CACHE_SERVED", "A", "Z9_CODE"))
    assert meta.warnings == ("MOCK_DATA", "CACHE_SERVED", "A", "Z9_CODE")


def test_provider_result_meta_rejects_invalid_warning_codes_without_echo() -> None:
    invalid_codes: list[object] = [
        "",
        " ",
        "mock_data",
        "Mock_Data",
        "1LEADING_DIGIT",
        "HAS-DASH",
        "HAS SPACE",
        "HAS.DOT",
        "test-secret-malicious-value",
        "Bearer secret-token",
        "a" * 129,
        "A" + ("B" * 128),
        12345,
    ]
    for bad_code in invalid_codes:
        with pytest.raises(DataContractError, match="warning codes must match") as exc_info:
            _meta(warnings=(bad_code,))  # type: ignore[arg-type]
        blob = str(exc_info.value) + repr(exc_info.value.details) + repr(exc_info.value)
        if isinstance(bad_code, str) and bad_code.strip():
            assert bad_code not in blob
        assert "MALICIOUS" not in blob
        assert "sk-live" not in blob
        assert "secret-token" not in blob
        assert "Bearer" not in blob
        assert exc_info.value.details.get("field") == "warnings"
        assert exc_info.value.details.get("index") == 0
        assert exc_info.value.details.get("rule") == "safe_code"
        # Stable details only — no payload key for the rejected code value.
        assert "value" not in exc_info.value.details
        assert "code" not in exc_info.value.details
        if isinstance(bad_code, str) and bad_code.strip():
            assert str(bad_code) not in blob


def test_provider_result_meta_rejects_naive_datetime() -> None:
    with pytest.raises(DataContractError, match="timezone-aware"):
        _meta(as_of=datetime(2026, 7, 16, 12, 0))


def test_provider_result_meta_rejects_bool_as_latency() -> None:
    with pytest.raises(DataContractError) as exc_info:
        _meta(latency_ms=True)  # type: ignore[arg-type]
    assert "latency_ms" in str(exc_info.value.details)
    assert "True" not in str(exc_info.value)
    assert "True" not in str(exc_info.value.details)


def test_provider_result_meta_rejects_negative_delay() -> None:
    with pytest.raises(DataContractError, match="nonnegative"):
        _meta(data_delay_seconds=-1)


# --- ProviderSuccess ---


def test_provider_success_value_contract() -> None:
    with pytest.raises(DataContractError, match="must not be None") as exc_info:
        ProviderSuccess(value=None, meta=_meta())  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "value"

    result = ProviderSuccess(value={"ok": True}, meta=_meta())
    assert result.value == {"ok": True}


# --- ProviderAttemptRecord ---


def test_provider_attempt_record_contract() -> None:
    rec = ProviderAttemptRecord(
        vendor=VendorId.YFINANCE,
        outcome=ProviderAttemptOutcome.SUCCESS,
        error_code=None,
        duration_ms=0,
        message=None,
    )
    assert rec.duration_ms == 0

    with pytest.raises(DataContractError) as exc_info:
        ProviderAttemptRecord(
            vendor=VendorId.NULL,
            outcome=ProviderAttemptOutcome.SUCCESS,
            error_code=None,
            duration_ms=False,  # type: ignore[arg-type]
            message=None,
        )
    assert "duration_ms" in str(exc_info.value.details)


def test_provider_attempt_record_rejects_invalid_error_code_without_echo() -> None:
    secret = "test-secret-malicious-value"
    with pytest.raises(DataContractError, match="error_code must match") as exc_info:
        ProviderAttemptRecord(
            vendor=VendorId.YFINANCE,
            outcome=ProviderAttemptOutcome.FAILURE,
            error_code=secret,
            duration_ms=1,
            message=None,
        )
    blob = str(exc_info.value) + str(exc_info.value.details)
    assert secret not in blob
    assert "MALICIOUS" not in blob
    assert exc_info.value.details.get("field") == "error_code"


def test_provider_attempt_record_accepts_valid_error_code() -> None:
    rec = ProviderAttemptRecord(
        vendor=VendorId.NULL,
        outcome=ProviderAttemptOutcome.FAILURE,
        error_code="PROVIDER_TIMEOUT_ERROR",
        duration_ms=5,
        message="timed out",
    )
    assert rec.error_code == "PROVIDER_TIMEOUT_ERROR"


# --- RouterExecutionResult ---


def test_router_result_contract_invariants() -> None:
    result = RouterExecutionResult(
        value="payload",
        ok=True,
        criticality=DataCriticality.CORE,
        meta=_meta(),
        attempts=(),
        warnings=(),
        error=None,
    )
    assert result.ok is True
    assert result.value == "payload"

    err = NoMarketData("none")
    result = RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.OPTIONAL,
        meta=None,
        attempts=(),
        warnings=(WarningInfo(code="OPTIONAL_DATA_UNAVAILABLE", message="unavailable"),),
        error=err,
    )
    assert result.ok is False
    assert result.error is err


def test_router_result_ok_true_requires_value_and_meta() -> None:
    with pytest.raises(DataContractError, match="non-null value"):
        RouterExecutionResult(
            value=None,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=_meta(),
            attempts=(),
            warnings=(),
            error=None,
        )
    with pytest.raises(DataContractError, match="non-null meta"):
        RouterExecutionResult(
            value="x",
            ok=True,
            criticality=DataCriticality.CORE,
            meta=None,
            attempts=(),
            warnings=(),
            error=None,
        )
    with pytest.raises(DataContractError, match="error is None"):
        RouterExecutionResult(
            value="x",
            ok=True,
            criticality=DataCriticality.CORE,
            meta=_meta(),
            attempts=(),
            warnings=(),
            error=ProviderNotConfigured("no"),
        )


def test_router_result_ok_false_requires_error_and_null_value_meta() -> None:
    with pytest.raises(DataContractError, match="value is None"):
        RouterExecutionResult(
            value="leak",
            ok=False,
            criticality=DataCriticality.CORE,
            meta=None,
            attempts=(),
            warnings=(),
            error=ProviderNotConfigured("no"),
        )
    with pytest.raises(DataContractError, match="meta is None"):
        RouterExecutionResult(
            value=None,
            ok=False,
            criticality=DataCriticality.CORE,
            meta=_meta(),
            attempts=(),
            warnings=(),
            error=ProviderNotConfigured("no"),
        )
    with pytest.raises(DataContractError, match="non-null error"):
        RouterExecutionResult(
            value=None,
            ok=False,
            criticality=DataCriticality.CORE,
            meta=None,
            attempts=(),
            warnings=(),
            error=None,
        )


# --- ToolDataPolicy ---


def test_tool_data_policy_freezes_overrides_as_mapping_proxy() -> None:
    policy = ToolDataPolicy(
        tool_name="instrument_resolve",
        required_categories=(DataCategory.INSTRUMENT_MASTER,),
        optional_categories=(DataCategory.NEWS,),
        category_chain_overrides={
            DataCategory.INSTRUMENT_MASTER: [
                VendorId.LOCAL_MASTER,
                VendorId.SEED_FIXTURE,
            ],
        },
    )
    assert isinstance(policy.category_chain_overrides, MappingProxyType)
    assert policy.category_chain_overrides[DataCategory.INSTRUMENT_MASTER] == (
        VendorId.LOCAL_MASTER,
        VendorId.SEED_FIXTURE,
    )
    with pytest.raises(TypeError):
        policy.category_chain_overrides[DataCategory.NEWS] = (VendorId.NULL,)  # type: ignore[index]
    chain = policy.category_chain_overrides[DataCategory.INSTRUMENT_MASTER]
    assert isinstance(chain, tuple)


def test_tool_data_policy_rejects_required_optional_overlap() -> None:
    with pytest.raises(DataContractError, match="must not overlap") as exc_info:
        ToolDataPolicy(
            tool_name="t",
            required_categories=(DataCategory.NEWS,),
            optional_categories=(DataCategory.NEWS,),
            category_chain_overrides={},
        )
    assert "no_overlap" in str(exc_info.value.details)
    # category value may appear as structured detail, but malicious free text must not
    assert "api_key" not in str(exc_info.value.details).lower()


def test_tool_data_policy_rejects_override_outside_declared() -> None:
    with pytest.raises(DataContractError, match="only reference") as exc_info:
        ToolDataPolicy(
            tool_name="t",
            required_categories=(DataCategory.INSTRUMENT_MASTER,),
            optional_categories=(),
            category_chain_overrides={
                DataCategory.SENTIMENT: (VendorId.STOCKTWITS,),
            },
        )
    assert exc_info.value.details.get("rule") == "override_category_not_declared"


def test_tool_data_policy_rejects_duplicate_vendors_in_override() -> None:
    with pytest.raises(DataContractError, match="duplicate vendors") as exc_info:
        ToolDataPolicy(
            tool_name="t",
            required_categories=(DataCategory.MARKET_SNAPSHOT,),
            optional_categories=(),
            category_chain_overrides={
                DataCategory.MARKET_SNAPSHOT: (
                    VendorId.MOCK_US,
                    VendorId.MOCK_US,
                ),
            },
        )
    assert exc_info.value.details.get("rule") == "no_duplicate_vendors"


def test_tool_data_policy_rejects_invalid_types_without_echoing_payload() -> None:
    poison = "Bearer test-secret-that-must-not-leak"
    with pytest.raises(DataContractError) as exc_info:
        ToolDataPolicy(
            tool_name="t",
            required_categories=(DataCategory.NEWS,),
            optional_categories=(),
            category_chain_overrides={
                DataCategory.NEWS: poison,  # type: ignore[dict-item]
            },
        )
    blob = str(exc_info.value) + repr(exc_info.value.details)
    assert poison not in blob
    assert "EVIL_TOKEN" not in blob
    assert "sk-" not in blob


def test_tool_data_policy_rejects_blank_tool_name() -> None:
    with pytest.raises(DataContractError, match="tool_name"):
        ToolDataPolicy(
            tool_name="   ",
            required_categories=(),
            optional_categories=(),
            category_chain_overrides={},
        )


def test_tool_data_policy_rejects_non_vendor_chain_element_type() -> None:
    with pytest.raises(DataContractError) as exc_info:
        ToolDataPolicy(
            tool_name="t",
            required_categories=(DataCategory.NEWS,),
            optional_categories=(),
            category_chain_overrides={
                DataCategory.NEWS: ("not_a_vendor",),  # type: ignore[dict-item]
            },
        )
    assert exc_info.value.details.get("rule") == "vendor_type"
    assert "not_a_vendor" not in str(exc_info.value.details)
