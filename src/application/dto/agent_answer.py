"""Typed final-answer blocks for the shared Agent runtime."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")


class AgentAnswerBlockKind(StrEnum):
    SUMMARY = "SUMMARY"
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    GAP = "GAP"
    NEXT_STEP = "NEXT_STEP"
    CITATION = "CITATION"


class AgentAnswerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AgentAnswerBlockKind
    text: str = Field(min_length=1, max_length=4_000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)
    source_urls: tuple[str, ...] = Field(default=(), max_length=20)
    as_of: str | None = Field(default=None, max_length=80)
    basis: str | None = Field(default=None, max_length=160)

    @field_validator("evidence_refs")
    @classmethod
    def _safe_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(
            _SAFE_REF.fullmatch(item) is None for item in values
        ):
            raise ValueError("evidence_refs must contain unique bounded safe references")
        return values

    @field_validator("source_urls")
    @classmethod
    def _safe_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            parsed = urlsplit(value)
            if (
                len(value) > 2_048
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("source_urls contains an invalid URL")
            if value not in normalized:
                normalized.append(value)
        return tuple(normalized)


class AgentAnswerEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    generated_by: Literal["model", "fallback"] = "model"
    blocks: tuple[AgentAnswerBlock, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _bounded_total(self) -> Self:
        if sum(len(item.text) for item in self.blocks) > 64_000:
            raise ValueError("Agent answer blocks exceed the total text budget")
        return self


__all__ = ["AgentAnswerBlock", "AgentAnswerBlockKind", "AgentAnswerEnvelope"]
