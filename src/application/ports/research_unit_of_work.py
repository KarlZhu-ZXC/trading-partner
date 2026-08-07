"""Research Unit of Work — single Session for business rows + audit."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from application.ports.assumption_repository import AssumptionRepository
from application.ports.audit_log_writer import AuditLogWriter
from application.ports.candidate_thesis_revision_repository import (
    CandidateThesisRevisionRepository,
)
from application.ports.decision_record_repository import DecisionRecordRepository
from application.ports.evidence_assessment_repository import (
    EvidenceAssessmentRepository,
)
from application.ports.evidence_repository import EvidenceRepository
from application.ports.invalidation_condition_repository import (
    InvalidationConditionRepository,
)
from application.ports.journal_repository import JournalRepository
from application.ports.open_question_repository import OpenQuestionRepository
from application.ports.research_event_repository import ResearchEventRepository
from application.ports.research_report_repository import ResearchReportRepository
from application.ports.research_search_index import ResearchSearchIndex
from application.ports.research_subject_repository import ResearchSubjectRepository
from application.ports.subject_evidence_link_repository import SubjectEvidenceLinkRepository
from application.ports.thesis_repository import ThesisRepository
from application.ports.thesis_revision_repository import ThesisRevisionRepository
from application.ports.trade_plan_repository import TradePlanRepository
from application.ports.watchlist_repository import WatchlistRepository


class ResearchUnitOfWork(Protocol):
    def __enter__(self) -> ResearchUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @property
    def subjects(self) -> ResearchSubjectRepository: ...

    @property
    def theses(self) -> ThesisRepository: ...

    @property
    def revisions(self) -> ThesisRevisionRepository: ...

    @property
    def assumptions(self) -> AssumptionRepository: ...

    @property
    def invalidations(self) -> InvalidationConditionRepository: ...

    @property
    def questions(self) -> OpenQuestionRepository: ...

    @property
    def watchlist(self) -> WatchlistRepository: ...

    @property
    def candidates(self) -> CandidateThesisRevisionRepository: ...

    @property
    def evidence(self) -> EvidenceRepository: ...

    @property
    def subject_evidence_links(self) -> SubjectEvidenceLinkRepository: ...

    @property
    def evidence_assessments(self) -> EvidenceAssessmentRepository: ...

    @property
    def reports(self) -> ResearchReportRepository: ...

    @property
    def events(self) -> ResearchEventRepository: ...

    @property
    def decisions(self) -> DecisionRecordRepository: ...

    @property
    def journal(self) -> JournalRepository: ...

    @property
    def search_index(self) -> ResearchSearchIndex: ...

    @property
    def trade_plans(self) -> TradePlanRepository: ...

    @property
    def audit(self) -> AuditLogWriter:
        """Session-bound audit writer; must not commit on its own."""
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
