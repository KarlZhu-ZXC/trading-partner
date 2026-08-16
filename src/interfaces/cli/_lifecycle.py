"""Lifecycle helper for operational CLI entry points.

The operational CLIs that follow the plain build → run → exactly-once aclose
shape use this async context manager instead of repeating the try/finally.
CLIs with locking, supervision, or multi-container semantics keep their own
lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from bootstrap import ApplicationContainer, build_default_application


@asynccontextmanager
async def application_container() -> AsyncIterator[ApplicationContainer]:
    container = build_default_application()
    try:
        yield container
    finally:
        await container.aclose()
