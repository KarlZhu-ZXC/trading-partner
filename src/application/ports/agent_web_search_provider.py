"""Provider-neutral private Web Search port for the shared Agent runtime."""

from __future__ import annotations

from typing import Protocol

from application.ports.agent_tool_gateway import AgentToolResult


class AgentWebSearchProvider(Protocol):
    async def search(self, query: str, *, max_results: int = 5) -> AgentToolResult: ...

    async def aclose(self) -> None: ...


__all__ = ["AgentWebSearchProvider"]
