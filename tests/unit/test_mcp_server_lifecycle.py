"""Production MCP stdio lifecycle tests (Phase 1E E5b)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Coroutine
from typing import Any

import pytest

from interfaces.mcp import server as server_module


def test_sensitive_http_dependency_logs_are_disabled() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).disabled = False

    server_module._suppress_sensitive_http_client_logs()

    assert logging.getLogger("httpx").disabled is True
    assert logging.getLogger("httpcore").disabled is True


class _RecordingContainer:
    def __init__(self, events: list[tuple[str, asyncio.AbstractEventLoop]]) -> None:
        self._events = events
        self.closed = False

    async def aclose(self) -> None:
        self._events.append(("aclose", asyncio.get_running_loop()))
        self.closed = True


class _RecordingServer:
    def __init__(
        self,
        events: list[tuple[str, asyncio.AbstractEventLoop]],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._events = events
        self._failure = failure

    async def run_stdio_async(self) -> None:
        self._events.append(("run_stdio_async", asyncio.get_running_loop()))
        if self._failure is not None:
            raise self._failure

    def run(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("the synchronous FastMCP.run API must not be called")


@pytest.mark.asyncio
async def test_run_stdio_runs_and_closes_container_on_same_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, asyncio.AbstractEventLoop]] = []
    container = _RecordingContainer(events)
    mcp_server = _RecordingServer(events)
    monkeypatch.setattr(server_module, "build_default_application", lambda: container)
    monkeypatch.setattr(server_module, "create_mcp_server", lambda value: mcp_server)

    await server_module._run_stdio()

    assert [name for name, _loop in events] == ["run_stdio_async", "aclose"]
    assert events[0][1] is asyncio.get_running_loop()
    assert events[1][1] is events[0][1]
    assert container.closed is True


@pytest.mark.asyncio
async def test_run_stdio_closes_container_and_preserves_server_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, asyncio.AbstractEventLoop]] = []
    container = _RecordingContainer(events)
    failure = RuntimeError("stdio failed")
    mcp_server = _RecordingServer(events, failure=failure)
    monkeypatch.setattr(server_module, "build_default_application", lambda: container)
    monkeypatch.setattr(server_module, "create_mcp_server", lambda value: mcp_server)

    with pytest.raises(RuntimeError, match="stdio failed") as raised:
        await server_module._run_stdio()

    assert raised.value is failure
    assert [name for name, _loop in events] == ["run_stdio_async", "aclose"]
    assert events[0][1] is events[1][1]
    assert container.closed is True


@pytest.mark.asyncio
async def test_run_stdio_closes_container_when_server_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, asyncio.AbstractEventLoop]] = []
    container = _RecordingContainer(events)
    failure = LookupError("server construction failed")
    monkeypatch.setattr(server_module, "build_default_application", lambda: container)

    def fail_create(value: object) -> None:
        assert value is container
        raise failure

    monkeypatch.setattr(server_module, "create_mcp_server", fail_create)

    with pytest.raises(LookupError, match="server construction failed") as raised:
        await server_module._run_stdio()

    assert raised.value is failure
    assert [name for name, _loop in events] == ["aclose"]
    assert events[0][1] is asyncio.get_running_loop()


def test_main_invokes_asyncio_run_once(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[Coroutine[Any, Any, None]] = []

    def fake_asyncio_run(coro: Coroutine[Any, Any, None]) -> None:
        assert inspect.iscoroutine(coro)
        received.append(coro)
        coro.close()

    monkeypatch.setattr(server_module.asyncio, "run", fake_asyncio_run)

    server_module.main()

    assert len(received) == 1
