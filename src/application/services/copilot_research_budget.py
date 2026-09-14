"""Per-turn monotonic attempted-call budgets with cancellation-safe awaits."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from application.dto.copilot_research import (
    CopilotResearchReceipt,
    ResearchPhase,
    ResearchStep,
    ResearchStopReason,
)

T = TypeVar("T")


class ResearchBudgetExhausted(Exception):
    def __init__(self, reason: ResearchStopReason) -> None:
        self.reason = reason
        super().__init__(reason)


class ResearchBudget:
    def __init__(
        self,
        *,
        mode: Literal["research", "challenge"],
        max_seconds: int,
        max_model_calls: int,
        max_tool_calls: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._now = monotonic
        self._started = monotonic()
        self._steps = [
            ResearchStep(
                code=code,
                status="SKIPPED" if code == "CHALLENGE" and mode == "research" else "PENDING",
            )
            for code in ("READ", "SYNTHESIZE", "CHALLENGE", "EVIDENCE_CHECK")
        ]
        self._receipt = CopilotResearchReceipt(
            mode=mode,
            phase="QUEUED",
            max_seconds=max_seconds,
            max_model_calls=max_model_calls,
            max_tool_calls=max_tool_calls,
            elapsed_ms=0,
            model_calls_attempted=0,
            tool_calls_attempted=0,
            completed_reads=0,
        )

    @property
    def remaining_seconds(self) -> float:
        return self._receipt.max_seconds - (self._now() - self._started)

    def check(self) -> None:
        if self.remaining_seconds <= 0:
            raise ResearchBudgetExhausted("TIME_BUDGET")

    def model(self) -> None:
        self.check()
        if self._receipt.model_calls_attempted >= self._receipt.max_model_calls:
            raise ResearchBudgetExhausted("MODEL_BUDGET")
        self._receipt = self._receipt.model_copy(
            update={"model_calls_attempted": self._receipt.model_calls_attempted + 1}
        )

    def tools(self, count: int) -> None:
        self.check()
        if self._receipt.tool_calls_attempted + count > self._receipt.max_tool_calls:
            raise ResearchBudgetExhausted("TOOL_BUDGET")
        self._receipt = self._receipt.model_copy(
            update={"tool_calls_attempted": self._receipt.tool_calls_attempted + count}
        )

    async def wait(self, operation: Callable[[], Awaitable[T]]) -> T:
        self.check()
        try:
            async with asyncio.timeout(self.remaining_seconds):
                result = await operation()
            self.check()
            return result
        except TimeoutError as error:
            raise ResearchBudgetExhausted("TIME_BUDGET") from error

    def complete_step(
        self, code: Literal["READ", "SYNTHESIZE", "CHALLENGE", "EVIDENCE_CHECK"]
    ) -> None:
        self._steps = [
            step.model_copy(update={"status": "COMPLETED"}) if step.code == code else step
            for step in self._steps
        ]

    def stop_step(self, code: Literal["READ", "SYNTHESIZE", "CHALLENGE", "EVIDENCE_CHECK"]) -> None:
        self._steps = [
            step.model_copy(update={"status": "STOPPED"}) if step.code == code else step
            for step in self._steps
        ]

    def read_completed(self) -> None:
        self.complete_step("READ")
        self._receipt = self._receipt.model_copy(
            update={"completed_reads": self._receipt.completed_reads + 1}
        )

    def snapshot(
        self,
        *,
        phase: ResearchPhase | None = None,
        stop_reason: ResearchStopReason | None = None,
        challenge_performed: bool | None = None,
        evidence_status: str | None = None,
        gaps: list[str] | None = None,
        verified_claims: int = 0,
        blocked_claims: int = 0,
        verified_refs: list[str] | None = None,
    ) -> CopilotResearchReceipt:
        value = self._receipt.model_dump()
        value["verified_claims"] = verified_claims
        value["blocked_claims"] = blocked_claims
        if verified_refs is not None:
            value["verified_refs"] = list(dict.fromkeys(verified_refs))[:32]
        value["elapsed_ms"] = max(0, int((self._now() - self._started) * 1000))
        if phase is not None:
            value["phase"] = phase
        if stop_reason is not None:
            value["stop_reason"] = stop_reason
        if challenge_performed is not None:
            value["challenge_performed"] = challenge_performed
        if evidence_status is not None:
            value["evidence_status"] = evidence_status
        if gaps is not None:
            value["gaps"] = list(dict.fromkeys(gaps))[:32]
        phase_code = {
            "READING": "READ",
            "SYNTHESIZING": "SYNTHESIZE",
            "CHALLENGING": "CHALLENGE",
            "CHECKING": "EVIDENCE_CHECK",
        }
        active_code = phase_code.get(value["phase"])
        self._steps = [
            step.model_copy(update={"status": "RUNNING"})
            if step.code == active_code and step.status == "PENDING"
            else step
            for step in self._steps
        ]
        if value["phase"] == "FINISHED":
            self._steps = [
                step.model_copy(
                    update={
                        "status": (
                            "SKIPPED"
                            if step.status == "PENDING"
                            and value["stop_reason"] in {"COMPLETED", "EVIDENCE_GAP"}
                            else "STOPPED"
                        )
                    }
                )
                if step.status in {"PENDING", "RUNNING"}
                else step
                for step in self._steps
            ]
        value["steps"] = [step.model_dump() for step in self._steps]
        self._receipt = CopilotResearchReceipt.model_validate(value)
        return self._receipt
