"""Private image storage port for the shared Agent Runtime."""

from __future__ import annotations

from typing import Protocol

from domain.agent.attachments import AgentImageAttachment


class AgentAttachmentStore(Protocol):
    """Store and retrieve validated Console Agent image bytes."""

    def save(
        self,
        *,
        attachment_id: str,
        content: bytes,
        media_type: str,
        original_name: str | None,
    ) -> AgentImageAttachment:
        """Persist one image and return its durable metadata."""
        ...

    def read(self, attachment: AgentImageAttachment) -> bytes:
        """Read and integrity-check one private image."""
        ...

    def delete(self, attachment: AgentImageAttachment) -> None:
        """Remove an image which was never durably attached to a message."""
        ...


__all__ = ["AgentAttachmentStore"]
