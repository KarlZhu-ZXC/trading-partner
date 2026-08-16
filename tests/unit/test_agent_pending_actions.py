"""Security-focused Agent-D pending-action lifecycle contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine

from application.ports.agent_action_gateway import AgentActionInvocationResult
from application.services.agent_pending_action_service import (
    AgentPendingActionService,
    pending_action_wire,
)
from domain.agent.enums import AgentChannel, AgentPendingActionStatus
from domain.agent.models import AgentPendingAction, arguments_digest
from domain.common.errors import DataContractError, PersistenceError, TradingPartnerError
from infrastructure.persistence.agent_pending_action_repository import (
    SqlAlchemyAgentPendingActionRepository,
)
from infrastructure.persistence.metadata import Base
from interfaces.agent.action_gateway import CompactAgentActionOperationGateway
from interfaces.mcp.tools.compact import (
    APPEND,
    CompactCapabilityRegistry,
    _register_dispatch_tool,
    _spec,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class IDs:
    def __init__(self) -> None:
        self.ordinal = 0

    def new(self, prefix: Any) -> str:
        self.ordinal += 1
        return f"{prefix.value}_00000000-0000-7000-8000-{self.ordinal:012d}"


class Repo:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.values: dict[str, AgentPendingAction] = {}

    def create_pending_action(self, value: AgentPendingAction) -> AgentPendingAction:
        self.values[value.action_id] = value
        return value

    def get_pending_action(self, action_id: str) -> AgentPendingAction | None:
        return self.values.get(action_id)

    def get_by_token_sha256(self, token_sha256: str) -> AgentPendingAction | None:
        if re.fullmatch(r"[0-9a-f]{64}", token_sha256) is None:
            raise DataContractError("token_sha256 must be a lowercase SHA-256 digest")
        return next(
            (item for item in self.values.values() if item.token_sha256 == token_sha256),
            None,
        )

    def list_pending_actions(
        self, conversation_id: str, **kwargs: Any
    ) -> tuple[AgentPendingAction, ...]:
        return tuple(
            item for item in self.values.values() if item.conversation_id == conversation_id
        )

    def expire_due(self, *, now: datetime | None = None, limit: int = 100) -> int:
        timestamp = now or self.clock.now()
        changed = 0
        for action_id, item in tuple(self.values.items())[:limit]:
            if item.expires_at > timestamp or item.status not in {
                AgentPendingActionStatus.PROPOSED,
                AgentPendingActionStatus.PRESENTED,
                AgentPendingActionStatus.CONFIRMED,
                AgentPendingActionStatus.EXECUTING,
            }:
                continue
            self.values[action_id] = replace(
                item,
                status=(
                    AgentPendingActionStatus.UNKNOWN
                    if item.status is AgentPendingActionStatus.EXECUTING
                    else AgentPendingActionStatus.EXPIRED
                ),
                version=item.version + 1,
                updated_at=timestamp,
            )
            changed += 1
        return changed

    def transition_exact(
        self,
        action_id: str,
        status: AgentPendingActionStatus,
        **kwargs: Any,
    ) -> AgentPendingAction:
        current = self.values[action_id]
        if kwargs["expected_version"] != current.version:
            raise PersistenceError(
                "version",
                retryable=False,
                code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
            )
        if kwargs["arguments_sha256"] != current.arguments_sha256:
            raise PersistenceError("hash", retryable=False)
        if kwargs["channel"] is not current.channel or kwargs["principal"] != current.principal:
            raise PersistenceError(
                "identity",
                retryable=False,
                code="AGENT_PENDING_ACTION_IDENTITY_MISMATCH",
            )
        token_sha256 = kwargs.get("token_sha256")
        if token_sha256 != current.token_sha256:
            raise PersistenceError(
                "token",
                retryable=False,
                code="AGENT_PENDING_ACTION_TOKEN_MISMATCH",
            )
        now = kwargs.get("now") or self.clock.now()
        if status is not AgentPendingActionStatus.EXPIRED and now >= current.expires_at:
            self.values[action_id] = replace(
                current,
                status=AgentPendingActionStatus.EXPIRED,
                version=current.version + 1,
                updated_at=now,
            )
            raise PersistenceError("expired", retryable=False, code="AGENT_PENDING_ACTION_EXPIRED")
        updated = replace(
            current,
            status=status,
            version=current.version + 1,
            updated_at=now,
            result_receipt_json=kwargs.get("result_receipt_json") or current.result_receipt_json,
        )
        self.values[action_id] = updated
        return updated

    def reissue_confirmation_token(
        self,
        action_id: str,
        *,
        conversation_id: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int,
        token_sha256: str,
        now: datetime,
    ) -> AgentPendingAction:
        current = self.values[action_id]
        if current.conversation_id != conversation_id or (
            current.channel is not channel or current.principal != principal
        ):
            raise PersistenceError(
                "identity",
                retryable=False,
                code="AGENT_PENDING_ACTION_IDENTITY_MISMATCH",
            )
        if current.version != expected_version:
            raise PersistenceError(
                "version",
                retryable=False,
                code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
            )
        if current.status is not AgentPendingActionStatus.PRESENTED:
            raise PersistenceError(
                "state",
                retryable=False,
                code="AGENT_PENDING_ACTION_STATE_CONFLICT",
            )
        if now >= current.expires_at:
            self.values[action_id] = replace(
                current,
                status=AgentPendingActionStatus.EXPIRED,
                version=current.version + 1,
                updated_at=now,
            )
            raise PersistenceError(
                "expired",
                retryable=False,
                code="AGENT_PENDING_ACTION_EXPIRED",
            )
        updated = replace(
            current,
            token_sha256=token_sha256,
            version=current.version + 1,
            updated_at=now,
        )
        self.values[action_id] = updated
        return updated


def test_sql_repository_lists_unresolved_actions_without_mutating_them(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-unresolved.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAgentPendingActionRepository(engine)
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)

    def store(
        action_id: str,
        status: AgentPendingActionStatus,
        *,
        created_minutes: int,
        expiry_minutes: int,
    ) -> None:
        created_at = now + timedelta(minutes=created_minutes)
        arguments = {"instrument_id": "equity:US:AAPL", "idempotency_key": action_id}
        repository.create_pending_action(
            AgentPendingAction(
                action_id=action_id,
                conversation_id="agent_conversation_unresolved",
                channel=AgentChannel.CONSOLE,
                principal="local-console",
                normalized_arguments=arguments,
                arguments_sha256=arguments_digest(arguments),
                presented_summary=action_id,
                expires_at=now + timedelta(minutes=expiry_minutes),
                created_at=created_at,
                updated_at=created_at,
                status=status,
            )
        )

    store("action_unknown", AgentPendingActionStatus.UNKNOWN, created_minutes=-3, expiry_minutes=5)
    store("action_stale", AgentPendingActionStatus.EXECUTING, created_minutes=-2, expiry_minutes=-1)
    store("action_live", AgentPendingActionStatus.EXECUTING, created_minutes=-1, expiry_minutes=5)
    store(
        "action_presented",
        AgentPendingActionStatus.PRESENTED,
        created_minutes=-4,
        expiry_minutes=5,
    )

    unresolved = repository.list_unresolved(now=now, limit=10)

    assert [item.action_id for item in unresolved] == ["action_stale", "action_unknown"]
    assert repository.get("action_stale").status is AgentPendingActionStatus.EXECUTING


def test_sql_repository_reissue_rotates_digest_with_exact_cas(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-reissue.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAgentPendingActionRepository(engine)
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    arguments = {"instrument_id": "equity:US:AAPL", "idempotency_key": "reissue-1"}
    old_token_hash = hashlib.sha256(b"old-token").hexdigest()
    new_token_hash = hashlib.sha256(b"new-token").hexdigest()
    action = AgentPendingAction(
        action_id="action_reissue",
        conversation_id="agent_conversation_reissue",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        normalized_arguments=arguments,
        arguments_sha256=arguments_digest(arguments),
        presented_summary="Add AAPL",
        expires_at=now + timedelta(minutes=10),
        created_at=now,
        updated_at=now,
        status=AgentPendingActionStatus.PRESENTED,
        version=2,
        capability="watchlist_manage",
        operation="add",
        token_sha256=old_token_hash,
    )
    repository.create_pending_action(action)

    updated = repository.reissue_confirmation_token(
        action.action_id,
        conversation_id=action.conversation_id,
        channel=action.channel,
        principal=action.principal,
        expected_version=action.version,
        token_sha256=new_token_hash,
        now=now + timedelta(minutes=1),
    )

    assert updated.version == 3
    assert updated.token_sha256 == new_token_hash
    assert updated.normalized_arguments == action.normalized_arguments
    assert updated.arguments_sha256 == action.arguments_sha256
    assert updated.capability == action.capability
    assert updated.operation == action.operation
    assert updated.expires_at == action.expires_at
    assert repository.get_by_token_sha256(old_token_hash) is None
    assert repository.get_by_token_sha256(new_token_hash) == updated
    with pytest.raises(PersistenceError) as stale:
        repository.reissue_confirmation_token(
            action.action_id,
            conversation_id=action.conversation_id,
            channel=action.channel,
            principal=action.principal,
            expected_version=action.version,
            token_sha256=old_token_hash,
            now=now + timedelta(minutes=1),
        )
    assert stale.value.code == "AGENT_PENDING_ACTION_VERSION_CONFLICT"


class OperationGateway:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.invocations: list[tuple[str, str, dict[str, Any]]] = []

    def validate_operation(
        self,
        capability: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if capability == "watchlist_manage" and operation == "add":
            if not isinstance(arguments.get("instrument_id"), str):
                raise ValueError("instrument_id required")
            return dict(arguments)
        if capability == "research_judgment_propose" and operation == "research_state":
            return dict(arguments)
        if (capability, operation) in {
            ("research_judgment_confirm", "candidate"),
            ("research_memory_append", "agenda_item"),
            ("research_workflow_run", "trade_retro"),
            ("research_workflow_run", "judgment_scorecard"),
        }:
            return dict(arguments)
        raise ValueError("unsupported")

    async def invoke_operation(
        self,
        capability: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> AgentActionInvocationResult:
        self.invocations.append((capability, operation, dict(arguments)))
        if self.failure is not None:
            raise self.failure
        return AgentActionInvocationResult(
            result={"ok": True, "request_id": "req_action", "id": "watch_1"},
            receipt_json='{"status":"SUCCEEDED","request_id":"req_action"}',
        )


def _service(
    *, gateway: OperationGateway | None = None
) -> tuple[AgentPendingActionService, Repo, Clock]:
    clock = Clock()
    repo = Repo(clock)
    service = AgentPendingActionService(
        repository=repo,
        operation_gateway=gateway or OperationGateway(),
        clock=clock,
        id_generator=IDs(),
    )
    return service, repo, clock


def _args() -> dict[str, Any]:
    return {
        "instrument_id": "equity:US:AAPL",
        "confirmed_by": "external_agent",
        "idempotency_key": "idem-1",
    }


def test_prepare_allowlist_schema_hash_and_token_are_bounded() -> None:
    service, repo, _clock = _service()
    proposal = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL https://secret.example?token=secret api_key=secret",
    )
    assert proposal.action.status is AgentPendingActionStatus.PRESENTED
    assert proposal.action.arguments_sha256 == arguments_digest(
        proposal.action.normalized_arguments
    )
    assert proposal.confirmation_token not in str(pending_action_wire(proposal.action))
    assert "normalized_arguments" not in pending_action_wire(proposal.action)
    assert pending_action_wire(proposal.action)["confirmation_details"] == (
        {"path": "instrument_id", "value": "equity:US:AAPL"},
    )
    assert proposal.action.token_sha256 not in str(pending_action_wire(proposal.action))
    assert "secret.example" not in proposal.action.presented_summary
    assert "api_key=secret" not in proposal.action.presented_summary
    assert repo.get_by_token_sha256(proposal.action.token_sha256 or "") == proposal.action
    with pytest.raises(DataContractError):
        repo.get_by_token_sha256("not-a-digest")


def test_reissue_rotates_only_confirmation_digest_and_invalidates_old_token() -> None:
    service, repo, _clock = _service()
    proposal = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    old = proposal.action

    reissued = service.reissue_confirmation(
        action_id=old.action_id,
        conversation_id=old.conversation_id,
        channel=old.channel,
        principal=old.principal,
        expected_version=old.version,
    )
    updated = reissued.action

    assert updated.status is AgentPendingActionStatus.PRESENTED
    assert updated.version == old.version + 1
    assert updated.token_sha256 != old.token_sha256
    assert updated.normalized_arguments == old.normalized_arguments
    assert updated.arguments_sha256 == old.arguments_sha256
    assert updated.capability == old.capability
    assert updated.operation == old.operation
    assert updated.expires_at == old.expires_at
    assert repo.get_by_token_sha256(old.token_sha256 or "") is None
    assert repo.get_by_token_sha256(updated.token_sha256 or "") == updated

    with pytest.raises(PersistenceError) as old_token:
        service.reject(
            action_id=old.action_id,
            token=proposal.confirmation_token,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            expected_version=updated.version,
        )
    assert old_token.value.code == "AGENT_PENDING_ACTION_TOKEN_MISMATCH"


def test_reissue_rejects_wrong_scope_terminal_expired_and_stale_version() -> None:
    service, repo, clock = _service()
    proposal = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    action_id = proposal.action.action_id

    with pytest.raises(PersistenceError) as wrong_scope:
        service.reissue_confirmation(
            action_id=action_id,
            conversation_id="other-conversation",
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            expected_version=proposal.action.version,
        )
    assert wrong_scope.value.code == "AGENT_PENDING_ACTION_IDENTITY_MISMATCH"

    with pytest.raises(PersistenceError) as wrong_version:
        service.reissue_confirmation(
            action_id=action_id,
            conversation_id=proposal.action.conversation_id,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            expected_version=999,
        )
    assert wrong_version.value.code == "AGENT_PENDING_ACTION_VERSION_CONFLICT"

    repo.values[action_id] = replace(
        repo.values[action_id],
        status=AgentPendingActionStatus.REJECTED,
    )
    with pytest.raises(PersistenceError) as terminal:
        service.reissue_confirmation(
            action_id=action_id,
            conversation_id=proposal.action.conversation_id,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            expected_version=proposal.action.version,
        )
    assert terminal.value.code == "AGENT_PENDING_ACTION_STATE_CONFLICT"

    repo.values[action_id] = replace(
        repo.values[action_id],
        status=AgentPendingActionStatus.PRESENTED,
        version=proposal.action.version,
        expires_at=clock.now() + timedelta(minutes=1),
    )
    clock.value = repo.values[action_id].expires_at
    with pytest.raises(PersistenceError) as expired:
        service.reissue_confirmation(
            action_id=action_id,
            conversation_id=proposal.action.conversation_id,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            expected_version=proposal.action.version,
        )
    assert expired.value.code == "AGENT_PENDING_ACTION_EXPIRED"
    assert repo.values[action_id].status is AgentPendingActionStatus.EXPIRED


def test_prepare_rejects_broker_sync_and_non_effective_proposals() -> None:
    service, _repo, _clock = _service()
    with pytest.raises(DataContractError, match="allowlist"):
        service.propose(
            conversation_id="agent_conversation_1",
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            capability="broker_order_manage",
            operation="submit",
            arguments={},
            presented_summary="order",
        )
    with pytest.raises(DataContractError, match="allowlist"):
        service.propose(
            conversation_id="agent_conversation_1",
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            capability="research_judgment_propose",
            operation="research_state",
            arguments={"payload": {"kind": "trade_plan"}},
            presented_summary="trade plan",
        )
    with pytest.raises(DataContractError, match="allowlist"):
        service.propose(
            conversation_id="agent_conversation_1",
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            capability="research_judgment_propose",
            operation="research_state",
            arguments={"payload": {"kind": "watchlist_item", "action": "archive"}},
            presented_summary="unsupported candidate transition",
        )


def test_prepare_allows_exact_research_closure_actions_and_requires_user_authority() -> None:
    service, _repo, _clock = _service()
    candidate = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="research_judgment_confirm",
        operation="candidate",
        arguments={
            "candidate_id": "candidate_1",
            "action": "confirm",
            "reviewed_by": "user",
            "submitted_via": "codex_chat",
            "authorization_note": "User explicitly confirmed this exact candidate.",
        },
        presented_summary="Confirm the exact candidate",
    )
    assert candidate.action.status is AgentPendingActionStatus.PRESENTED
    assert {
        ("research_memory_append", "agenda_item"),
        ("research_workflow_run", "trade_retro"),
        ("research_workflow_run", "judgment_scorecard"),
    }.issubset(service.allowlist)

    with pytest.raises(DataContractError) as missing_authority:
        service.propose(
            conversation_id="agent_conversation_1",
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            capability="research_judgment_confirm",
            operation="candidate",
            arguments={
                "candidate_id": "candidate_1",
                "action": "confirm",
                "reviewed_by": "external_agent",
                "submitted_via": "direct",
            },
            presented_summary="Confirm the candidate",
        )
    assert missing_authority.value.code == "AGENT_ACTION_NOT_ALLOWED"


def test_expired_executing_action_becomes_unknown_instead_of_retryable() -> None:
    service, repo, clock = _service()
    proposal = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    repo.values[proposal.action.action_id] = replace(
        proposal.action,
        status=AgentPendingActionStatus.EXECUTING,
    )
    clock.value = proposal.action.expires_at

    actions = service.list(
        "agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        include_terminal=True,
    )

    assert actions[0].status is AgentPendingActionStatus.UNKNOWN


@pytest.mark.asyncio
async def test_confirm_requires_exact_token_identity_version_and_is_single_use() -> None:
    service, repo, _clock = _service()
    proposal = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    with pytest.raises(PersistenceError) as wrong_token:
        await service.confirm(
            action_id=proposal.action.action_id,
            token="wrong",
            expected_version=proposal.action.version,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
        )
    assert wrong_token.value.code == "AGENT_PENDING_ACTION_TOKEN_MISMATCH"
    with pytest.raises(PersistenceError) as wrong_channel:
        await service.confirm(
            action_id=proposal.action.action_id,
            token=proposal.confirmation_token,
            expected_version=proposal.action.version,
            channel=AgentChannel.TELEGRAM,
            principal="local-console",
        )
    assert wrong_channel.value.code == "AGENT_PENDING_ACTION_IDENTITY_MISMATCH"
    with pytest.raises(PersistenceError) as wrong_principal:
        await service.confirm(
            action_id=proposal.action.action_id,
            token=proposal.confirmation_token,
            expected_version=proposal.action.version,
            channel=AgentChannel.CONSOLE,
            principal="other-console",
        )
    assert wrong_principal.value.code == "AGENT_PENDING_ACTION_IDENTITY_MISMATCH"
    with pytest.raises(PersistenceError) as wrong_version:
        await service.confirm(
            action_id=proposal.action.action_id,
            token=proposal.confirmation_token,
            expected_version=999,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
        )
    assert wrong_version.value.code == "AGENT_PENDING_ACTION_VERSION_CONFLICT"
    result = await service.confirm(
        action_id=proposal.action.action_id,
        token=proposal.confirmation_token,
        expected_version=proposal.action.version,
        channel=AgentChannel.CONSOLE,
        principal="local-console",
    )
    assert result.action.status is AgentPendingActionStatus.SUCCEEDED
    assert repo.values[proposal.action.action_id].result_receipt_json is not None
    with pytest.raises(PersistenceError) as replay:
        await service.confirm(
            action_id=proposal.action.action_id,
            token=proposal.confirmation_token,
            expected_version=result.action.version,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
        )
    assert replay.value.code == "AGENT_PENDING_ACTION_ALREADY_USED"


@pytest.mark.asyncio
async def test_expiry_and_failure_states_never_retry() -> None:
    failure_gateway = OperationGateway(
        failure=TradingPartnerError("typed", code="WATCHLIST_FAILED")
    )
    service, _repo, clock = _service(gateway=failure_gateway)
    proposal = service.propose(
        conversation_id="agent_conversation_1",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    result = await service.confirm(
        action_id=proposal.action.action_id,
        token=proposal.confirmation_token,
        expected_version=proposal.action.version,
        channel=AgentChannel.CONSOLE,
        principal="local-console",
    )
    assert result.action.status is AgentPendingActionStatus.FAILED
    assert len(failure_gateway.invocations) == 1

    expiring, _repo2, clock2 = _service()
    proposal2 = expiring.propose(
        conversation_id="agent_conversation_2",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    clock2.value += timedelta(minutes=11)
    with pytest.raises(PersistenceError) as expired:
        await expiring.confirm(
            action_id=proposal2.action.action_id,
            token=proposal2.confirmation_token,
            expected_version=proposal2.action.version,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
        )
    assert expired.value.code == "AGENT_PENDING_ACTION_EXPIRED"


@pytest.mark.asyncio
async def test_unexpected_invocation_maps_to_unknown_without_retry() -> None:
    gateway = OperationGateway(failure=RuntimeError("provider details are hidden"))
    service, _repo, _clock = _service(gateway=gateway)
    proposal = service.propose(
        conversation_id="agent_conversation_3",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    result = await service.confirm(
        action_id=proposal.action.action_id,
        token=proposal.confirmation_token,
        expected_version=proposal.action.version,
        channel=AgentChannel.CONSOLE,
        principal="local-console",
    )
    assert result.action.status is AgentPendingActionStatus.UNKNOWN
    assert gateway.invocations == [("watchlist_manage", "add", {**_args(), "confirmed_by": "user"})]


@pytest.mark.asyncio
async def test_compact_gateway_revalidates_closed_operation_and_executes_once() -> None:
    invocations: list[dict[str, Any]] = []

    async def watchlist_add(
        instrument_id: str,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        invocations.append(
            {
                "instrument_id": instrument_id,
                "confirmed_by": confirmed_by,
                "idempotency_key": idempotency_key,
            }
        )
        return {"request_id": "req_compact", "membership_id": "wl_1"}

    registry = CompactCapabilityRegistry()
    _register_dispatch_tool(
        registry,
        name="watchlist_manage",
        description="one closed write",
        variants=(
            _spec(
                "add",
                watchlist_add,
                ("instrument_id", "confirmed_by", "idempotency_key"),
            ),
        ),
        policy=APPEND,
    )
    operation_gateway = CompactAgentActionOperationGateway(registry)
    service, repo, clock = _service()
    service = AgentPendingActionService(
        repository=repo,
        operation_gateway=operation_gateway,
        clock=clock,
        id_generator=IDs(),
    )
    proposal = service.propose(
        conversation_id="agent_conversation_compact",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="watchlist_manage",
        operation="add",
        arguments=_args(),
        presented_summary="Add AAPL",
    )
    result = await service.confirm(
        action_id=proposal.action.action_id,
        token=proposal.confirmation_token,
        expected_version=proposal.action.version,
        channel=AgentChannel.CONSOLE,
        principal="local-console",
    )
    assert result.action.status is AgentPendingActionStatus.SUCCEEDED
    assert invocations == [{**_args(), "confirmed_by": "user"}]
    with pytest.raises(DataContractError) as invalid:
        service.propose(
            conversation_id="agent_conversation_compact",
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            capability="watchlist_manage",
            operation="add",
            arguments={"instrument_id": "equity:US:AAPL"},
            presented_summary="invalid",
        )
    assert invalid.value.code == "AGENT_ACTION_SCHEMA_INVALID"
