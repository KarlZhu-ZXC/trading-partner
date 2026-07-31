"""Bounded batch quote response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from application.dto.tool_envelope import ToolEnvelope


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketBatchQuoteItemDTO(_DTO):
    instrument_id: str
    result: ToolEnvelope[Any]


class MarketBatchQuotesDTO(_DTO):
    items: tuple[MarketBatchQuoteItemDTO, ...]
    total_requested: int = Field(ge=1, le=50)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
