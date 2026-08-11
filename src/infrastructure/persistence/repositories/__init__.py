"""Session-bound research repositories and append-only guards."""

from infrastructure.persistence.repositories.append_only import (
    register_append_only_listeners,
)
from infrastructure.persistence.repositories.assumption import (
    SqlAlchemyAssumptionRepository,
)
from infrastructure.persistence.repositories.candidate_thesis_revision import (
    SqlAlchemyCandidateThesisRevisionRepository,
    register_candidate_payload_listeners,
)
from infrastructure.persistence.repositories.catalyst_agenda import (
    SqlAlchemyCatalystAgendaRepository,
)
from infrastructure.persistence.repositories.decision_record import (
    SqlAlchemyDecisionRecordRepository,
)
from infrastructure.persistence.repositories.evidence import (
    SqlAlchemyEvidenceRepository,
)
from infrastructure.persistence.repositories.evidence_assessment import (
    SqlAlchemyEvidenceAssessmentRepository,
)
from infrastructure.persistence.repositories.invalidation_condition import (
    SqlAlchemyInvalidationConditionRepository,
)
from infrastructure.persistence.repositories.journal import (
    SqlAlchemyJournalRepository,
)
from infrastructure.persistence.repositories.monitor_lifecycle import (
    SqlAlchemyMonitorLifecycleReader,
)
from infrastructure.persistence.repositories.open_question import (
    SqlAlchemyOpenQuestionRepository,
)
from infrastructure.persistence.repositories.research_event import (
    SqlAlchemyResearchEventRepository,
)
from infrastructure.persistence.repositories.research_report import (
    SqlAlchemyResearchReportRepository,
)
from infrastructure.persistence.repositories.research_search_index import (
    SqlAlchemyResearchSearchIndex,
)
from infrastructure.persistence.repositories.research_subject import (
    SqlAlchemyResearchSubjectRepository,
)
from infrastructure.persistence.repositories.subject_evidence_link import (
    SqlAlchemySubjectEvidenceLinkRepository,
)
from infrastructure.persistence.repositories.thesis import SqlAlchemyThesisRepository
from infrastructure.persistence.repositories.thesis_revision import (
    SqlAlchemyThesisRevisionRepository,
)
from infrastructure.persistence.repositories.watchlist import (
    SqlAlchemyWatchlistRepository,
)
from infrastructure.persistence.repositories.watchlist_group import (
    SqlAlchemyWatchlistGroupRepository,
)
from infrastructure.persistence.repositories.watchlist_membership import (
    SqlAlchemyWatchlistMembershipRepository,
)
from infrastructure.persistence.repositories.watchlist_mutation import (
    SqlAlchemyWatchlistMutationRepository,
)

# Ensure append-only / payload immutability listeners are active on import.
register_append_only_listeners()
register_candidate_payload_listeners()

__all__ = [
    "SqlAlchemyAssumptionRepository",
    "SqlAlchemyCandidateThesisRevisionRepository",
    "SqlAlchemyCatalystAgendaRepository",
    "SqlAlchemySubjectEvidenceLinkRepository",
    "SqlAlchemyDecisionRecordRepository",
    "SqlAlchemyEvidenceAssessmentRepository",
    "SqlAlchemyEvidenceRepository",
    "SqlAlchemyInvalidationConditionRepository",
    "SqlAlchemyResearchSubjectRepository",
    "SqlAlchemyJournalRepository",
    "SqlAlchemyMonitorLifecycleReader",
    "SqlAlchemyOpenQuestionRepository",
    "SqlAlchemyResearchEventRepository",
    "SqlAlchemyResearchReportRepository",
    "SqlAlchemyResearchSearchIndex",
    "SqlAlchemyThesisRepository",
    "SqlAlchemyThesisRevisionRepository",
    "SqlAlchemyWatchlistGroupRepository",
    "SqlAlchemyWatchlistMembershipRepository",
    "SqlAlchemyWatchlistMutationRepository",
    "SqlAlchemyWatchlistRepository",
    "register_append_only_listeners",
    "register_candidate_payload_listeners",
]
