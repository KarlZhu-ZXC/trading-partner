"""SQLAlchemy persistence for the shared Agent Runtime conversation core."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.agent_conversation_repository import AgentConversationRepository
from domain.agent.enums import (
    AgentChannel,
    AgentConversationStatus,
    AgentMessageRole,
    AgentPendingActionStatus,
    AgentTurnStatus,
)
from domain.agent.models import (
    AgentChannelBinding,
    AgentChannelCursor,
    AgentConversation,
    AgentMessage,
    AgentPendingAction,
    AgentToolReceipt,
    AgentTurn,
)
from domain.common.errors import DataContractError, IdempotencyConflict, PersistenceError
from domain.common.ids import EntityIdPrefix
from infrastructure.persistence.agent_pending_action_repository import (
    SqlAlchemyAgentPendingActionRepository,
)
from infrastructure.persistence.orm import (
    AgentChannelBindingRow,
    AgentChannelCursorRow,
    AgentConversationRow,
    AgentMessageRow,
    AgentToolReceiptRow,
    AgentTurnRow,
)
from infrastructure.system.id_generator import Uuid7IdGenerator

_SAFE_ERROR_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")


def _now(value: datetime | None) -> datetime:
    return value if value is not None else datetime.now(UTC)


def _limit(value: int, *, maximum: int = 500) -> int:
    if type(value) is not int or value <= 0:
        raise DataContractError("limit must be a positive integer")
    return min(value, maximum)


def _conversation(row: AgentConversationRow) -> AgentConversation:
    return AgentConversation(
        conversation_id=row.conversation_id,
        owner_principal=row.owner_principal,
        title=row.title,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
        status=AgentConversationStatus(row.status),
        rolling_summary=row.rolling_summary,
        summary_through_sequence=row.summary_through_sequence,
        next_message_sequence=row.next_message_sequence,
        version=row.version,
    )


def _binding(row: AgentChannelBindingRow) -> AgentChannelBinding:
    return AgentChannelBinding(
        binding_id=row.binding_id,
        conversation_id=row.conversation_id,
        channel=AgentChannel(row.channel),
        external_conversation_ref=row.external_conversation_ref,
        is_active=bool(row.is_active),
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
    )


def _message(row: AgentMessageRow) -> AgentMessage:
    return AgentMessage(
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        role=AgentMessageRole(row.role),
        content=row.content,
        sequence=row.sequence,
        channel=AgentChannel(row.channel) if row.channel else None,
        external_message_ref=row.external_message_ref,
        model=row.model,
        request_id=row.request_id,
        model_receipt_json=row.model_receipt_json,
        created_at=datetime.fromisoformat(row.created_at),
    )


def _turn(row: AgentTurnRow) -> AgentTurn:
    return AgentTurn(
        turn_id=row.turn_id,
        conversation_id=row.conversation_id,
        user_message_id=row.user_message_id,
        assistant_message_id=row.assistant_message_id,
        channel=AgentChannel(row.channel),
        status=AgentTurnStatus(row.status),
        error_code=row.error_code,
        model_id=row.model_id,
        reasoning_effort=row.reasoning_effort,
        started_at=datetime.fromisoformat(row.started_at),
        updated_at=datetime.fromisoformat(row.updated_at),
        completed_at=(
            datetime.fromisoformat(row.completed_at) if row.completed_at is not None else None
        ),
        version=row.version,
    )


def _receipt(row: AgentToolReceiptRow) -> AgentToolReceipt:
    return AgentToolReceipt(
        receipt_id=row.receipt_id,
        conversation_id=row.conversation_id,
        message_id=row.message_id,
        capability=row.capability,
        operation=row.operation,
        arguments_sha256=row.arguments_sha256,
        request_id=row.request_id,
        source_codes=tuple(row.source_codes),
        warning_codes=tuple(row.warning_codes),
        error_codes=tuple(row.error_codes),
        created_at=datetime.fromisoformat(row.created_at),
    )


def _cursor(row: AgentChannelCursorRow) -> AgentChannelCursor:
    return AgentChannelCursor(
        cursor_id=row.cursor_id,
        channel=AgentChannel(row.channel),
        cursor_key=row.cursor_key,
        last_update_id=row.last_update_id,
        version=row.version,
        updated_at=datetime.fromisoformat(row.updated_at),
    )


def _same_message(left: AgentMessage, right: AgentMessage) -> bool:
    return (
        left.conversation_id == right.conversation_id
        and left.role is right.role
        and left.content == right.content
        and left.channel is right.channel
        and left.external_message_ref == right.external_message_ref
        and left.model == right.model
        and left.request_id == right.request_id
        and left.model_receipt_json == right.model_receipt_json
    )


class SqlAlchemyAgentConversationRepository(AgentConversationRepository):
    """Conversation store with atomic append sequencing and CAS updates."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._ids = Uuid7IdGenerator()

    def create_conversation(self, value: AgentConversation) -> AgentConversation:
        row = AgentConversationRow(
            conversation_id=value.conversation_id,
            owner_principal=value.owner_principal,
            title=value.title,
            status=value.status.value,
            rolling_summary=value.rolling_summary,
            summary_through_sequence=value.summary_through_sequence,
            next_message_sequence=value.next_message_sequence,
            version=value.version,
            created_at=value.created_at.isoformat(),
            updated_at=value.updated_at.isoformat(),
        )
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                session.add(row)
            return value
        except IntegrityError as exc:
            existing = self.get_conversation(value.conversation_id)
            if existing is not None and existing == value:
                return existing
            raise IdempotencyConflict("Agent conversation id was reused") from exc

    create = create_conversation

    def get_conversation(self, conversation_id: str) -> AgentConversation | None:
        with Session(self._engine) as session:
            row = session.get(AgentConversationRow, conversation_id)
            return None if row is None else _conversation(row)

    get = get_conversation

    def list_conversations(
        self,
        owner_principal: str | None = None,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[AgentConversation, ...]:
        bounded_limit = _limit(limit)
        with Session(self._engine) as session:
            query = select(AgentConversationRow)
            if owner_principal is not None:
                query = query.where(AgentConversationRow.owner_principal == owner_principal)
            if not include_archived:
                query = query.where(
                    AgentConversationRow.status == AgentConversationStatus.ACTIVE.value
                )
            rows = session.scalars(
                query.order_by(
                    AgentConversationRow.updated_at.desc(),
                    AgentConversationRow.conversation_id,
                ).limit(bounded_limit)
            )
            return tuple(_conversation(row) for row in rows)

    list = list_conversations

    def archive_conversation(
        self,
        conversation_id: str,
        *,
        owner_principal: str | None = None,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> AgentConversation:
        if owner_principal is not None and not owner_principal.strip():
            raise DataContractError("owner_principal must not be blank")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise DataContractError("expected_version must be positive")
        timestamp = _now(now)
        with Session(self._engine) as session:
            predicates = [AgentConversationRow.conversation_id == conversation_id]
            if owner_principal is not None:
                predicates.append(AgentConversationRow.owner_principal == owner_principal)
            if expected_version is not None:
                predicates.append(AgentConversationRow.version == expected_version)
            result = session.execute(
                update(AgentConversationRow)
                .where(*predicates)
                .values(
                    status=AgentConversationStatus.ARCHIVED.value,
                    updated_at=timestamp.isoformat(),
                    version=AgentConversationRow.version + 1,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                raise PersistenceError(
                    "Agent conversation archive version conflict",
                    details={"conversation_id": conversation_id},
                    retryable=False,
                    code="AGENT_CONVERSATION_VERSION_CONFLICT",
                )
            session.commit()
        value = self.get_conversation(conversation_id)
        assert value is not None
        return value

    def bind_channel(self, value: AgentChannelBinding) -> AgentChannelBinding:
        if self.get_conversation(value.conversation_id) is None:
            raise PersistenceError("Agent conversation was not found", retryable=False)
        existing = self.get_binding(
            value.channel,
            value.external_conversation_ref,
            active_only=False,
        )
        if existing is not None:
            if existing.conversation_id != value.conversation_id:
                # Historical inactive bindings may be superseded by a new
                # conversation (``/new`` or a one-time handoff).  The partial
                # active unique index permits the new row while retaining the
                # old immutable binding history.
                if existing.is_active or not value.is_active:
                    raise IdempotencyConflict("External Agent conversation is already bound")
            elif existing.is_active == value.is_active:
                return existing
            if existing.conversation_id == value.conversation_id and value.is_active:
                with Session(self._engine) as session:
                    result = session.execute(
                        update(AgentChannelBindingRow)
                        .where(AgentChannelBindingRow.binding_id == existing.binding_id)
                        .values(
                            is_active=True,
                            updated_at=value.updated_at.isoformat(),
                        )
                    )
                    if result.rowcount != 1:  # type: ignore[attr-defined]
                        session.rollback()
                        raise PersistenceError("Agent channel binding was changed concurrently")
                    session.commit()
                rebound = self.get_binding(value.channel, value.external_conversation_ref)
                assert rebound is not None
                return rebound

        row = AgentChannelBindingRow(
            binding_id=value.binding_id,
            conversation_id=value.conversation_id,
            channel=value.channel.value,
            external_conversation_ref=value.external_conversation_ref,
            is_active=value.is_active,
            created_at=value.created_at.isoformat(),
            updated_at=value.updated_at.isoformat(),
        )
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                session.add(row)
            return value
        except IntegrityError as exc:
            replay = self.get_binding(value.channel, value.external_conversation_ref)
            if replay is not None and replay.conversation_id == value.conversation_id:
                return replay
            raise IdempotencyConflict(
                "Agent channel binding conflicts with an active binding"
            ) from exc

    bind = bind_channel

    def get_binding(
        self,
        channel: AgentChannel,
        external_conversation_ref: str,
        *,
        active_only: bool = True,
    ) -> AgentChannelBinding | None:
        if not isinstance(channel, AgentChannel):
            raise DataContractError("channel is invalid")
        with Session(self._engine) as session:
            query = select(AgentChannelBindingRow).where(
                AgentChannelBindingRow.channel == channel.value,
                AgentChannelBindingRow.external_conversation_ref == external_conversation_ref,
            )
            if active_only:
                query = query.where(AgentChannelBindingRow.is_active.is_(True))
            row = session.scalars(
                query.order_by(AgentChannelBindingRow.updated_at.desc()).limit(1)
            ).first()
            return None if row is None else _binding(row)

    get_active_binding = get_binding

    def deactivate_channel(
        self,
        channel: AgentChannel,
        external_conversation_ref: str,
        *,
        now: datetime | None = None,
    ) -> AgentChannelBinding | None:
        current = self.get_binding(channel, external_conversation_ref)
        if current is None:
            return None
        timestamp = _now(now)
        with Session(self._engine) as session:
            result = session.execute(
                update(AgentChannelBindingRow)
                .where(
                    AgentChannelBindingRow.binding_id == current.binding_id,
                    AgentChannelBindingRow.is_active.is_(True),
                )
                .values(is_active=False, updated_at=timestamp.isoformat())
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                return self.get_binding(channel, external_conversation_ref)
            session.commit()
        with Session(self._engine) as session:
            row = session.get(AgentChannelBindingRow, current.binding_id)
            return None if row is None else _binding(row)

    deactivate = deactivate_channel

    def append_message(self, value: AgentMessage) -> AgentMessage:
        if value.external_message_ref is not None:
            with Session(self._engine) as session:
                existing_row = session.scalar(
                    select(AgentMessageRow).where(
                        AgentMessageRow.channel
                        == (value.channel.value if value.channel is not None else None),
                        AgentMessageRow.external_message_ref == value.external_message_ref
                    )
                )
                if existing_row is not None:
                    existing = _message(existing_row)
                    if _same_message(existing, value):
                        return existing
                    raise IdempotencyConflict("Agent external message reference was reused")

        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                conversation = session.scalar(
                    select(AgentConversationRow).where(
                        AgentConversationRow.conversation_id == value.conversation_id
                    )
                )
                if conversation is None:
                    raise PersistenceError("Agent conversation was not found", retryable=False)
                if conversation.status != AgentConversationStatus.ACTIVE.value:
                    raise PersistenceError(
                        "Archived Agent conversations are read-only",
                        retryable=False,
                    )
                # RETURNING makes sequence allocation one atomic database
                # operation; no application-side max(sequence) race exists.
                result = session.execute(
                    update(AgentConversationRow)
                    .where(AgentConversationRow.conversation_id == value.conversation_id)
                    .values(
                        next_message_sequence=AgentConversationRow.next_message_sequence + 1,
                        updated_at=value.created_at.isoformat(),
                        version=AgentConversationRow.version + 1,
                    )
                    .returning(AgentConversationRow.next_message_sequence)
                )
                next_sequence = result.scalar_one_or_none()
                if next_sequence is None:
                    raise PersistenceError("Agent conversation was not found", retryable=False)
                sequence = int(next_sequence) - 1
                row = AgentMessageRow(
                    message_id=value.message_id,
                    conversation_id=value.conversation_id,
                    sequence=sequence,
                    role=value.role.value,
                    content=value.content,
                    channel=value.channel.value if value.channel else None,
                    external_message_ref=value.external_message_ref,
                    model=value.model,
                    request_id=value.request_id,
                    model_receipt_json=value.model_receipt_json,
                    created_at=value.created_at.isoformat(),
                )
                session.add(row)
            return replace(value, sequence=sequence)
        except IntegrityError as exc:
            if value.external_message_ref is not None:
                replay = self._get_message_by_external_ref(
                    value.channel,
                    value.external_message_ref,
                )
                if replay is not None and _same_message(replay, value):
                    return replay
            raise PersistenceError(
                "Agent message append conflicted",
                details={"conversation_id": value.conversation_id},
                retryable=True,
            ) from exc

    append = append_message

    def _get_message_by_external_ref(
        self,
        channel: AgentChannel | None,
        external_message_ref: str,
    ) -> AgentMessage | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(AgentMessageRow).where(
                    AgentMessageRow.channel == (channel.value if channel is not None else None),
                    AgentMessageRow.external_message_ref == external_message_ref
                )
            )
            return None if row is None else _message(row)

    def list_messages(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        newest_first: bool = False,
    ) -> tuple[AgentMessage, ...]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise DataContractError("after_sequence must be an integer >= 0")
        bounded_limit = _limit(limit)
        with Session(self._engine) as session:
            order = (
                AgentMessageRow.sequence.desc()
                if newest_first
                else AgentMessageRow.sequence
            )
            rows = tuple(session.scalars(
                select(AgentMessageRow)
                .where(
                    AgentMessageRow.conversation_id == conversation_id,
                    AgentMessageRow.sequence > after_sequence,
                )
                .order_by(order)
                .limit(bounded_limit)
            ))
            values = tuple(_message(row) for row in rows)
            # Model context remains chronological even though the database
            # query selects the newest bounded tail first.
            return tuple(reversed(values)) if newest_first else values

    def get_message_by_external_ref(
        self,
        channel: AgentChannel,
        external_message_ref: str,
    ) -> AgentMessage | None:
        """Return one channel-scoped inbound message for idempotent adapters."""

        if not isinstance(channel, AgentChannel):
            raise DataContractError("channel is invalid")
        if not isinstance(external_message_ref, str) or not external_message_ref.strip():
            raise DataContractError("external_message_ref must not be blank")
        return self._get_message_by_external_ref(channel, external_message_ref)

    def append_tool_receipt(self, value: AgentToolReceipt) -> AgentToolReceipt:
        if self.get_conversation(value.conversation_id) is None:
            raise PersistenceError("Agent conversation was not found", retryable=False)
        row = AgentToolReceiptRow(
            receipt_id=value.receipt_id,
            conversation_id=value.conversation_id,
            message_id=value.message_id,
            capability=value.capability,
            operation=value.operation,
            arguments_sha256=value.arguments_sha256,
            request_id=value.request_id,
            source_codes=value.source_codes,
            warning_codes=value.warning_codes,
            error_codes=value.error_codes,
            created_at=value.created_at.isoformat(),
        )
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                session.add(row)
            return value
        except IntegrityError as exc:
            existing = self.get_tool_receipt(value.receipt_id)
            if existing is not None and existing == value:
                return existing
            raise IdempotencyConflict("Agent tool receipt id was reused") from exc

    append_receipt = append_tool_receipt

    def create_turn(self, value: AgentTurn) -> AgentTurn:
        """Insert one RUNNING turn, replaying an identical turn id safely."""

        row = AgentTurnRow(
            turn_id=value.turn_id,
            conversation_id=value.conversation_id,
            user_message_id=value.user_message_id,
            assistant_message_id=value.assistant_message_id,
            channel=value.channel.value,
            status=value.status.value,
            error_code=value.error_code,
            model_id=value.model_id,
            reasoning_effort=value.reasoning_effort,
            started_at=value.started_at.isoformat(),
            updated_at=value.updated_at.isoformat(),
            completed_at=value.completed_at.isoformat() if value.completed_at else None,
            version=value.version,
        )
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                session.add(row)
            return value
        except IntegrityError as exc:
            existing = self.get_turn(value.turn_id)
            if existing is not None and existing == value:
                return existing
            raise IdempotencyConflict("Agent turn id was reused") from exc

    def get_turn(self, turn_id: str) -> AgentTurn | None:
        with Session(self._engine) as session:
            row = session.get(AgentTurnRow, turn_id)
            return None if row is None else _turn(row)

    def latest_turn(self, conversation_id: str) -> AgentTurn | None:
        with Session(self._engine) as session:
            row = session.scalars(
                select(AgentTurnRow)
                .where(AgentTurnRow.conversation_id == conversation_id)
                .order_by(AgentTurnRow.started_at.desc(), AgentTurnRow.turn_id.desc())
                .limit(1)
            ).first()
            return None if row is None else _turn(row)

    get_latest_turn = latest_turn

    def list_turns(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
        newest_first: bool = True,
    ) -> tuple[AgentTurn, ...]:
        bounded_limit = _limit(limit)
        with Session(self._engine) as session:
            order = AgentTurnRow.started_at.desc() if newest_first else AgentTurnRow.started_at
            tie_breaker = AgentTurnRow.turn_id.desc() if newest_first else AgentTurnRow.turn_id
            rows = session.scalars(
                select(AgentTurnRow)
                .where(AgentTurnRow.conversation_id == conversation_id)
                .order_by(order, tie_breaker)
                .limit(bounded_limit)
            )
            return tuple(_turn(row) for row in rows)

    def update_turn(
        self,
        turn_id: str,
        *,
        status: AgentTurnStatus,
        expected_version: int,
        assistant_message_id: str | None = None,
        error_code: str | None = None,
        completed_at: datetime | None = None,
        now: datetime | None = None,
    ) -> AgentTurn:
        """CAS update of lifecycle state; exception text is never persisted."""

        if not isinstance(status, AgentTurnStatus):
            raise DataContractError("status is invalid")
        if type(expected_version) is not int or expected_version < 1:
            raise DataContractError("expected_version must be positive")
        if assistant_message_id is not None and not assistant_message_id.strip():
            raise DataContractError("assistant_message_id must not be blank")
        if error_code is not None and (
            not isinstance(error_code, str) or _SAFE_ERROR_CODE.fullmatch(error_code) is None
        ):
            raise DataContractError("error_code must be text or null")
        timestamp = _now(now)
        terminal = status in {
            AgentTurnStatus.COMPLETED,
            AgentTurnStatus.FAILED,
            AgentTurnStatus.CANCELLED,
        }
        if terminal and completed_at is None:
            completed_at = timestamp
        if not terminal and completed_at is not None:
            raise DataContractError("active Agent turns cannot have completed_at")
        completed_value = completed_at.isoformat() if completed_at is not None else None
        with Session(self._engine) as session:
            result = session.execute(
                update(AgentTurnRow)
                .where(
                    AgentTurnRow.turn_id == turn_id,
                    AgentTurnRow.version == expected_version,
                )
                .values(
                    status=status.value,
                    assistant_message_id=assistant_message_id,
                    error_code=error_code,
                    completed_at=completed_value,
                    updated_at=timestamp.isoformat(),
                    version=AgentTurnRow.version + 1,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                raise PersistenceError(
                    "Agent turn version conflict",
                    details={"turn_id": turn_id},
                    retryable=False,
                    code="AGENT_TURN_VERSION_CONFLICT",
                )
            session.commit()
        value = self.get_turn(turn_id)
        if value is None:
            raise PersistenceError("Agent turn was not found", retryable=False)
        return value

    cas_update_turn = update_turn

    def get_tool_receipt(self, receipt_id: str) -> AgentToolReceipt | None:
        with Session(self._engine) as session:
            row = session.get(AgentToolReceiptRow, receipt_id)
            return None if row is None else _receipt(row)

    def list_tool_receipts(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[AgentToolReceipt, ...]:
        bounded_limit = _limit(limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AgentToolReceiptRow)
                .where(AgentToolReceiptRow.conversation_id == conversation_id)
                .order_by(AgentToolReceiptRow.created_at, AgentToolReceiptRow.receipt_id)
                .limit(bounded_limit)
            )
            return tuple(_receipt(row) for row in rows)

    def update_summary(
        self,
        conversation_id: str,
        rolling_summary: str,
        summary_through_sequence: int,
        expected_summary_through_sequence: int = 0,
        *,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> AgentConversation:
        if (
            type(expected_summary_through_sequence) is not int
            or expected_summary_through_sequence < 0
        ):
            raise DataContractError("expected_summary_through_sequence must be >= 0")
        if type(summary_through_sequence) is not int or summary_through_sequence < 0:
            raise DataContractError("summary_through_sequence must be >= 0")
        if summary_through_sequence < expected_summary_through_sequence:
            raise DataContractError("summary_through_sequence must be monotonic")
        if not isinstance(rolling_summary, str) or len(rolling_summary) > 32_000:
            raise DataContractError("rolling_summary must be bounded text")
        timestamp = _now(now)
        predicates = [
            AgentConversationRow.conversation_id == conversation_id,
            AgentConversationRow.summary_through_sequence
            == expected_summary_through_sequence,
        ]
        if expected_version is not None:
            if type(expected_version) is not int or expected_version < 1:
                raise DataContractError("expected_version must be positive")
            predicates.append(AgentConversationRow.version == expected_version)
        with Session(self._engine) as session:
            result = session.execute(
                update(AgentConversationRow)
                .where(*predicates)
                .values(
                    rolling_summary=rolling_summary,
                    summary_through_sequence=summary_through_sequence,
                    updated_at=timestamp.isoformat(),
                    version=AgentConversationRow.version + 1,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                raise PersistenceError(
                    "Agent conversation summary version conflict",
                    details={"conversation_id": conversation_id},
                    retryable=False,
                )
            session.commit()
        value = self.get_conversation(conversation_id)
        if value is None:
            raise PersistenceError("Agent conversation was not found", retryable=False)
        return value

    def get_cursor(
        self,
        channel: AgentChannel,
        cursor_key: str = "default",
    ) -> AgentChannelCursor | None:
        if not isinstance(channel, AgentChannel):
            raise DataContractError("channel is invalid")
        with Session(self._engine) as session:
            row = session.scalar(
                select(AgentChannelCursorRow).where(
                    AgentChannelCursorRow.channel == channel.value,
                    AgentChannelCursorRow.cursor_key == cursor_key,
                )
            )
            return None if row is None else _cursor(row)

    def advance_cursor(
        self,
        channel: AgentChannel,
        cursor_key: str = "default",
        update_id: int | None = None,
        expected_update_id: int | None = None,
        *,
        next_update_id: int | None = None,
        now: datetime | None = None,
    ) -> AgentChannelCursor:
        if not isinstance(channel, AgentChannel):
            raise DataContractError("channel is invalid")
        if update_id is None:
            update_id = next_update_id
        if type(update_id) is not int or update_id < -1:
            raise DataContractError("update_id must be an integer >= -1")
        if expected_update_id is not None and (
            type(expected_update_id) is not int or expected_update_id < -1
        ):
            raise DataContractError("expected_update_id must be an integer >= -1")
        timestamp = _now(now)
        current = self.get_cursor(channel, cursor_key)
        if current is None:
            if expected_update_id not in (None, -1):
                raise PersistenceError(
                    "Agent channel cursor version conflict",
                    retryable=False,
                )
            cursor_id = self._ids.new(EntityIdPrefix.AGENT_CURSOR)
            value = AgentChannelCursor(
                cursor_id=cursor_id,
                channel=channel,
                cursor_key=cursor_key,
                last_update_id=update_id,
                updated_at=timestamp,
            )
            try:
                with Session(self._engine, expire_on_commit=False) as session, session.begin():
                    session.add(
                        AgentChannelCursorRow(
                            cursor_id=value.cursor_id,
                            channel=value.channel.value,
                            cursor_key=value.cursor_key,
                            last_update_id=value.last_update_id,
                            version=value.version,
                            updated_at=value.updated_at.isoformat(),
                        )
                    )
                return value
            except IntegrityError as exc:
                raise PersistenceError(
                    "Agent channel cursor was created concurrently",
                    retryable=True,
                ) from exc
        if expected_update_id is not None and expected_update_id != current.last_update_id:
            raise PersistenceError("Agent channel cursor version conflict", retryable=False)
        if update_id < current.last_update_id:
            raise PersistenceError(
                "Agent channel cursor cannot move backwards",
                retryable=False,
            )
        if update_id == current.last_update_id:
            return current
        with Session(self._engine) as session:
            result = session.execute(
                update(AgentChannelCursorRow)
                .where(
                    AgentChannelCursorRow.cursor_id == current.cursor_id,
                    AgentChannelCursorRow.version == current.version,
                    AgentChannelCursorRow.last_update_id == current.last_update_id,
                )
                .values(
                    last_update_id=update_id,
                    version=current.version + 1,
                    updated_at=timestamp.isoformat(),
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                raise PersistenceError("Agent channel cursor version conflict", retryable=False)
            session.commit()
        updated = self.get_cursor(channel, cursor_key)
        assert updated is not None
        return updated

    # Pending actions share the same engine but remain a separate protocol so
    # Agent-A cannot accidentally acquire an execution port.
    def create_pending_action(self, value: AgentPendingAction) -> AgentPendingAction:
        return SqlAlchemyAgentPendingActionRepository(self._engine).create_pending_action(value)

    def get_pending_action(self, action_id: str) -> AgentPendingAction | None:
        return SqlAlchemyAgentPendingActionRepository(self._engine).get_pending_action(action_id)

    def transition_exact(
        self,
        action_id: str,
        status: AgentPendingActionStatus,
        *,
        arguments_sha256: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int,
        token_sha256: str | None = None,
        result_receipt_json: str | None = None,
        now: datetime | None = None,
    ) -> AgentPendingAction:
        return SqlAlchemyAgentPendingActionRepository(self._engine).transition_exact(
            action_id,
            status,
            arguments_sha256=arguments_sha256,
            channel=channel,
            principal=principal,
            expected_version=expected_version,
            token_sha256=token_sha256,
            result_receipt_json=result_receipt_json,
            now=now,
        )

    def list_pending_actions(
        self,
        conversation_id: str,
        *,
        channel: AgentChannel | None = None,
        principal: str | None = None,
        include_terminal: bool = False,
        limit: int = 100,
    ) -> tuple[AgentPendingAction, ...]:
        return SqlAlchemyAgentPendingActionRepository(self._engine).list_pending_actions(
            conversation_id,
            channel=channel,
            principal=principal,
            include_terminal=include_terminal,
            limit=limit,
        )

    def expire_due_pending_actions(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        return SqlAlchemyAgentPendingActionRepository(self._engine).expire_due(
            now=now,
            limit=limit,
        )


# Compatibility spelling used by some infrastructure imports.
AgentConversationRepositorySqlAlchemy = SqlAlchemyAgentConversationRepository
