"""Research domain model compatibility façade.

The research domain is split into validation helpers and bounded model modules.
This module deliberately re-exports the historical public names so existing
application, persistence, and test imports do not need to change.
"""

from domain.research.memory_models import (
    DecisionRecord,
    Evidence,
    EvidenceAssessment,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    SubjectEvidenceLink,
)
from domain.research.subject_models import (
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    OpenQuestion,
    ResearchSubject,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)
from domain.research.validation import (
    FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES,
    FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES,
    FROZEN_RESEARCH_MODEL_NAMES,
    RESEARCH_SCHEMA_VERSION,
    canonicalize_research_json_object,
    compute_evidence_content_sha256,
    compute_report_content_sha256,
)

__all__ = [
    "FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES",
    "FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES",
    "FROZEN_RESEARCH_MODEL_NAMES",
    "RESEARCH_SCHEMA_VERSION",
    "Assumption",
    "CandidateThesisRevision",
    "DecisionRecord",
    "Evidence",
    "EvidenceAssessment",
    "InvalidationCondition",
    "JournalEntry",
    "OpenQuestion",
    "ResearchEvent",
    "ResearchReport",
    "ResearchSubject",
    "SubjectEvidenceLink",
    "Thesis",
    "ThesisRevision",
    "WatchlistItem",
    "canonicalize_research_json_object",
    "compute_evidence_content_sha256",
    "compute_report_content_sha256",
]
