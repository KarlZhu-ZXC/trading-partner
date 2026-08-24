"""Export canonical Pydantic schemas consumed by Console code generation."""

from __future__ import annotations

import json

from application.dto.agent_answer import AgentAnswerEnvelope
from application.dto.attention import AttentionDigestDTO, AttentionHealthSummaryDTO
from application.dto.operational_job import OperationalJobRunDTO
from interfaces.console.agent_api import (
    ArchiveConversationRequest,
    CreateConversationRequest,
    EphemeralContextRequest,
    PendingActionDecisionRequest,
    PendingActionReissueRequest,
    ResetAgentPreferencesRequest,
    SendMessageRequest,
    TelegramHandoffRequest,
    UpdateAgentPreferencesRequest,
)

MODELS = {
    "AgentAnswerEnvelopeContract": AgentAnswerEnvelope,
    "AgentArchiveConversationRequest": ArchiveConversationRequest,
    "AgentCreateConversationRequest": CreateConversationRequest,
    "AgentEphemeralContextContract": EphemeralContextRequest,
    "AgentPendingActionDecisionRequest": PendingActionDecisionRequest,
    "AgentPendingActionReissueRequest": PendingActionReissueRequest,
    "AgentResetPreferencesRequest": ResetAgentPreferencesRequest,
    "AgentSendMessageRequest": SendMessageRequest,
    "AgentTelegramHandoffRequest": TelegramHandoffRequest,
    "AgentUpdatePreferencesRequest": UpdateAgentPreferencesRequest,
    "AttentionDigestContract": AttentionDigestDTO,
    "AttentionHealthSummaryContract": AttentionHealthSummaryDTO,
    "OperationalJobRunContract": OperationalJobRunDTO,
}


def main() -> None:
    payload = {
        name: model.model_json_schema(mode="validation")
        for name, model in sorted(MODELS.items())
    }
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
