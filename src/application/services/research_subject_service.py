"""ResearchSubject application service (create/archive with confirmed candidates)."""

from __future__ import annotations

from application.dto.research import (
    ResearchSubjectDTO,
    ResearchSubjectListDTO,
    SubjectUpdateCandidatePayload,
    candidate_payload_to_json,
    payloads_equal_json,
)
from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY, ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
    normalize_idempotency_key,
    propose_candidate,
    require_confirm_reviewer,
)
from application.services.research_state_invariants import ensure_subject_can_leave_tracking
from application.services.subject_metadata_policy import validate_subject_metadata
from domain.common.enums import (
    CandidateKind,
    CandidateStatus,
    ConfirmationMode,
    ResearchSubjectStatus,
    ResearchSubjectType,
)
from domain.common.errors import (
    DuplicateIdempotencyKey,
    InputValidationError,
    UnauthorizedReviewer,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject


class ResearchSubjectService:
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

    def create_subject(
        self,
        *,
        subject_type: ResearchSubjectType,
        title: str,
        summary: str,
        primary_instrument_id: str | None,
        topic_tags: tuple[str, ...],
        linked_subject_ids: tuple[str, ...],
        confirmed_by: str,
        idempotency_key: str,
    ) -> ToolEnvelope[ResearchSubjectDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(confirmed_by, action="create_subject")
            metadata = validate_subject_metadata(title=title, summary=summary)
            title_n = metadata.title
            summary_n = metadata.summary
            tags = tuple(t.strip().lower() for t in topic_tags if t.strip())
            linked = tuple(x.strip() for x in linked_subject_ids if x.strip())

            payload = SubjectUpdateCandidatePayload(
                kind="case_status_change",
                action="create",
                subject_type=subject_type,
                new_status=ResearchSubjectStatus.DRAFT,
                title=title_n,
                summary=summary_n,
                primary_instrument_id=primary_instrument_id,
                topic_tags=tags,
                linked_subject_ids=linked,
            )
            payload_json = candidate_payload_to_json(payload)

            with self._uow_factory() as uow:
                key = normalize_idempotency_key(idempotency_key)
                existing = uow.candidates.get_by_idempotency_key(key)
                if existing is not None:
                    if not payloads_equal_json(existing.payload_json, payload_json):
                        raise DuplicateIdempotencyKey(
                            "idempotency_key already used with a different payload",
                            details={
                                "idempotency_key": key,
                                "existing_candidate_id": existing.candidate_id,
                            },
                        )
                    if existing.subject_id is None:
                        raise InputValidationError(
                            "existing create candidate missing subject_id",
                            details={"candidate_id": existing.candidate_id},
                        )
                    subject = uow.subjects.get(existing.subject_id)
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=ResearchSubjectDTO.from_domain(subject),
                        warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                        degraded=True,
                    )

                now = self._clock.now()
                subject_id = self._id_generator.new(EntityIdPrefix.SUBJECT)
                subject = ResearchSubject(
                    subject_id=subject_id,
                    subject_type=subject_type,
                    title=title_n,
                    summary=summary_n,
                    status=ResearchSubjectStatus.DRAFT,
                    primary_instrument_id=primary_instrument_id,
                    topic_tags=tags,
                    created_at=now,
                    updated_at=now,
                    created_by=confirmed_by.strip(),
                    archived_at=None,
                    archived_reason=None,
                    linked_subject_ids=linked,
                    evidence_ids=(),
                    report_ids=(),
                    event_ids=(),
                    decision_ids=(),
                    schema_version=RESEARCH_SCHEMA_VERSION,
                )
                uow.subjects.add(subject)

                candidate, _dup, _warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.SUBJECT_STATUS_CHANGE,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
                    payload_model=payload,
                    confirmation_mode=ConfirmationMode.NORMAL,
                    proposed_by=confirmed_by.strip(),
                    proposed_by_rationale="User-confirmed investment subject create",
                    idempotency_key=key,
                    status=CandidateStatus.CONFIRMED,
                    reviewed_at=now,
                    reviewed_by=confirmed_by.strip(),
                    review_note="create confirmed",
                )

                uow.audit.append(
                    "phase1b.research_subject.created",
                    {
                        "subject_id": subject_id,
                        "confirmed_by": confirmed_by.strip(),
                        "candidate_id": candidate.candidate_id,
                    },
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchSubjectDTO.from_domain(subject),
                )
        except Exception as exc:  # noqa: BLE001 — map to ToolEnvelope
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def get_subject(self, subject_id: str) -> ToolEnvelope[ResearchSubjectDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                subject = uow.subjects.get(subject_id)
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchSubjectDTO.from_domain(subject),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def list_subjects(
        self,
        *,
        subject_type: ResearchSubjectType | None = None,
        status: ResearchSubjectStatus | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> ToolEnvelope[ResearchSubjectListDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                items = uow.subjects.list(
                    subject_type=subject_type,
                    status=status,
                    primary_instrument_id=primary_instrument_id,
                    topic_tag=topic_tag,
                    include_archived=include_archived,
                    limit=limit,
                    offset=offset,
                )
                data = ResearchSubjectListDTO(
                    items=ResearchSubjectDTO.from_domain_list(items),
                    total=len(items),
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

    def archive_subject(
        self,
        subject_id: str,
        *,
        archived_reason: str,
        reviewed_by: str,
        idempotency_key: str,
    ) -> ToolEnvelope[ResearchSubjectDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(reviewed_by, action="archive_subject")
            reason = archived_reason.strip()
            if not reason or len(reason) > 1000:
                raise InputValidationError("archived_reason must be 1..1000 characters")
            if reviewed_by.strip() == "codex":
                raise UnauthorizedReviewer(
                    "archive_subject does not accept reviewed_by=codex",
                    details={"reviewed_by": "codex"},
                )

            payload = SubjectUpdateCandidatePayload(
                kind="case_status_change",
                action="archive",
                new_status=ResearchSubjectStatus.ARCHIVED,
                archived_reason=reason,
            )
            payload_json = candidate_payload_to_json(payload)

            with self._uow_factory() as uow:
                key = normalize_idempotency_key(idempotency_key)
                existing = uow.candidates.get_by_idempotency_key(key)
                if existing is not None:
                    if not payloads_equal_json(existing.payload_json, payload_json):
                        raise DuplicateIdempotencyKey(
                            "idempotency_key already used with a different payload",
                            details={
                                "idempotency_key": key,
                                "existing_candidate_id": existing.candidate_id,
                            },
                        )
                    subject = uow.subjects.get(subject_id)
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=ResearchSubjectDTO.from_domain(subject),
                        warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                        degraded=True,
                    )

                subject = uow.subjects.get(subject_id)
                ensure_subject_can_leave_tracking(
                    uow,
                    subject,
                    attempted_subject_status=ResearchSubjectStatus.ARCHIVED,
                )
                now = self._clock.now()
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

                candidate, _dup, _warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.SUBJECT_STATUS_CHANGE,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
                    payload_model=payload,
                    confirmation_mode=ConfirmationMode.STRICT_REVIEW,
                    proposed_by=reviewed_by.strip(),
                    proposed_by_rationale="User-confirmed investment subject archive",
                    idempotency_key=key,
                    status=CandidateStatus.CONFIRMED,
                    reviewed_at=now,
                    reviewed_by=reviewed_by.strip(),
                    review_note="archive confirmed",
                )

                uow.audit.append(
                    "phase1b.research_subject.archived",
                    {
                        "subject_id": subject_id,
                        "reviewed_by": reviewed_by.strip(),
                        "candidate_id": candidate.candidate_id,
                        "archived_reason": reason,
                    },
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchSubjectDTO.from_domain(archived),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def update_subject_metadata(
        self,
        subject_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        topic_tags: tuple[str, ...] | None = None,
        linked_subject_ids: tuple[str, ...] | None = None,
        reviewed_by: str,
        idempotency_key: str,
    ) -> ToolEnvelope[ResearchSubjectDTO]:
        """Confirmed metadata update (user/external_agent only) with audit candidate."""
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(reviewed_by, action="update_subject_metadata")
            if (
                title is None
                and summary is None
                and topic_tags is None
                and linked_subject_ids is None
            ):
                raise InputValidationError("update_subject_metadata requires at least one field")

            with self._uow_factory() as uow:
                subject = uow.subjects.get(subject_id)
                title_n = title.strip() if title is not None else None
                summary_n = summary.strip() if summary is not None else None
                metadata = validate_subject_metadata(
                    title=title_n if title_n is not None else subject.title,
                    summary=summary_n if summary_n is not None else subject.summary,
                )
                payload = SubjectUpdateCandidatePayload(
                    kind="case_status_change",
                    action="update",
                    title=title_n,
                    summary=summary_n,
                    topic_tags=(
                        tuple(t.strip().lower() for t in topic_tags if t.strip())
                        if topic_tags is not None
                        else None
                    ),
                    linked_subject_ids=(
                        tuple(x.strip() for x in linked_subject_ids if x.strip())
                        if linked_subject_ids is not None
                        else None
                    ),
                )
                payload_json = candidate_payload_to_json(payload)
                key = normalize_idempotency_key(idempotency_key)
                existing = uow.candidates.get_by_idempotency_key(key)
                if existing is not None:
                    if not payloads_equal_json(existing.payload_json, payload_json):
                        raise DuplicateIdempotencyKey(
                            "idempotency_key already used with a different payload",
                            details={
                                "idempotency_key": key,
                                "existing_candidate_id": existing.candidate_id,
                            },
                        )
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=ResearchSubjectDTO.from_domain(subject),
                        warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                        degraded=True,
                    )

                now = self._clock.now()
                updated = ResearchSubject(
                    subject_id=subject.subject_id,
                    subject_type=subject.subject_type,
                    title=metadata.title,
                    summary=metadata.summary,
                    status=subject.status,
                    primary_instrument_id=subject.primary_instrument_id,
                    topic_tags=(
                        tuple(t.strip().lower() for t in topic_tags if t.strip())
                        if topic_tags is not None
                        else subject.topic_tags
                    ),
                    created_at=subject.created_at,
                    updated_at=now,
                    created_by=subject.created_by,
                    archived_at=subject.archived_at,
                    archived_reason=subject.archived_reason,
                    linked_subject_ids=(
                        tuple(x.strip() for x in linked_subject_ids if x.strip())
                        if linked_subject_ids is not None
                        else subject.linked_subject_ids
                    ),
                    evidence_ids=subject.evidence_ids,
                    report_ids=subject.report_ids,
                    event_ids=subject.event_ids,
                    decision_ids=subject.decision_ids,
                    schema_version=subject.schema_version,
                )
                uow.subjects.update(updated)

                candidate, _dup, _warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.SUBJECT_STATUS_CHANGE,
                    subject_id=subject_id,
                    thesis_id=None,
                    target_revision_no=None,
                    payload_model=payload,
                    confirmation_mode=ConfirmationMode.NORMAL,
                    proposed_by=reviewed_by.strip(),
                    proposed_by_rationale="User-confirmed subject metadata update",
                    idempotency_key=key,
                    status=CandidateStatus.CONFIRMED,
                    reviewed_at=now,
                    reviewed_by=reviewed_by.strip(),
                    review_note="metadata update confirmed",
                )

                uow.audit.append(
                    "phase1b.research_subject.metadata_updated",
                    {
                        "subject_id": subject_id,
                        "reviewed_by": reviewed_by.strip(),
                        "candidate_id": candidate.candidate_id,
                    },
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchSubjectDTO.from_domain(updated),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
