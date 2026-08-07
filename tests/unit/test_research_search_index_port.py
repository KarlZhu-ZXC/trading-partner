"""Unit tests for Phase 1C C3 ResearchSearchIndex port surface and projection helpers."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import get_type_hints

from application.dto.research_memory import ResearchSearchPageDTO, ResearchSearchQuery
from application.ports.research_search_index import ResearchSearchIndex
from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSearchEntityType,
)
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    DecisionRecord,
    Evidence,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    SubjectEvidenceLink,
    compute_evidence_content_sha256,
    compute_report_content_sha256,
)
from infrastructure.persistence.repositories._research_search_projection import (
    compose_body_fts,
    instrument_ids_text,
    project_decision,
    project_event,
    project_evidence,
    project_journal,
    project_report,
    stable_instrument_union,
)
from infrastructure.persistence.repositories.research_search_index import (
    SqlAlchemyResearchSearchIndex,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"


def _public_methods(cls: type) -> set[str]:
    methods: set[str] = set()
    for name, member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if inspect.isfunction(member) or inspect.ismethod(member) or callable(member):
            methods.add(name)
    return methods


def test_port_and_impl_public_surface() -> None:
    required = frozenset({"index", "refresh_evidence_membership", "search", "rebuild", "probe"})
    port_methods = {
        name
        for name, _ in inspect.getmembers(ResearchSearchIndex)
        if not name.startswith("_") and callable(getattr(ResearchSearchIndex, name, None))
    }
    port_methods |= set(getattr(ResearchSearchIndex, "__protocol_attrs__", set()))
    assert required.issubset(port_methods)

    impl_methods = _public_methods(SqlAlchemyResearchSearchIndex)
    assert required.issubset(impl_methods)
    assert "update" not in impl_methods
    assert "delete" not in impl_methods
    assert "add" not in impl_methods  # C3 uses index(), not §7.2 draft add()


def test_search_return_type_is_page_dto() -> None:
    hints = get_type_hints(ResearchSearchIndex.search)
    assert hints["return"] is ResearchSearchPageDTO
    assert hints["query"] is ResearchSearchQuery


def test_uow_exposes_search_index() -> None:
    assert hasattr(ResearchUnitOfWork, "search_index")
    assert hasattr(SqlAlchemyResearchUnitOfWork, "search_index")


def test_stable_instrument_union_preserves_order() -> None:
    assert stable_instrument_union((A_SHARE, US), (US, "equity:US:AAPL"), (A_SHARE,)) == (
        A_SHARE,
        US,
        "equity:US:AAPL",
    )


def test_instrument_ids_text_space_join() -> None:
    assert instrument_ids_text((A_SHARE, US)) == f"{A_SHARE} {US}"


def test_compose_body_fts_truncates_to_200k() -> None:
    body = "字" * 250_000
    out = compose_body_fts(summary="摘要", body_content=body)
    assert len(out) == 200_000


def test_project_evidence_fields() -> None:
    content_sha = compute_evidence_content_sha256(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="贵州茅台发布业绩预告",
        summary="预告摘要",
        content_text="正文内容",
        structured_data_json=None,
        source_name="mock_a_share",
        source_vendor="mock_a_share",
        source_record_id=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE,),
    )
    evidence = Evidence(
        evidence_id="evidence_00000000-0000-7000-8000-000000000001",
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="贵州茅台发布业绩预告",
        summary="预告摘要",
        content_text="正文内容",
        structured_data_json=None,
        source_name="mock_a_share",
        source_vendor="mock_a_share",
        source_record_id=None,
        source_url=None,
        published_at=EARLIER,
        observed_at=NOW,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE,),
        topic_tags=("a-share", "liquor"),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=None,
        content_sha256=content_sha,
        supersedes_evidence_id=None,
        recorded_by="provider:mock_a_share",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    link = SubjectEvidenceLink(
        link_id="rev_00000000-0000-7000-8000-000000000002",
        subject_id="case_00000000-0000-7000-8000-000000000003",
        evidence_id=evidence.evidence_id,
        linked_at=NOW,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    proj = project_evidence(evidence, links=(link,))
    assert proj.entity_type is ResearchSearchEntityType.EVIDENCE
    assert proj.visible_at == NOW
    assert proj.occurred_at == EARLIER
    assert "茅" in proj.title_fts
    assert proj.case_memberships[0].membership_visible_at == NOW
    assert proj.topic_tags == ("a-share", "liquor")
    assert proj.instrument_ids == (A_SHARE,)


def test_project_report_uses_referenced_instruments_not_case_primary() -> None:
    subject_id = "case_00000000-0000-7000-8000-000000000010"
    content_sha = compute_report_content_sha256(
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Review",
        summary="Summary",
        content_markdown="# body",
        as_of=EARLIER,
        evidence_ids=(),
        thesis_revision_ids=(),
    )
    report = ResearchReport(
        report_id="report_00000000-0000-7000-8000-000000000011",
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Review",
        summary="Summary",
        content_markdown="# body",
        as_of=EARLIER,
        created_at=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        content_sha256=content_sha,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    proj = project_report(report, referenced_instrument_ids=(US, A_SHARE))
    assert proj.instrument_ids == (US, A_SHARE)
    assert proj.visible_at == NOW
    assert proj.occurred_at == EARLIER
    assert proj.topic_tags == ()


def test_project_event_summary_once() -> None:
    event = ResearchEvent(
        event_id="event_00000000-0000-7000-8000-000000000020",
        subject_id="case_00000000-0000-7000-8000-000000000021",
        event_type=ResearchEventType.COMPANY,
        title="Earnings",
        summary="Beat expectations",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(US,),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="news",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    proj = project_event(event)
    assert proj.body_fts.count("Beat") == 1
    assert proj.supersedes_entity_id is None


def test_project_decision_union_primary_then_evidence_instruments() -> None:
    decision = DecisionRecord(
        decision_id="decision_00000000-0000-7000-8000-000000000030",
        subject_id="case_00000000-0000-7000-8000-000000000031",
        decision_type=DecisionType.WATCH,
        title="Watch NVDA",
        rationale="Need more data",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=US,
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    proj = project_decision(decision, referenced_instrument_ids=(A_SHARE, US))
    assert proj.instrument_ids == (US, A_SHARE)
    assert "Need more data" in proj.body_fts or "Need" in proj.body_fts


def test_decision_thesis_as_of_predicate_exactly_once_in_source() -> None:
    """Decision thesis_id EXISTS must apply tr.confirmed_at <= :as_of exactly once.

    v1.12 Report/Decision share visible revision intersection under as_of. The
    original defect was duplicating the predicate on Decision; removing both
    is also wrong. Count interpolations of as_of_rev plus any hardcoded
    confirmed_at clause inside each branch.
    """
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src/infrastructure/persistence/repositories/research_search_index.py"
    )
    text = path.read_text(encoding="utf-8")
    marker = 'as_of_rev = " AND tr.confirmed_at <= :as_of" if query.as_of else ""'
    assert text.count(marker) == 1
    start = text.index(marker)
    thesis_block = text[start : text.index("if query.stances:", start)]

    report_seg = thesis_block[
        thesis_block.index("d.entity_type = 'report' AND EXISTS (") : thesis_block.index(
            "d.entity_type = 'decision' AND EXISTS ("
        )
    ]
    decision_seg = thesis_block[thesis_block.index("d.entity_type = 'decision' AND EXISTS (") :]

    def _as_of_predicate_count(segment: str) -> int:
        return segment.count("{as_of_rev}") + segment.count("tr.confirmed_at <= :as_of")

    assert _as_of_predicate_count(report_seg) == 1, report_seg
    assert _as_of_predicate_count(decision_seg) == 1, decision_seg


def test_project_journal_membership_only_when_case_present() -> None:
    with_case = JournalEntry(
        journal_id="journal_00000000-0000-7000-8000-000000000040",
        subject_id="case_00000000-0000-7000-8000-000000000041",
        entry_type=JournalEntryType.NOTE,
        title="Note",
        body_markdown="body text",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(US,),
        topic_tags=("memo",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    without = JournalEntry(
        journal_id="journal_00000000-0000-7000-8000-000000000042",
        subject_id=None,
        entry_type=JournalEntryType.NOTE,
        title="Free note",
        body_markdown="orphan",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(),
        topic_tags=(),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    assert len(project_journal(with_case).case_memberships) == 1
    assert project_journal(without).case_memberships == ()
