"""Bounded Research evidence finalization, without model or persistence access."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from application.dto.agent_answer import AgentAnswerEnvelope
from application.dto.copilot_research import ResearchStopReason
from application.dto.research_presentation import ResearchAnswerPresentation
from application.ports.agent_model_provider import ModelResponse
from application.ports.agent_tool_gateway import AgentToolReceipt
from application.services.agent_answer_protocol import render_agent_answer
from application.services.copilot_research_budget import ResearchBudget
from application.services.copilot_research_evidence import guard_research_answer


@dataclass(frozen=True, slots=True)
class ResearchFinalization:
    response: ModelResponse
    envelope: AgentAnswerEnvelope
    presentation: ResearchAnswerPresentation | None
    summary: dict[str, object]


def finalize_research_answer(
    *,
    final_response: ModelResponse,
    answer_envelope: AgentAnswerEnvelope,
    research_budget: ResearchBudget,
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
    model_responses: Sequence[ModelResponse],
    research_stop: ResearchStopReason | None,
    research_extra_gaps: Sequence[str],
    challenge_performed: bool,
) -> ResearchFinalization:
    """Check one selected answer and close its budget evidence step.

    The caller owns critique selection, event emission and the single durable
    assistant-message/turn transition shared with standard Chat.
    """
    research_presentation = None
    checked = guard_research_answer(
        answer_envelope,
        receipts=receipts,
        tool_payloads=tool_payloads,
        evidence_cutoff=(
            datetime.fromisoformat(research_budget.question_context.evidence_cutoff)
            if research_budget.question_context
            else None
        ),
    )
    research_budget.complete_step("EVIDENCE_CHECK")
    answer_envelope = checked.envelope
    final_response = replace(final_response, text=render_agent_answer(answer_envelope))
    if (
        checked.presentation is not None
        and checked.presentation.rendered_text() == final_response.text
    ):
        research_presentation = checked.presentation
    if final_response.finish_reason == "tool_round_limit":
        research_stop = "ROUND_LIMIT"
    raw_codes = checked.summary.get("codes", [])
    evidence_codes = (
        [code for code in raw_codes if isinstance(code, str)] if isinstance(raw_codes, list) else []
    )
    evidence_codes.extend(research_extra_gaps)
    evidence_status = (
        "GAPS" if research_extra_gaps else str(checked.summary.get("status", "NOT_CHECKED"))
    )
    stop = research_stop or ("EVIDENCE_GAP" if evidence_status == "GAPS" else "COMPLETED")
    if research_stop is not None:
        final_response = replace(final_response, finish_reason=research_stop.lower())
    verified_count = checked.summary.get("verified_count", 0)
    missing_count = checked.summary.get("missing_count", 0)
    research_summary = research_budget.snapshot(
        phase="FINISHED",
        stop_reason=stop,
        challenge_performed=challenge_performed,
        evidence_status="STOPPED" if research_stop else evidence_status,
        gaps=evidence_codes,
        verified_claims=verified_count if type(verified_count) is int else 0,
        blocked_claims=missing_count if type(missing_count) is int else 0,
        verified_refs=list(
            dict.fromkeys(ref for block in answer_envelope.blocks for ref in block.evidence_refs)
        )[:32],
    ).model_dump()
    research_summary["usage_complete"] = (
        len(model_responses) == research_summary["model_calls_attempted"]
        and bool(model_responses)
        and all(
            item.usage is not None
            and item.usage.input_tokens is not None
            and item.usage.output_tokens is not None
            and item.usage.total_tokens is not None
            for item in model_responses
        )
    )
    return ResearchFinalization(
        response=final_response,
        envelope=answer_envelope,
        presentation=research_presentation,
        summary=research_summary,
    )
