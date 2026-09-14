import asyncio

import pytest
from pydantic import ValidationError

from application.dto.agent import AgentTurnRequest
from application.dto.copilot_research import CopilotResearchReceipt
from application.services.copilot_research_budget import ResearchBudget, ResearchBudgetExhausted
from domain.agent.enums import AgentChannel


def budget(**kwargs):
    return ResearchBudget(
        mode="research", max_seconds=30, max_model_calls=1, max_tool_calls=2, **kwargs
    )


def test_attempted_model_and_whole_tool_batch_admission():
    value = budget()
    value.model()
    with pytest.raises(ResearchBudgetExhausted, match="MODEL_BUDGET"):
        value.model()
    with pytest.raises(ResearchBudgetExhausted, match="TOOL_BUDGET"):
        value.tools(3)
    assert value.snapshot().tool_calls_attempted == 0
    value.tools(2)
    assert value.snapshot().model_calls_attempted == 1
    assert value.snapshot().tool_calls_attempted == 2


@pytest.mark.asyncio
async def test_stalled_await_cancelled_by_budget(monkeypatch):
    monkeypatch.setattr(ResearchBudget, "remaining_seconds", property(lambda self: 0.01))
    cancelled = asyncio.Event()

    async def stalled():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    with pytest.raises(ResearchBudgetExhausted, match="TIME_BUDGET"):
        await budget().wait(stalled)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_timeout():
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await budget().wait(cancelled)


@pytest.mark.parametrize(
    "field,value",
    [
        ("research_mode", "auto"),
        ("research_max_seconds", 29),
        ("research_max_model_calls", 17),
        ("research_max_tool_calls", True),
    ],
)
def test_research_request_limits(field, value):
    with pytest.raises(ValueError):
        AgentTurnRequest(
            conversation_id="c",
            owner_principal="u",
            channel=AgentChannel.CONSOLE,
            content="Research",
            **{field: value},
        )


def test_receipt_rejects_private_diagnostic_prose():
    payload = budget().snapshot().model_dump()
    payload["gaps"] = ["https://private/secret"]
    with pytest.raises(ValidationError):
        CopilotResearchReceipt.model_validate(payload)


def test_research_receipt_survives_large_receipt_compaction():
    import json

    from application.ports.agent_model_provider import ModelResponse
    from application.services.agent_runtime_receipts import model_receipt_json

    summary = budget().snapshot(phase="FINISHED", stop_reason="MODEL_BUDGET").model_dump()
    response = ModelResponse(text="Safe", model="test")
    encoded = model_receipt_json(response, [response], 1, ["x" * 4000] * 32, research=summary)
    assert json.loads(encoded)["research"] == summary
    assert len(encoded.encode()) <= 16384


def test_completed_steps_survive_budget_stop_and_evidence_gaps():
    value = budget()
    value.snapshot(phase="READING")
    value.read_completed()
    value.snapshot(phase="SYNTHESIZING")
    result = value.snapshot(phase="FINISHED", stop_reason="MODEL_BUDGET")
    states = {step.code: step.status for step in result.steps}
    assert states["READ"] == "COMPLETED"
    assert states["SYNTHESIZE"] == "STOPPED"
    assert states["CHALLENGE"] == "SKIPPED"
    value = budget()
    value.complete_step("SYNTHESIZE")
    value.complete_step("EVIDENCE_CHECK")
    result = value.snapshot(phase="FINISHED", stop_reason="EVIDENCE_GAP", evidence_status="GAPS")
    assert all(
        step.status == "COMPLETED"
        for step in result.steps
        if step.code in {"SYNTHESIZE", "EVIDENCE_CHECK"}
    )


def test_synthesis_completed_before_challenge_timeout():
    value = ResearchBudget(mode="challenge", max_seconds=30, max_model_calls=2, max_tool_calls=1)
    value.complete_step("SYNTHESIZE")
    value.snapshot(phase="CHALLENGING")
    result = value.snapshot(phase="FINISHED", stop_reason="TIME_BUDGET")
    states = {step.code: step.status for step in result.steps}
    assert states["SYNTHESIZE"] == "COMPLETED"
    assert states["CHALLENGE"] == "STOPPED"
    assert result.challenge_performed is False


def test_verified_refs_survive_large_typed_answer_compaction():
    import json

    from application.dto.agent_answer import AgentAnswerBlock, AgentAnswerEnvelope
    from application.ports.agent_model_provider import ModelResponse
    from application.services.agent_answer_protocol import agent_answer_envelope_json
    from application.services.agent_runtime_receipts import model_receipt_json

    refs = ["req_synthetic/result/data/last", "req_synthetic/result/data/as_of"]
    answer = AgentAnswerEnvelope(
        blocks=tuple(
            AgentAnswerBlock(
                kind="INFERENCE",
                text="Synthetic qualitative interpretation. " * 90,
                evidence_refs=(refs[index % 2],),
            )
            for index in range(8)
        )
    )
    summary = (
        budget()
        .snapshot(phase="FINISHED", stop_reason="COMPLETED", verified_refs=[*refs, refs[0]])
        .model_dump()
    )
    response = ModelResponse(text="Safe", model="test")
    encoded = model_receipt_json(
        response,
        [response],
        1,
        [],
        research=summary,
        answer_envelope=agent_answer_envelope_json(answer),
    )
    restored = json.loads(encoded)
    assert restored["answer_envelope"]["truncated"] is True
    assert restored["research"]["verified_refs"] == refs
    assert len(encoded.encode()) <= 16384


@pytest.mark.parametrize("reference", ["req/private text", "req/" + "x" * 160, "req/<script>"])
def test_verified_ref_schema_rejects_unbounded_or_unsafe_refs(reference):
    with pytest.raises(ValidationError):
        budget().snapshot(verified_refs=[reference])


def test_combined_large_answer_and_links_keep_receipt_within_bound():
    import json

    from application.ports.agent_model_provider import ModelResponse
    from application.services.agent_runtime_receipts import model_receipt_json

    response = ModelResponse(text="answer", model="test")
    research = (
        ResearchBudget(mode="research", max_seconds=60, max_model_calls=2, max_tool_calls=2)
        .snapshot()
        .model_dump()
    )
    research["verified_refs"] = [f"req_{i}/result/" + "x" * 130 for i in range(32)]
    encoded = model_receipt_json(
        response,
        [response],
        1,
        [],
        research=research,
        artifact_urls=["/api/agent/artifacts/" + str(i) + "a" * 450 + ".png" for i in range(20)],
        answer_envelope=json.dumps({"schema_version": 1, "blocks": [{"text": "x" * 7000}]}),
    )
    assert len(encoded.encode()) <= 16_384
    stored = json.loads(encoded)
    assert stored["research"]["verified_refs"] == research["verified_refs"]
