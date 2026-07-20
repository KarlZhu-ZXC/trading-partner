"""Secret redaction port for logs, envelopes, and audit payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class SecretRedactor(Protocol):
    def redact_mapping(
        self,
        value: Mapping[str, object],
    ) -> dict[str, object]:
        """Return a deep-copied mapping with secret values redacted."""
        ...

    def redact_text(self, value: str) -> str:
        """Return text with secret-like substrings redacted."""
        ...
