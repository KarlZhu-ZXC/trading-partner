"""Durable one-time handoff token boundary for Console/Telegram Agent channels."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.agent.enums import AgentChannel
from domain.agent.models import AgentChannelHandoff


class AgentHandoffRepository(Protocol):
    def create_handoff(self, value: AgentChannelHandoff) -> AgentChannelHandoff: ...

    def get_by_token_sha256(self, token_sha256: str) -> AgentChannelHandoff | None: ...

    def consume_exact(
        self,
        token_sha256: str,
        *,
        target_channel: AgentChannel,
        owner_principal: str,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> AgentChannelHandoff: ...
