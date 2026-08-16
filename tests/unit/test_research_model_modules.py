from domain.research import models
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


def test_models_facade_reexports_all_research_entities() -> None:
    entities = (
        Assumption,
        CandidateThesisRevision,
        DecisionRecord,
        Evidence,
        EvidenceAssessment,
        InvalidationCondition,
        JournalEntry,
        OpenQuestion,
        ResearchEvent,
        ResearchReport,
        ResearchSubject,
        SubjectEvidenceLink,
        Thesis,
        ThesisRevision,
        WatchlistItem,
    )

    assert len(entities) == 15
    for entity in entities:
        assert getattr(models, entity.__name__) is entity


def test_models_facade_reexports_canonical_helpers() -> None:
    assert models.RESEARCH_SCHEMA_VERSION == 1
    assert models.FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES == ("SubjectEvidenceLink",)
    assert models.canonicalize_research_json_object('{"b":2,"a":1}') == '{"a":1,"b":2}'
