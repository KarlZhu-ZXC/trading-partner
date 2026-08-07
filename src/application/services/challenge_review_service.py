"""Deterministic middle-mode Challenge Review service."""

from __future__ import annotations

from application.dto.challenge import (
    ChallengeReviewDTO,
    ChallengeReviewResolveInput,
    ChallengeReviewStartDTO,
    ChallengeReviewStartInput,
)
from application.dto.research_context import ResearchContextBuildInput, ResearchContextDTO
from application.dto.tool_envelope import ToolEnvelope
from application.ports.challenge_review_repository import ChallengeReviewRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    envelope_failure,
    envelope_success,
    require_confirm_reviewer,
)
from application.services.idempotency import canonical_payload_sha256
from application.services.research_context_builder import ResearchContextBuilder
from domain.challenge.enums import (
    ChallengeDimension,
    ChallengeFindingSeverity,
    ChallengeReviewStatus,
    ChallengeTrigger,
)
from domain.challenge.models import ChallengeFinding, ChallengeQuestion, ChallengeReview
from domain.common.actor import ActorContext
from domain.common.enums import ConfirmationMode, EvidenceStance
from domain.common.errors import IdempotencyConflict, InputValidationError
from domain.common.ids import EntityIdPrefix

_QUESTIONS: tuple[tuple[ChallengeDimension, str], ...] = (
    (ChallengeDimension.FALSIFIABILITY, "What observable fact would make this action wrong?"),
    (ChallengeDimension.EVIDENCE_QUALITY, "Which new primary evidence justifies the change?"),
    (ChallengeDimension.HIDDEN_ASSUMPTIONS, "Which untested assumptions carry the conclusion?"),
    (ChallengeDimension.CONTRARY_EVIDENCE, "What is the strongest contrary evidence?"),
    (ChallengeDimension.VALUATION_EXPECTATIONS, "What expectations are embedded in valuation?"),
    (ChallengeDimension.OPPORTUNITY_COST, "What alternative use of capital is better?"),
    (ChallengeDimension.PORTFOLIO_BIAS, "Is position size biasing the judgment?"),
    (ChallengeDimension.TIME_HORIZON_CONSISTENCY, "Does the horizon match the thesis and action?"),
    (
        ChallengeDimension.MOVING_THE_GOALPOSTS_RISK,
        "Has any invalidation condition been relaxed after adverse evidence?",
    ),
    (ChallengeDimension.MISSING_INFORMATION, "Which missing fact could reverse the decision?"),
)


class ChallengeReviewService:
    def __init__(
        self,
        repository: ChallengeReviewRepository,
        context_builder: ResearchContextBuilder,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._repository = repository
        self._context = context_builder
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    def start(self, request: ChallengeReviewStartInput) -> ToolEnvelope[ChallengeReviewStartDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            if request.trigger is ChallengeTrigger.DISCUSSION:
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ChallengeReviewStartDTO(
                        mode=ConfirmationMode.NORMAL,
                        persisted=False,
                        review=None,
                    ),
                )
            assert request.idempotency_key is not None
            payload_sha256 = canonical_payload_sha256(
                request.model_dump(mode="json", exclude={"idempotency_key"})
            )
            existing = self._repository.get_by_start_idempotency_key(request.idempotency_key)
            if existing is not None:
                review, existing_sha256 = existing
                if existing_sha256 != payload_sha256:
                    raise IdempotencyConflict("Challenge Review start idempotency key was reused")
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ChallengeReviewStartDTO(
                        mode=ConfirmationMode.STRICT_REVIEW,
                        persisted=True,
                        review=ChallengeReviewDTO.from_domain(review),
                    ),
                )
            context_envelope = self._context.build(
                ResearchContextBuildInput(subject_id=request.subject_id, token_budget=4_000)
            )
            if not context_envelope.ok or context_envelope.data is None:
                raise InputValidationError("Durable research context is unavailable")
            context = context_envelope.data
            now = self._clock.now()
            review_id = self._ids.new(EntityIdPrefix.RUN)
            questions = tuple(
                ChallengeQuestion(
                    question_id=self._ids.new(EntityIdPrefix.REV),
                    review_id=review_id,
                    dimension=dimension,
                    prompt=prompt,
                    ordinal=ordinal,
                )
                for ordinal, (dimension, prompt) in enumerate(_QUESTIONS, 1)
            )
            findings = self._findings(review_id, request.trigger, context)
            review = self._repository.append(
                ChallengeReview(
                    review_id=review_id,
                    subject_id=request.subject_id,
                    mode=ConfirmationMode.STRICT_REVIEW,
                    trigger=request.trigger,
                    proposed_action=request.proposed_action.strip(),
                    related_candidate_id=request.related_candidate_id,
                    related_evidence_ids=request.related_evidence_ids,
                    position_context_snapshot_id=request.position_context_snapshot_id,
                    context_as_of=context_envelope.as_of,
                    status=ChallengeReviewStatus.OPEN,
                    questions=questions,
                    findings=findings,
                    created_at=now,
                    execution_effect=False,
                ),
                idempotency_key=request.idempotency_key,
                payload_sha256=payload_sha256,
            )
            return envelope_success(
                request_id=request_id,
                clock=self._clock,
                data=ChallengeReviewStartDTO(
                    mode=ConfirmationMode.STRICT_REVIEW,
                    persisted=True,
                    review=ChallengeReviewDTO.from_domain(review),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def get(self, review_id: str) -> ToolEnvelope[ChallengeReviewDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            return envelope_success(
                request_id=request_id,
                clock=self._clock,
                data=ChallengeReviewDTO.from_domain(self._repository.get(review_id)),
            )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def resolve(
        self,
        request: ChallengeReviewResolveInput,
        *,
        actor_context: ActorContext | None = None,
    ) -> ToolEnvelope[ChallengeReviewDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(
                request.confirmed_by,
                action="challenge_review_resolve",
                actor_context=actor_context
                or ActorContext.caller_asserted(
                    request.confirmed_by,
                    request_id=request_id,
                ),
            )
            payload_sha256 = canonical_payload_sha256(
                request.model_dump(mode="json", exclude={"idempotency_key"})
            )
            existing = self._repository.get_by_resolution_idempotency_key(request.idempotency_key)
            if existing is not None:
                review, existing_sha256 = existing
                if existing_sha256 != payload_sha256:
                    raise IdempotencyConflict(
                        "Challenge Review resolution idempotency key was reused"
                    )
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ChallengeReviewDTO.from_domain(review),
                )
            review = self._repository.resolve(
                request.review_id,
                resolution=request.resolution,
                rationale=request.rationale.strip(),
                confirmed_by=request.confirmed_by,
                resolved_at=self._clock.now(),
                resolution_id=self._ids.new(EntityIdPrefix.REV),
                idempotency_key=request.idempotency_key,
                payload_sha256=payload_sha256,
            )
            return envelope_success(
                request_id=request_id,
                clock=self._clock,
                data=ChallengeReviewDTO.from_domain(review),
            )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def _findings(
        self, review_id: str, trigger: ChallengeTrigger, context: ResearchContextDTO
    ) -> tuple[ChallengeFinding, ...]:
        items: list[ChallengeFinding] = []

        def add(
            dimension: ChallengeDimension,
            severity: ChallengeFindingSeverity,
            summary: str,
            evidence_ids: tuple[str, ...] = (),
        ) -> None:
            items.append(
                ChallengeFinding(
                    finding_id=self._ids.new(EntityIdPrefix.REV),
                    review_id=review_id,
                    dimension=dimension,
                    severity=severity,
                    summary=summary,
                    evidence_ids=evidence_ids,
                )
            )

        state = context.research_state
        evidence = context.evidence
        positions = context.positions
        if not state.invalidations:
            add(
                ChallengeDimension.FALSIFIABILITY,
                ChallengeFindingSeverity.CRITICAL,
                "No active invalidation condition is present in durable context.",
            )
        contrary = tuple(
            item.evidence_id for item in evidence if EvidenceStance.CONTRADICTS in item.stances
        )
        if not contrary:
            add(
                ChallengeDimension.CONTRARY_EVIDENCE,
                ChallengeFindingSeverity.WARNING,
                "No contrary evidence is linked to the current subject.",
            )
        if not positions:
            add(
                ChallengeDimension.PORTFOLIO_BIAS,
                ChallengeFindingSeverity.INFO,
                "No durable portfolio position context is available.",
            )
        if trigger is ChallengeTrigger.CONFIDENCE_WITHOUT_EVIDENCE:
            add(
                ChallengeDimension.EVIDENCE_QUALITY,
                ChallengeFindingSeverity.CRITICAL,
                "Confidence is proposed to increase without identified new evidence.",
            )
        if trigger is ChallengeTrigger.POSITION_THESIS_CONFLICT:
            add(
                ChallengeDimension.PORTFOLIO_BIAS,
                ChallengeFindingSeverity.CRITICAL,
                "The caller identified a position/thesis inconsistency.",
            )
        if trigger is ChallengeTrigger.INVALIDATION_RELAXATION:
            add(
                ChallengeDimension.MOVING_THE_GOALPOSTS_RISK,
                ChallengeFindingSeverity.CRITICAL,
                "An invalidation condition is proposed to be relaxed.",
            )
        return tuple(items)
