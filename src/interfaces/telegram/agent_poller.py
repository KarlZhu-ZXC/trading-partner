"""Authorized Telegram long-polling adapter for the Shared Agent Runtime.

The deterministic notification Outbox uses its own sender and retry loop.  This
module only handles inbound Agent messages and their direct replies.  It does
not expose a generic Telegram API, accepts one configured numeric chat, and
advances the durable update cursor only after a complete reply has been sent.

The HTTP Bot API client lives in :mod:`infrastructure.providers.telegram`; this
interface module owns the transport-neutral polling and conversation policy.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from application.dto.agent import AgentTurnEvent, AgentTurnRequest
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.services.agent_context_service import AgentContextService
from application.services.agent_conversation_metrics import AgentConversationMetricsService
from application.services.agent_handoff_service import AgentHandoffService
from application.services.agent_preferences_service import AgentPreferencesService
from application.services.agent_runtime_service import AgentRuntimeService
from domain.agent.enums import (
    AgentChannel,
    AgentConversationStatus,
    AgentMessageRole,
    AgentPendingActionStatus,
)
from domain.agent.models import AgentChannelBinding, AgentMessage, AgentPendingAction
from domain.common.errors import DataContractError, PersistenceError, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.notifications.rendering import TELEGRAM_MAX_TEXT_LENGTH

TELEGRAM_AGENT_OWNER_PRINCIPAL = "local-console"
TELEGRAM_AGENT_CURSOR_KEY = "agent"
TELEGRAM_AGENT_CHANNEL = AgentChannel.TELEGRAM
_TELEGRAM_CHAT_ID_PATTERN = re.compile(r"-?[0-9]+")
_TELEGRAM_USER_ID_PATTERN = re.compile(r"[0-9]+")
_OPAQUE_CALLBACK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_DEFAULT_POLL_TIMEOUT_SECONDS = 30
_MAX_UPDATE_BATCH = 100
_MAX_CALLBACK_DATA_BYTES = 64


class TelegramAgentClientError(TradingPartnerError):
    """Secret-safe Telegram transport failure."""

    default_code = "TELEGRAM_AGENT_TRANSPORT_FAILURE"
    default_retryable = True


@dataclass(frozen=True, slots=True)
class TelegramUpdate:
    update_id: int
    chat_id: str | None
    text: str | None
    user_id: str | None = None
    callback_query_id: str | None = None
    callback_data: str | None = None
    callback_user_id: str | None = None
    callback_message_id: int | None = None


class TelegramAgentClient(Protocol):
    """Minimal Telegram surface required by the inbound Agent poller."""

    async def get_updates(
        self,
        *,
        offset: int,
        timeout_seconds: int,
    ) -> tuple[TelegramUpdate, ...]: ...

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Mapping[str, object] | None = None,
    ) -> bool: ...

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool: ...

    async def aclose(self) -> None: ...


class TelegramActionGateway(Protocol):
    """Narrow Agent-D confirmation surface used by Telegram callbacks."""

    def get_by_token(
        self,
        token: str,
        *,
        channel: AgentChannel,
        principal: str,
    ) -> AgentPendingAction | None: ...

    async def confirm(
        self,
        *,
        action_id: str,
        token: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int | None = None,
    ) -> Any: ...

    def reject(
        self,
        *,
        action_id: str,
        token: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int | None = None,
    ) -> AgentPendingAction: ...


def validate_agent_chat_id(value: str | None) -> str | None:
    """Accept only the configured numeric Telegram chat id.

    Notification delivery may support ``@channel`` names for historical
    compatibility, but inbound Agent access deliberately does not.
    """

    if value is None:
        return None
    normalized = value.strip()
    return normalized if _TELEGRAM_CHAT_ID_PATTERN.fullmatch(normalized) else None


def validate_agent_user_id(value: str | None) -> str | None:
    """Normalize an optional numeric Telegram user allowlist value."""

    if value is None:
        return None
    normalized = value.strip()
    return normalized if _TELEGRAM_USER_ID_PATTERN.fullmatch(normalized) else None


def split_telegram_text(text: str, *, maximum: int = TELEGRAM_MAX_TEXT_LENGTH) -> tuple[str, ...]:
    """Split bounded plain text into Telegram-safe chunks without dropping data."""

    if not isinstance(text, str) or not text:
        raise DataContractError("Telegram message text must not be blank")
    if type(maximum) is not int or maximum < 1:
        raise DataContractError("Telegram message maximum must be positive")
    if len(text) <= maximum:
        return (text,)

    chunks: list[str] = []
    remaining = text
    while len(remaining) > maximum:
        cut = remaining.rfind("\n", 1, maximum + 1)
        if cut < maximum // 2:
            cut = maximum
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class TelegramPollReceipt:
    fetched: int
    processed: int
    ignored: int
    failed: int
    cursor: int | None


class TelegramAgentPoller:
    """Single-chat, durable-cursor Telegram Agent adapter."""

    def __init__(
        self,
        *,
        repository: AgentConversationRepository,
        context_service: AgentContextService,
        runtime: AgentRuntimeService,
        handoff_service: AgentHandoffService,
        client: TelegramAgentClient,
        authorized_chat_id: str,
        clock: Clock,
        id_generator: IdGenerator,
        action_gateway: TelegramActionGateway | None = None,
        preferences_service: AgentPreferencesService | None = None,
        metrics_service: AgentConversationMetricsService | None = None,
        authorized_user_id: str | None = None,
        poll_timeout_seconds: int = _DEFAULT_POLL_TIMEOUT_SECONDS,
        cursor_key: str = TELEGRAM_AGENT_CURSOR_KEY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        normalized_chat_id = validate_agent_chat_id(authorized_chat_id)
        if normalized_chat_id is None:
            raise DataContractError("Telegram Agent requires a numeric authorized chat id")
        normalized_user_id = validate_agent_user_id(authorized_user_id)
        if authorized_user_id is not None and normalized_user_id is None:
            raise DataContractError("Telegram Agent authorized user id is invalid")
        if normalized_user_id is None and not normalized_chat_id.startswith("-"):
            normalized_user_id = normalized_chat_id
        if type(poll_timeout_seconds) is not int or not 0 <= poll_timeout_seconds <= 50:
            raise DataContractError("Telegram Agent poll timeout is invalid")
        self._repository = repository
        self._context = context_service
        self._runtime = runtime
        self._handoffs = handoff_service
        self._client = client
        self._authorized_chat_id = normalized_chat_id
        self._action_gateway = action_gateway
        self._preferences = preferences_service
        self._metrics = metrics_service or AgentConversationMetricsService(repository)
        self._authorized_user_id = normalized_user_id
        self._pending_action_users: dict[str, str] = {}
        self._clock = clock
        self._id_generator = id_generator
        self._poll_timeout_seconds = poll_timeout_seconds
        self._cursor_key = cursor_key.strip() or TELEGRAM_AGENT_CURSOR_KEY
        self._sleep = sleep

    @property
    def authorized_chat_id(self) -> str:
        return self._authorized_chat_id

    async def run_once(self) -> TelegramPollReceipt:
        cursor = self._repository.get_cursor(
            TELEGRAM_AGENT_CHANNEL,
            self._cursor_key,
        )
        expected = -1 if cursor is None else cursor.last_update_id
        updates = await self._client.get_updates(
            offset=expected + 1,
            timeout_seconds=self._poll_timeout_seconds,
        )
        processed = ignored = failed = 0
        for update in sorted(updates, key=lambda item: item.update_id):
            if update.update_id <= expected:
                continue
            try:
                handled = await self._handle_update(update)
                self._repository.advance_cursor(
                    TELEGRAM_AGENT_CHANNEL,
                    self._cursor_key,
                    update.update_id,
                    expected,
                )
                expected = update.update_id
                if handled:
                    processed += 1
                else:
                    ignored += 1
            except Exception:
                # Do not advance the cursor after a failed model or send.  A
                # later run sees the durable assistant marker and skips model
                # execution and outbound replay (at-most-once boundary).
                failed += 1
                break
        return TelegramPollReceipt(
            fetched=len(updates),
            processed=processed,
            ignored=ignored,
            failed=failed,
            cursor=None if expected < 0 else expected,
        )

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                await self.run_once()
            except TelegramAgentClientError as error:
                if not error.retryable:
                    raise
                await self._sleep(5.0)
            except (PersistenceError, TradingPartnerError):
                await self._sleep(2.0)

    async def _handle_update(self, update: TelegramUpdate) -> bool:
        if update.callback_query_id is not None:
            return await self._handle_callback(update)
        if update.chat_id != self._authorized_chat_id or update.text is None:
            return False
        if self._authorized_user_id is not None and update.user_id != self._authorized_user_id:
            return False
        text = update.text.strip()
        if not text:
            return False

        external_ref = str(update.update_id)
        existing = self._repository.get_message_by_external_ref(
            TELEGRAM_AGENT_CHANNEL,
            external_ref,
        )
        if existing is not None:
            # The assistant marker is durable before Telegram delivery.  Skip
            # replay so a crash cannot execute the model or send a duplicate;
            # this is deliberately at-most-once and may lose a reply if the
            # process dies after persistence but before ``sendMessage``.
            assistant = self._repository.get_message_by_external_ref(
                TELEGRAM_AGENT_CHANNEL,
                f"{external_ref}:assistant",
            )
            if assistant is None:
                await self._persist_failure_marker(existing, external_ref)
            return True

        conversation = self._conversation_for_chat()
        command, argument = _command(text)
        if command == "/help":
            await self._persist_and_send_command(
                conversation.conversation_id,
                external_ref,
                text,
                (
                    "命令：/new、/context、/continue <接续码>、/portfolio、"
                    "/watchlist、/monitors、/preferences、/help。"
                ),
            )
            return True
        if command == "/new":
            conversation = self._new_conversation()
            await self._persist_and_send_command(
                conversation.conversation_id,
                external_ref,
                text,
                "已创建新的 Telegram Agent 会话。",
            )
            return True
        if command == "/context":
            reply = self._context_reply(conversation)
            await self._persist_and_send_command(
                conversation.conversation_id,
                external_ref,
                text,
                reply,
            )
            return True
        if command == "/preferences":
            reply = self._preferences_reply(argument, update.user_id)
            await self._persist_and_send_command(
                conversation.conversation_id,
                external_ref,
                text,
                reply,
            )
            return True
        if command == "/continue":
            reply, continued = self._continue_conversation(argument)
            if continued is not None:
                conversation = continued
            await self._persist_and_send_command(
                conversation.conversation_id,
                external_ref,
                text,
                reply,
            )
            return True
        prompt = {
            "/portfolio": "请读取当前持仓与组合概览，只使用持久化账户快照，不要刷新券商。",
            "/watchlist": "请读取当前 Watchlist。",
            "/monitors": "请读取当前 Monitor 状态。",
        }.get(command, text)
        pending_cards: list[tuple[str, Mapping[str, object]]] = []
        request = AgentTurnRequest(
            conversation_id=conversation.conversation_id,
            owner_principal=TELEGRAM_AGENT_OWNER_PRINCIPAL,
            channel=TELEGRAM_AGENT_CHANNEL,
            content=prompt,
            external_message_ref=external_ref,
        )
        result = await self._run_runtime_turn(request, pending_cards)
        await self._send_all(result.text)
        for token, pending in pending_cards:
            self._remember_pending_user(token, update.user_id)
            await self._send_pending_action_card(token, pending)
        return True

    def _context_reply(self, conversation: Any) -> str:
        metrics = self._metrics.aggregate(conversation.conversation_id)
        statuses = ", ".join(
            f"{key}={value}" for key, value in metrics.turn_statuses.items() if value
        ) or "none"
        api_styles = ", ".join(metrics.api_styles) or "unknown"
        truncation = "\n采样：已截断至最近 500 条。" if metrics.truncated else ""
        warning = (
            f"\n异常回执：{metrics.malformed_receipt_count} 条（已忽略）"
            if metrics.malformed_receipt_count
            else ""
        )
        return (
            f"当前会话：{conversation.title}\n"
            f"模型调用：{metrics.model_calls}\n"
            f"Token：输入 {metrics.input_tokens} / 输出 {metrics.output_tokens} / "
            f"总计 {metrics.total_tokens}\n"
            f"Web Search：{metrics.web_search_calls}（使用回合 {metrics.web_search_used_turns}）\n"
            f"Extractor：{metrics.web_extractor_calls}"
            f"（使用回合 {metrics.web_extractor_used_turns}）\n"
            f"延迟：{metrics.latency_ms} ms\n"
            f"API style：{api_styles}\n"
            f"回合状态：{statuses}{truncation}{warning}"
        )

    def _preferences_reply(self, argument: str | None, user_id: str | None) -> str:
        owner = TELEGRAM_AGENT_OWNER_PRINCIPAL
        if argument is None:
            value = self._preferences.get(owner) if self._preferences is not None else None
            if value is None:
                return (
                    "当前偏好尚未持久化（使用默认值）：语言 zh-CN；密度 standard；"
                    "风险表达 balanced；默认图表 否；后台网页上下文 是。"
                )
            return (
                "当前 Agent 偏好：\n"
                f"语言：{value.language.value}\n"
                f"密度：{value.response_density.value}\n"
                f"来源：{', '.join(value.preferred_source_codes) or '未指定'}\n"
                f"风险表达：{value.risk_style.value}\n"
                f"默认图表：{'是' if value.default_chart else '否'}\n"
                f"后台网页上下文：{'是' if value.web_background else '否'}\n"
                f"版本：{value.version}"
            )
        if not argument.casefold().startswith("set "):
            return (
                "用法：/preferences 或 /preferences set key=value ... "
                "version=N idempotency_key=... authorization_note=..."
            )
        if self._preferences is None:
            return "偏好持久化当前不可用。"
        try:
            tokens = shlex.split(argument[4:])
            values: dict[str, object] = {}
            expected_version: int | None = None
            idempotency_key: str | None = None
            authorization_note: str | None = None
            for token in tokens:
                key, separator, raw = token.partition("=")
                if not separator or not key or not raw:
                    raise ValueError
                if key == "version":
                    expected_version = int(raw)
                elif key == "idempotency_key":
                    idempotency_key = raw
                elif key == "authorization_note":
                    authorization_note = raw
                else:
                    values[key] = raw
            if expected_version is None or idempotency_key is None or authorization_note is None:
                return "写入必须显式提供 version、idempotency_key 和 authorization_note。"
            for key in ("default_chart",):
                if key in values:
                    values[key] = _parse_bool(str(values[key]))
            updated = self._preferences.update(
                owner,
                values,
                expected_version=expected_version,
                actor=f"telegram:{user_id or self._authorized_chat_id}",
                idempotency_key=idempotency_key,
                authorization_note=authorization_note,
            )
            return f"偏好已保存，版本 {updated.version}。"
        except (ValueError, TypeError, DataContractError):
            return "偏好写入失败：字段、版本或显式授权参数无效。"

    async def _run_runtime_turn(
        self,
        request: AgentTurnRequest,
        pending_cards: list[tuple[str, Mapping[str, object]]],
    ) -> Any:
        async def event_sink(event: AgentTurnEvent) -> None:
            if event.type != "pending_action":
                return
            raw_pending = event.data.get("pending_action")
            token = event.data.get("confirmation_token")
            if not isinstance(raw_pending, Mapping) or not isinstance(token, str):
                return
            if raw_pending.get("channel") != TELEGRAM_AGENT_CHANNEL.value:
                return
            if raw_pending.get("principal") != TELEGRAM_AGENT_OWNER_PRINCIPAL:
                return
            if not _valid_callback_token(token):
                return
            pending_cards.append((token, dict(raw_pending)))

        run_turn = self._runtime.run_turn
        try:
            parameters = inspect.signature(run_turn).parameters
            accepts_sink = "event_sink" in parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            accepts_sink = True
        if accepts_sink:
            return await run_turn(request, event_sink=event_sink)
        return await run_turn(request)

    def _remember_pending_user(self, token: str, user_id: str | None) -> None:
        if user_id is None:
            return
        if len(self._pending_action_users) >= 256:
            oldest = next(iter(self._pending_action_users))
            self._pending_action_users.pop(oldest, None)
        self._pending_action_users[token] = user_id

    async def _send_pending_action_card(
        self,
        token: str,
        pending: Mapping[str, object],
    ) -> None:
        confirm_data = _callback_data("c", token)
        reject_data = _callback_data("r", token)
        summary = pending.get("presented_summary")
        bounded_summary = summary.strip()[:1_800] if isinstance(summary, str) else "请确认该动作。"
        capability = pending.get("capability")
        operation = pending.get("operation")
        label = ""
        if isinstance(capability, str) and isinstance(operation, str):
            label = f"\n能力：{capability[:80]} / {operation[:80]}"
        detail_lines: list[str] = []
        raw_details = pending.get("confirmation_details")
        if isinstance(raw_details, (list, tuple)):
            for item in raw_details[:16]:
                if not isinstance(item, Mapping):
                    continue
                path = item.get("path")
                value = item.get("value")
                if isinstance(path, str) and isinstance(value, str):
                    detail_lines.append(f"• {path[:120]}：{value[:240]}")
        details = "\n\n执行参数：\n" + "\n".join(detail_lines) if detail_lines else ""
        text = f"待确认动作：\n{bounded_summary}{label}{details}\n\n请在有效期内选择："
        markup: Mapping[str, object] = {
            "inline_keyboard": [
                [
                    {"text": "确认", "callback_data": confirm_data},
                    {"text": "拒绝", "callback_data": reject_data},
                ]
            ]
        }
        delivered = await self._client.send_message(
            chat_id=self._authorized_chat_id,
            text=text[:TELEGRAM_MAX_TEXT_LENGTH],
            reply_markup=markup,
        )
        if delivered is not True:
            raise TelegramAgentClientError("Telegram Agent action card was not delivered")

    async def _handle_callback(self, update: TelegramUpdate) -> bool:
        if update.chat_id != self._authorized_chat_id:
            return False
        callback_id = update.callback_query_id
        if callback_id is None:
            return False
        token, action = _decode_callback_data(update.callback_data)
        if not self._callback_user_allowed(update, token):
            await self._answer_callback(callback_id, "此确认不属于授权用户。")
            return True
        if token is None or action is None:
            await self._answer_callback(callback_id, "确认按钮无效或已过期。")
            return True
        gateway = self._action_gateway
        if gateway is None:
            await self._answer_callback(callback_id, "Telegram 确认功能尚未启用。")
            return True
        pending = gateway.get_by_token(
            token,
            channel=TELEGRAM_AGENT_CHANNEL,
            principal=TELEGRAM_AGENT_OWNER_PRINCIPAL,
        )
        if pending is None:
            await self._answer_callback(callback_id, "确认按钮无效或已过期。")
            return True
        if pending.status is not AgentPendingActionStatus.PRESENTED:
            await self._answer_callback(callback_id, "该动作已处理。")
            return True
        if pending.expires_at <= self._clock.now():
            await self._answer_callback(callback_id, "该动作已过期。")
            return True
        try:
            if action == "c":
                execution = await gateway.confirm(
                    action_id=pending.action_id,
                    token=token,
                    channel=TELEGRAM_AGENT_CHANNEL,
                    principal=TELEGRAM_AGENT_OWNER_PRINCIPAL,
                    expected_version=pending.version,
                )
                final = execution.action
                reply = f"动作已确认：{final.status.value}。"
            else:
                rejected = gateway.reject(
                    action_id=pending.action_id,
                    token=token,
                    channel=TELEGRAM_AGENT_CHANNEL,
                    principal=TELEGRAM_AGENT_OWNER_PRINCIPAL,
                    expected_version=pending.version,
                )
                reply = f"动作已拒绝：{rejected.status.value}。"
        except TradingPartnerError as error:
            if error.code in {
                "AGENT_PENDING_ACTION_ALREADY_USED",
                "AGENT_PENDING_ACTION_STATE_CONFLICT",
                "AGENT_PENDING_ACTION_VERSION_CONFLICT",
            }:
                reply = "该动作已处理。"
            elif error.code == "AGENT_PENDING_ACTION_EXPIRED":
                reply = "该动作已过期。"
            else:
                reply = "动作确认失败，请在 Console 查看状态。"
        await self._answer_callback(callback_id, reply)
        if reply.startswith("动作已"):
            await self._send_all(reply)
        return True

    def _callback_user_allowed(self, update: TelegramUpdate, token: str | None) -> bool:
        user_id = validate_agent_user_id(update.callback_user_id)
        if user_id is None:
            return False
        if self._authorized_user_id is None or user_id != self._authorized_user_id:
            return False
        expected = self._pending_action_users.get(token) if token is not None else None
        return expected is None or expected == user_id

    async def _answer_callback(self, callback_id: str, text: str) -> None:
        answered = await self._client.answer_callback_query(
            callback_query_id=callback_id,
            text=text[:200],
            show_alert=False,
        )
        if answered is not True:
            raise TelegramAgentClientError("Telegram callback acknowledgement failed")

    def _conversation_for_chat(self) -> Any:
        binding = self._repository.get_binding(
            TELEGRAM_AGENT_CHANNEL,
            self._authorized_chat_id,
        )
        if binding is not None:
            conversation = self._repository.get_conversation(binding.conversation_id)
            if conversation is not None and conversation.status is AgentConversationStatus.ACTIVE:
                return conversation
            self._repository.deactivate_channel(
                TELEGRAM_AGENT_CHANNEL,
                self._authorized_chat_id,
                now=self._clock.now(),
            )
        conversation = self._context.create_conversation(
            owner_principal=TELEGRAM_AGENT_OWNER_PRINCIPAL,
            title="Telegram 会话",
        )
        self._repository.bind_channel(
            AgentChannelBinding(
                binding_id=self._id_generator.new(EntityIdPrefix.AGENT_BINDING),
                conversation_id=conversation.conversation_id,
                channel=TELEGRAM_AGENT_CHANNEL,
                external_conversation_ref=self._authorized_chat_id,
                created_at=self._clock.now(),
                updated_at=self._clock.now(),
            )
        )
        return conversation

    def _new_conversation(self) -> Any:
        self._repository.deactivate_channel(
            TELEGRAM_AGENT_CHANNEL,
            self._authorized_chat_id,
            now=self._clock.now(),
        )
        return self._conversation_for_chat()

    def _continue_conversation(self, code: str | None) -> tuple[str, Any | None]:
        if code is None:
            return "接续码无效；请在 Console 中生成一次性接续码后重试。", None
        try:
            handoff = self._handoffs.consume(
                code,
                target_channel=TELEGRAM_AGENT_CHANNEL,
                owner_principal=TELEGRAM_AGENT_OWNER_PRINCIPAL,
                now=self._clock.now(),
            )
        except TradingPartnerError:
            return "接续码无效或已过期。", None
        target = self._repository.get_conversation(handoff.conversation_id)
        if target is None or target.owner_principal != TELEGRAM_AGENT_OWNER_PRINCIPAL:
            return "接续码无效或已过期。", None
        if target.status is not AgentConversationStatus.ACTIVE:
            return "该会话已归档，无法接续。", None
        self._repository.deactivate_channel(
            TELEGRAM_AGENT_CHANNEL,
            self._authorized_chat_id,
            now=self._clock.now(),
        )
        try:
            self._repository.bind_channel(
                AgentChannelBinding(
                    binding_id=self._id_generator.new(EntityIdPrefix.AGENT_BINDING),
                    conversation_id=target.conversation_id,
                    channel=TELEGRAM_AGENT_CHANNEL,
                    external_conversation_ref=self._authorized_chat_id,
                    created_at=self._clock.now(),
                    updated_at=self._clock.now(),
                )
            )
        except TradingPartnerError:
            return "该接续码当前不可用，请从 Console 重新生成。", None
        return "已接续到指定 Agent 会话。", target

    async def _persist_and_send_command(
        self,
        conversation_id: str,
        external_ref: str,
        user_content: str,
        reply: str,
    ) -> None:
        now = self._clock.now()
        self._repository.append_message(
            AgentMessage(
                message_id=self._id_generator.new(EntityIdPrefix.AGENT_MESSAGE),
                conversation_id=conversation_id,
                role=AgentMessageRole.USER,
                content=user_content,
                channel=TELEGRAM_AGENT_CHANNEL,
                external_message_ref=external_ref,
                created_at=now,
            )
        )
        assistant = self._repository.append_message(
            AgentMessage(
                message_id=self._id_generator.new(EntityIdPrefix.AGENT_MESSAGE),
                conversation_id=conversation_id,
                role=AgentMessageRole.ASSISTANT,
                content=reply,
                channel=TELEGRAM_AGENT_CHANNEL,
                external_message_ref=f"{external_ref}:assistant",
                model="telegram-command",
                created_at=now,
            )
        )
        await self._send_all(assistant.content)

    async def _persist_failure_marker(self, user: AgentMessage, external_ref: str) -> None:
        assistant = self._repository.append_message(
            AgentMessage(
                message_id=self._id_generator.new(EntityIdPrefix.AGENT_MESSAGE),
                conversation_id=user.conversation_id,
                role=AgentMessageRole.ASSISTANT,
                content="上一条消息已收到但未完成，请重新发送。",
                channel=TELEGRAM_AGENT_CHANNEL,
                external_message_ref=f"{external_ref}:assistant",
                model="telegram-replay-guard",
                created_at=self._clock.now(),
            )
        )
        await self._send_all(assistant.content)

    async def _send_all(self, text: str) -> None:
        for chunk in split_telegram_text(text):
            delivered = await self._client.send_message(
                chat_id=self._authorized_chat_id,
                text=chunk,
            )
            if delivered is not True:
                raise TelegramAgentClientError("Telegram Agent reply was not delivered")


def _parse_update(value: object) -> TelegramUpdate | None:
    if not isinstance(value, Mapping):
        return None
    update_id = value.get("update_id")
    if type(update_id) is not int or update_id < 0:
        return None
    callback = value.get("callback_query")
    if isinstance(callback, Mapping):
        callback_id = callback.get("id")
        callback_data = callback.get("data")
        callback_message = callback.get("message")
        callback_chat = None
        callback_message_id = None
        if isinstance(callback_message, Mapping):
            callback_chat = _chat_id_from(callback_message.get("chat"))
            raw_message_id = callback_message.get("message_id")
            if type(raw_message_id) is int and raw_message_id >= 0:
                callback_message_id = raw_message_id
        if (
            isinstance(callback_id, str)
            and callback_id
            and isinstance(callback_data, str)
            and callback_chat is not None
        ):
            return TelegramUpdate(
                update_id=update_id,
                chat_id=callback_chat,
                text=None,
                callback_query_id=callback_id,
                callback_data=callback_data,
                callback_user_id=_user_id_from(value=callback.get("from")),
                callback_message_id=callback_message_id,
            )
    message = value.get("message")
    if not isinstance(message, Mapping):
        return None
    chat = message.get("chat")
    chat_id = None
    if isinstance(chat, Mapping):
        raw_chat_id = chat.get("id")
        if type(raw_chat_id) is int:
            chat_id = str(raw_chat_id)
        elif isinstance(raw_chat_id, str) and _TELEGRAM_CHAT_ID_PATTERN.fullmatch(raw_chat_id):
            chat_id = raw_chat_id
    raw_text = message.get("text")
    text = raw_text if isinstance(raw_text, str) else None
    user_id = _user_id_from(value=message.get("from"))
    return TelegramUpdate(update_id=update_id, chat_id=chat_id, text=text, user_id=user_id)


def _chat_id_from(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw_chat_id = value.get("id")
    if type(raw_chat_id) is int:
        return str(raw_chat_id)
    if isinstance(raw_chat_id, str) and _TELEGRAM_CHAT_ID_PATTERN.fullmatch(raw_chat_id):
        return raw_chat_id
    return None


def _user_id_from(*, value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw_user_id = value.get("id")
    if type(raw_user_id) is int and raw_user_id >= 0:
        return str(raw_user_id)
    if isinstance(raw_user_id, str) and _TELEGRAM_USER_ID_PATTERN.fullmatch(raw_user_id):
        return raw_user_id
    return None


def _valid_callback_token(token: str) -> bool:
    return bool(
        token
        and len(token.encode("utf-8")) <= _MAX_CALLBACK_DATA_BYTES - 2
        and _OPAQUE_CALLBACK_TOKEN_PATTERN.fullmatch(token)
    )


def _callback_data(action: str, token: str) -> str:
    if action not in {"c", "r"} or not _valid_callback_token(token):
        raise DataContractError("Telegram callback token is invalid")
    value = f"{action}:{token}"
    if len(value.encode("utf-8")) > _MAX_CALLBACK_DATA_BYTES:
        raise DataContractError("Telegram callback data exceeds Telegram limit")
    return value


def _decode_callback_data(value: str | None) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_CALLBACK_DATA_BYTES:
        return None, None
    action, separator, token = value.partition(":")
    if not separator or action not in {"c", "r"} or not _valid_callback_token(token):
        return None, None
    return token, action


def _command(text: str) -> tuple[str, str | None]:
    first, separator, rest = text.partition(" ")
    command = first.split("@", 1)[0].lower()
    return command, rest.strip() if separator and rest.strip() else None


def _parse_bool(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on", "是"}:
        return True
    if normalized in {"0", "false", "no", "off", "否"}:
        return False
    raise ValueError("boolean preference is invalid")


__all__ = [
    "TELEGRAM_AGENT_CHANNEL",
    "TELEGRAM_AGENT_CURSOR_KEY",
    "TELEGRAM_AGENT_OWNER_PRINCIPAL",
    "TelegramAgentClient",
    "TelegramAgentClientError",
    "TelegramAgentPoller",
    "TelegramActionGateway",
    "TelegramPollReceipt",
    "TelegramUpdate",
    "_MAX_UPDATE_BATCH",
    "_parse_update",
    "split_telegram_text",
    "validate_agent_chat_id",
    "validate_agent_user_id",
]
