"""Phase 1C C2b: repository port surfaces, append-only guards, UoW properties."""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from application.ports.decision_record_repository import DecisionRecordRepository
from application.ports.evidence_assessment_repository import (
    EvidenceAssessmentRepository,
)
from application.ports.evidence_repository import EvidenceRepository
from application.ports.journal_repository import JournalRepository
from application.ports.research_event_repository import ResearchEventRepository
from application.ports.research_report_repository import ResearchReportRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.subject_evidence_link_repository import SubjectEvidenceLinkRepository
from infrastructure.persistence.repositories.decision_record import (
    SqlAlchemyDecisionRecordRepository,
)
from infrastructure.persistence.repositories.evidence import (
    SqlAlchemyEvidenceRepository,
)
from infrastructure.persistence.repositories.evidence_assessment import (
    SqlAlchemyEvidenceAssessmentRepository,
)
from infrastructure.persistence.repositories.journal import (
    SqlAlchemyJournalRepository,
)
from infrastructure.persistence.repositories.research_event import (
    SqlAlchemyResearchEventRepository,
)
from infrastructure.persistence.repositories.research_report import (
    SqlAlchemyResearchReportRepository,
)
from infrastructure.persistence.repositories.subject_evidence_link import (
    SqlAlchemySubjectEvidenceLinkRepository,
)
from infrastructure.persistence.repositories.thesis_revision import (
    SqlAlchemyThesisRevisionRepository,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork


def _public_methods(cls: type) -> set[str]:
    methods: set[str] = set()
    for name, member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if inspect.isfunction(member) or inspect.ismethod(member) or callable(member):
            methods.add(name)
    return methods


_FORBIDDEN_MUTATORS = frozenset({"update", "delete", "upsert", "remove", "save", "patch", "merge"})

_PORT_IMPL: list[tuple[type, type, frozenset[str]]] = [
    (
        EvidenceRepository,
        SqlAlchemyEvidenceRepository,
        frozenset({"add", "get", "get_by_content_sha256"}),
    ),
    (
        SubjectEvidenceLinkRepository,
        SqlAlchemySubjectEvidenceLinkRepository,
        frozenset({"add", "get", "exists", "list_evidence", "list_subjects"}),
    ),
    (
        EvidenceAssessmentRepository,
        SqlAlchemyEvidenceAssessmentRepository,
        frozenset({"add", "list_for_evidence", "list_for_thesis"}),
    ),
    (
        ResearchReportRepository,
        SqlAlchemyResearchReportRepository,
        frozenset({"add", "get", "get_by_content_sha256", "list_by_subject"}),
    ),
    (
        ResearchEventRepository,
        SqlAlchemyResearchEventRepository,
        frozenset({"add", "get", "list_timeline"}),
    ),
    (
        DecisionRecordRepository,
        SqlAlchemyDecisionRecordRepository,
        frozenset({"add", "get", "get_by_idempotency_key", "list_by_subject"}),
    ),
    (
        JournalRepository,
        SqlAlchemyJournalRepository,
        frozenset({"add", "get", "get_by_idempotency_key", "list"}),
    ),
]


@pytest.mark.parametrize(
    ("port", "impl", "required"),
    _PORT_IMPL,
    ids=[p[1].__name__ for p in _PORT_IMPL],
)
def test_port_and_impl_public_surface(port: type, impl: type, required: frozenset[str]) -> None:
    port_methods = {
        name
        for name, _ in inspect.getmembers(port)
        if not name.startswith("_") and callable(getattr(port, name, None))
    }
    # Protocol methods may appear via __protocol_attrs__ on 3.13; fall back to annotations
    if not required.issubset(port_methods):
        port_methods |= set(getattr(port, "__protocol_attrs__", set()))
    assert required.issubset(port_methods), (port.__name__, port_methods)

    impl_methods = _public_methods(impl)
    assert required.issubset(impl_methods), (impl.__name__, impl_methods)
    assert not (impl_methods & _FORBIDDEN_MUTATORS), (
        f"{impl.__name__} exposes mutators: {impl_methods & _FORBIDDEN_MUTATORS}"
    )


def test_decision_and_journal_add_require_idempotency_kwargs() -> None:
    decision_sig = inspect.signature(SqlAlchemyDecisionRecordRepository.add)
    journal_sig = inspect.signature(SqlAlchemyJournalRepository.add)
    for sig, name in ((decision_sig, "decision"), (journal_sig, "journal")):
        params = sig.parameters
        assert "idempotency_key" in params, name
        assert "idempotency_payload_sha256" in params, name
        assert params["idempotency_key"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["idempotency_payload_sha256"].kind is inspect.Parameter.KEYWORD_ONLY


def test_report_port_includes_get_by_content_sha256() -> None:
    sig = inspect.signature(ResearchReportRepository.get_by_content_sha256)
    assert "content_sha256" in sig.parameters
    hints = get_type_hints(ResearchReportRepository.get_by_content_sha256)
    assert "return" in hints


def test_phase1b_thesis_revision_surface_unchanged() -> None:
    methods = _public_methods(SqlAlchemyThesisRevisionRepository)
    assert "append" in methods
    assert "get" in methods
    assert "update" not in methods
    assert "delete" not in methods


def test_uow_protocol_has_phase1c_business_and_search_index_properties() -> None:
    expected = {
        "evidence",
        "subject_evidence_links",
        "evidence_assessments",
        "reports",
        "events",
        "decisions",
        "journal",
        "search_index",
    }
    for name in expected:
        assert hasattr(ResearchUnitOfWork, name), name
        assert hasattr(SqlAlchemyResearchUnitOfWork, name), name


def test_uow_keeps_phase1b_properties() -> None:
    for name in (
        "subjects",
        "theses",
        "revisions",
        "assumptions",
        "invalidations",
        "questions",
        "watchlist",
        "candidates",
        "audit",
    ):
        assert hasattr(ResearchUnitOfWork, name)
        assert hasattr(SqlAlchemyResearchUnitOfWork, name)


def test_research_search_index_modules_exist_after_c3() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert (root / "src/application/ports/research_search_index.py").is_file()
    assert (root / "src/infrastructure/persistence/repositories/research_search_index.py").is_file()
