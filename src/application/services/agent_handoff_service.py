"""Create and consume opaque, single-use Agent channel handoff codes."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from application.ports.agent_handoff_repository import AgentHandoffRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.agent.enums import AgentChannel
from domain.agent.models import AgentChannelHandoff
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix


class AgentHandoffService:
    def __init__(
        self,
        repository: AgentHandoffRepository,
        clock: Clock,
        id_generator: IdGenerator,
        *,
        default_ttl_seconds: int = 600,
    ) -> None:
        if type(default_ttl_seconds) is not int or not 30 <= default_ttl_seconds <= 3600:
            raise ValueError("default_ttl_seconds must be in [30,3600]")
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator
        self._default_ttl_seconds = default_ttl_seconds

    def create(
        self,
        *,
        conversation_id: str,
        owner_principal: str,
        target_channel: AgentChannel,
        ttl_seconds: int | None = None,
    ) -> tuple[AgentChannelHandoff, str]:
        if not isinstance(target_channel, AgentChannel):
            raise DataContractError("handoff target_channel is invalid")
        ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if type(ttl) is not int or not 30 <= ttl <= 3600:
            raise DataContractError("handoff ttl_seconds must be in [30,3600]")
        raw_token = secrets.token_urlsafe(32)
        now = self._clock.now()
        value = AgentChannelHandoff(
            handoff_id=self._id_generator.new(EntityIdPrefix.AGENT_HANDOFF),
            conversation_id=conversation_id,
            owner_principal=owner_principal,
            target_channel=target_channel,
            token_sha256=_digest(raw_token),
            expires_at=now + timedelta(seconds=ttl),
            created_at=now,
        )
        return self._repository.create_handoff(value), raw_token

    def consume(
        self,
        raw_token: str,
        *,
        target_channel: AgentChannel,
        owner_principal: str,
        now: datetime | None = None,
    ) -> AgentChannelHandoff:
        if not isinstance(raw_token, str) or not 16 <= len(raw_token) <= 256:
            raise DataContractError("handoff token is invalid")
        effective_now = self._clock.now() if now is None else now
        return self._repository.consume_exact(
            _digest(raw_token),
            target_channel=target_channel,
            owner_principal=owner_principal,
            now=effective_now,
        )


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


__all__ = ["AgentHandoffService"]
