"""Caller identity and assurance carried across write boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from domain.common.errors import DataContractError


class ActorType(StrEnum):
    USER = "user"
    EXTERNAL_AGENT = "external_agent"
    SYSTEM = "system"


class ActorAssurance(StrEnum):
    CALLER_ASSERTED = "caller_asserted"
    AUTHENTICATED = "authenticated"


class ActorSubmissionChannel(StrEnum):
    DIRECT = "direct"
    CODEX_CHAT = "codex_chat"
    MCP_CHAT = "mcp_chat"

    @property
    def is_current_chat(self) -> bool:
        return self in {ActorSubmissionChannel.CODEX_CHAT, ActorSubmissionChannel.MCP_CHAT}


CURRENT_CHAT_SUBMISSION_VALUES = frozenset(
    {
        ActorSubmissionChannel.CODEX_CHAT.value,
        ActorSubmissionChannel.MCP_CHAT.value,
    }
)


@dataclass(frozen=True, slots=True)
class ActorContext:
    actor_type: ActorType
    principal_id: str
    assurance: ActorAssurance
    request_id: str
    submitted_via: ActorSubmissionChannel = ActorSubmissionChannel.DIRECT
    authorization_note: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("principal_id", self.principal_id),
            ("request_id", self.request_id),
        ):
            if not value.strip() or len(value) > 128:
                raise DataContractError(f"{field_name} must be a bounded non-blank string")
        if self.authorization_note is not None:
            note = self.authorization_note.strip()
            if not note or len(note) > 4000:
                raise DataContractError(
                    "authorization_note must be a non-blank string of at most 4000 characters"
                )
        if self.submitted_via.is_current_chat and self.actor_type is not ActorType.USER:
            raise DataContractError("current-chat submission requires actor_type=user")
        if self.submitted_via.is_current_chat and self.authorization_note is None:
            raise DataContractError("current-chat submission requires authorization_note")

    @classmethod
    def caller_asserted(cls, confirmed_by: str, *, request_id: str) -> ActorContext:
        return cls(
            actor_type=ActorType(confirmed_by.strip()),
            principal_id=confirmed_by.strip(),
            assurance=ActorAssurance.CALLER_ASSERTED,
            request_id=request_id,
        )

    @classmethod
    def current_chat_authorized(
        cls,
        *,
        request_id: str,
        authorization_note: str,
        submitted_via: ActorSubmissionChannel | str = ActorSubmissionChannel.MCP_CHAT,
    ) -> ActorContext:
        """Represent an explicit local user's decision relayed by the current chat host."""
        channel = (
            submitted_via
            if isinstance(submitted_via, ActorSubmissionChannel)
            else ActorSubmissionChannel(submitted_via)
        )
        if not channel.is_current_chat:
            raise DataContractError("submitted_via must be a current-chat channel")
        return cls(
            actor_type=ActorType.USER,
            principal_id=ActorType.USER.value,
            assurance=ActorAssurance.CALLER_ASSERTED,
            request_id=request_id,
            submitted_via=channel,
            authorization_note=authorization_note,
        )

    @classmethod
    def codex_chat_authorized(
        cls,
        *,
        request_id: str,
        authorization_note: str,
    ) -> ActorContext:
        """Compatibility alias for Codex hosts; same gate as ``mcp_chat``."""
        return cls.current_chat_authorized(
            request_id=request_id,
            authorization_note=authorization_note,
            submitted_via=ActorSubmissionChannel.CODEX_CHAT,
        )
