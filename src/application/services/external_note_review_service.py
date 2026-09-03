"""Durable, confirmation-gated outcomes for exact Observation revisions."""

from __future__ import annotations

from application.dto.external_note_review import (
    ExternalNoteReviewDTO,
    ExternalNoteReviewTransitionInput,
)
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.id_generator import IdGenerator
from application.services._research_support import UowFactory
from domain.common.errors import (
    DataContractError,
    ExternalNoteReviewNotFound,
    InvalidExternalNoteReviewTransition,
    InvalidResearchLink,
    PersistenceError,
)
from domain.common.ids import EntityIdPrefix
from domain.external_note.enums import ExternalNoteReviewStatus, NoteCoverage
from domain.external_note.models import ExternalNoteReview
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection


class ExternalNoteReviewService:
    """Materialize pending reviews and append explicit human outcomes."""

    def __init__(
        self,
        reviews: ExternalNoteReviewRepository,
        notes: ExternalNoteRepository,
        research_uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._reviews = reviews
        self._notes = notes
        self._research_uow_factory = research_uow_factory
        self._clock = clock
        self._ids = id_generator

    def ensure_pending(
        self,
        *,
        note_revision_id: str,
        subject_id: str | None = None,
    ) -> ExternalNoteReviewDTO:
        revision_id = note_revision_id.strip()
        revision = self._notes.revision_by_id(revision_id)
        if revision is None:
            raise ExternalNoteReviewNotFound("Observation revision was not found")
        if revision.coverage is not NoteCoverage.FULL:
            raise InvalidExternalNoteReviewTransition(
                "Only a FULL Observation revision can enter judgment review"
            )
        interpretation = self._notes.interpretation_for_revision(revision_id)
        if interpretation is None or interpretation.status != "SUCCEEDED":
            raise InvalidExternalNoteReviewTransition(
                "A successful interpretation is required before judgment review"
            )
        normalized_subject = subject_id.strip() if subject_id is not None else None
        if normalized_subject:
            with self._research_uow_factory() as uow:
                uow.subjects.get(normalized_subject)
        current = self._reviews.latest_for_revision(revision_id)
        if current is not None:
            if (
                normalized_subject is not None
                and current.subject_id is None
                and current.status is ExternalNoteReviewStatus.PENDING
            ):
                mapped = ExternalNoteReview(
                    review_id=current.review_id,
                    note_revision_id=current.note_revision_id,
                    note_id=current.note_id,
                    version=current.version + 1,
                    status=ExternalNoteReviewStatus.PENDING,
                    subject_id=normalized_subject,
                    decision_id=None,
                    due_at=None,
                    actor="system",
                    authorization_note=(
                        "Observation matched one exact Research Subject before review."
                    ),
                    idempotency_key=(
                        f"observation-review-subject:{revision_id}:{normalized_subject}"
                    ),
                    created_at=self._clock.now(),
                )
                return ExternalNoteReviewDTO.from_domain(
                    self._reviews.append(mapped, expected_version=current.version)
                )
            if (
                normalized_subject is not None
                and current.subject_id is not None
                and normalized_subject != current.subject_id
            ):
                raise InvalidExternalNoteReviewTransition(
                    "Observation review is already mapped to another Research Subject"
                )
            return ExternalNoteReviewDTO.from_domain(current)
        value = ExternalNoteReview(
            review_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_REVIEW),
            note_revision_id=revision.note_revision_id,
            note_id=revision.note_id,
            version=1,
            status=ExternalNoteReviewStatus.PENDING,
            subject_id=normalized_subject,
            decision_id=None,
            due_at=None,
            actor="system",
            authorization_note="Eligible FULL Observation revision awaits explicit review.",
            idempotency_key=f"observation-review-pending:{revision.note_revision_id}",
            created_at=self._clock.now(),
        )
        persisted: ExternalNoteReview | None
        try:
            persisted = self._reviews.append(value, expected_version=0)
        except PersistenceError:
            # A concurrent Console/background materializer may have won the
            # unique note-revision/idempotency race. Re-read only that exact
            # source revision; unrelated persistence failures still surface.
            persisted = self._reviews.latest_for_revision(revision_id)
            if persisted is None:
                raise
        if persisted is None:  # pragma: no cover - narrowed by both branches above
            raise PersistenceError("Observation review materialization returned no row")
        return ExternalNoteReviewDTO.from_domain(persisted)

    def transition(
        self,
        request: ExternalNoteReviewTransitionInput,
    ) -> ExternalNoteReviewDTO:
        current = self._reviews.latest(request.review_id)
        if current is None:
            raise ExternalNoteReviewNotFound("Observation review was not found")
        try:
            status = ExternalNoteReviewStatus(request.status)
        except ValueError as exc:
            raise DataContractError("Observation review status is invalid") from exc
        if status not in {
            ExternalNoteReviewStatus.DEFERRED,
            ExternalNoteReviewStatus.ADOPTED,
            ExternalNoteReviewStatus.NO_ACTION,
        }:
            raise InvalidExternalNoteReviewTransition(
                "Human review may defer, adopt, or record no action"
            )
        if current.status not in {
            ExternalNoteReviewStatus.PENDING,
            ExternalNoteReviewStatus.DEFERRED,
        }:
            raise InvalidExternalNoteReviewTransition(
                "A terminal Observation review cannot be changed"
            )
        if request.actor not in {"user", "external_agent"}:
            raise InvalidExternalNoteReviewTransition(
                "Observation review actor must be user or external_agent"
            )
        subject_id = request.subject_id or current.subject_id
        if status in {
            ExternalNoteReviewStatus.ADOPTED,
            ExternalNoteReviewStatus.NO_ACTION,
        }:
            if subject_id is None or request.decision_id is None:
                raise InvalidExternalNoteReviewTransition(
                    "A terminal review requires an exact Subject and Decision"
                )
            with self._research_uow_factory() as uow:
                subject = uow.subjects.get(subject_id)
                decision = uow.decisions.get(request.decision_id)
            if decision.subject_id != subject.subject_id:
                raise InvalidResearchLink(
                    "Observation review Decision belongs to another Research Subject"
                )
            if decision.external_note_revision_id != current.note_revision_id:
                raise InvalidResearchLink(
                    "Observation review Decision must name the exact note revision"
                )
        value = ExternalNoteReview(
            review_id=current.review_id,
            note_revision_id=current.note_revision_id,
            note_id=current.note_id,
            version=current.version + 1,
            status=status,
            subject_id=subject_id,
            decision_id=request.decision_id,
            due_at=request.due_at,
            actor=request.actor,
            authorization_note=request.authorization_note,
            idempotency_key=request.idempotency_key,
            created_at=self._clock.now(),
        )
        return ExternalNoteReviewDTO.from_domain(
            self._reviews.append(value, expected_version=request.expected_version)
        )

    def list_latest(
        self,
        *,
        statuses: frozenset[ExternalNoteReviewStatus] | None = None,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ExternalNoteReviewDTO, ...]:
        if not 1 <= limit <= 500:
            raise DataContractError("Observation review limit must be between 1 and 500")
        return tuple(
            ExternalNoteReviewDTO.from_domain(item)
            for item in self._reviews.list_latest(
                statuses=statuses,
                subject_id=subject_id,
                limit=limit,
            )
        )

    def get_for_revision(self, note_revision_id: str) -> ExternalNoteReviewDTO | None:
        value = self._reviews.latest_for_revision(note_revision_id.strip())
        return ExternalNoteReviewDTO.from_domain(value) if value is not None else None

    def pending_projections(self, *, limit: int = 100) -> tuple[ReviewItemProjection, ...]:
        values = self._reviews.list_latest(
            statuses=frozenset(
                {
                    ExternalNoteReviewStatus.PENDING,
                    ExternalNoteReviewStatus.DEFERRED,
                }
            ),
            limit=limit,
        )
        result: list[ReviewItemProjection] = []
        for value in values:
            revision = self._notes.revision_by_id(value.note_revision_id)
            if revision is None:
                raise ExternalNoteReviewNotFound(
                    "Observation review source revision was not found"
                )
            href = "/decision-workbench#notes"
            if value.subject_id is not None:
                href = (
                    "/decision-workbench?subject_id="
                    f"{value.subject_id}#notes"
                )
            result.append(
                ReviewItemProjection(
                    source_key=f"OBSERVATION_REVIEW_DUE:{value.review_id}",
                    source_type=ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
                    source_ref=value.note_revision_id,
                    subject_id=value.subject_id,
                    title=f"View review due · {revision.title}",
                    detail=(
                        "A FULL Observation revision has a successful draft "
                        "interpretation but no confirmed Decision outcome."
                    ),
                    severity=ReviewItemSeverity.ATTENTION,
                    recommended_action="REVIEW_OBSERVATION",
                    href=href,
                    due_at=value.due_at,
                )
            )
        return tuple(result)
