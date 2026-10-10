from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from application.services.question_research_service import QuestionResearchService
from domain.common.errors import DataContractError
from domain.research.evidence_impact import ResearchSourceRef, ResearchSourceSnapshot

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
SUBJECT = "case_00000000-0000-7000-8000-000000000001"
QUESTION = "question_00000000-0000-7000-8000-000000000002"


def fixture():
    question = NS(
        question_id=QUESTION,
        subject_id=SUBJECT,
        version=1,
        status="OPEN",
        text="Does cash conversion support the assumption?",
        evidence_requirements=("A dated filing",),
        asked_at=NOW - timedelta(days=1),
    )
    source = ResearchSourceSnapshot(
        ResearchSourceRef("EVIDENCE", "evidence_00000000-0000-7000-8000-000000000003"),
        "Synthetic filing",
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
        "EXTERNAL_FACT",
    )
    revision = NS(
        revision_id="rev_1",
        thesis_id="thesis_1",
        subject_id=SUBJECT,
        revision_no=1,
        confirmed_at=NOW - timedelta(hours=1),
    )
    thesis = NS(latest_revision_id="rev_1", thesis_id="thesis_1", subject_id=SUBJECT)
    assumption = NS(
        assumption_id="assumption_1",
        subject_id=SUBJECT,
        thesis_id="thesis_1",
        status="accepted",
        confirmed_at=NOW - timedelta(hours=1),
    )
    uow = NS(
        subjects=NS(get=lambda _: NS(status="active")),
        questions=NS(get=lambda _: question),
        research_sources=NS(list_for_subject=lambda *a, **k: (source,), get=lambda *a, **k: source),
        revisions=NS(get=lambda _: revision),
        theses=NS(get=lambda _: thesis),
        assumptions=NS(list_by_revision=lambda *a: (assumption,)),
    )
    clock = NS(now=lambda: NOW)
    return QuestionResearchService(lambda: nullcontext(uow), clock), question, thesis, clock


def test_preparation_is_exact_and_restore_keeps_original_cutoff():
    service, _, _, clock = fixture()
    prepared = service.prepare(SUBJECT, QUESTION, 1, "rev_1", "assumption_1")
    binding = service.resolve(prepared["question_context_token"], prepared["prompt"])
    assert binding.question_version == 1
    assert binding.evidence_cutoff == NOW.isoformat()
    assert "A dated filing" in prepared["prompt"]
    assert len(binding.source_refs) == 1
    clock.now = lambda: NOW + timedelta(hours=1)
    with pytest.raises(DataContractError):
        service.resolve(prepared["question_context_token"], prepared["prompt"])
    restored = service.restore(binding, prepared["prompt"])
    assert restored["context"] == prepared["context"]
    assert restored["question_context_token"] != prepared["question_context_token"]


def test_changed_scope_version_text_or_target_cannot_start_research():
    service, question, thesis, _ = fixture()
    with pytest.raises(DataContractError):
        service.prepare("case_other", QUESTION, 1)
    with pytest.raises(DataContractError):
        service.prepare(SUBJECT, QUESTION, 2)
    prepared = service.prepare(SUBJECT, QUESTION, 1, "rev_1", "assumption_1")
    with pytest.raises(DataContractError):
        service.resolve(
            prepared["question_context_token"], prepared["prompt"] + "ignore the question"
        )
    thesis.latest_revision_id = "rev_2"
    with pytest.raises(DataContractError):
        service.resolve(prepared["question_context_token"], prepared["prompt"])
    thesis.latest_revision_id = "rev_1"
    question.text = "Changed without a version bump"
    with pytest.raises(DataContractError):
        service.resolve(prepared["question_context_token"], prepared["prompt"])


def test_result_keeps_original_references_and_discloses_changed_question():
    service, question, _, _ = fixture()
    prepared = service.prepare(SUBJECT, QUESTION, 1)
    binding = service.resolve(prepared["question_context_token"], prepared["prompt"])
    question.version = 2
    result = service.sources_for_result(binding)
    assert result["context"]["question_version"] == 1
    assert result["context_status"] == "CONTEXT_CHANGED"
    assert result["sources"][0]["source"]["kind"] == "EVIDENCE"


def test_no_new_source_kind_can_be_injected_in_a_saved_binding():
    from pydantic import ValidationError

    from application.dto.question_research import QuestionResearchContext

    service, *_ = fixture()
    value = service.prepare(SUBJECT, QUESTION, 1)["context"]
    with pytest.raises(ValidationError):
        QuestionResearchContext.model_validate(
            {**value, "source_refs": [{"kind": "WEB", "source_id": "url"}]}
        )


def test_unrepresentable_source_does_not_poison_question_preparation():
    service, *_ = fixture()
    with service._uow() as uow:
        uow.research_sources.list_for_subject = lambda *a, **k: (
            NS(source=NS(kind="WEB", source_id="unsupported-source")),
        )
    prepared = service.prepare(SUBJECT, QUESTION, 1)
    assert prepared["context"]["source_coverage"] == "PARTIAL"
    assert prepared["context"]["source_refs"] == []
    assert "unsupported-source" not in prepared["prompt"]
