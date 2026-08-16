"""Focused deterministic evidence-binding checks for Agent answers."""

from __future__ import annotations

from application.services.agent_evidence_guard import (
    evidence_manifest_json,
    guard_agent_response,
)


def test_exact_numbers_and_percentages_bind_to_current_turn_payload() -> None:
    result = guard_agent_response(
        "XAUUSD 当前价 2,347.50，日变动 +1.25%。",
        receipts=(
            {
                "capability": "market_data_get",
                "operation": "quote",
                "request_id": "req_quote",
                "as_of": "2026-08-13T10:00:00+00:00",
                "freshness": "fresh",
                "source_codes": ["PRIMARY:dukascopy"],
            },
        ),
        tool_payloads=({"display_price": "2347.50", "change_pct": "1.25%"},),
    )
    assert result.verified is True
    assert result.unverified_claims == ()
    assert result.manifest["facts"][0]["capability"] == "market_data_get"
    assert '"facts"' in evidence_manifest_json(result)


def test_unbound_number_is_marked_unverified_and_repair_is_bounded() -> None:
    result = guard_agent_response(
        "黄金会涨到 9999.99，预计上涨 42.0%。",
        tool_payloads=({"display_price": "2347.50"},),
    )
    assert result.verified is False
    assert len(result.unverified_claims) == 2
    assert "未验证" in result.text
    assert result.repair_request is not None
    assert "9999.99" in str(result.repair_request["claims"])
    assert len(evidence_manifest_json(result).encode()) <= 16_384


def test_completed_action_requires_confirmation_and_basis_mismatch_is_visible() -> None:
    result = guard_agent_response(
        "已成交，成交价 100.00，昨收 99.00。",
        receipts=(
            {
                "capability": "market_data_get",
                "operation": "quote",
                "price_basis": "midpoint",
            },
        ),
        tool_payloads=({"display_price": "100.00", "previous_close": "99.00"},),
    )
    assert result.verified is False
    reasons = {item.reason for item in result.unverified_claims}
    assert "NO_CONFIRMATION_RECEIPT" in reasons
    assert "PRICE_BASIS_MISMATCH" in reasons
    assert result.manifest["basis_issues"] == ["PRICE_BASIS_MISMATCH"]


def test_nested_result_quality_is_preserved_and_missing_disclosure_is_unverified() -> None:
    result = guard_agent_response(
        "XAUUSD 当前价 2,347.50。",
        tool_payloads=(
            {
                "result": {
                    "ok": True,
                    "as_of": "2026-08-13T10:00:00+00:00",
                    "freshness": "stale",
                    "degraded": True,
                    "warnings": [{"code": "STALE_QUOTE"}],
                    "errors": [{"code": "UPSTREAM_TIMEOUT"}],
                    "data": {"display_price": "2347.50"},
                },
                "receipt": {
                    "capability": "market_data_get",
                    "operation": "quote",
                    "request_id": "req_nested_quality",
                },
            },
        ),
    )

    assert result.verified is False
    assert any(item.reason == "QUALITY_DISCLOSURE_MISSING" for item in result.unverified_claims)
    assert result.repair_request is not None
    fact = result.manifest["facts"][0]
    assert fact["freshness"] == "stale"
    assert fact["degraded"] is True
    assert fact["as_of"] == "2026-08-13T10:00:00+00:00"
    assert fact["warning_codes"] == ["STALE_QUOTE"]
    assert fact["error_codes"] == ["UPSTREAM_TIMEOUT"]
    assert "FRESHNESS_STALE" in result.manifest["quality_issues"]
    assert "DEGRADED" in result.manifest["quality_issues"]
    assert "STALE_QUOTE" in result.manifest["quality_issues"]


def test_quality_disclosure_allows_bound_claim_and_preserves_receipt_warning() -> None:
    result = guard_agent_response(
        "XAUUSD 当前价 2,347.50；数据延迟且处于降级状态，请按该新鲜度解读。",
        tool_payloads=(
            {
                "result": {
                    "freshness": "delayed",
                    "degraded": True,
                    "warnings": ["DELAYED_QUOTE"],
                    "data": {"display_price": "2347.50"},
                },
                "receipt": {
                    "capability": "market_data_get",
                    "operation": "quote",
                    "request_id": "req_disclosed_quality",
                    "warning_codes": ["DELAYED_QUOTE"],
                },
            },
        ),
    )

    assert result.verified is True
    assert result.unverified_claims == ()
    fact = result.manifest["facts"][0]
    assert fact["freshness"] == "delayed"
    assert fact["degraded"] is True
    assert fact["warning_codes"] == ["DELAYED_QUOTE"]
    assert result.manifest["quality_issues"][:2] == ["DEGRADED", "FRESHNESS_DELAYED"]


def test_direct_payload_quality_envelope_is_not_dropped() -> None:
    result = guard_agent_response(
        "报价 100.00。",
        tool_payloads=(
            {
                "capability": "market_data_get",
                "operation": "quote",
                "freshness": "stale",
                "degraded": True,
                "warnings": ["STALE_QUOTE"],
                "data": {"display_price": "100.00"},
            },
        ),
    )

    assert result.verified is False
    assert any(item.reason == "QUALITY_DISCLOSURE_MISSING" for item in result.unverified_claims)
    assert result.manifest["facts"][0]["freshness"] == "stale"


def test_iso_timestamp_components_are_not_treated_as_numeric_claims() -> None:
    result = guard_agent_response(
        "价格 4405.870；quote_at=2026-08-13T03:44:00Z；"
        "as_of=2026-08-13T03:45:19.724695Z；数据延迟约 80 秒，处于降级状态。",
        tool_payloads=(
            {
                "result": {
                    "as_of": "2026-08-13T03:45:19.724695Z",
                    "freshness": "fresh",
                    "degraded": True,
                    "data": {
                        "display_price": "4405.870",
                        "quote_at": "2026-08-13T03:44:00Z",
                        "data_delay_seconds": 80,
                    },
                },
                "receipt": {
                    "capability": "market_data_get",
                    "operation": "quote",
                    "request_id": "req_iso_timestamp",
                },
            },
        ),
    )

    assert result.verified is True
    assert result.unverified_claims == ()
    assert {item.normalized for item in result.claims} == {
        "date:2026-08-13",
        "4405.87",
        "80",
    }


def test_midpoint_disclosure_saying_not_a_trade_does_not_claim_execution() -> None:
    result = guard_agent_response(
        "报价 4406.250，price_basis=mid，是买卖中间价，非成交价；"
        "数据延迟且处于降级状态。",
        tool_payloads=(
            {
                "result": {
                    "freshness": "delayed",
                    "degraded": True,
                    "data": {"display_price": "4406.250", "price_basis": "mid"},
                },
                "receipt": {
                    "capability": "market_data_get",
                    "operation": "quote",
                    "request_id": "req_midpoint_disclosure",
                },
            },
        ),
    )

    assert result.verified is True
    assert result.unverified_claims == ()
    assert result.manifest["basis_issues"] == []
