"""Exact-confirmation persistence port for Agent pending actions.

The repository has no execution method. The runtime may persist a pending action;
the action service separately validates exact confirmation before dispatching an
allowed operation through an existing application service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.agent.enums import AgentChannel, AgentPendingActionStatus
from domain.agent.models import AgentPendingAction


class AgentPendingActionRepository(Protocol):
    def create_pending_action(self, value: AgentPendingAction) -> AgentPendingAction: ...

    def get_pending_action(self, action_id: str) -> AgentPendingAction | None: ...

    def get_pending_action_by_token_sha256(
        self,
        token_sha256: str,
    ) -> AgentPendingAction | None: ...

    def get_by_token_sha256(self, token_sha256: str) -> AgentPendingAction | None: ...

    def transition_exact(
        self,
        action_id: str,
        status: AgentPendingActionStatus,
        *,
        arguments_sha256: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int,
        token_sha256: str | None = None,
        result_receipt_json: str | None = None,
        now: datetime | None = None,
    ) -> AgentPendingAction: ...

    def reissue_confirmation_token(
        self,
        action_id: str,
        *,
        conversation_id: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int,
        token_sha256: str,
        now: datetime,
    ) -> AgentPendingAction: ...

    def list_pending_actions(
        self,
        conversation_id: str,
        *,
        channel: AgentChannel | None = None,
        principal: str | None = None,
        include_terminal: bool = False,
        limit: int = 100,
    ) -> tuple[AgentPendingAction, ...]: ...

    def list_unresolved(
        self,
        *,
        now: datetime,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentPendingAction, ...]: ...

    def expire_due(self, *, now: datetime | None = None, limit: int = 100) -> int: ...
