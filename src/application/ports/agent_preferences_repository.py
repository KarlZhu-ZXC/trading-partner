"""Persistence boundary for owner-scoped Agent presentation preferences."""

from __future__ import annotations

from typing import Protocol

from domain.agent.preferences import AgentPreferences, AgentPreferencesRevision


class AgentPreferencesRepository(Protocol):
    def get(self, owner_principal: str) -> AgentPreferences | None: ...

    def create(
        self,
        value: AgentPreferences,
        revision: AgentPreferencesRevision,
    ) -> AgentPreferences: ...

    def update(
        self,
        value: AgentPreferences,
        *,
        expected_version: int,
        revision: AgentPreferencesRevision,
    ) -> AgentPreferences: ...

    def list_history(
        self,
        owner_principal: str,
        *,
        limit: int = 100,
    ) -> tuple[AgentPreferencesRevision, ...]: ...


__all__ = ["AgentPreferencesRepository"]
