"""Thesis revision propose/confirm lifecycle and research state updates."""

from __future__ import annotations

from application.dto.research import (
    AssumptionCandidatePayload,
    CandidateRevisionDTO,
    ConfirmedStateUpdateDTO,
    InvalidationCandidatePayload,
    OpenQuestionCandidatePayload,
    SubjectUpdateCandidatePayload,
    ThesisHistoryDTO,
    ThesisRevisionCandidatePayload,
    TradePlanCandidatePayload,
    WatchlistCandidatePayload,
    kind_from_payload,
    parse_candidate_payload,
)
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    CODEX_ACTOR,
    CONFIRM_REVIEWERS,
    UowFactory,
    actor_audit_fields,
    build_research_state,
    candidate_to_dto,
    envelope_failure,
    envelope_success,
    propose_candidate,
    require_confirm_reviewer,
)
from application.services.research_state_invariants import (
    ensure_active_trade_plan_dependencies,
    ensure_subject_can_host_live_thesis,
    ensure_subject_can_leave_tracking,
    ensure_thesis_status_transition,
)
from application.services.subject_metadata_policy import validate_subject_metadata
from domain.common.actor import ActorContext
from domain.common.enums import (
    AssumptionStatus,
    CandidateKind,
    CandidateStatus,
    ConfirmationMode,
    InvalidationSeverity,
    InvalidationStatus,
    ResearchSubjectStatus,
    ThesisRole,
    ThesisStatus,
    WatchlistItemStatus,
)
from domain.common.errors import (
    CandidateAlreadyResolved,
    DataContractError,
    InputValidationError,
    InvalidationConditionNarrowingForbidden,
    StrictReviewRequired,
    UnauthorizedReviewer,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    Assumption,
    InvalidationCondition,
    OpenQuestion,
    ResearchSubject,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlan, TradePlanCondition


class ThesisRevisionService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._redactor = secret_redactor

    # ------------------------------------------------------------------ propose

    def propose_revision(
        self,
        *,
        subject_id: str,
        thesis_id: str | None,
        payload: ThesisRevisionCandidatePayload,
        confirmation_mode: ConfirmationMode = ConfirmationMode.STRICT_REVIEW,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            if payload.kind != "thesis_revision":
                raise InputValidationError("payload.kind must be thesis_revision")
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                if thesis_id is not None:
                    thesis = uow.theses.get(thesis_id)
                    if thesis.subject_id != subject_id:
                        raise InputValidationError(
                            "thesis_id does not belong to subject_id",
                            details={"thesis_id": thesis_id, "subject_id": subject_id},
                        )
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.THESIS_REVISION,
                    subject_id=subject_id,
                    thesis_id=thesis_id,
                    target_revision_no=payload.replaces_revision_no,
                    payload_model=payload,
                    confirmation_mode=confirmation_mode,
                    proposed_by=proposed_by,
                    proposed_by_rationale=proposed_by_rationale,
                    idempotency_key=idempotency_key,
                )
                if not is_dup:
                    uow.audit.append(
                        "phase1b.candidate.proposed",
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": candidate.kind.value,
                            "subject_id": subject_id,
                            "proposed_by": proposed_by,
                        },
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(candidate),
                    warnings=(warn,) if warn is not None else (),
                    degraded=warn is not None,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def propose_assumption(
        self,
        *,
        subject_id: str,
        thesis_id: str,
        revision_no: int,
        payload: AssumptionCandidatePayload,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            body = payload
            if body.thesis_id != thesis_id or body.revision_no != revision_no:
                body = AssumptionCandidatePayload(
                    kind="assumption",
                    thesis_id=thesis_id,
                    revision_no=revision_no,
                    statement=payload.statement,
                    basis=payload.basis,
                    falsifiability=payload.falsifiability,
                )
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                thesis = uow.theses.get(thesis_id)
                if thesis.subject_id != subject_id:
                    raise InputValidationError("thesis_id does not belong to subject_id")
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.ASSUMPTION,
                    subject_id=subject_id,
                    thesis_id=thesis_id,
                    target_revision_no=revision_no,
                    payload_model=body,
                    confirmation_mode=confirmation_mode,
                    proposed_by=proposed_by,
                    proposed_by_rationale=proposed_by_rationale,
                    idempotency_key=idempotency_key,
                )
                if not is_dup:
                    uow.audit.append(
                        "phase1b.candidate.proposed",
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": candidate.kind.value,
                            "subject_id": subject_id,
                        },
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(candidate),
                    warnings=(warn,) if warn is not None else (),
                    degraded=warn is not None,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def propose_invalidation(
        self,
        *,
        subject_id: str,
        thesis_id: str,
        revision_no: int,
        payload: InvalidationCandidatePayload,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            body = payload
            if body.thesis_id != thesis_id or body.revision_no != revision_no:
                body = InvalidationCandidatePayload(
                    kind="invalidation_condition",
                    thesis_id=thesis_id,
                    revision_no=revision_no,
                    description=payload.description,
                    observable=payload.observable,
                    severity=payload.severity,
                    relaxes_invalidation_id=payload.relaxes_invalidation_id,
                )
            # Relaxation of HARD requires STRICT_REVIEW.
            if body.relaxes_invalidation_id is not None:
                if confirmation_mode != ConfirmationMode.STRICT_REVIEW:
                    raise StrictReviewRequired(
                        "relaxing an invalidation condition requires STRICT_REVIEW",
                        details={"relaxes_invalidation_id": body.relaxes_invalidation_id},
                    )
                if body.severity == InvalidationSeverity.SOFT:
                    # narrowing/relaxation path is allowed only under STRICT_REVIEW
                    pass
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                thesis = uow.theses.get(thesis_id)
                if thesis.subject_id != subject_id:
                    raise InputValidationError("thesis_id does not belong to subject_id")
                if body.relaxes_invalidation_id is not None:
                    existing = uow.invalidations.get(body.relaxes_invalidation_id)
                    is_hard_to_soft = (
                        existing.severity == InvalidationSeverity.HARD
                        and body.severity == InvalidationSeverity.SOFT
                    )
                    if is_hard_to_soft and confirmation_mode != ConfirmationMode.STRICT_REVIEW:
                        raise InvalidationConditionNarrowingForbidden(
                            "HARD→SOFT relaxation requires STRICT_REVIEW candidate",
                            details={
                                "invalidation_id": body.relaxes_invalidation_id,
                            },
                        )
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.INVALIDATION_CONDITION,
                    subject_id=subject_id,
                    thesis_id=thesis_id,
                    target_revision_no=revision_no,
                    payload_model=body,
                    confirmation_mode=confirmation_mode,
                    proposed_by=proposed_by,
                    proposed_by_rationale=proposed_by_rationale,
                    idempotency_key=idempotency_key,
                )
                if not is_dup:
                    uow.audit.append(
                        "phase1b.candidate.proposed",
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": candidate.kind.value,
                            "subject_id": subject_id,
                        },
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(candidate),
                    warnings=(warn,) if warn is not None else (),
                    degraded=warn is not None,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def propose_state_update(
        self,
        *,
        subject_id: str | None,
        thesis_id: str | None = None,
        payload: (
            ThesisRevisionCandidatePayload
            | AssumptionCandidatePayload
            | InvalidationCandidatePayload
            | OpenQuestionCandidatePayload
            | WatchlistCandidatePayload
            | SubjectUpdateCandidatePayload
            | TradePlanCandidatePayload
        ),
        confirmation_mode: ConfirmationMode = ConfirmationMode.STRICT_REVIEW,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        """research_state_update semantics: always PROPOSED, never formal write."""
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            kind = kind_from_payload(payload)
            if kind == CandidateKind.SUBJECT_STATUS_CHANGE:
                assert isinstance(payload, SubjectUpdateCandidatePayload)
                if payload.action == "create":
                    raise InputValidationError(
                        "subject create must use research_subject_create, not research_state_update"
                    )
                if (
                    payload.action == "archive"
                    and confirmation_mode != ConfirmationMode.STRICT_REVIEW
                ):
                    raise StrictReviewRequired(
                        "subject archive candidate requires STRICT_REVIEW",
                    )
            if kind != CandidateKind.WATCHLIST_ITEM and subject_id is None:
                raise InputValidationError("subject_id is required for non-watchlist candidates")

            with self._uow_factory() as uow:
                subject = uow.subjects.get(subject_id) if subject_id is not None else None
                if (
                    isinstance(payload, SubjectUpdateCandidatePayload)
                    and payload.action == "update"
                ):
                    if subject is None:
                        raise InputValidationError("subject update candidate requires subject_id")
                    validate_subject_metadata(
                        title=payload.title if payload.title is not None else subject.title,
                        summary=payload.summary if payload.summary is not None else subject.summary,
                    )
                target_revision_no: int | None = None
                resolved_thesis = thesis_id
                if isinstance(payload, AssumptionCandidatePayload):
                    resolved_thesis = payload.thesis_id
                    target_revision_no = payload.revision_no
                elif isinstance(payload, InvalidationCandidatePayload):
                    resolved_thesis = payload.thesis_id
                    target_revision_no = payload.revision_no
                    if (
                        payload.relaxes_invalidation_id is not None
                        and confirmation_mode != ConfirmationMode.STRICT_REVIEW
                    ):
                        raise StrictReviewRequired(
                            "relaxing invalidation requires STRICT_REVIEW",
                        )
                elif isinstance(payload, ThesisRevisionCandidatePayload):
                    target_revision_no = payload.replaces_revision_no
                elif isinstance(payload, TradePlanCandidatePayload):
                    if subject_id is None:
                        raise InputValidationError("trade_plan candidate requires subject_id")
                    thesis = uow.theses.get(payload.thesis_id)
                    if thesis.subject_id != subject_id:
                        raise InputValidationError(
                            "Trade Plan thesis_id does not belong to subject_id"
                        )
                    subject = uow.subjects.get(subject_id)
                    if (
                        subject.primary_instrument_id is not None
                        and subject.primary_instrument_id != payload.instrument_id
                    ):
                        raise InputValidationError(
                            "Trade Plan instrument must match the Subject primary Instrument"
                        )
                    current = uow.trade_plans.get_current_by_subject(subject_id)
                    if payload.plan_id is None:
                        if current is not None:
                            raise InputValidationError(
                                "Subject already has a Trade Plan; append a version instead"
                            )
                    elif (
                        current is None
                        or current.plan_id != payload.plan_id
                        or current.version != payload.expected_version
                    ):
                        raise InputValidationError(
                            "Trade Plan expected_version does not match current version"
                        )
                    resolved_thesis = payload.thesis_id
                    target_revision_no = payload.expected_version

                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=kind,
                    subject_id=subject_id,
                    thesis_id=resolved_thesis,
                    target_revision_no=target_revision_no,
                    payload_model=payload,
                    confirmation_mode=confirmation_mode,
                    proposed_by=proposed_by,
                    proposed_by_rationale=proposed_by_rationale,
                    idempotency_key=idempotency_key,
                    status=CandidateStatus.PROPOSED,
                )
                if not is_dup:
                    uow.audit.append(
                        "phase1b.candidate.proposed",
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": candidate.kind.value,
                            "subject_id": subject_id,
                            "proposed_by": proposed_by,
                        },
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(candidate),
                    warnings=(warn,) if warn is not None else (),
                    degraded=warn is not None,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    # --------------------------------------------------------------- lifecycle

    def confirm_candidate(
        self,
        candidate_id: str,
        *,
        reviewed_by: str,
        review_note: str | None = None,
        actor_context: ActorContext | None = None,
    ) -> ToolEnvelope[ConfirmedStateUpdateDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            context = require_confirm_reviewer(
                reviewed_by,
                action="confirm_candidate",
                actor_context=actor_context,
            )
            with self._uow_factory() as uow:
                candidate = uow.candidates.get(candidate_id)
                if candidate.status != CandidateStatus.PROPOSED:
                    raise CandidateAlreadyResolved(
                        f"Candidate already resolved with status={candidate.status.value}",
                        details={
                            "candidate_id": candidate_id,
                            "status": candidate.status.value,
                        },
                    )
                now = self._clock.now()
                if candidate.expires_at < now:
                    uow.candidates.update_status(
                        candidate_id,
                        new_status=CandidateStatus.EXPIRED,
                        reviewed_at=None,
                        reviewed_by=None,
                        review_note=None,
                        rejection_reason=None,
                    )
                    uow.commit()
                    raise CandidateAlreadyResolved(
                        "Candidate expired before confirm",
                        details={"candidate_id": candidate_id},
                    )

                affected_type, affected_id = self._apply_confirmed_payload(
                    uow,
                    candidate,
                    reviewed_by=reviewed_by.strip(),
                    now=now,
                )

                uow.candidates.update_status(
                    candidate_id,
                    new_status=CandidateStatus.CONFIRMED,
                    reviewed_at=now,
                    reviewed_by=reviewed_by.strip(),
                    review_note=review_note,
                    rejection_reason=None,
                )
                confirmed = uow.candidates.get(candidate_id)
                audit_payload = {
                    "candidate_id": candidate_id,
                    "kind": candidate.kind.value,
                    "reviewed_by": reviewed_by.strip(),
                    "affected_entity_type": affected_type,
                    "affected_entity_id": affected_id,
                }
                audit_payload.update(actor_audit_fields(context))
                uow.audit.append(
                    "phase1b.candidate.confirmed",
                    audit_payload,
                    request_id=request_id,
                )

                research_state = None
                if candidate.subject_id is not None:
                    research_state = build_research_state(uow, candidate.subject_id)

                uow.commit()
                data = ConfirmedStateUpdateDTO(
                    candidate=candidate_to_dto(confirmed),
                    research_state=research_state,
                    affected_entity_type=affected_type,
                    affected_entity_id=affected_id,
                )
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def reject_candidate(
        self,
        candidate_id: str,
        *,
        reviewed_by: str,
        rejection_reason: str,
        actor_context: ActorContext | None = None,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            context = require_confirm_reviewer(
                reviewed_by,
                action="reject_candidate",
                actor_context=actor_context,
            )
            reason = rejection_reason.strip()
            if not reason:
                raise InputValidationError("rejection_reason must be non-empty")
            with self._uow_factory() as uow:
                candidate = uow.candidates.get(candidate_id)
                if candidate.status != CandidateStatus.PROPOSED:
                    raise CandidateAlreadyResolved(
                        f"Candidate already resolved with status={candidate.status.value}",
                        details={"candidate_id": candidate_id},
                    )
                now = self._clock.now()
                uow.candidates.update_status(
                    candidate_id,
                    new_status=CandidateStatus.REJECTED,
                    reviewed_at=now,
                    reviewed_by=reviewed_by.strip(),
                    review_note=None,
                    rejection_reason=reason,
                )
                rejected = uow.candidates.get(candidate_id)
                audit_payload = {
                    "candidate_id": candidate_id,
                    "reviewed_by": reviewed_by.strip(),
                    "rejection_reason": reason,
                }
                audit_payload.update(actor_audit_fields(context))
                uow.audit.append(
                    "phase1b.candidate.rejected",
                    audit_payload,
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(rejected),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def withdraw_candidate(
        self,
        candidate_id: str,
        *,
        reviewed_by: str,
        review_note: str,
        actor_context: ActorContext | None = None,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            note = review_note.strip()
            if not note:
                raise InputValidationError("review_note must be non-empty for withdraw")
            actor = reviewed_by.strip()
            with self._uow_factory() as uow:
                candidate = uow.candidates.get(candidate_id)
                if candidate.status != CandidateStatus.PROPOSED:
                    raise CandidateAlreadyResolved(
                        f"Candidate already resolved with status={candidate.status.value}",
                        details={"candidate_id": candidate_id},
                    )
                # Withdraw: original proposer, or user/external_agent.
                # codex may only withdraw its own NORMAL candidates.
                if actor == CODEX_ACTOR:
                    if candidate.proposed_by != CODEX_ACTOR:
                        raise UnauthorizedReviewer(
                            "codex may only withdraw its own candidates",
                            details={"proposed_by": candidate.proposed_by},
                        )
                    if candidate.confirmation_mode != ConfirmationMode.NORMAL:
                        raise UnauthorizedReviewer(
                            "codex may only withdraw NORMAL confirmation_mode candidates",
                            details={
                                "confirmation_mode": candidate.confirmation_mode.value,
                            },
                        )
                elif actor not in CONFIRM_REVIEWERS and actor != candidate.proposed_by:
                    raise UnauthorizedReviewer(
                        "withdraw requires original proposer or user/external_agent",
                        details={"reviewed_by": actor, "proposed_by": candidate.proposed_by},
                    )
                elif actor in CONFIRM_REVIEWERS:
                    require_confirm_reviewer(
                        actor,
                        action="withdraw_candidate",
                        actor_context=actor_context,
                    )
                elif actor != candidate.proposed_by:
                    raise UnauthorizedReviewer(
                        "only original proposer may withdraw",
                        details={"reviewed_by": actor, "proposed_by": candidate.proposed_by},
                    )

                now = self._clock.now()
                uow.candidates.update_status(
                    candidate_id,
                    new_status=CandidateStatus.WITHDRAWN,
                    reviewed_at=now,
                    reviewed_by=actor,
                    review_note=note,
                    rejection_reason=None,
                )
                withdrawn = uow.candidates.get(candidate_id)
                audit_payload = {
                    "candidate_id": candidate_id,
                    "reviewed_by": actor,
                    "review_note": note,
                }
                if actor_context is not None:
                    audit_payload.update(actor_audit_fields(actor_context))
                uow.audit.append(
                    "phase1b.candidate.withdrawn",
                    audit_payload,
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(withdrawn),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def expire_due(
        self, *, now: object | None = None, limit: int = 200
    ) -> ToolEnvelope[tuple[str, ...]]:
        """Mark PROPOSED candidates past expires_at as EXPIRED (Phase 3 scheduler hook)."""
        from datetime import datetime

        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            when = now if isinstance(now, datetime) else self._clock.now()
            with self._uow_factory() as uow:
                expired_ids = uow.candidates.expire_due(now=when, limit=limit)
                if expired_ids:
                    uow.audit.append(
                        "phase1b.candidate.expired_batch",
                        {"candidate_ids": list(expired_ids), "count": len(expired_ids)},
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=expired_ids,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def get_revision_history(self, thesis_id: str) -> ToolEnvelope[ThesisHistoryDTO]:
        from application.dto.research import ThesisDTO, ThesisRevisionDTO

        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                thesis = uow.theses.get(thesis_id)
                revisions = uow.revisions.list_by_thesis(thesis_id)
                edges = tuple((r.revision_no, r.supersedes_revision_no) for r in revisions)
                data = ThesisHistoryDTO(
                    thesis=ThesisDTO.from_domain(thesis),
                    revisions=ThesisRevisionDTO.from_domain_list(revisions),
                    supersedes_edges=edges,
                )
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    # -------------------------------------------------------------- apply kinds

    def _apply_confirmed_payload(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        *,
        reviewed_by: str,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        payload = parse_candidate_payload(candidate.payload_json)
        kind = candidate.kind

        if kind is CandidateKind.THESIS_REVISION:
            assert isinstance(payload, ThesisRevisionCandidatePayload)
            return self._apply_thesis_revision(
                uow, candidate, payload, reviewed_by=reviewed_by, now=now
            )
        if kind is CandidateKind.ASSUMPTION:
            assert isinstance(payload, AssumptionCandidatePayload)
            return self._apply_assumption(uow, candidate, payload, reviewed_by=reviewed_by, now=now)
        if kind is CandidateKind.INVALIDATION_CONDITION:
            assert isinstance(payload, InvalidationCandidatePayload)
            return self._apply_invalidation(
                uow, candidate, payload, reviewed_by=reviewed_by, now=now
            )
        if kind is CandidateKind.OPEN_QUESTION:
            assert isinstance(payload, OpenQuestionCandidatePayload)
            return self._apply_open_question(
                uow, candidate, payload, reviewed_by=reviewed_by, now=now
            )
        if kind is CandidateKind.WATCHLIST_ITEM:
            assert isinstance(payload, WatchlistCandidatePayload)
            return self._apply_watchlist(uow, candidate, payload, now=now)
        if kind is CandidateKind.SUBJECT_STATUS_CHANGE:
            assert isinstance(payload, SubjectUpdateCandidatePayload)
            return self._apply_subject_update(uow, candidate, payload, now=now)
        if kind is CandidateKind.TRADE_PLAN:
            assert isinstance(payload, TradePlanCandidatePayload)
            return self._apply_trade_plan(uow, candidate, payload, reviewed_by=reviewed_by, now=now)
        raise DataContractError(f"unsupported candidate kind: {kind}")

    def _apply_trade_plan(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: TradePlanCandidatePayload,
        *,
        reviewed_by: str,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("trade_plan candidate requires subject_id")
        subject = uow.subjects.get(subject_id)
        thesis = uow.theses.get(payload.thesis_id)
        if thesis.subject_id != subject_id:
            raise DataContractError("Trade Plan thesis does not belong to Subject")
        status = TradePlanStatus(payload.status)
        ensure_active_trade_plan_dependencies(
            subject,
            thesis,
            attempted_child_status=status,
        )
        if (
            subject.primary_instrument_id is not None
            and subject.primary_instrument_id != payload.instrument_id
        ):
            raise DataContractError("Trade Plan instrument cannot diverge from Subject")

        current = uow.trade_plans.get_current_by_subject(subject_id)
        if payload.plan_id is None:
            if current is not None:
                raise DataContractError("Subject already has a Trade Plan")
            plan_id = self._id_generator.new(EntityIdPrefix.TRADE_PLAN)
            version = 1
        else:
            if current is None or current.plan_id != payload.plan_id:
                raise DataContractError("Trade Plan identity is not current for Subject")
            if payload.expected_version != current.version:
                from domain.common.errors import TradePlanVersionConflict

                raise TradePlanVersionConflict(
                    "Trade Plan expected_version does not match current version",
                    details={"current_version": current.version},
                )
            plan_id = current.plan_id
            version = current.version + 1

        conditions = tuple(
            TradePlanCondition(
                condition_code=item.condition_code,
                phase=TradePlanConditionPhase(item.phase),
                mode=TradePlanConditionMode(item.mode),
                description=item.description,
                severity=item.severity,
                fact_type=(TradePlanFactType(item.fact_type) if item.fact_type else None),
                metric_key=item.metric_key,
                comparator=(TradePlanComparator(item.comparator) if item.comparator else None),
                threshold=item.threshold,
                unit=item.unit,
                instrument_id=item.instrument_id,
                max_fact_age_seconds=item.max_fact_age_seconds,
                event_after=item.event_after,
            )
            for item in payload.conditions
        )
        plan = TradePlan(
            plan_id=plan_id,
            version=version,
            subject_id=subject_id,
            thesis_id=payload.thesis_id,
            instrument_id=payload.instrument_id,
            status=status,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            currency=payload.currency,
            reference_price=payload.reference_price,
            reference_price_at=payload.reference_price_at,
            target_position_percent=payload.target_position_percent,
            max_position_percent=payload.max_position_percent,
            risk_budget_percent=payload.risk_budget_percent,
            stop_price=payload.stop_price,
            conditions=conditions,
            notes=payload.notes,
            confirmed_by=reviewed_by,
            created_at=now,
            idempotency_key=candidate.idempotency_key,
        )
        uow.trade_plans.append(plan)
        self._touch_subject(uow, subject_id, now)
        return "trade_plan", plan.plan_id

    def _touch_subject(self, uow: ResearchUnitOfWork, subject_id: str, now: object) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        subject = uow.subjects.get(subject_id)
        updated = ResearchSubject(
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            title=subject.title,
            summary=subject.summary,
            status=subject.status,
            primary_instrument_id=subject.primary_instrument_id,
            topic_tags=subject.topic_tags,
            created_at=subject.created_at,
            updated_at=now,
            created_by=subject.created_by,
            archived_at=subject.archived_at,
            archived_reason=subject.archived_reason,
            linked_subject_ids=subject.linked_subject_ids,
            evidence_ids=subject.evidence_ids,
            report_ids=subject.report_ids,
            event_ids=subject.event_ids,
            decision_ids=subject.decision_ids,
            schema_version=subject.schema_version,
        )
        uow.subjects.update(updated)

    def _apply_thesis_revision(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: ThesisRevisionCandidatePayload,
        *,
        reviewed_by: str,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("thesis_revision candidate requires subject_id")

        subject = uow.subjects.get(subject_id)

        role = (
            ThesisRole(payload.thesis_role)
            if payload.thesis_role is not None
            else ThesisRole.PRIMARY
        )
        thesis_status = (
            ThesisStatus(payload.thesis_status)
            if payload.thesis_status is not None
            else ThesisStatus.ACTIVE
        )

        if candidate.thesis_id is None:
            if thesis_status in {ThesisStatus.INVALIDATED, ThesisStatus.ARCHIVED}:
                raise InputValidationError(
                    "new Thesis must start as DRAFT or a live status",
                    details={
                        "attempted_child_status": thesis_status.value,
                    },
                )
            ensure_subject_can_host_live_thesis(
                subject,
                attempted_child_status=thesis_status,
            )
            # New thesis: revision_no = 1
            if role == ThesisRole.PRIMARY and thesis_status == ThesisStatus.ACTIVE:
                existing_primaries = uow.subjects.list_active_primary_thesis_ids(subject_id)
                if existing_primaries:
                    raise DataContractError(
                        "active primary thesis already exists for subject",
                        details={
                            "subject_id": subject_id,
                            "existing": list(existing_primaries),
                        },
                    )
            thesis_id = self._id_generator.new(EntityIdPrefix.THESIS)
            revision_id = self._id_generator.new(EntityIdPrefix.REV)
            revision_no = 1
            supersedes = None
            thesis = Thesis(
                thesis_id=thesis_id,
                subject_id=subject_id,
                title=payload.title,
                role=role,
                status=thesis_status,
                current_revision_no=1,
                latest_revision_id=revision_id,
                parent_thesis_id=payload.parent_thesis_id,
                rival_thesis_ids=payload.rival_thesis_ids,
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
            uow.theses.add(thesis)
        else:
            thesis_id = candidate.thesis_id
            thesis = uow.theses.get(thesis_id)
            if thesis.subject_id != subject_id:
                raise DataContractError(
                    "Thesis does not belong to candidate Subject",
                    details={
                        "thesis_id": thesis.thesis_id,
                        "subject_id": subject_id,
                        "thesis_subject_id": thesis.subject_id,
                    },
                )
            thesis_status = (
                ThesisStatus(payload.thesis_status)
                if payload.thesis_status is not None
                else thesis.status
            )
            if (
                thesis_status is not thesis.status
                and candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW
            ):
                raise StrictReviewRequired(
                    "changing Thesis status requires STRICT_REVIEW",
                    details={
                        "thesis_id": thesis.thesis_id,
                        "from": thesis.status.value,
                        "to": thesis_status.value,
                    },
                )
            ensure_thesis_status_transition(
                uow,
                subject,
                thesis,
                attempted_child_status=thesis_status,
            )
            revision_no = uow.revisions.next_revision_no(thesis_id)
            supersedes = thesis.current_revision_no
            revision_id = self._id_generator.new(EntityIdPrefix.REV)

        from domain.common.enums import ConfidenceBand, InvestmentRating

        revision = ThesisRevision(
            revision_id=revision_id,
            thesis_id=thesis_id,
            subject_id=subject_id,
            revision_no=revision_no,
            supersedes_revision_no=supersedes,
            statement=payload.statement,
            rationale=payload.rationale,
            confidence_band=ConfidenceBand(payload.confidence_band),
            rating=InvestmentRating(payload.rating),
            confirmation_mode=candidate.confirmation_mode,
            proposed_by=candidate.proposed_by,
            confirmed_by=reviewed_by,
            proposed_at=candidate.proposed_at,
            confirmed_at=now,
            observation_window_start=payload.observation_window_start,
            observation_window_end=payload.observation_window_end,
            invalidation_check_note=payload.invalidation_check_note,
            schema_version=RESEARCH_SCHEMA_VERSION,
        )
        uow.revisions.append(revision)
        if candidate.thesis_id is not None:
            uow.theses.advance_current_revision(
                thesis_id,
                new_revision_no=revision_no,
                new_latest_revision_id=revision_id,
            )
            if thesis_status is not thesis.status:
                uow.theses.update_status(
                    thesis_id,
                    new_status=thesis_status,
                    archived_at=(now if thesis_status is ThesisStatus.ARCHIVED else None),
                )

        for ap in payload.assumptions:
            uow.assumptions.add(
                Assumption(
                    assumption_id=self._id_generator.new(EntityIdPrefix.REV),
                    thesis_id=thesis_id,
                    subject_id=subject_id,
                    revision_no=revision_no,
                    statement=ap.statement,
                    basis=ap.basis,
                    falsifiability=ap.falsifiability,
                    status=AssumptionStatus.ACCEPTED,
                    proposed_at=candidate.proposed_at,
                    confirmed_at=now,
                    proposed_by=candidate.proposed_by,
                    confirmed_by=reviewed_by,
                    retired_at=None,
                    retired_reason=None,
                )
            )

        for inv in payload.invalidations:
            severity = InvalidationSeverity(inv.severity)
            # HARD create must be ARMED
            status = InvalidationStatus.ARMED
            if severity is InvalidationSeverity.HARD and status is not InvalidationStatus.ARMED:
                raise DataContractError("HARD invalidation must be created as ARMED")
            uow.invalidations.add(
                InvalidationCondition(
                    invalidation_id=self._id_generator.new(EntityIdPrefix.REV),
                    thesis_id=thesis_id,
                    subject_id=subject_id,
                    revision_no=revision_no,
                    description=inv.description,
                    observable=inv.observable,
                    severity=severity,
                    status=status,
                    proposed_at=candidate.proposed_at,
                    confirmed_at=now,
                    last_checked_at=None,
                    triggered_at=None,
                    triggered_reason=None,
                    proposed_by=candidate.proposed_by,
                    confirmed_by=reviewed_by,
                )
            )

        self._touch_subject(uow, subject_id, now)
        return "thesis_revision", revision_id

    def _apply_assumption(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: AssumptionCandidatePayload,
        *,
        reviewed_by: str,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("assumption candidate requires subject_id")
        assumption_id = self._id_generator.new(EntityIdPrefix.REV)
        uow.assumptions.add(
            Assumption(
                assumption_id=assumption_id,
                thesis_id=payload.thesis_id,
                subject_id=subject_id,
                revision_no=payload.revision_no,
                statement=payload.statement,
                basis=payload.basis,
                falsifiability=payload.falsifiability,
                status=AssumptionStatus.ACCEPTED,
                proposed_at=candidate.proposed_at,
                confirmed_at=now,
                proposed_by=candidate.proposed_by,
                confirmed_by=reviewed_by,
                retired_at=None,
                retired_reason=None,
            )
        )
        self._touch_subject(uow, subject_id, now)
        return "assumption", assumption_id

    def _apply_invalidation(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: InvalidationCandidatePayload,
        *,
        reviewed_by: str,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("invalidation candidate requires subject_id")
        severity = InvalidationSeverity(payload.severity)
        # New HARD must be ARMED (INV-9)
        status = InvalidationStatus.ARMED
        if severity is InvalidationSeverity.HARD and status is not InvalidationStatus.ARMED:
            raise DataContractError("HARD invalidation must be created as ARMED")

        if payload.relaxes_invalidation_id is not None:
            if candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW:
                raise StrictReviewRequired(
                    "HARD relaxation requires STRICT_REVIEW",
                    details={"invalidation_id": payload.relaxes_invalidation_id},
                )
            old = uow.invalidations.get(payload.relaxes_invalidation_id)
            if old.severity is InvalidationSeverity.HARD and severity is InvalidationSeverity.SOFT:
                uow.invalidations.transition_status(
                    payload.relaxes_invalidation_id,
                    new_status=InvalidationStatus.RETIRED,
                    triggered_at=None,
                    triggered_reason=None,
                    last_checked_at=now,
                )

        inv_id = self._id_generator.new(EntityIdPrefix.REV)
        uow.invalidations.add(
            InvalidationCondition(
                invalidation_id=inv_id,
                thesis_id=payload.thesis_id,
                subject_id=subject_id,
                revision_no=payload.revision_no,
                description=payload.description,
                observable=payload.observable,
                severity=severity,
                status=status,
                proposed_at=candidate.proposed_at,
                confirmed_at=now,
                last_checked_at=None,
                triggered_at=None,
                triggered_reason=None,
                proposed_by=candidate.proposed_by,
                confirmed_by=reviewed_by,
            )
        )
        self._touch_subject(uow, subject_id, now)
        return "invalidation_condition", inv_id

    def _apply_open_question(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: OpenQuestionCandidatePayload,
        *,
        reviewed_by: str,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.common.enums import OpenQuestionStatus
        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("open_question candidate requires subject_id")

        if payload.action == "create":
            qid = self._id_generator.new(EntityIdPrefix.REV)
            uow.questions.add(
                OpenQuestion(
                    question_id=qid,
                    subject_id=subject_id,
                    text=payload.text or "",
                    status=OpenQuestionStatus.OPEN,
                    asked_at=now,
                    answered_at=None,
                    answer_summary=None,
                    closed_without_answer_reason=None,
                    proposed_by=candidate.proposed_by,
                )
            )
            self._touch_subject(uow, subject_id, now)
            return "open_question", qid
        if payload.action == "answer":
            assert payload.question_id is not None
            assert payload.answer_summary is not None
            uow.questions.answer(
                payload.question_id,
                answered_at=now,
                answer_summary=payload.answer_summary,
            )
            self._touch_subject(uow, subject_id, now)
            return "open_question", payload.question_id
        if payload.action == "mark_stale":
            assert payload.question_id is not None
            uow.questions.mark_stale(payload.question_id)
            self._touch_subject(uow, subject_id, now)
            return "open_question", payload.question_id
        if payload.action == "close":
            assert payload.question_id is not None
            assert payload.closed_reason is not None
            uow.questions.close_without_answer(
                payload.question_id,
                closed_reason=payload.closed_reason,
            )
            self._touch_subject(uow, subject_id, now)
            return "open_question", payload.question_id
        raise DataContractError(f"unknown open_question action: {payload.action}")

    def _apply_watchlist(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: WatchlistCandidatePayload,
        *,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.common.enums import Market
        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)

        if payload.action == "create":
            market_value = payload.market
            if market_value is None:
                raise DataContractError("create watchlist payload requires market")
            item_id = self._id_generator.new(EntityIdPrefix.SNAPSHOT)
            item = WatchlistItem(
                item_id=item_id,
                market=Market(str(market_value)),
                symbol=(payload.symbol or "").strip(),
                display_name=(payload.display_name or "").strip(),
                thesis_hint=(payload.thesis_hint or "").strip(),
                triggers=payload.triggers,
                subject_id=payload.subject_id
                if payload.subject_id is not None
                else candidate.subject_id,
                status=WatchlistItemStatus.WATCHING,
                created_at=now,
                updated_at=now,
                expires_at=payload.expires_at,
                promoted_to_subject_id=None,
                triggered_at=None,
                triggered_reason=None,
            )
            uow.watchlist.add(item)
            return "watchlist_item", item_id

        assert payload.item_id is not None
        assert payload.new_status is not None
        new_status = WatchlistItemStatus(payload.new_status)
        triggered_at = now if new_status is WatchlistItemStatus.TRIGGERED else None
        uow.watchlist.update_status(
            payload.item_id,
            new_status=new_status,
            triggered_at=triggered_at,
            triggered_reason=payload.triggered_reason,
            promoted_to_subject_id=payload.promoted_to_subject_id,
            expires_at=payload.expires_at,
        )
        return "watchlist_item", payload.item_id

    def _apply_subject_update(
        self,
        uow: ResearchUnitOfWork,
        candidate: object,
        payload: SubjectUpdateCandidatePayload,
        *,
        now: object,
    ) -> tuple[str, str | None]:
        from datetime import datetime

        from domain.research.models import CandidateThesisRevision

        assert isinstance(candidate, CandidateThesisRevision)
        assert isinstance(now, datetime)
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("subject_status_change candidate requires subject_id")
        subject = uow.subjects.get(subject_id)

        if payload.action == "create":
            # create is handled by ResearchSubjectService; confirm path is a no-op guard
            raise DataContractError(
                "subject create candidates are confirmed at creation time, "
                "not via confirm_candidate"
            )
        if payload.action == "archive":
            if candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW:
                raise StrictReviewRequired("archive requires STRICT_REVIEW")
            ensure_subject_can_leave_tracking(
                uow,
                subject,
                attempted_subject_status=ResearchSubjectStatus.ARCHIVED,
            )
            reason = payload.archived_reason or "archived"
            archived = ResearchSubject(
                subject_id=subject.subject_id,
                subject_type=subject.subject_type,
                title=subject.title,
                summary=subject.summary,
                status=ResearchSubjectStatus.ARCHIVED,
                primary_instrument_id=subject.primary_instrument_id,
                topic_tags=subject.topic_tags,
                created_at=subject.created_at,
                updated_at=now,
                created_by=subject.created_by,
                archived_at=now,
                archived_reason=reason,
                linked_subject_ids=subject.linked_subject_ids,
                evidence_ids=subject.evidence_ids,
                report_ids=subject.report_ids,
                event_ids=subject.event_ids,
                decision_ids=subject.decision_ids,
                schema_version=subject.schema_version,
            )
            uow.subjects.update(archived)
            return "research_subject", subject_id

        # update
        if payload.subject_type is not None or payload.primary_instrument_id is not None:
            raise InputValidationError(
                "Subject type and primary instrument are immutable after creation",
            )
        new_status = (
            ResearchSubjectStatus(payload.new_status)
            if payload.new_status is not None
            else subject.status
        )
        metadata = validate_subject_metadata(
            title=payload.title if payload.title is not None else subject.title,
            summary=payload.summary if payload.summary is not None else subject.summary,
        )
        ensure_subject_can_leave_tracking(
            uow,
            subject,
            attempted_subject_status=new_status,
        )
        if (
            subject.status == ResearchSubjectStatus.ACTIVE
            and new_status != ResearchSubjectStatus.ACTIVE
            and candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW
        ):
            raise StrictReviewRequired(
                "leaving ACTIVE subject status requires STRICT_REVIEW",
                details={"from": subject.status.value, "to": new_status.value},
            )
        is_archived = new_status == ResearchSubjectStatus.ARCHIVED
        updated = ResearchSubject(
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            title=metadata.title,
            summary=metadata.summary,
            status=new_status,
            primary_instrument_id=subject.primary_instrument_id,
            topic_tags=payload.topic_tags if payload.topic_tags is not None else subject.topic_tags,
            created_at=subject.created_at,
            updated_at=now,
            created_by=subject.created_by,
            archived_at=now if is_archived else None,
            archived_reason=(
                (payload.archived_reason or subject.archived_reason or "archived")
                if is_archived
                else None
            ),
            linked_subject_ids=(
                payload.linked_subject_ids
                if payload.linked_subject_ids is not None
                else subject.linked_subject_ids
            ),
            evidence_ids=subject.evidence_ids,
            report_ids=subject.report_ids,
            event_ids=subject.event_ids,
            decision_ids=subject.decision_ids,
            schema_version=subject.schema_version,
        )
        uow.subjects.update(updated)
        return "research_subject", subject_id
