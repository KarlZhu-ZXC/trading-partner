"""Build the durable research application graph.

The research services share the same deterministic runtime collaborators and
unit-of-work factory.  Keeping their construction here makes that wiring
explicit while leaving persistence ownership in the bootstrap facade.
"""

from __future__ import annotations

from dataclasses import dataclass

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.challenge_review_repository import ChallengeReviewRepository
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import UowFactory
from application.services.challenge_review_service import ChallengeReviewService
from application.services.decision_record_service import DecisionRecordService
from application.services.journal_service import JournalService
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_context_builder import ResearchContextBuilder
from application.services.research_search_service import ResearchSearchService
from application.services.research_state_query_service import ResearchStateQueryService
from application.services.research_subject_service import ResearchSubjectService
from application.services.research_timeline_service import ResearchTimelineService
from application.services.thesis_revision_service import ThesisRevisionService


@dataclass(frozen=True, slots=True)
class ResearchServices:
    """Application services for the durable research graph."""

    research_subjects: ResearchSubjectService
    thesis_revisions: ThesisRevisionService
    research_state: ResearchStateQueryService
    research_archive: ResearchArchiveService
    research_search: ResearchSearchService
    research_timeline: ResearchTimelineService
    journal: JournalService
    decisions: DecisionRecordService
    research_context: ResearchContextBuilder
    challenge: ChallengeReviewService


def build_research_services(
    *,
    uow_factory: UowFactory,
    account_snapshot_repository: AccountSnapshotRepository,
    challenge_review_repository: ChallengeReviewRepository,
    clock: Clock,
    id_generator: IdGenerator,
    secret_redactor: SecretRedactor,
    external_notes: ExternalNoteRepository | None = None,
) -> ResearchServices:
    """Construct all research services from one shared dependency set."""

    research_context = ResearchContextBuilder(
        uow_factory,
        account_snapshot_repository,
        clock,
        id_generator,
        secret_redactor,
    )
    return ResearchServices(
        research_subjects=ResearchSubjectService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        thesis_revisions=ThesisRevisionService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        research_state=ResearchStateQueryService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        research_archive=ResearchArchiveService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        research_search=ResearchSearchService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        research_timeline=ResearchTimelineService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        journal=JournalService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
            external_notes=external_notes,
        ),
        decisions=DecisionRecordService(
            uow_factory,
            clock,
            id_generator,
            secret_redactor,
        ),
        research_context=research_context,
        challenge=ChallengeReviewService(
            challenge_review_repository,
            research_context,
            clock,
            id_generator,
            secret_redactor,
        ),
    )


__all__ = ["ResearchServices", "build_research_services"]
