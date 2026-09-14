"""Closed deterministic research progress; never model reasoning or private prose."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ResearchPhase = Literal["QUEUED", "READING", "SYNTHESIZING", "CHALLENGING", "CHECKING", "FINISHED"]
ResearchStopReason = Literal[
    "COMPLETED",
    "TIME_BUDGET",
    "MODEL_BUDGET",
    "TOOL_BUDGET",
    "ROUND_LIMIT",
    "EVIDENCE_GAP",
    "CANCELLED",
    "FAILED",
]


class ResearchStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: Literal["READ", "SYNTHESIZE", "CHALLENGE", "EVIDENCE_CHECK"]
    status: Literal["PENDING", "RUNNING", "COMPLETED", "SKIPPED", "STOPPED"]


class CopilotResearchReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["research", "challenge"]
    phase: ResearchPhase
    stop_reason: ResearchStopReason | None = None
    max_seconds: int = Field(ge=30, le=600)
    max_model_calls: int = Field(ge=1, le=16)
    max_tool_calls: int = Field(ge=1, le=48)
    elapsed_ms: int = Field(ge=0)
    model_calls_attempted: int = Field(ge=0)
    tool_calls_attempted: int = Field(ge=0)
    completed_reads: int = Field(ge=0)
    usage_complete: bool = False
    cost_usd: None = None
    provider_internal_attempts: None = None
    challenge_performed: bool = False
    verified_claims: int = Field(default=0, ge=0)
    blocked_claims: int = Field(default=0, ge=0)
    verified_refs: list[
        Annotated[
            str,
            Field(
                min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)+$"
            ),
        ]
    ] = Field(default_factory=list, max_length=32)
    evidence_status: Literal["NOT_CHECKED", "VERIFIED", "GAPS", "STOPPED"] = "NOT_CHECKED"
    gaps: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]] = Field(
        default_factory=list, max_length=32
    )
    steps: list[ResearchStep] = Field(default_factory=list, max_length=4)
