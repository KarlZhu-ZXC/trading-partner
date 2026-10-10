"""Recover an exact research-work proposal before rechecking newer mutable state."""

from application.dto.research import (
    OpenQuestionCandidatePayload,
    candidate_payload_to_json,
    parse_candidate_payload,
    payloads_equal_json,
)
from application.dto.research_links import EvidenceImpactCandidatePayload
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services._research_support import normalize_idempotency_key
from domain.common.enums import ConfirmationMode
from domain.common.errors import DuplicateIdempotencyKey
from domain.research.models import CandidateThesisRevision


def replay_research_work(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str | None,
    payload: OpenQuestionCandidatePayload | EvidenceImpactCandidatePayload,
    proposed_by: str,
    confirmation_mode: ConfirmationMode,
    idempotency_key: str,
) -> CandidateThesisRevision | None:
    prior = uow.candidates.get_by_idempotency_key(normalize_idempotency_key(idempotency_key))
    if prior is None:
        return None
    previous_payload = parse_candidate_payload(prior.payload_json)
    if (
        isinstance(payload, OpenQuestionCandidatePayload)
        and isinstance(
            previous_payload,
            OpenQuestionCandidatePayload,
        )
        and payload.expected_version is None
    ):
        payload = payload.model_copy(update={"expected_version": previous_payload.expected_version})
    if (
        prior.subject_id != subject_id
        or prior.kind.value != payload.kind
        or prior.proposed_by != proposed_by.strip()
        or prior.confirmation_mode != confirmation_mode
        or not payloads_equal_json(
            candidate_payload_to_json(previous_payload),
            candidate_payload_to_json(payload),
        )
    ):
        raise DuplicateIdempotencyKey("Proposal key belongs to a different request or scope")
    return prior
