from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from application.dto.agent import AgentTurnRequest
from application.ports.agent_model_provider import (
    ModelCatalog,
    ModelCatalogItem,
    ModelResponse,
    ModelToolCall,
)
from application.ports.agent_tool_gateway import (
    AgentToolDescriptor,
    AgentToolReceipt,
    AgentToolResult,
)
from application.services.agent_context_service import AgentContextService
from application.services.agent_conversation_metrics import AgentConversationMetricsService
from application.services.agent_preferences_service import AgentPreferencesService
from application.services.agent_runtime_service import AgentRuntimeService
from domain.agent.enums import (
    AgentChannel,
    AgentConversationStatus,
    AgentMessageRole,
    AgentPendingActionStatus,
    AgentTurnStatus,
)
from domain.agent.models import (
    AgentConversation,
    AgentMessage,
    AgentPendingAction,
    AgentTurn,
    arguments_digest,
)
from domain.agent.models import AgentToolReceipt as DurableReceipt
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator
from interfaces.console import api
from interfaces.console.agent_api import (
    AGENT_OWNER_PRINCIPAL,
    AgentRuntimeState,
    EphemeralContextRequest,
    _event_stream,
    build_agent_runtime_state,
    get_agent_chart_artifact,
)
from interfaces.mcp.tools.compact import CompactCapabilityRegistry


class _Repository:
    def __init__(self) -> None:
        self.conversations: dict[str, AgentConversation] = {}
        self.messages: dict[str, list[AgentMessage]] = {}
        self.receipts: list[DurableReceipt] = []
        self.turns: dict[str, AgentTurn] = {}

    def create_conversation(self, value: AgentConversation) -> AgentConversation:
        self.conversations[value.conversation_id] = value
        self.messages[value.conversation_id] = []
        return value

    def get_conversation(self, conversation_id: str) -> AgentConversation | None:
        return self.conversations.get(conversation_id)

    def list_conversations(
        self,
        owner_principal: str | None = None,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[AgentConversation, ...]:
        return tuple(
            item
            for item in self.conversations.values()
            if (owner_principal is None or item.owner_principal == owner_principal)
            and (include_archived or item.status.value == "ACTIVE")
        )[:limit]

    def append_message(self, value: AgentMessage) -> AgentMessage:
        conversation = self.conversations[value.conversation_id]
        stored = replace(value, sequence=conversation.next_message_sequence)
        self.messages[value.conversation_id].append(stored)
        self.conversations[value.conversation_id] = replace(
            conversation,
            next_message_sequence=conversation.next_message_sequence + 1,
            updated_at=value.created_at,
            version=conversation.version + 1,
        )
        return stored

    def archive_conversation(
        self,
        conversation_id: str,
        *,
        owner_principal: str | None = None,
        expected_version: int | None = None,
        now: Any = None,
    ) -> AgentConversation:
        current = self.conversations[conversation_id]
        if owner_principal != current.owner_principal or expected_version != current.version:
            from domain.common.errors import PersistenceError

            raise PersistenceError(
                "version conflict",
                code="AGENT_CONVERSATION_VERSION_CONFLICT",
            )
        updated = replace(
            current,
            status=AgentConversationStatus.ARCHIVED,
            updated_at=now or current.updated_at,
            version=current.version + 1,
        )
        self.conversations[conversation_id] = updated
        return updated

    def list_messages(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        newest_first: bool = False,
    ) -> tuple[AgentMessage, ...]:
        values = tuple(
            item
            for item in self.messages.get(conversation_id, ())
            if item.sequence > after_sequence
        )
        return values[-limit:] if newest_first else values[:limit]

    def append_tool_receipt(self, value: DurableReceipt) -> DurableReceipt:
        self.receipts.append(value)
        return value

    def create_turn(self, value: AgentTurn) -> AgentTurn:
        self.turns[value.turn_id] = value
        return value

    def get_turn(self, turn_id: str) -> AgentTurn | None:
        return self.turns.get(turn_id)

    def latest_turn(self, conversation_id: str) -> AgentTurn | None:
        values = [item for item in self.turns.values() if item.conversation_id == conversation_id]
        return max(values, key=lambda item: item.started_at) if values else None

    def list_turns(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
        newest_first: bool = True,
    ) -> tuple[AgentTurn, ...]:
        values = sorted(
            (item for item in self.turns.values() if item.conversation_id == conversation_id),
            key=lambda item: item.started_at,
            reverse=newest_first,
        )
        return tuple(values[:limit])

    def update_turn(
        self,
        turn_id: str,
        *,
        status: AgentTurnStatus,
        expected_version: int,
        assistant_message_id: str | None = None,
        error_code: str | None = None,
        completed_at: Any = None,
        now: Any = None,
    ) -> AgentTurn:
        current = self.turns[turn_id]
        assert current.version == expected_version
        updated = replace(
            current,
            status=status,
            assistant_message_id=assistant_message_id,
            error_code=error_code,
            completed_at=completed_at,
            updated_at=now or current.updated_at,
            version=current.version + 1,
        )
        self.turns[turn_id] = updated
        return updated

    def list_tool_receipts(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[DurableReceipt, ...]:
        return tuple(
            item for item in self.receipts if item.conversation_id == conversation_id
        )[:limit]

    def update_summary(self, *args: Any, **kwargs: Any) -> AgentConversation:
        raise AssertionError("summary is not expected in this focused fixture")


class _Model:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


class _CatalogModel(_Model):
    def __init__(self, responses: list[ModelResponse]) -> None:
        super().__init__(responses)
        self.model = "configured-model"
        self.config = SimpleNamespace(
            model="configured-model",
            api_style="responses",
            reasoning_mode="effort",
            native_web_search="responses_web_search",
            api_key="must-not-leak",
            base_url="https://secret.example/v1",
        )

    async def list_models(self, *, force_refresh: bool = False) -> ModelCatalog:
        _ = force_refresh
        return ModelCatalog(
            models=(
                ModelCatalogItem(id="configured-model"),
                ModelCatalogItem(id="catalog/model", reasoning_efforts=("high", "max")),
            ),
            fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
            cached=True,
        )


class _FailureModel(_Model):
    async def complete(self, request: Any) -> ModelResponse:
        _ = request
        raise RuntimeError("api_key=secret https://secret.example/v1")


class _Gateway:
    def search(self, query: str, limit: int = 3) -> tuple[AgentToolDescriptor, ...]:
        _ = query, limit
        return ()

    async def read(
        self,
        capability: str,
        operation: str | None,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        return AgentToolResult(
            result={"ok": True, "data": dict(arguments)},
            receipt=AgentToolReceipt(
                capability=capability,
                operation=operation,
                request_id="req_test",
                effect="READ_DURABLE",
                result_size_bytes=32,
            ),
        )


class _PendingGateway:
    def __init__(self, action: AgentPendingAction, token: str) -> None:
        self.action = action
        self.token = token

    def get(self, action_id: str) -> AgentPendingAction | None:
        return self.action if action_id == self.action.action_id else None

    def list(self, conversation_id: str, **kwargs: Any) -> tuple[AgentPendingAction, ...]:
        _ = kwargs
        return (self.action,) if conversation_id == self.action.conversation_id else ()

    async def confirm(self, **kwargs: Any) -> Any:
        assert kwargs["token"] == self.token
        self.action = replace(
            self.action,
            status=AgentPendingActionStatus.SUCCEEDED,
            version=self.action.version + 3,
            updated_at=datetime.now(UTC),
        )
        return SimpleNamespace(action=self.action, result={"ok": True, "receipt_id": "r1"})

    def reissue_confirmation(self, **kwargs: Any) -> Any:
        assert kwargs["conversation_id"] == self.action.conversation_id
        assert kwargs["expected_version"] == self.action.version
        self.token = f"{self.token}-rotated"
        self.action = replace(
            self.action,
            token_sha256=hashlib.sha256(self.token.encode()).hexdigest(),
            version=self.action.version + 1,
            updated_at=datetime.now(UTC),
        )
        return SimpleNamespace(action=self.action, confirmation_token=self.token)

    def reject(self, **kwargs: Any) -> AgentPendingAction:
        assert kwargs["token"] == self.token
        self.action = replace(
            self.action,
            status=AgentPendingActionStatus.REJECTED,
            version=self.action.version + 1,
            updated_at=datetime.now(UTC),
        )
        return self.action


def _client_state(*, enabled: bool, model: _Model | None = None) -> tuple[_Repository, str]:
    repository = _Repository()
    context = AgentContextService(
        repository=repository,
        clock=SystemClock(),
        id_generator=Uuid7IdGenerator(),
    )
    conversation = context.create_conversation(
        owner_principal=AGENT_OWNER_PRINCIPAL,
        title="Existing",
    )
    runtime = None
    if enabled and model is not None:
        runtime = AgentRuntimeService(
            repository=repository,
            context_service=context,
            model_provider=model,
            tool_gateway=_Gateway(),
            clock=SystemClock(),
            id_generator=Uuid7IdGenerator(),
            system_prompt="safe prompt",
        )
    api.app.state.console_session_token = "test-token"
    api.app.state.agent_runtime_state = AgentRuntimeState(
        repository=repository,
        context_service=context,
        capability_gateway=None,
        runtime=runtime,
        model_providers={"default": model} if model is not None else {},
        metrics_service=AgentConversationMetricsService(repository),
        status={
            "channel": "CONSOLE",
            "owner_principal": AGENT_OWNER_PRINCIPAL,
            "enabled": enabled,
            "available": runtime is not None,
            "state": "READY" if runtime is not None else "DISABLED",
            "diagnostics": [] if runtime is not None else [{"code": "AGENT_DISABLED"}],
        },
    )
    return repository, conversation.conversation_id


def _pending_client_state() -> tuple[_Repository, str, str, _PendingGateway]:
    repository, conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    created = datetime.now(UTC)
    normalized = {"instrument_id": "equity:US:AAPL"}
    token = "console-confirm-token"
    action = AgentPendingAction(
        action_id="agent_pending_action_1",
        conversation_id=conversation_id,
        channel=AgentChannel.CONSOLE,
        principal=AGENT_OWNER_PRINCIPAL,
        normalized_arguments=normalized,
        arguments_sha256=arguments_digest(normalized),
        presented_summary="Add AAPL to watchlist",
        expires_at=created + timedelta(minutes=5),
        created_at=created,
        updated_at=created,
        status=AgentPendingActionStatus.PRESENTED,
        version=2,
        capability="watchlist_manage",
        operation="add",
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
    )
    pending = _PendingGateway(action, token)
    state = api.app.state.agent_runtime_state
    api.app.state.agent_runtime_state = replace(state, action_gateway=pending)
    return repository, conversation_id, token, pending


@pytest.mark.asyncio
async def test_disabled_agent_keeps_history_readable_but_rejects_create() -> None:
    _repository, conversation_id = _client_state(enabled=False)
    transport = httpx.ASGITransport(app=api.app)
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        status = await client.get("/api/agent/status")
        conversations = await client.get("/api/agent/conversations")
        messages = await client.get(f"/api/agent/conversations/{conversation_id}/messages")
        create = await client.post(
            "/api/agent/conversations",
            headers=headers,
            json={"title": "New"},
        )

    assert status.status_code == 200
    assert status.json()["state"] == "DISABLED"
    assert conversations.json()["count"] == 1
    assert messages.json()["items"] == []
    assert create.status_code == 503
    assert create.json()["detail"]["code"] == "AGENT_DISABLED"


@pytest.mark.asyncio
async def test_console_agent_post_requires_loopback_session_token() -> None:
    _repository, conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            f"/api/agent/conversations/{conversation_id}/messages/stream",
            json={"content": "未经授权"},
        )
    assert response.status_code == 403
    assert "session token" in response.json()["detail"]


def test_ephemeral_context_request_is_strictly_bounded_and_forbids_extra() -> None:
    valid = EphemeralContextRequest(
        location="/research?subject=case_1",
        selection="selected text",
        content_excerpt="a bounded excerpt",
    )
    assert valid.to_dto().location == "/research?subject=case_1"

    with pytest.raises(ValueError):
        EphemeralContextRequest(
            location="/research?subject=case_1",
            content_excerpt="x" * 16_384,
            extra="must be rejected",
        )

    with pytest.raises(ValueError):
        EphemeralContextRequest(
            selection="x" * 8_192,
            content_excerpt="y" * 8_193,
        )

    navigation = EphemeralContextRequest(
        route_hash="/research?subject=case_1#thesis",
        surface="research",
        selected_subject_id="case_1",
        selected_monitor_id="monitor_1",
        selected_run_id="run_1",
        active_tab="thesis",
        workbench_subject_id="case_1",
    ).to_dto()
    assert navigation.as_dict()["selected_subject_id"] == "case_1"
    assert "case_1" in (navigation.as_dict()["route_hash"] or "")


@pytest.mark.asyncio
async def test_console_agent_sse_emits_fixed_events_and_persists_messages() -> None:
    model = _Model([ModelResponse(text="回答", model="fake-model")])
    repository, conversation_id = _client_state(enabled=True, model=model)
    transport = httpx.ASGITransport(app=api.app)
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            f"/api/agent/conversations/{conversation_id}/messages/stream",
            headers=headers,
            json={"content": "问题"},
        )

    events = [line for line in response.text.splitlines() if line.startswith("event: ")]
    payloads = response.text.split("\n\n")
    assert response.status_code == 200
    assert [line.removeprefix("event: ") for line in events] == [
        "message_started",
        "text_delta",
        "completed",
    ]
    assert [item.role.value for item in repository.messages[conversation_id]] == [
        "USER",
        "ASSISTANT",
    ]
    assert len(repository.turns) == 1
    turn = next(iter(repository.turns.values()))
    assert turn.status is AgentTurnStatus.COMPLETED
    completed_payload = next(item for item in payloads if "event: completed" in item)
    assert json.loads(completed_payload.split("data: ", 1)[1])["turn_id"] == turn.turn_id


@pytest.mark.asyncio
async def test_console_lists_provider_models_and_forwards_exact_selection() -> None:
    model = _CatalogModel([ModelResponse(text="回答", model="catalog/model")])
    _repository, conversation_id = _client_state(enabled=True, model=model)
    transport = httpx.ASGITransport(app=api.app)
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        catalog = await client.get("/api/agent/providers/default/models", headers=headers)
        response = await client.post(
            f"/api/agent/conversations/{conversation_id}/messages/stream",
            headers=headers,
            json={
                "content": "问题",
                "model_id": "default",
                "model": "catalog/model",
                "reasoning_effort": "max",
            },
        )

    encoded = json.dumps(catalog.json())
    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["models"]] == [
        "configured-model",
        "catalog/model",
    ]
    assert catalog.json()["models"][1]["reasoning_efforts"] == ["high", "max"]
    assert "must-not-leak" not in encoded
    assert "secret.example" not in encoded
    assert response.status_code == 200
    assert model.requests[0].model == "catalog/model"


@pytest.mark.asyncio
async def test_console_turn_history_is_readable_after_refresh() -> None:
    repository, conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    now = datetime.now(UTC)
    repository.create_turn(
        AgentTurn(
            turn_id="agent_turn_console",
            conversation_id=conversation_id,
            user_message_id="agent_message_missing_user",
            assistant_message_id=None,
            channel=AgentChannel.CONSOLE,
            status=AgentTurnStatus.RUNNING,
            model_id="model",
            reasoning_effort=None,
            started_at=now,
            updated_at=now,
        )
    )
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.get(
            f"/api/agent/conversations/{conversation_id}/turns",
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["latest_turn"]["turn_id"] == "agent_turn_console"


@pytest.mark.asyncio
async def test_console_reconnect_replays_terminal_turn_without_live_runtime() -> None:
    repository, conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    now = datetime.now(UTC)
    user = repository.append_message(
        AgentMessage(
            message_id="agent_message_reconnect_user",
            conversation_id=conversation_id,
            role=AgentMessageRole.USER,
            content="原问题",
            created_at=now,
        )
    )
    assistant = repository.append_message(
        AgentMessage(
            message_id="agent_message_reconnect_assistant",
            conversation_id=conversation_id,
            role=AgentMessageRole.ASSISTANT,
            content="已完成的回答",
            created_at=now,
        )
    )
    repository.create_turn(
        AgentTurn(
            turn_id="agent_turn_reconnect",
            conversation_id=conversation_id,
            user_message_id=user.message_id,
            assistant_message_id=assistant.message_id,
            channel=AgentChannel.CONSOLE,
            status=AgentTurnStatus.COMPLETED,
            started_at=now,
            updated_at=now,
            completed_at=now,
            version=2,
        )
    )
    state = api.app.state.agent_runtime_state
    api.app.state.agent_runtime_state = replace(state, runtime=None)
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.get(
            f"/api/agent/conversations/{conversation_id}/turns/agent_turn_reconnect/stream",
            headers=headers,
        )

    assert response.status_code == 200
    assert "event: completed" in response.text
    assert "已完成的回答" in response.text
    assert '"replay":true' in response.text


@pytest.mark.asyncio
async def test_console_retry_replays_failed_turn_from_durable_original_prompt() -> None:
    model = _Model([ModelResponse(text="重试已完成")])
    repository, conversation_id = _client_state(enabled=True, model=model)
    failed_at = datetime.now(UTC)
    original = repository.append_message(
        AgentMessage(
            message_id="agent_message_retry_source",
            conversation_id=conversation_id,
            role=AgentMessageRole.USER,
            content="读取原始问题",
            channel=AgentChannel.CONSOLE,
            created_at=failed_at,
        )
    )
    repository.create_turn(
        AgentTurn(
            turn_id="agent_turn_retry_source",
            conversation_id=conversation_id,
            user_message_id=original.message_id,
            channel=AgentChannel.CONSOLE,
            status=AgentTurnStatus.FAILED,
            model_id="default",
            reasoning_effort="high",
            error_code="AGENT_TURN_PROCESS_INTERRUPTED",
            started_at=failed_at,
            updated_at=failed_at,
            completed_at=failed_at,
            version=2,
        )
    )
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            f"/api/agent/conversations/{conversation_id}/turns/agent_turn_retry_source/retry",
            headers=headers,
        )

    assert response.status_code == 200
    assert "event: completed" in response.text
    assert "重试已完成" in response.text
    assert len(model.requests) == 1
    assert model.requests[0].reasoning_effort == "high"
    turns = repository.list_turns(conversation_id)
    assert len(turns) == 2
    assert turns[0].status is AgentTurnStatus.COMPLETED
    assert turns[0].turn_id != "agent_turn_retry_source"
    repeated_prompts = [
        item.content for item in repository.messages[conversation_id]
    ].count("读取原始问题")
    assert repeated_prompts == 2


@pytest.mark.asyncio
async def test_agent_conversation_metrics_are_durable_and_secret_safe() -> None:
    repository, conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    repository.append_message(
        AgentMessage(
            message_id="agent_message_metrics",
            conversation_id=conversation_id,
            role=AgentMessageRole.ASSISTANT,
            content="answer",
            model_receipt_json=json.dumps(
                {
                    "model_calls": 2,
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    "latency_ms": 220,
                    "web_search_used": True,
                    "api_style": "responses",
                }
            ),
            created_at=datetime.now(UTC),
        )
    )
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.get(
            f"/api/agent/conversations/{conversation_id}/metrics",
            headers=headers,
        )
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["model_calls"] == 2
    assert metrics["total_tokens"] == 15
    assert metrics["latency_ms"] == 220
    assert metrics["api_styles"] == ["responses"]


@pytest.mark.asyncio
async def test_console_agent_preferences_are_versioned_and_reject_fact_fields() -> None:
    class PreferencesRepo:
        value = None
        revisions: list[Any] = []

        def get(self, owner: str) -> Any:
            return self.value if self.value and self.value.owner_principal == owner else None

        def create(self, value: Any, revision: Any) -> Any:
            self.value = value
            self.revisions.append(revision)
            return value

        def update(self, value: Any, *, expected_version: int, revision: Any) -> Any:
            assert self.value.version == expected_version
            self.value = value
            self.revisions.append(revision)
            return value

        def list_history(self, owner: str, *, limit: int = 100) -> tuple[Any, ...]:
            return tuple(item for item in self.revisions if item.owner_principal == owner)[:limit]

    _repository, _conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    service = AgentPreferencesService(PreferencesRepo(), SystemClock(), Uuid7IdGenerator())
    api.app.state.agent_runtime_state = replace(
        api.app.state.agent_runtime_state,
        preferences_service=service,
    )
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        initial = await client.get("/api/agent/preferences", headers=headers)
        updated = await client.put(
            "/api/agent/preferences",
            headers=headers,
            json={
                "language": "en",
                "response_density": "compact",
                "expected_version": 0,
                "idempotency_key": "console-pref-create",
                "authorization_note": "Explicit Console presentation preference.",
            },
        )
        rejected = await client.put(
            "/api/agent/preferences",
            headers=headers,
            json={
                "price": 4310,
                "expected_version": 1,
                "idempotency_key": "console-pref-bad",
                "authorization_note": "Must reject factual fields.",
            },
        )
        history = await client.get("/api/agent/preferences/history", headers=headers)
    assert initial.json()["preferences"]["version"] == 0
    assert updated.status_code == 200
    assert updated.json()["preferences"]["language"] == "en"
    assert updated.json()["preferences"]["version"] == 1
    assert rejected.status_code == 422
    assert len(history.json()["items"]) == 1


def test_agent_status_metadata_is_secret_safe() -> None:
    repository = _Repository()
    container = SimpleNamespace(
        settings=SimpleNamespace(
            agent_enabled=True,
            resolved_llm_config=SimpleNamespace(
                model="safe-model",
                api_style="responses",
                reasoning_mode="effort",
                native_web_search="disabled",
                base_url="https://secret.example/v1",
                api_key="secret-key",
            ),
        ),
        operations=SimpleNamespace(agent_conversations=repository),
        context=SimpleNamespace(clock=SystemClock(), id_generator=Uuid7IdGenerator()),
        resources=SimpleNamespace(agent_model_provider=_Model([ModelResponse(text="ok")])),
    )
    state = build_agent_runtime_state(container, CompactCapabilityRegistry())
    encoded = json.dumps(state.status, ensure_ascii=False)
    assert state.status["model"] == "safe-model"
    assert state.status["default_model_id"] == "default"
    assert state.status["models"] == [
        {
            "id": "default",
            "provider": "default",
            "model": "safe-model",
            "api_style": "responses",
            "reasoning_mode": "effort",
            "reasoning_effort": None,
            "reasoning_efforts": ["low", "medium", "high", "max"],
            "native_web_search": "disabled",
            "is_default": True,
        }
    ]
    assert "secret.example" not in encoded
    assert "secret-key" not in encoded
    assert "api_key" not in encoded


@pytest.mark.asyncio
async def test_archive_requires_owner_and_expected_version() -> None:
    repository, conversation_id = _client_state(
        enabled=True,
        model=_Model([ModelResponse(text="unused")]),
    )
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        stale = await client.post(
            f"/api/agent/conversations/{conversation_id}/archive",
            headers=headers,
            json={"expected_version": 2},
        )
        archived = await client.post(
            f"/api/agent/conversations/{conversation_id}/archive",
            headers=headers,
            json={"expected_version": 1},
        )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "AGENT_CONVERSATION_VERSION_CONFLICT"
    assert archived.status_code == 200
    assert repository.conversations[conversation_id].status is AgentConversationStatus.ARCHIVED


@pytest.mark.asyncio
async def test_pending_action_console_projection_and_confirm_token_alias() -> None:
    _repository, conversation_id, token, pending = _pending_client_state()
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        listed = await client.get(
            f"/api/agent/conversations/{conversation_id}/pending-actions",
            headers=headers,
        )
        confirmed = await client.post(
            f"/api/agent/conversations/{conversation_id}/pending-actions/"
            f"{pending.action.action_id}/confirm",
            headers=headers,
            json={"token": token, "expected_version": 2},
        )
    item = listed.json()["items"][0]
    assert listed.status_code == 200
    assert "confirmation_token" not in item
    assert "normalized_arguments" not in item
    assert confirmed.status_code == 200
    assert confirmed.json()["action"]["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_pending_action_reissue_returns_one_new_token_and_safe_projection() -> None:
    _repository, conversation_id, old_token, pending = _pending_client_state()
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            f"/api/agent/conversations/{conversation_id}/pending-actions/"
            f"{pending.action.action_id}/reissue",
            headers=headers,
            json={"expected_version": 2},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["confirmation_token"] != old_token
    assert body["action"]["version"] == 3
    encoded = json.dumps(body, ensure_ascii=False)
    assert "token_sha256" not in encoded
    assert pending.action.token_sha256 not in encoded


@pytest.mark.asyncio
async def test_sse_tool_events_and_failures_are_bounded() -> None:
    model = _Model(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="call_search",
                        name="tp_capability_search",
                        arguments=json.dumps({"query": "positions"}),
                    ),
                )
            ),
            ModelResponse(text="工具完成"),
        ]
    )
    _repository, conversation_id = _client_state(enabled=True, model=model)
    headers = {"X-Trading-Partner-Console-Token": "test-token"}
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            f"/api/agent/conversations/{conversation_id}/messages/stream",
            headers=headers,
            json={"content": "工具问题"},
        )
    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert events == [
        "message_started",
        "tool_started",
        "tool_finished",
        "text_delta",
        "completed",
    ]

    failure_model = _FailureModel([])
    failure_repository, conversation_id = _client_state(enabled=True, model=failure_model)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        failed = await client.post(
            f"/api/agent/conversations/{conversation_id}/messages/stream",
            headers=headers,
            json={"content": "失败问题"},
        )
    assert "failed" in failed.text
    assert "secret.example" not in failed.text
    assert "api_key=secret" not in failed.text
    failed_turn = next(iter(failure_repository.turns.values()))
    assert failed_turn.status is AgentTurnStatus.FAILED
    failed_payload = next(item for item in failed.text.split("\n\n") if "event: failed" in item)
    assert json.loads(failed_payload.split("data: ", 1)[1])["turn_id"] == failed_turn.turn_id


@pytest.mark.asyncio
async def test_stream_disconnect_does_not_cancel_durable_turn() -> None:
    model = _Model([ModelResponse(text="断线后仍完成")])
    repository, conversation_id = _client_state(enabled=True, model=model)
    state = api.app.state.agent_runtime_state
    runtime = state.runtime
    assert runtime is not None
    stream = _event_stream(
        runtime,
        AgentTurnRequest(
            conversation_id=conversation_id,
            owner_principal=AGENT_OWNER_PRINCIPAL,
            channel=AgentChannel.CONSOLE,
            content="断线问题",
        ),
    )
    first = await anext(stream)
    assert b"message_started" in first
    await stream.aclose()
    await asyncio.sleep(0)
    assert [item.role.value for item in repository.messages[conversation_id]] == [
        "USER",
        "ASSISTANT",
    ]


@pytest.mark.asyncio
async def test_same_conversation_turns_are_serialized() -> None:
    model = _Model([ModelResponse(text="第一"), ModelResponse(text="第二")])
    repository, conversation_id = _client_state(enabled=True, model=model)
    runtime = api.app.state.agent_runtime_state.runtime
    assert runtime is not None
    await asyncio.gather(
        runtime.run_turn(
            AgentTurnRequest(
                conversation_id=conversation_id,
                owner_principal=AGENT_OWNER_PRINCIPAL,
                channel=AgentChannel.CONSOLE,
                content="一",
            )
        ),
        runtime.run_turn(
            AgentTurnRequest(
                conversation_id=conversation_id,
                owner_principal=AGENT_OWNER_PRINCIPAL,
                channel=AgentChannel.CONSOLE,
                content="二",
            )
        ),
    )
    assert [item.role.value for item in repository.messages[conversation_id]] == [
        "USER",
        "ASSISTANT",
        "USER",
        "ASSISTANT",
    ]


def test_chart_artifact_route_is_png_only_and_traversal_safe(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "artifacts" / "technical"
    root.mkdir(parents=True)
    (root / "req_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    response = get_agent_chart_artifact("req_1.png")
    assert response.media_type == "image/png"
    assert response.path is not None and str(response.path).endswith("req_1.png")

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as traversal:
        get_agent_chart_artifact("../secret.png")
    assert traversal.value.status_code == 404
    with pytest.raises(HTTPException) as missing:
        get_agent_chart_artifact("missing.png")
    assert missing.value.status_code == 404
