"""Read-only aggregate for pending research judgments, without state hydration."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResearchAttentionSummary:
    subject_id: str
    title: str
    pending_count: int


class ResearchAttentionReader(Protocol):
    def pending_by_subject(self) -> tuple[ResearchAttentionSummary, ...]:
        """All non-archived subjects with PROPOSED candidates, in stable ID order.

        Expiry remains a separate explicit state transition. No read expires or
        confirms candidates, and pending counts must not be capped by list limits.
        """
        ...
