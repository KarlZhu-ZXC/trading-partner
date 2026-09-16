"""Opt-in real-model acceptance on fixed synthetic facts and an in-memory runtime.

No business database, market provider, web search, broker or private note is wired.
Automatic checks measure protocol retention, not semantic research correctness.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from application.dto.agent import AgentTurnRequest
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_model_provider import AgentModelProvider, ModelRequest, ModelResponse
from application.ports.agent_tool_gateway import (
    AgentToolDescriptor,
    AgentToolReceipt,
    AgentToolResult,
)
from application.services.agent_context_service import AgentContextService
from application.services.agent_runtime_service import AgentRuntimeService
from domain.agent.enums import AgentChannel
from domain.common.errors import DataContractError
from interfaces.agent.prompts import AGENT_SYSTEM_PROMPT
from interfaces.cli.agent_behavior_evaluation import (
    _Clock,
    _fingerprint_manifest,
    _Ids,
    _Repository,
)


@dataclass(frozen=True)
class ResearchAcceptanceCase:
    case_id: str
    question: str
    capability: str
    operation: str
    data: dict[str, Any]
    required_paths: tuple[str, ...]
    mode: Literal["research", "challenge"] = "research"


CASES = (
    ResearchAcceptanceCase(
        "inline_quote",
        "读取合成标的 SYNTH 的报价，在解释中用内联字段引用呈现价格和报价日期，"
        "说明该时点的报价不能保证现在可成交。",
        "market_data_get",
        "quote",
        {
            "instrument_id": "equity:US:SYNTH",
            "last": "123.45",
            "currency": "USD",
            "quote_at": "2026-08-13T11:00:00Z",
            "price_basis": "last",
            "freshness": "stale",
        },
        ("/data/last", "/data/quote_at"),
    ),
    ResearchAcceptanceCase(
        "cross_currency",
        "读取合成持仓，引用两项市值，解释为何不能直接相加或比较仓位大小。",
        "portfolio_get",
        "positions",
        {
            "positions": [
                {
                    "instrument_id": "equity:US:SYNTH",
                    "account_ref": "synthetic_us",
                    "market_value": "100",
                    "currency": "USD",
                    "snapshot_at": "2026-08-13T10:00:00Z",
                },
                {
                    "instrument_id": "equity:CN:600000",
                    "account_ref": "synthetic_cn",
                    "market_value": "100",
                    "currency": "CNY",
                    "snapshot_at": "2026-08-12T10:00:00Z",
                },
            ]
        },
        ("/data/positions/0/market_value", "/data/positions/1/market_value"),
    ),
    ResearchAcceptanceCase(
        "missing_fees",
        "读取合成收益数据，引用费前收益与费用字段，说明目前能否判断净收益，不要把缺失费用当零。",
        "portfolio_get",
        "performance",
        {
            "realized_pnl": "80",
            "fees": None,
            "net_trading_pnl": None,
            "currency": "USD",
            "basis": "gross_before_fees",
            "account_ref": "synthetic_us",
        },
        ("/data/realized_pnl", "/data/fees"),
    ),
    ResearchAcceptanceCase(
        "annual_counter_review",
        "读取合成财务数据并反方审查：收入增长已经证明最近单季改善了吗？"
        "引用收入与期间类型，区分年度数据、推断与还缺少的证据。",
        "us_company_get",
        "financials",
        {
            "instrument_id": "equity:US:SYNTH",
            "revenue": "100",
            "currency": "USD",
            "unit": "millions",
            "period_type": "ANNUAL",
            "period_end": "2025-12-31",
            "published_at": "2026-02-01T12:00:00Z",
        },
        ("/data/revenue", "/data/period_type"),
        "challenge",
    ),
)


class SyntheticResearchGateway:
    """Only the exact fixture operation is callable; no delegate/fallback exists."""

    def __init__(self, case: ResearchAcceptanceCase) -> None:
        self.case = case
        self.read_count = 0

    def search(
        self, query: str, limit: int = 3, *, mode: str = "read"
    ) -> tuple[AgentToolDescriptor, ...]:
        if mode != "read":
            return ()
        return (
            AgentToolDescriptor(
                capability=self.case.capability,
                operation=self.case.operation,
                description="Read this acceptance case's fixed synthetic snapshot; no arguments.",
                schema={"type": "object", "properties": {}, "additionalProperties": False},
                effect="READ_DURABLE",
                confirmation_required=False,
                auto_allowed=True,
            ),
        )

    async def read(
        self, capability: str, operation: str | None, arguments: Mapping[str, Any]
    ) -> AgentToolResult:
        if (capability, operation) != (self.case.capability, self.case.operation) or arguments:
            raise DataContractError(
                "Synthetic evaluation operation does not match its closed schema"
            )
        self.read_count += 1
        return AgentToolResult(
            result={"ok": True, "as_of": "2026-08-13T12:00:00Z", "data": self.case.data},
            receipt=AgentToolReceipt(
                capability=capability,
                operation=operation,
                effect="READ_DURABLE",
                request_id=f"req_acceptance_{self.case.case_id}_{self.read_count}",
                source_codes=("SYNTHETIC_ACCEPTANCE",),
            ),
        )

    async def propose(
        self, capability: str, operation: str, arguments: Mapping[str, Any]
    ) -> AgentToolResult:
        raise DataContractError("Synthetic evaluation has no write capability")


class _RecordedModel:
    """Capture only synthetic final text, not wire payloads or hidden reasoning."""

    def __init__(self, provider: AgentModelProvider) -> None:
        self.provider = provider
        self.answers: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        call: dict[str, Any] = {
            "ordinal": len(self.calls) + 1,
            "requested_effort": request.reasoning_effort,
            "message_count": len(request.messages),
            "status": "INTERRUPTED",
        }
        self.calls.append(call)
        try:
            response = await self.provider.complete(request)
            call.update(
                status="RETURNED",
                text_chars=len(response.text),
                tool_calls=[tool.name for tool in response.tool_calls],
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
            )
            if response.text and not response.tool_calls:
                self.answers.append(response.text[:64_000])
            return response
        except Exception:
            call["status"] = "FAILED"
            raise
        finally:
            call["elapsed_ms"] = round((time.monotonic() - started) * 1000)

    async def aclose(self) -> None:
        pass  # Caller owns the provider across cases.


async def run_live_research_acceptance(
    provider: AgentModelProvider,
    *,
    cases: tuple[ResearchAcceptanceCase, ...] = CASES,
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None,
    on_case: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    fingerprint = _fingerprint_manifest()
    fingerprint["src/interfaces/cli/copilot_research_live_evaluation.py"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    results: list[dict[str, Any]] = []
    ids = _Ids()
    for case in cases:
        repository, clock = _Repository(), _Clock()
        port = cast(AgentConversationRepository, repository)
        context = AgentContextService(repository=port, clock=clock, id_generator=ids)
        conversation = context.create_conversation(
            owner_principal="synthetic-eval", title=case.case_id
        )
        gateway = SyntheticResearchGateway(case)
        recorded_model = _RecordedModel(provider)
        runtime = AgentRuntimeService(
            repository=port,
            context_service=context,
            model_provider=recorded_model,
            tool_gateway=gateway,
            clock=clock,
            id_generator=ids,
            system_prompt=AGENT_SYSTEM_PROMPT + "\n本次全部数据与标的均为合成验收样本。",
        )
        try:
            response = await runtime.run_turn(
                AgentTurnRequest(
                    conversation_id=conversation.conversation_id,
                    owner_principal="synthetic-eval",
                    channel=AgentChannel.CONSOLE,
                    content=case.question,
                    research_mode=case.mode,
                    research_max_seconds=180,
                    research_max_model_calls=5,
                    research_max_tool_calls=6,
                    reasoning_effort=reasoning_effort,
                )
            )
            message = repository.messages[conversation.conversation_id][-1]
            receipt = json.loads(message.model_receipt_json or "{}")
            research = receipt.get("research", {})
            envelope = receipt.get("answer_envelope", {})
            refs = research.get("verified_refs", [])
            errors = []
            if not gateway.read_count:
                errors.append("NO_FIXTURE_READ")
            if research.get("stop_reason") != "COMPLETED":
                errors.append("INCOMPLETE_RESEARCH")
            if research.get("blocked_claims") != 0:
                errors.append("BLOCKED_ANSWER_CONTENT")
            if envelope.get("generated_by") != "model":
                errors.append("UNSTRUCTURED_ANSWER")
            if any(not any(ref.endswith(path) for ref in refs) for path in case.required_paths):
                errors.append("REQUIRED_FIELD_NOT_RETAINED")
            if not any(b.get("kind") in {"INFERENCE", "GAP"} for b in envelope.get("blocks", [])):
                errors.append("EXPLANATION_NOT_RETAINED")
            if case.case_id == "inline_quote" and not all(
                marker in response.text
                for marker in ("〔result/data/last:", "〔result/data/quote_at:")
            ):
                errors.append("INLINE_FIELD_NOT_RETAINED")
            if case.mode == "challenge" and not research.get("challenge_performed"):
                errors.append("COUNTER_REVIEW_NOT_PERFORMED")
            result = {
                "case_id": case.case_id,
                "passed": not errors,
                "errors": errors,
                "research": research,
                "answer": response.text,
                "usage": receipt.get("usage"),
                "model": message.model,
                "semantic_review": "REQUIRED",
                "synthetic_draft_answers": recorded_model.answers,
                "model_calls": recorded_model.calls,
            }
        except Exception:  # noqa: BLE001 - do not emit provider exception strings or payloads
            result = {
                "case_id": case.case_id,
                "passed": False,
                "errors": ["EVALUATION_FAILED"],
                "semantic_review": "UNAVAILABLE",
                "model_calls": recorded_model.calls,
            }
        results.append(result)
        if on_case:
            on_case(result)
    return {
        "schema_version": 1,
        "synthetic_data_only": True,
        "live_model": True,
        "passed": bool(results) and all(item["passed"] for item in results),
        "semantic_correctness": "NOT_AUTOMATICALLY_VERIFIED",
        "fingerprint_manifest": fingerprint,
        "results": results,
    }
