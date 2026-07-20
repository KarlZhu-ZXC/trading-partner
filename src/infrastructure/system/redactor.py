"""Default secret redactor for mappings, logs, and audit payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_REDACTED = "***REDACTED***"

# Key names that indicate secret material.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|token|password|"
    r"secret|authorization|credential|private[_-]?key|bearer)",
    re.IGNORECASE,
)

# Inline secret patterns in free text.
_SECRET_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(
        r"(?i)\b[A-Z0-9_]*(?:API_KEY|API_SECRET|TOKEN|PASSWORD|SECRET)\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@"),
)


class DefaultSecretRedactor:
    def redact_mapping(self, value: Mapping[str, object]) -> dict[str, object]:
        return {str(k): self._redact_value(k, v) for k, v in value.items()}

    def redact_text(self, value: str) -> str:
        if not value:
            return value
        result = value
        for pattern in _SECRET_TEXT_PATTERNS:
            result = pattern.sub(_REDACTED, result)
        return result

    def _redact_value(self, key: object, value: object) -> object:
        key_str = str(key)
        if _SECRET_KEY_RE.search(key_str):
            if value is None:
                return None
            return _REDACTED
        if isinstance(value, Mapping):
            return self.redact_mapping(value)
        if isinstance(value, list | tuple):
            return [self._redact_value(key, item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def redact_any(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return self.redact_mapping(value)
        if isinstance(value, str):
            return self.redact_text(value)
        return value
