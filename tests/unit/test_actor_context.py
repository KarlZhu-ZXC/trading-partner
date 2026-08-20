from __future__ import annotations

import pytest

from application.services._research_support import require_confirm_reviewer
from domain.common.actor import (
    ActorAssurance,
    ActorContext,
    ActorSubmissionChannel,
    ActorType,
)
from domain.common.errors import ConfirmerMismatch, DataContractError


def test_caller_asserted_stdio_actor_is_explicitly_untrusted() -> None:
    context = ActorContext.caller_asserted("user", request_id="req_1")

    assert context.actor_type is ActorType.USER
    assert context.assurance is ActorAssurance.CALLER_ASSERTED
    assert require_confirm_reviewer(
        "user", action="test", actor_context=context
    ) is context


def test_authenticated_actor_mismatch_is_rejected() -> None:
    context = ActorContext(
        actor_type=ActorType.USER,
        principal_id="principal_1",
        assurance=ActorAssurance.AUTHENTICATED,
        request_id="req_1",
    )

    with pytest.raises(ConfirmerMismatch):
        require_confirm_reviewer(
            "external_agent",
            action="test",
            actor_context=context,
        )


def test_explicit_user_chat_authorization_is_relayed_without_impersonating_codex() -> None:
    context = ActorContext.codex_chat_authorized(
        request_id="req_chat_1",
        authorization_note="我确认采用 63 个交易日事件周期修正版",
    )

    assert context.actor_type is ActorType.USER
    assert context.principal_id == "user"
    assert context.assurance is ActorAssurance.CALLER_ASSERTED
    assert context.submitted_via is ActorSubmissionChannel.CODEX_CHAT
    assert context.authorization_note == "我确认采用 63 个交易日事件周期修正版"
    assert require_confirm_reviewer(
        "user",
        action="confirm_candidate",
        actor_context=context,
    ) is context


def test_mcp_chat_authorization_is_a_current_chat_channel() -> None:
    context = ActorContext.current_chat_authorized(
        request_id="req_chat_mcp_1",
        authorization_note="Confirm the TSLA trim in this chat",
        submitted_via=ActorSubmissionChannel.MCP_CHAT,
    )

    assert context.submitted_via is ActorSubmissionChannel.MCP_CHAT
    assert context.submitted_via.is_current_chat
    assert context.actor_type is ActorType.USER
    assert require_confirm_reviewer(
        "user",
        action="confirm_candidate",
        actor_context=context,
    ) is context


def test_codex_chat_channel_cannot_claim_an_external_agent() -> None:
    with pytest.raises(DataContractError, match="actor_type=user"):
        ActorContext(
            actor_type=ActorType.EXTERNAL_AGENT,
            principal_id="external_agent",
            assurance=ActorAssurance.CALLER_ASSERTED,
            request_id="req_chat_2",
            submitted_via=ActorSubmissionChannel.CODEX_CHAT,
            authorization_note="approve",
        )
