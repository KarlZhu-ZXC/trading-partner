from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
import pytest

from application.dto.agent import AgentTurnEvent, AgentTurnRequest, AgentTurnResult
from application.ports.agent_model_provider import ModelUsage
from application.services.agent_context_service import AgentContextService
from application.services.agent_handoff_service import AgentHandoffService
from domain.agent.enums import AgentChannel, AgentMessageRole, AgentPendingActionStatus
from domain.agent.models import (
    AgentChannelBinding,
    AgentChannelCursor,
    AgentChannelHandoff,
    AgentConversation,
    AgentMessage,
    AgentPendingAction,
    arguments_digest,
)
from domain.common.ids import EntityIdPrefix
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator
from interfaces.telegram.agent_client import TelegramBotAgentClient
from interfaces.telegram.agent_poller import (
    TELEGRAM_AGENT_CHANNEL,
    TELEGRAM_AGENT_CURSOR_KEY,
    TelegramAgentPoller,
    TelegramPollReceipt,
    TelegramUpdate,
    split_telegram_text,
    validate_agent_chat_id,
    validate_agent_user_id,
)


class MemoryAgentRepository:
    def __init__(self) -> None:
        self.conversations: dict[str, AgentConversation] = {}
        self.bindings: dict[tuple[AgentChannel, str], AgentChannelBinding] = {}
        self.messages: dict[str, list[AgentMessage]] = {}
        self.cursors: dict[tuple[AgentChannel, str], AgentChannelCursor] = {}
        self.handoffs: dict[str, AgentChannelHandoff] = {}
        self.fail_cursor_once = False

    def create_conversation(self, value: AgentConversation) -> AgentConversation:
        self.conversations[value.conversation_id] = value
        self.messages[value.conversation_id] = []
        return value

    def get_conversation(self, conversation_id: str) -> AgentConversation | None:
        return self.conversations.get(conversation_id)

    def list_conversations(self, *args: Any, **kwargs: Any) -> tuple[AgentConversation, ...]:
        return tuple(self.conversations.values())

    def archive_conversation(self, *args: Any, **kwargs: Any) -> AgentConversation:
        raise NotImplementedError

    def bind_channel(self, value: AgentChannelBinding) -> AgentChannelBinding:
        existing = self.bindings.get((value.channel, value.external_conversation_ref))
        if existing is not None:
            if value.is_active and not existing.is_active:
                if existing.conversation_id == value.conversation_id:
                    existing = replace(existing, is_active=True)
                    self.bindings[(value.channel, value.external_conversation_ref)] = existing
                    return existing
                # Keep the latest active binding while retaining the old
                # inactive row in the real repository's history.
            else:
                return existing
        self.bindings[(value.channel, value.external_conversation_ref)] = value
        return value

    def get_binding(
        self,
        channel: AgentChannel,
        external_conversation_ref: str,
        *,
        active_only: bool = True,
    ) -> AgentChannelBinding | None:
        value = self.bindings.get((channel, external_conversation_ref))
        if value is None or (active_only and not value.is_active):
            return None
        return value

    def deactivate_channel(
        self,
        channel: AgentChannel,
        external_conversation_ref: str,
        *,
        now: Any = None,
    ) -> AgentChannelBinding | None:
        value = self.bindings.get((channel, external_conversation_ref))
        if value is None:
            return None
        value = replace(value, is_active=False, updated_at=now or value.updated_at)
        self.bindings[(channel, external_conversation_ref)] = value
        return value

    def append_message(self, value: AgentMessage) -> AgentMessage:
        if value.external_message_ref is not None:
            existing = self.get_message_by_external_ref(value.channel, value.external_message_ref)
            if existing is not None:
                return existing
        current = self.conversations[value.conversation_id]
        stored = replace(value, sequence=current.next_message_sequence)
        self.messages[value.conversation_id].append(stored)
        self.conversations[value.conversation_id] = replace(
            current,
            next_message_sequence=current.next_message_sequence + 1,
            version=current.version + 1,
            updated_at=value.created_at,
        )
        return stored

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
            for item in self.messages.get(conversation_id, [])
            if item.sequence > after_sequence
        )
        return values[-limit:] if newest_first else values[:limit]

    def get_message_by_external_ref(
        self,
        channel: AgentChannel,
        external_message_ref: str,
    ) -> AgentMessage | None:
        return next(
            (
                item
                for items in self.messages.values()
                for item in items
                if item.channel is channel and item.external_message_ref == external_message_ref
            ),
            None,
        )

    def append_tool_receipt(self, value: Any) -> Any:
        return value

    def list_tool_receipts(self, conversation_id: str, *, limit: int = 100) -> tuple[Any, ...]:
        return ()

    def get_tool_receipt(self, receipt_id: str) -> None:
        return None

    def update_summary(self, *args: Any, **kwargs: Any) -> AgentConversation:
        raise NotImplementedError

    def create_handoff(self, value: AgentChannelHandoff) -> AgentChannelHandoff:
        self.handoffs[value.token_sha256] = value
        return value

    def get_by_token_sha256(self, token_sha256: str) -> AgentChannelHandoff | None:
        return self.handoffs.get(token_sha256)

    def consume_exact(
        self,
        token_sha256: str,
        *,
        target_channel: AgentChannel,
        owner_principal: str,
        expected_version: int | None = None,
        now: Any = None,
    ) -> AgentChannelHandoff:
        current = self.handoffs[token_sha256]
        assert current.target_channel is target_channel
        assert current.owner_principal == owner_principal
        assert current.consumed_at is None
        updated = replace(
            current,
            consumed_at=now or SystemClock().now(),
            version=current.version + 1,
        )
        self.handoffs[token_sha256] = updated
        return updated

    def get_cursor(
        self,
        channel: AgentChannel,
        cursor_key: str = TELEGRAM_AGENT_CURSOR_KEY,
    ) -> AgentChannelCursor | None:
        return self.cursors.get((channel, cursor_key))

    def advance_cursor(
        self,
        channel: AgentChannel,
        cursor_key: str = TELEGRAM_AGENT_CURSOR_KEY,
        update_id: int | None = None,
        expected_update_id: int | None = None,
        **kwargs: Any,
    ) -> AgentChannelCursor:
        if self.fail_cursor_once:
            self.fail_cursor_once = False
            raise RuntimeError("cursor crash")
        assert update_id is not None
        current = self.get_cursor(channel, cursor_key)
        current_id = -1 if current is None else current.last_update_id
        assert expected_update_id in (None, current_id)
        value = AgentChannelCursor(
            cursor_id="agent_cursor_test",
            channel=channel,
            cursor_key=cursor_key,
            last_update_id=update_id,
            updated_at=SystemClock().now(),
            version=1 if current is None else current.version + 1,
        )
        self.cursors[(channel, cursor_key)] = value
        return value


class FakeClient:
    def __init__(self, batches: list[tuple[TelegramUpdate, ...]]) -> None:
        self.batches = list(batches)
        self.offsets: list[int] = []
        self.sent: list[str] = []
        self.markups: list[dict[str, object] | None] = []
        self.callback_answers: list[tuple[str, str | None]] = []

    async def get_updates(self, *, offset: int, timeout_seconds: int) -> tuple[TelegramUpdate, ...]:
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else ()

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Any = None,
    ) -> bool:
        self.sent.append(text)
        self.markups.append(reply_markup)
        return True

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        self.callback_answers.append((callback_query_id, text))
        return True

    async def aclose(self) -> None:
        return None


class FakeRuntime:
    def __init__(self, repository: MemoryAgentRepository) -> None:
        self.repository = repository
        self.calls: list[AgentTurnRequest] = []
        self.ids = Uuid7IdGenerator()

    async def run_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.calls.append(request)
        now = SystemClock().now()
        user = self.repository.append_message(
            AgentMessage(
                self.ids.new(EntityIdPrefix.AGENT_MESSAGE),
                request.conversation_id,
                AgentMessageRole.USER,
                request.content,
                now,
                channel=request.channel,
                external_message_ref=request.external_message_ref,
            )
        )
        assistant = self.repository.append_message(
            AgentMessage(
                self.ids.new(EntityIdPrefix.AGENT_MESSAGE),
                request.conversation_id,
                AgentMessageRole.ASSISTANT,
                "runtime answer",
                now,
                channel=request.channel,
                external_message_ref=(
                    f"{request.external_message_ref}:assistant"
                    if request.external_message_ref
                    else None
                ),
            )
        )
        return AgentTurnResult(
            conversation_id=request.conversation_id,
            user_message_id=user.message_id,
            assistant_message_id=assistant.message_id,
            text=assistant.content,
            tool_rounds=0,
            tool_receipts=(),
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


class ActionRuntime(FakeRuntime):
    def __init__(
        self,
        repository: MemoryAgentRepository,
        pending: AgentPendingAction,
        token: str,
    ) -> None:
        super().__init__(repository)
        self.pending = pending
        self.token = token

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: Any = None,
    ) -> AgentTurnResult:
        result = await super().run_turn(request)
        if event_sink is not None:
            await event_sink(
                AgentTurnEvent(
                    type="pending_action",
                    data={
                        "conversation_id": request.conversation_id,
                        "pending_action": {
                            "action_id": self.pending.action_id,
                            "channel": AgentChannel.TELEGRAM.value,
                            "principal": "local-console",
                            "capability": self.pending.capability,
                            "operation": self.pending.operation,
                            "presented_summary": self.pending.presented_summary,
                            "status": self.pending.status.value,
                            "version": self.pending.version,
                        },
                        "confirmation_token": self.token,
                    },
                )
            )
        return result


class FakeActionGateway:
    def __init__(self, action: AgentPendingAction, token: str) -> None:
        self.action = action
        self.token = token
        self.confirm_calls = 0
        self.reject_calls = 0

    def get_by_token(
        self,
        token: str,
        *,
        channel: AgentChannel,
        principal: str,
    ) -> AgentPendingAction | None:
        if token != self.token or self.action.channel is not channel:
            return None
        if self.action.principal != principal:
            return None
        return self.action

    async def confirm(
        self,
        *,
        action_id: str,
        token: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int | None = None,
    ) -> Any:
        assert action_id == self.action.action_id
        assert token == self.token
        assert channel is AgentChannel.TELEGRAM
        assert principal == "local-console"
        assert expected_version == self.action.version
        self.confirm_calls += 1
        self.action = replace(
            self.action,
            status=AgentPendingActionStatus.SUCCEEDED,
            version=self.action.version + 1,
        )
        return type("Execution", (), {"action": self.action})()

    def reject(
        self,
        *,
        action_id: str,
        token: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int | None = None,
    ) -> AgentPendingAction:
        assert action_id == self.action.action_id
        assert token == self.token
        assert channel is AgentChannel.TELEGRAM
        assert principal == "local-console"
        assert expected_version == self.action.version
        self.reject_calls += 1
        self.action = replace(
            self.action,
            status=AgentPendingActionStatus.REJECTED,
            version=self.action.version + 1,
        )
        return self.action


def _pending_action(*, clock: SystemClock, expired: bool = False) -> tuple[AgentPendingAction, str]:
    now = clock.now()
    token = "opaque_token_1234567890"
    created_at = now - timedelta(minutes=2) if expired else now
    expires_at = now - timedelta(minutes=1) if expired else now + timedelta(minutes=10)
    value = AgentPendingAction(
        action_id="agent_pending_action_00000000-0000-7000-8000-000000000001",
        conversation_id="agent_conversation_00000000-0000-7000-8000-000000000001",
        channel=AgentChannel.TELEGRAM,
        principal="local-console",
        normalized_arguments={"payload": {"instrument_id": "equity:US:AAPL"}},
        arguments_sha256=arguments_digest({"payload": {"instrument_id": "equity:US:AAPL"}}),
        presented_summary="更新 Watchlist AAPL",
        expires_at=expires_at,
        created_at=created_at,
        updated_at=created_at,
        status=AgentPendingActionStatus.PRESENTED,
        version=1,
        capability="watchlist_manage",
        operation="add",
        token_sha256="a" * 64,
    )
    return value, token


def _poller(
    repository: MemoryAgentRepository,
    client: FakeClient,
    runtime: FakeRuntime | None = None,
) -> TelegramAgentPoller:
    context = AgentContextService(
        repository=repository,
        clock=SystemClock(),
        id_generator=Uuid7IdGenerator(),
    )
    return TelegramAgentPoller(
        repository=repository,
        context_service=context,
        runtime=runtime or FakeRuntime(repository),
        handoff_service=AgentHandoffService(
            repository,
            SystemClock(),
            Uuid7IdGenerator(),
        ),
        client=client,
        authorized_chat_id="-1001",
        clock=SystemClock(),
        id_generator=Uuid7IdGenerator(),
    )


def test_chat_id_gate_and_text_split() -> None:
    assert validate_agent_chat_id("-1001") == "-1001"
    assert validate_agent_chat_id("@channel") is None
    text = "a" * 4100
    chunks = split_telegram_text(text)
    assert len(chunks) == 2
    assert "".join(chunks) == text
    multiline = ("line\n" * 1500).strip()
    assert "".join(split_telegram_text(multiline, maximum=100)) == multiline


@pytest.mark.asyncio
async def test_authorized_gate_offset_and_cursor() -> None:
    repository = MemoryAgentRepository()
    runtime = FakeRuntime(repository)
    client = FakeClient(
        [
            (
                TelegramUpdate(1, "999", "陌生 chat"),
                TelegramUpdate(2, "-1001", "读取持仓"),
            )
        ]
    )
    receipt = await _poller(repository, client, runtime).run_once()
    assert receipt == TelegramPollReceipt(fetched=2, processed=1, ignored=1, failed=0, cursor=2)
    assert client.offsets == [0]
    assert client.sent == ["runtime answer"]
    assert len(runtime.calls) == 1
    assert repository.get_cursor(TELEGRAM_AGENT_CHANNEL, TELEGRAM_AGENT_CURSOR_KEY)


@pytest.mark.asyncio
async def test_duplicate_after_cursor_or_send_crash_does_not_call_model_twice() -> None:
    repository = MemoryAgentRepository()
    runtime = FakeRuntime(repository)
    client = FakeClient(
        [
            (TelegramUpdate(7, "-1001", "读取持仓"),),
            (TelegramUpdate(7, "-1001", "读取持仓"),),
        ]
    )
    poller = _poller(repository, client, runtime)
    repository.fail_cursor_once = True
    first = await poller.run_once()
    second = await poller.run_once()
    assert first.failed == 1
    assert second.processed == 1
    assert len(runtime.calls) == 1
    assert client.sent == ["runtime answer"]


@pytest.mark.asyncio
async def test_duplicate_update_after_cursor_is_skipped() -> None:
    repository = MemoryAgentRepository()
    runtime = FakeRuntime(repository)
    client = FakeClient(
        [
            (TelegramUpdate(9, "-1001", "读取持仓"),),
            (TelegramUpdate(9, "-1001", "读取持仓"),),
        ]
    )
    poller = _poller(repository, client, runtime)
    first = await poller.run_once()
    second = await poller.run_once()
    assert first.processed == 1
    assert second.processed == 0
    assert second.failed == 0
    assert len(runtime.calls) == 1
    assert client.sent == ["runtime answer"]


@pytest.mark.asyncio
async def test_help_command_is_durable_and_idempotent() -> None:
    repository = MemoryAgentRepository()
    client = FakeClient(
        [
            (TelegramUpdate(9, "-1001", "/help"),),
            (TelegramUpdate(9, "-1001", "/help"),),
        ]
    )
    poller = _poller(repository, client)
    first = await poller.run_once()
    second = await poller.run_once()
    assert first.processed == 1
    assert second.processed == 0
    assert len(client.sent) == 1
    assert "命令" in client.sent[0]


@pytest.mark.asyncio
async def test_continue_consumes_one_time_handoff_without_accepting_conversation_id() -> None:
    repository = MemoryAgentRepository()
    context = AgentContextService(
        repository=repository,
        clock=SystemClock(),
        id_generator=Uuid7IdGenerator(),
    )
    target = context.create_conversation(
        owner_principal="local-console",
        title="Console conversation",
    )
    handoffs = AgentHandoffService(repository, SystemClock(), Uuid7IdGenerator())
    _, raw_token = handoffs.create(
        conversation_id=target.conversation_id,
        owner_principal="local-console",
        target_channel=AgentChannel.TELEGRAM,
    )
    client = FakeClient([(TelegramUpdate(11, "-1001", f"/continue {raw_token}"),)])
    poller = TelegramAgentPoller(
        repository=repository,
        context_service=context,
        runtime=FakeRuntime(repository),
        handoff_service=handoffs,
        client=client,
        authorized_chat_id="-1001",
        clock=SystemClock(),
        id_generator=Uuid7IdGenerator(),
    )
    receipt = await poller.run_once()
    assert receipt.processed == 1
    binding = repository.get_binding(AgentChannel.TELEGRAM, "-1001")
    assert binding is not None
    assert binding.conversation_id == target.conversation_id
    assert client.sent == ["已接续到指定 Agent 会话。"]


@pytest.mark.asyncio
async def test_pending_action_card_uses_opaque_bounded_callbacks_and_confirms_once() -> None:
    repository = MemoryAgentRepository()
    clock = SystemClock()
    pending, token = _pending_action(clock=clock)
    actions = FakeActionGateway(pending, token)
    runtime = ActionRuntime(repository, pending, token)
    client = FakeClient(
        [
            (TelegramUpdate(20, "-1001", "准备更新", user_id="42"),),
            (
                TelegramUpdate(
                    21,
                    "-1001",
                    None,
                    callback_query_id="callback-1",
                    callback_data=f"c:{token}",
                    callback_user_id="42",
                ),
            ),
            (
                TelegramUpdate(
                    22,
                    "-1001",
                    None,
                    callback_query_id="callback-duplicate",
                    callback_data=f"c:{token}",
                    callback_user_id="42",
                ),
            ),
        ]
    )
    context = AgentContextService(
        repository=repository,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    poller = TelegramAgentPoller(
        repository=repository,
        context_service=context,
        runtime=runtime,
        handoff_service=AgentHandoffService(repository, clock, Uuid7IdGenerator()),
        client=client,
        authorized_chat_id="-1001",
        authorized_user_id="42",
        action_gateway=actions,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    first = await poller.run_once()
    assert first.processed == 1
    markup = client.markups[-1]
    assert markup is not None
    buttons = markup["inline_keyboard"][0]  # type: ignore[index]
    callback_data = [button["callback_data"] for button in buttons]  # type: ignore[index]
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)
    assert all(value in {f"c:{token}", f"r:{token}"} for value in callback_data)
    second = await poller.run_once()
    third = await poller.run_once()
    assert second.processed == 1
    assert third.processed == 1
    assert actions.confirm_calls == 1
    assert actions.reject_calls == 0
    assert client.callback_answers[0][0] == "callback-1"
    assert "动作已确认" in client.sent[-1]


@pytest.mark.asyncio
async def test_callback_rejects_stranger_user_and_restart_requires_durable_user_allowlist() -> None:
    repository = MemoryAgentRepository()
    clock = SystemClock()
    pending, token = _pending_action(clock=clock)
    actions = FakeActionGateway(pending, token)
    context = AgentContextService(
        repository=repository,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    stranger = FakeClient(
        [
            (
                TelegramUpdate(
                    30,
                    "-1001",
                    None,
                    callback_query_id="stranger",
                    callback_data=f"r:{token}",
                    callback_user_id="99",
                ),
            )
        ]
    )
    poller = TelegramAgentPoller(
        repository=repository,
        context_service=context,
        runtime=FakeRuntime(repository),
        handoff_service=AgentHandoffService(repository, clock, Uuid7IdGenerator()),
        client=stranger,
        authorized_chat_id="-1001",
        authorized_user_id="42",
        action_gateway=actions,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    await poller.run_once()
    assert actions.reject_calls == 0
    assert stranger.callback_answers == [("stranger", "此确认不属于授权用户。")]

    restart_client = FakeClient(
        [
            (
                TelegramUpdate(
                    31,
                    "-1001",
                    None,
                    callback_query_id="after-restart",
                    callback_data=f"r:{token}",
                    callback_user_id="42",
                ),
            )
        ]
    )
    restarted = TelegramAgentPoller(
        repository=repository,
        context_service=context,
        runtime=FakeRuntime(repository),
        handoff_service=AgentHandoffService(repository, clock, Uuid7IdGenerator()),
        client=restart_client,
        authorized_chat_id="-1001",
        authorized_user_id="42",
        action_gateway=actions,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    await restarted.run_once()
    assert actions.reject_calls == 1


@pytest.mark.asyncio
async def test_expired_callback_is_acknowledged_without_gateway_execution() -> None:
    repository = MemoryAgentRepository()
    clock = SystemClock()
    pending, token = _pending_action(clock=clock, expired=True)
    actions = FakeActionGateway(pending, token)
    client = FakeClient(
        [
            (
                TelegramUpdate(
                    40,
                    "-1001",
                    None,
                    callback_query_id="expired",
                    callback_data=f"c:{token}",
                    callback_user_id="42",
                ),
            )
        ]
    )
    context = AgentContextService(
        repository=repository,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    poller = TelegramAgentPoller(
        repository=repository,
        context_service=context,
        runtime=FakeRuntime(repository),
        handoff_service=AgentHandoffService(repository, clock, Uuid7IdGenerator()),
        client=client,
        authorized_chat_id="-1001",
        authorized_user_id="42",
        action_gateway=actions,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )
    await poller.run_once()
    assert actions.confirm_calls == 0
    assert client.callback_answers == [("expired", "该动作已过期。")]


def test_callback_update_parser_preserves_chat_and_user_identity() -> None:
    from interfaces.telegram.agent_poller import _parse_update

    update = _parse_update(
        {
            "update_id": 55,
            "callback_query": {
                "id": "cb",
                "from": {"id": 42},
                "data": "c:opaque_token",
                "message": {"message_id": 7, "chat": {"id": -1001}},
            },
        }
    )
    assert update is not None
    assert update.chat_id == "-1001"
    assert update.callback_user_id == "42"
    assert update.callback_query_id == "cb"
    assert update.callback_data == "c:opaque_token"
    assert validate_agent_user_id("42") == "42"
    assert validate_agent_user_id("not-user") is None


def test_group_chat_without_user_allowlist_is_unavailable_but_private_chat_defaults() -> None:
    from interfaces.cli.agent import _configuration_status

    group = type(
        "Settings",
        (),
        {
            "telegram_bot_token": "token",
            "telegram_chat_id": "-1001",
            "telegram_agent_user_id": None,
            "telegram_agent_enabled": True,
            "resolved_llm_config": object(),
        },
    )()
    private = type(
        "Settings",
        (),
        {
            "telegram_bot_token": "token",
            "telegram_chat_id": "42",
            "telegram_agent_user_id": None,
            "telegram_agent_enabled": True,
            "resolved_llm_config": object(),
        },
    )()
    assert _configuration_status(group)["available"] is False
    assert _configuration_status(private)["available"] is True


@pytest.mark.asyncio
async def test_http_client_requests_callback_updates_and_answers() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read()
        body = json.loads(payload.decode())
        requests.append((request.url.path, body))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 60,
                            "callback_query": {
                                "id": "cb-http",
                                "from": {"id": 42},
                                "data": "c:opaque_token",
                                "message": {
                                    "message_id": 8,
                                    "chat": {"id": -1001},
                                },
                            },
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"ok": True, "result": True})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = TelegramBotAgentClient(bot_token="123:secret", client=http_client)
    updates = await client.get_updates(offset=9, timeout_seconds=30)
    assert updates[0].callback_query_id == "cb-http"
    assert requests[0][1]["allowed_updates"] == ["message", "callback_query"]
    assert await client.answer_callback_query(callback_query_id="cb-http", text="ok")
    assert requests[1][0].endswith("/answerCallbackQuery")
    assert requests[1][1]["callback_query_id"] == "cb-http"
    await http_client.aclose()
