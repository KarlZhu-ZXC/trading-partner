"""HTTP transport port (Phase 1E E1).

Transport handles bytes only — no business parsing, body logging, or
non-allowlist redirects. ``params`` are pre-encoded ``str`` values so adapters
own Decimal/date canonicalization before request construction (§22).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """Outbound HTTP request bytes envelope.

    ``__repr__`` is intentionally secret-safe: it never renders param values,
    header values, query string, or body bytes (credentials may live there).
    """

    method: Literal["GET", "POST"]
    url: str
    params: Mapping[str, str]
    headers: Mapping[str, str]
    body: bytes | None
    timeout_seconds: float

    def __repr__(self) -> str:
        parts = urlsplit(self.url)
        host = parts.hostname or ""
        path = parts.path or ""
        # Scheme + host + path only — never query/fragment/userinfo.
        safe_base = f"{parts.scheme}://{host}{path}" if parts.scheme else f"{host}{path}"
        param_keys = sorted(str(k) for k in self.params) if self.params else []
        header_keys = sorted(str(k) for k in self.headers) if self.headers else []
        body_len = len(self.body) if self.body is not None else 0
        return (
            f"HttpRequest(method={self.method!r}, url={safe_base!r}, "
            f"param_keys={param_keys!r}, header_keys={header_keys!r}, "
            f"body_len={body_len}, timeout_seconds={self.timeout_seconds!r})"
        )


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    async def send(self, request: HttpRequest) -> HttpResponse:
        """Send one HTTP request and return the raw response."""
        ...
