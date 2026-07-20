"""Research domain namespace.

Phase 1B implements Investment Case / Thesis state models while preserving the
Phase 1A frozen 12-name registry (including Phase 1C placeholders).
"""

from domain.research.models import (
    FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES,
    FROZEN_RESEARCH_MODEL_NAMES,
    RESEARCH_SCHEMA_VERSION,
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    InvestmentCase,
    OpenQuestion,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)

__all__ = [
    "FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES",
    "FROZEN_RESEARCH_MODEL_NAMES",
    "RESEARCH_SCHEMA_VERSION",
    "Assumption",
    "CandidateThesisRevision",
    "InvalidationCondition",
    "InvestmentCase",
    "OpenQuestion",
    "Thesis",
    "ThesisRevision",
    "WatchlistItem",
]
