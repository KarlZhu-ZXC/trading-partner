"""Acceptance harness checks use a scripted provider, never an external model."""

import json

import pytest

from application.ports.agent_model_provider import ModelResponse, ModelToolCall
from domain.common.errors import DataContractError
from interfaces.cli.agent_behavior_evaluation import _Model
from interfaces.cli.copilot_research_live_evaluation import (
    CASES,
    SyntheticResearchGateway,
    run_live_research_acceptance,
)


@pytest.mark.asyncio
async def test_synthetic_gateway_has_no_live_or_write_fallback():
    gateway = SyntheticResearchGateway(CASES[0])
    assert gateway.search("anything", mode="prepare_action") == ()
    for capability, operation, arguments in [
        ("broker_order_manage", "submit", {}),
        ("market_data_get", "quote", {"instrument_id": "equity:US:REAL"}),
    ]:
        with pytest.raises(DataContractError):
            await gateway.read(capability, operation, arguments)
    with pytest.raises(DataContractError):
        await gateway.propose("research_judgment_propose", "thesis_revision", {})
    assert gateway.read_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["valid", "unbound_number", "missing_inline_price"])
async def test_acceptance_requires_retained_fields_and_explanation(variant):
    case = CASES[0]
    prefix = "req_acceptance_inline_quote_1/result/data/"
    text = (
        f"报价为@evidence({prefix}last)，时点为@evidence({prefix}quote_at)，不能保证当前成交。"
        if variant == "valid"
        else "当前价格为999。"
    )
    if variant == "missing_inline_price":
        text = f"价格需复查，时点为@evidence({prefix}quote_at)。"
    provider = _Model(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="read",
                        name="tp_read",
                        arguments=json.dumps(
                            {
                                "capability": case.capability,
                                "operation": case.operation,
                                "arguments": {},
                            }
                        ),
                    ),
                )
            ),
            ModelResponse(
                text=json.dumps(
                    {
                        "blocks": [
                            {
                                "kind": "INFERENCE",
                                "text": text,
                                "evidence_refs": [prefix + "last", prefix + "quote_at"],
                            }
                        ]
                    }
                )
            ),
        ]
    )
    report = await run_live_research_acceptance(provider, cases=(case,), reasoning_effort="high")
    valid = variant == "valid"
    assert report["passed"] is valid
    assert report["semantic_correctness"] == "NOT_AUTOMATICALLY_VERIFIED"
    result = report["results"][0]
    assert result["semantic_review"] == "REQUIRED"
    assert len(result["model_calls"]) == 2
    assert all(call["status"] == "RETURNED" for call in result["model_calls"])
    assert all(call["elapsed_ms"] >= 0 for call in result["model_calls"])
    assert all(request.reasoning_effort == "high" for request in provider.requests)
    if valid:
        assert "123.45" in result["answer"]
        assert "SYNTHETIC_ACCEPTANCE" in result["answer"]
        assert result["research"]["verified_claims"] == 0
    elif variant == "unbound_number":
        assert "BLOCKED_ANSWER_CONTENT" in result["errors"]
        assert "999" not in result["answer"]
    else:
        assert "INLINE_FIELD_NOT_RETAINED" in result["errors"]
    assert all(not request.native_web_search for request in provider.requests)
    assert all(
        getattr(tool, "name", "") not in {"tp_propose", "tp_prepare_action", "tp_web_search"}
        for request in provider.requests
        for tool in request.tools
    )
