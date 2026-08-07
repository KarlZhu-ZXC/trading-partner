"""OpenQuestion propose-only service (formal rows land on confirm)."""

from __future__ import annotations

from application.dto.research import CandidateRevisionDTO, OpenQuestionCandidatePayload
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    candidate_to_dto,
    envelope_failure,
    envelope_success,
    propose_candidate,
)
from domain.common.enums import CandidateKind, CandidateStatus, ConfirmationMode
from domain.common.errors import InputValidationError
from domain.common.ids import EntityIdPrefix


class OpenQuestionService:
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

    def propose_question(
        self,
        *,
        subject_id: str,
        text: str,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            body = text.strip()
            if not body:
                raise InputValidationError("open question text must be non-empty")
            payload = OpenQuestionCandidatePayload(
                kind="open_question",
                action="create",
                text=body,
            )
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.OPEN_QUESTION,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
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
                            "kind": CandidateKind.OPEN_QUESTION.value,
                            "subject_id": subject_id,
                            "action": "create",
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

    def propose_answer(
        self,
        *,
        subject_id: str,
        question_id: str,
        answer_summary: str,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            summary = answer_summary.strip()
            if not summary:
                raise InputValidationError("answer_summary must be non-empty")
            payload = OpenQuestionCandidatePayload(
                kind="open_question",
                action="answer",
                question_id=question_id,
                answer_summary=summary,
            )
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                question = uow.questions.get(question_id)
                if question.subject_id != subject_id:
                    raise InputValidationError(
                        "question_id does not belong to subject_id",
                        details={"question_id": question_id, "subject_id": subject_id},
                    )
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.OPEN_QUESTION,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
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
                            "kind": CandidateKind.OPEN_QUESTION.value,
                            "subject_id": subject_id,
                            "action": "answer",
                            "question_id": question_id,
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

    def propose_mark_stale(
        self,
        *,
        subject_id: str,
        question_id: str,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            payload = OpenQuestionCandidatePayload(
                kind="open_question",
                action="mark_stale",
                question_id=question_id,
            )
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                uow.questions.get(question_id)
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.OPEN_QUESTION,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
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
                            "kind": CandidateKind.OPEN_QUESTION.value,
                            "action": "mark_stale",
                            "question_id": question_id,
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

    def propose_close(
        self,
        *,
        subject_id: str,
        question_id: str,
        closed_reason: str,
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            reason = closed_reason.strip()
            if not reason:
                raise InputValidationError("closed_reason must be non-empty")
            payload = OpenQuestionCandidatePayload(
                kind="open_question",
                action="close",
                question_id=question_id,
                closed_reason=reason,
            )
            with self._uow_factory() as uow:
                uow.subjects.get(subject_id)
                uow.questions.get(question_id)
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.OPEN_QUESTION,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
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
                            "kind": CandidateKind.OPEN_QUESTION.value,
                            "action": "close",
                            "question_id": question_id,
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
