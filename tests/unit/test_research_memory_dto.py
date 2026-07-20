"""Phase 1C C1b research-memory DTO / search query / page / timeline contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from application.dto.research_memory import (
    CaseEvidenceLinkDTO,
    DecisionRecordDTO,
    EvidenceAssessmentDTO,
    EvidenceDTO,
    JournalEntryDTO,
    JournalSearchPageDTO,
    ResearchEventDTO,
    ResearchReportDTO,
    ResearchSearchHitDTO,
    ResearchSearchPageDTO,
    ResearchSearchQuery,
    ResearchTimelineDTO,
    ResearchTimelineItemDTO,
)
from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSearchEntityType,
    ResearchTimelineEntityType,
)
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    CaseEvidenceLink,
    DecisionRecord,
    Evidence,
    EvidenceAssessment,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    compute_evidence_content_sha256,
    compute_report_content_sha256,
)

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
LATER = NOW + timedelta(hours=1)
NAIVE = datetime(2026, 7, 16, 12, 0, 0)

CASE_ID = "case_00000000-0000-7000-8000-000000000001"
EVIDENCE_ID = "evidence_00000000-0000-7000-8000-000000000001"
EVIDENCE_ID_2 = "evidence_00000000-0000-7000-8000-000000000002"
REV_ID = "rev_00000000-0000-7000-8000-000000000001"
REV_ID_2 = "rev_00000000-0000-7000-8000-000000000002"
THESIS_ID = "thesis_00000000-0000-7000-8000-000000000001"
REPORT_ID = "report_00000000-0000-7000-8000-000000000001"
EVENT_ID = "event_00000000-0000-7000-8000-000000000001"
DECISION_ID = "decision_00000000-0000-7000-8000-000000000001"
JOURNAL_ID = "journal_00000000-0000-7000-8000-000000000001"
RUN_ID = "run_00000000-0000-7000-8000-000000000001"
SNAPSHOT_ID = "snapshot_00000000-0000-7000-8000-000000000001"
A_SHARE_INSTRUMENT = "equity:A_SHARE:600519.SH"
US_INSTRUMENT = "equity:US:NVDA"


def _evidence_hash(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "evidence_type": EvidenceType.MARKET_SNAPSHOT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "Moutai quote",
        "summary": "Close snapshot",
        "content_text": None,
        "structured_data_json": None,
        "source_name": "mock_a_share",
        "source_vendor": "mock_a_share",
        "source_record_id": None,
        "published_at": EARLIER,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (A_SHARE_INSTRUMENT,),
    }
    base.update(overrides)
    return compute_evidence_content_sha256(**base)


def _evidence(**overrides: Any) -> Evidence:
    base: dict[str, Any] = {
        "evidence_id": EVIDENCE_ID,
        "evidence_type": EvidenceType.MARKET_SNAPSHOT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "Moutai quote",
        "summary": "Close snapshot",
        "content_text": None,
        "structured_data_json": None,
        "source_name": "mock_a_share",
        "source_vendor": "mock_a_share",
        "source_record_id": None,
        "source_url": None,
        "published_at": EARLIER,
        "observed_at": NOW,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (A_SHARE_INSTRUMENT,),
        "topic_tags": ("a-share", "liquor"),
        "quality": EvidenceQuality.PRIMARY,
        "reliability": ReliabilityLevel.HIGH,
        "confidence": Decimal("0.9"),
        "content_sha256": "",
        "supersedes_evidence_id": None,
        "recorded_by": "provider:mock_a_share",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    if "content_sha256" not in overrides or base["content_sha256"] == "":
        base["content_sha256"] = _evidence_hash(
            evidence_type=base["evidence_type"],
            origin=base["origin"],
            title=base["title"],
            summary=base["summary"],
            content_text=base["content_text"],
            structured_data_json=base["structured_data_json"],
            source_name=base["source_name"],
            source_vendor=base["source_vendor"],
            source_record_id=base["source_record_id"],
            published_at=base["published_at"],
            effective_from=base["effective_from"],
            effective_to=base["effective_to"],
            instrument_ids=base["instrument_ids"],
        )
    return Evidence(**base)


def _report_hash(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "case_id": CASE_ID,
        "report_type": ResearchReportType.DEEP_DIVE,
        "title": "NVDA thesis review",
        "summary": "Structural demand intact",
        "content_markdown": "# Review\nDetails",
        "as_of": EARLIER,
        "evidence_ids": (EVIDENCE_ID,),
        "thesis_revision_ids": (REV_ID,),
    }
    base.update(overrides)
    return compute_report_content_sha256(**base)


def _report(**overrides: Any) -> ResearchReport:
    base: dict[str, Any] = {
        "report_id": REPORT_ID,
        "case_id": CASE_ID,
        "report_type": ResearchReportType.DEEP_DIVE,
        "title": "NVDA thesis review",
        "summary": "Structural demand intact",
        "content_markdown": "# Review\nDetails",
        "as_of": EARLIER,
        "created_at": NOW,
        "created_by": "codex",
        "research_run_id": None,
        "evidence_ids": (EVIDENCE_ID,),
        "thesis_revision_ids": (REV_ID,),
        "supersedes_report_id": None,
        "content_sha256": "",
        "model_name": None,
        "prompt_version": None,
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    if "content_sha256" not in overrides or base["content_sha256"] == "":
        base["content_sha256"] = _report_hash(
            case_id=base["case_id"],
            report_type=base["report_type"],
            title=base["title"],
            summary=base["summary"],
            content_markdown=base["content_markdown"],
            as_of=base["as_of"],
            evidence_ids=base["evidence_ids"],
            thesis_revision_ids=base["thesis_revision_ids"],
        )
    return ResearchReport(**base)


def _assessment(**overrides: Any) -> EvidenceAssessment:
    base: dict[str, Any] = {
        "assessment_id": REV_ID,
        "evidence_id": EVIDENCE_ID,
        "case_id": CASE_ID,
        "thesis_id": THESIS_ID,
        "thesis_revision_id": REV_ID_2,
        "stance": EvidenceStance.SUPPORTS,
        "materiality": Decimal("0.75"),
        "rationale": "Confirms demand narrative",
        "assessed_at": NOW,
        "assessed_by": "codex",
        "confirmed_by": "user",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return EvidenceAssessment(**base)


def _link(**overrides: Any) -> CaseEvidenceLink:
    base: dict[str, Any] = {
        "link_id": REV_ID,
        "case_id": CASE_ID,
        "evidence_id": EVIDENCE_ID,
        "linked_at": NOW,
        "linked_by": "user",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return CaseEvidenceLink(**base)


def _event(**overrides: Any) -> ResearchEvent:
    base: dict[str, Any] = {
        "event_id": EVENT_ID,
        "case_id": CASE_ID,
        "event_type": ResearchEventType.EARNINGS,
        "title": "Q2 earnings",
        "summary": "Beat consensus",
        "occurred_at": EARLIER,
        "recorded_at": NOW,
        "published_at": EARLIER,
        "instrument_ids": (US_INSTRUMENT,),
        "evidence_ids": (EVIDENCE_ID,),
        "report_ids": (REPORT_ID,),
        "related_entity_type": None,
        "related_entity_id": None,
        "source_name": "sec_edgar",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return ResearchEvent(**base)


def _decision(**overrides: Any) -> DecisionRecord:
    base: dict[str, Any] = {
        "decision_id": DECISION_ID,
        "case_id": CASE_ID,
        "decision_type": DecisionType.WATCH,
        "title": "Keep watching",
        "rationale": "Need more evidence",
        "decided_at": EARLIER,
        "recorded_at": NOW,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "primary_instrument_id": US_INSTRUMENT,
        "thesis_revision_ids": (REV_ID,),
        "evidence_ids": (EVIDENCE_ID,),
        "report_ids": (REPORT_ID,),
        "supersedes_decision_id": None,
        "position_context_snapshot_id": None,
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return DecisionRecord(**base)


def _journal(**overrides: Any) -> JournalEntry:
    base: dict[str, Any] = {
        "journal_id": JOURNAL_ID,
        "case_id": CASE_ID,
        "entry_type": JournalEntryType.NOTE,
        "title": "Personal note",
        "body_markdown": "Watching margin trends",
        "created_at": NOW,
        "authored_by": "codex",
        "confirmed_by": "user",
        "instrument_ids": (US_INSTRUMENT,),
        "topic_tags": ("margin",),
        "related_entity_type": None,
        "related_entity_id": None,
        "supersedes_journal_id": None,
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return JournalEntry(**base)


def _evidence_hit(**overrides: Any) -> ResearchSearchHitDTO:
    base: dict[str, Any] = {
        "entity_type": ResearchSearchEntityType.EVIDENCE,
        "entity_id": EVIDENCE_ID,
        "case_id": CASE_ID,
        "title": "Moutai quote",
        "snippet": "Close snapshot",
        "visible_at": NOW,
        "occurred_at": EARLIER,
        "instrument_ids": (A_SHARE_INSTRUMENT,),
        "topic_tags": ("a-share",),
        "matched_stances": (EvidenceStance.SUPPORTS,),
        "matched_assessment_ids": (REV_ID,),
        "score": Decimal("1.25"),
        "source_name": "mock_a_share",
    }
    base.update(overrides)
    return ResearchSearchHitDTO(**base)


def _timeline_item(**overrides: Any) -> ResearchTimelineItemDTO:
    base: dict[str, Any] = {
        "entity_type": ResearchTimelineEntityType.EVIDENCE,
        "entity_id": EVIDENCE_ID,
        "case_id": CASE_ID,
        "title": "Moutai quote",
        "summary": "Close snapshot",
        "occurred_at": NOW,
        "visible_at": NOW,
        "instrument_ids": (A_SHARE_INSTRUMENT,),
        "source_name": "mock_a_share",
    }
    base.update(overrides)
    return ResearchTimelineItemDTO(**base)


def _rebuild(dto: Any, **updates: Any) -> Any:
    """Re-validate a DTO after field updates (model_copy skips validation)."""
    data = dto.model_dump()
    data.update(updates)
    return type(dto).model_validate(data)


# --- Enum wire values ---


def test_research_search_entity_type_wire_values() -> None:
    assert [m.value for m in ResearchSearchEntityType] == [
        "evidence",
        "report",
        "event",
        "decision",
        "journal",
    ]


def test_research_timeline_entity_type_wire_values() -> None:
    assert [m.value for m in ResearchTimelineEntityType] == [
        "evidence",
        "report",
        "event",
        "decision",
        "journal",
        "thesis_revision",
        "candidate_resolution",
    ]


# --- EvidenceDTO ---


def test_evidence_dto_happy_path_and_from_domain() -> None:
    domain = _evidence(instrument_ids=(A_SHARE_INSTRUMENT, US_INSTRUMENT))
    dto = EvidenceDTO.from_domain(domain)
    assert dto.evidence_id == EVIDENCE_ID
    assert dto.evidence_type == EvidenceType.MARKET_SNAPSHOT.value
    assert dto.instrument_ids == (A_SHARE_INSTRUMENT, US_INSTRUMENT)
    assert dto.confidence == Decimal("0.9")
    assert dto.model_dump(mode="json")["evidence_type"] == "market_snapshot"
    assert dto.model_dump(mode="json")["origin"] == "external_fact"


def test_evidence_dto_extra_and_frozen() -> None:
    dto = EvidenceDTO.from_domain(_evidence())
    with pytest.raises(ValidationError):
        EvidenceDTO.model_validate({**dto.model_dump(), "extra_field": 1})
    with pytest.raises(ValidationError):
        dto.title = "mutated"  # type: ignore[misc]


def test_evidence_dto_prefix_duplicate_and_aware_time_rules() -> None:
    dto = EvidenceDTO.from_domain(_evidence())
    with pytest.raises(ValidationError, match="evidence_id"):
        _rebuild(dto, evidence_id="ev_00000000-0000-7000-8000-000000000001")
    with pytest.raises(ValidationError):
        _rebuild(dto, observed_at=NAIVE)
    with pytest.raises(ValidationError, match="duplicate"):
        _rebuild(dto, instrument_ids=(A_SHARE_INSTRUMENT, A_SHARE_INSTRUMENT))


# --- CaseEvidenceLinkDTO ---


def test_case_evidence_link_dto_happy_and_from_domain() -> None:
    dto = CaseEvidenceLinkDTO.from_domain(_link())
    assert dto.link_id == REV_ID
    assert dto.case_id == CASE_ID
    assert dto.model_dump(mode="json")["linked_at"].startswith("2026-07-16")


# --- EvidenceAssessmentDTO ---


def test_evidence_assessment_dto_happy_and_from_domain() -> None:
    dto = EvidenceAssessmentDTO.from_domain(_assessment())
    assert dto.stance == EvidenceStance.SUPPORTS.value
    assert dto.materiality == Decimal("0.75")
    assert dto.model_dump(mode="json")["stance"] == "supports"


def test_evidence_assessment_dto_rejects_float_materiality() -> None:
    dto = EvidenceAssessmentDTO.from_domain(_assessment())
    with pytest.raises(ValidationError, match="materiality"):
        _rebuild(dto, materiality=0.5)


# --- ResearchReportDTO ---


def test_research_report_dto_happy_and_from_domain() -> None:
    domain = _report(research_run_id=RUN_ID)
    dto = ResearchReportDTO.from_domain(domain)
    assert dto.report_id == REPORT_ID
    assert dto.research_run_id == RUN_ID
    assert dto.model_dump(mode="json")["report_type"] == "deep_dive"


# --- ResearchEventDTO ---


def test_research_event_dto_happy_and_from_domain() -> None:
    dto = ResearchEventDTO.from_domain(_event())
    assert dto.event_type == ResearchEventType.EARNINGS.value
    assert dto.instrument_ids == (US_INSTRUMENT,)
    assert dto.model_dump(mode="json")["event_type"] == "earnings"


# --- DecisionRecordDTO ---


def test_decision_record_dto_happy_from_domain_and_execution_effect() -> None:
    dto = DecisionRecordDTO.from_domain(_decision())
    assert dto.execution_effect is False
    assert dto.model_dump()["execution_effect"] is False
    assert dto.model_dump(mode="json")["decision_type"] == "watch"
    assert dto.model_dump(mode="json")["confirmation_mode"] == "normal"
    with pytest.raises(ValidationError):
        _rebuild(dto, execution_effect=True)


def test_decision_record_dto_accepts_a_share_and_us_instruments() -> None:
    a = DecisionRecordDTO.from_domain(_decision(primary_instrument_id=A_SHARE_INSTRUMENT))
    u = DecisionRecordDTO.from_domain(_decision(primary_instrument_id=US_INSTRUMENT))
    assert a.primary_instrument_id == A_SHARE_INSTRUMENT
    assert u.primary_instrument_id == US_INSTRUMENT


# --- JournalEntryDTO ---


def test_journal_entry_dto_happy_and_from_domain() -> None:
    dto = JournalEntryDTO.from_domain(_journal())
    assert dto.entry_type == JournalEntryType.NOTE.value
    assert dto.model_dump(mode="json")["entry_type"] == "note"


def test_journal_entry_dto_optional_case_id() -> None:
    dto = JournalEntryDTO.from_domain(_journal(case_id=None))
    assert dto.case_id is None


def test_all_seven_entity_dtos_reject_prefixes_time_duplicate_and_schema_version() -> None:
    evidence = EvidenceDTO.from_domain(_evidence())
    link = CaseEvidenceLinkDTO.from_domain(_link())
    assessment = EvidenceAssessmentDTO.from_domain(_assessment())
    report = ResearchReportDTO.from_domain(_report())
    event = ResearchEventDTO.from_domain(_event())
    decision = DecisionRecordDTO.from_domain(_decision(position_context_snapshot_id=SNAPSHOT_ID))
    journal = JournalEntryDTO.from_domain(_journal())

    assert decision.position_context_snapshot_id == SNAPSHOT_ID

    prefix_checks: list[tuple[Any, str, str, str]] = [
        (evidence, "evidence_id", "ev_00000000-0000-7000-8000-000000000001", "evidence_id"),
        (
            link,
            "link_id",
            "journal_00000000-0000-7000-8000-000000000001",
            "link_id",
        ),
        (
            assessment,
            "assessment_id",
            "assessment_00000000-0000-7000-8000-000000000001",
            "assessment_id",
        ),
        (report, "report_id", "case_00000000-0000-7000-8000-000000000001", "report_id"),
        (
            event,
            "event_id",
            "decision_00000000-0000-7000-8000-000000000001",
            "event_id",
        ),
        (
            decision,
            "decision_id",
            "report_00000000-0000-7000-8000-000000000001",
            "decision_id",
        ),
        (
            decision,
            "position_context_snapshot_id",
            "snap_00000000-0000-7000-8000-000000000001",
            "position_context_snapshot_id",
        ),
        (journal, "journal_id", "run_00000000-0000-7000-8000-000000000001", "journal_id"),
    ]
    for dto, field_name, bad_value, match_field in prefix_checks:
        with pytest.raises(ValidationError, match=match_field):
            _rebuild(dto, **{field_name: bad_value})

    aware_time_checks = [
        (evidence, "observed_at"),
        (link, "linked_at"),
        (assessment, "assessed_at"),
        (report, "created_at"),
        (event, "occurred_at"),
        (decision, "recorded_at"),
        (journal, "created_at"),
    ]
    for dto, field in aware_time_checks:
        with pytest.raises(ValidationError):
            _rebuild(dto, **{field: NAIVE})

    duplicate_checks = [
        (evidence, {"instrument_ids": (A_SHARE_INSTRUMENT, A_SHARE_INSTRUMENT)}),
        (report, {"evidence_ids": (EVIDENCE_ID, EVIDENCE_ID)}),
    ]
    for dto, updates in duplicate_checks:
        with pytest.raises(ValidationError, match="duplicate"):
            _rebuild(dto, **updates)

    all_dtos = (
        evidence,
        link,
        assessment,
        report,
        event,
        decision,
        journal,
    )
    for dto in all_dtos:
        with pytest.raises(ValidationError, match="schema_version"):
            _rebuild(dto, schema_version=True)
        with pytest.raises(ValidationError, match="schema_version"):
            _rebuild(dto, schema_version=False)
        for invalid_version in (0, 2, 99):
            with pytest.raises(ValidationError, match="schema_version"):
                _rebuild(dto, schema_version=invalid_version)
        ok = _rebuild(dto, schema_version=RESEARCH_SCHEMA_VERSION)
        assert ok.schema_version == RESEARCH_SCHEMA_VERSION


# --- ResearchSearchQuery ---


def test_search_query_happy_paths() -> None:
    q = ResearchSearchQuery(text="margin", limit=10, offset=0)
    assert q.text == "margin"
    assert q.limit == 10
    assert q.entity_types == ()

    by_case = ResearchSearchQuery(case_id=CASE_ID)
    assert by_case.case_id == CASE_ID

    by_instrument = ResearchSearchQuery(instrument_id=A_SHARE_INSTRUMENT)
    assert by_instrument.instrument_id == A_SHARE_INSTRUMENT

    by_us = ResearchSearchQuery(instrument_id=US_INSTRUMENT)
    assert by_us.instrument_id == US_INSTRUMENT


def test_search_query_blank_text_normalizes_to_none_and_needs_other_filter() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ResearchSearchQuery(text="   ")
    with pytest.raises(ValidationError, match="at least one"):
        ResearchSearchQuery(text="")
    q = ResearchSearchQuery(text="  ", case_id=CASE_ID)
    assert q.text is None
    q2 = ResearchSearchQuery(text="keep spaces  ", case_id=CASE_ID)
    assert q2.text == "keep spaces  "


def test_search_query_thesis_only_leaves_entity_types_empty() -> None:
    """Thesis-only default entity types remain a service concern (C1b)."""
    q = ResearchSearchQuery(thesis_id=THESIS_ID)
    assert q.thesis_id == THESIS_ID
    assert q.entity_types == ()


def test_search_query_stances_require_case_or_thesis() -> None:
    with pytest.raises(ValidationError, match="stances"):
        ResearchSearchQuery(stances=(EvidenceStance.CONTRADICTS,), text="x")
    ok_case = ResearchSearchQuery(case_id=CASE_ID, stances=(EvidenceStance.CONTRADICTS,))
    assert ok_case.stances == (EvidenceStance.CONTRADICTS.value,)
    ok_thesis = ResearchSearchQuery(thesis_id=THESIS_ID, stances=(EvidenceStance.SUPPORTS,))
    assert ok_thesis.stances == (EvidenceStance.SUPPORTS.value,)


def test_search_query_stances_reject_non_evidence_entity_types() -> None:
    with pytest.raises(ValidationError, match="entity_types"):
        ResearchSearchQuery(
            case_id=CASE_ID,
            stances=(EvidenceStance.SUPPORTS,),
            entity_types=(ResearchSearchEntityType.REPORT,),
        )
    with pytest.raises(ValidationError, match="entity_types"):
        ResearchSearchQuery(
            case_id=CASE_ID,
            stances=(EvidenceStance.SUPPORTS,),
            entity_types=(
                ResearchSearchEntityType.EVIDENCE,
                ResearchSearchEntityType.REPORT,
            ),
        )
    ok = ResearchSearchQuery(
        case_id=CASE_ID,
        stances=(EvidenceStance.SUPPORTS,),
        entity_types=(ResearchSearchEntityType.EVIDENCE,),
    )
    assert ok.entity_types == (ResearchSearchEntityType.EVIDENCE.value,)
    ok_empty = ResearchSearchQuery(case_id=CASE_ID, stances=(EvidenceStance.NEUTRAL,))
    assert ok_empty.entity_types == ()


def test_search_query_journal_entry_types_happy_and_standalone_filter() -> None:
    q = ResearchSearchQuery(
        journal_entry_types=(JournalEntryType.NOTE, JournalEntryType.POSTMORTEM)
    )
    assert q.journal_entry_types == (
        JournalEntryType.NOTE.value,
        JournalEntryType.POSTMORTEM.value,
    )
    assert q.entity_types == ()
    with_journal_entity = ResearchSearchQuery(
        journal_entry_types=(JournalEntryType.REFLECTION,),
        entity_types=(ResearchSearchEntityType.JOURNAL,),
    )
    assert with_journal_entity.entity_types == (ResearchSearchEntityType.JOURNAL.value,)


def test_search_query_journal_entry_types_reject_invalid_filter_combinations() -> None:
    invalid_cases = [
        {
            "journal_entry_types": (JournalEntryType.NOTE,),
            "entity_types": (ResearchSearchEntityType.REPORT,),
        },
        {
            "journal_entry_types": (JournalEntryType.NOTE,),
            "entity_types": (
                ResearchSearchEntityType.JOURNAL,
                ResearchSearchEntityType.EVIDENCE,
            ),
        },
        {
            "journal_entry_types": (JournalEntryType.NOTE,),
            "evidence_types": (EvidenceType.SEC_FILING,),
        },
        {
            "journal_entry_types": (JournalEntryType.NOTE,),
            "case_id": CASE_ID,
            "stances": (EvidenceStance.SUPPORTS,),
        },
        {
            "journal_entry_types": (JournalEntryType.NOTE,),
            "thesis_id": THESIS_ID,
        },
    ]
    for payload in invalid_cases:
        with pytest.raises(ValidationError, match="journal_entry_types"):
            ResearchSearchQuery(**payload)


def test_search_query_rejects_duplicate_filter_tuples() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ResearchSearchQuery(
            case_id=CASE_ID,
            entity_types=(
                ResearchSearchEntityType.EVIDENCE,
                ResearchSearchEntityType.EVIDENCE,
            ),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        ResearchSearchQuery(topic_tags=("a", "a"), case_id=CASE_ID)
    with pytest.raises(ValidationError, match="duplicate"):
        ResearchSearchQuery(
            case_id=CASE_ID,
            stances=(EvidenceStance.SUPPORTS, EvidenceStance.SUPPORTS),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        ResearchSearchQuery(
            journal_entry_types=(JournalEntryType.NOTE, JournalEntryType.NOTE),
        )


def test_search_query_visible_range_and_aware_times() -> None:
    with pytest.raises(ValidationError, match="visible_to"):
        ResearchSearchQuery(visible_from=LATER, visible_to=EARLIER)
    ok = ResearchSearchQuery(visible_from=EARLIER, visible_to=LATER)
    assert ok.visible_from == EARLIER
    with pytest.raises(ValidationError):
        ResearchSearchQuery(as_of=NAIVE)
    with pytest.raises(ValidationError):
        ResearchSearchQuery(visible_from=NAIVE)


def test_search_query_limit_offset_and_id_prefixes() -> None:
    with pytest.raises(ValidationError):
        ResearchSearchQuery(case_id=CASE_ID, limit=0)
    with pytest.raises(ValidationError):
        ResearchSearchQuery(case_id=CASE_ID, limit=101)
    with pytest.raises(ValidationError):
        ResearchSearchQuery(case_id=CASE_ID, offset=-1)
    with pytest.raises(ValidationError, match="case_id"):
        ResearchSearchQuery(case_id="bad_case")
    with pytest.raises(ValidationError, match="thesis_id"):
        ResearchSearchQuery(thesis_id="thesis_not-uuid7")


def test_search_query_extra_and_frozen() -> None:
    q = ResearchSearchQuery(case_id=CASE_ID)
    with pytest.raises(ValidationError):
        ResearchSearchQuery.model_validate({**q.model_dump(), "extra": True})
    with pytest.raises(ValidationError):
        q.limit = 5  # type: ignore[misc]


def test_search_query_json_enum_wire_output() -> None:
    q = ResearchSearchQuery(
        case_id=CASE_ID,
        entity_types=(ResearchSearchEntityType.DECISION, ResearchSearchEntityType.EVENT),
        evidence_types=(EvidenceType.SEC_FILING,),
        journal_entry_types=(),
        stances=(),
    )
    dumped = q.model_dump(mode="json")
    assert dumped["entity_types"] == ["decision", "event"]
    assert dumped["evidence_types"] == ["sec_filing"]
    assert dumped["journal_entry_types"] == []
    q_journal = ResearchSearchQuery(
        journal_entry_types=(JournalEntryType.OBSERVATION, JournalEntryType.QUESTION)
    )
    dumped_j = q_journal.model_dump(mode="json")
    assert dumped_j["journal_entry_types"] == ["observation", "question"]


# --- ResearchSearchHitDTO ---


def test_search_hit_happy_evidence_and_non_evidence() -> None:
    hit = _evidence_hit()
    assert hit.score == Decimal("1.25")
    assert hit.matched_stances == (EvidenceStance.SUPPORTS.value,)
    report_hit = _evidence_hit(
        entity_type=ResearchSearchEntityType.REPORT,
        entity_id=REPORT_ID,
        matched_stances=(),
        matched_assessment_ids=(),
        score=None,
    )
    assert report_hit.entity_type == ResearchSearchEntityType.REPORT.value
    assert report_hit.model_dump(mode="json")["entity_type"] == "report"


def test_search_hit_rejects_non_evidence_stances() -> None:
    with pytest.raises(ValidationError, match="non-Evidence"):
        _evidence_hit(
            entity_type=ResearchSearchEntityType.REPORT,
            entity_id=REPORT_ID,
            matched_stances=(EvidenceStance.SUPPORTS,),
            matched_assessment_ids=(),
        )
    with pytest.raises(ValidationError, match="non-Evidence"):
        _evidence_hit(
            entity_type=ResearchSearchEntityType.JOURNAL,
            entity_id=JOURNAL_ID,
            matched_stances=(),
            matched_assessment_ids=(REV_ID,),
        )


def test_search_hit_stance_and_assessment_cardinality_and_duplicates() -> None:
    hit = _evidence_hit(
        matched_stances=(EvidenceStance.SUPPORTS, EvidenceStance.CONTRADICTS),
        matched_assessment_ids=(REV_ID,),
    )
    assert len(hit.matched_stances) == 2
    assert len(hit.matched_assessment_ids) == 1
    with pytest.raises(ValidationError, match="duplicate"):
        _evidence_hit(matched_stances=(EvidenceStance.SUPPORTS, EvidenceStance.SUPPORTS))
    with pytest.raises(ValidationError, match="duplicate"):
        _evidence_hit(matched_assessment_ids=(REV_ID, REV_ID))


def test_search_hit_score_must_be_finite_decimal() -> None:
    with pytest.raises(ValidationError, match="score"):
        _evidence_hit(score=1.25)
    with pytest.raises(ValidationError, match="score"):
        _evidence_hit(score=Decimal("NaN"))
    with pytest.raises(ValidationError, match="score"):
        _evidence_hit(score=Decimal("Infinity"))
    ok = _evidence_hit(score=Decimal("0"))
    assert ok.score == Decimal("0")


def test_search_hit_rejects_naive_times_and_bad_entity_id() -> None:
    with pytest.raises(ValidationError):
        _evidence_hit(visible_at=NAIVE)
    with pytest.raises(ValidationError):
        _evidence_hit(occurred_at=NAIVE)
    with pytest.raises(ValidationError, match="entity_id"):
        _evidence_hit(entity_id="report_00000000-0000-7000-8000-000000000001")


def test_search_hit_a_share_and_us_instruments_unique() -> None:
    hit = _evidence_hit(instrument_ids=(A_SHARE_INSTRUMENT, US_INSTRUMENT))
    assert hit.instrument_ids == (A_SHARE_INSTRUMENT, US_INSTRUMENT)
    with pytest.raises(ValidationError, match="duplicate"):
        _evidence_hit(instrument_ids=(US_INSTRUMENT, US_INSTRUMENT))


# --- ResearchSearchPageDTO ---


def test_search_page_pagination_invariants_matrix() -> None:
    items = (_evidence_hit(),)
    item_variation = _evidence_hit(entity_id=EVIDENCE_ID_2)

    valid_cases = [
        {"items": items, "total": 5, "limit": 1, "offset": 0, "has_more": True},
        {"items": items, "total": 1, "limit": 20, "offset": 0, "has_more": False},
        {"items": items, "total": 10, "limit": 1, "offset": 3, "has_more": True},
        {"items": items, "total": 4, "limit": 1, "offset": 3, "has_more": False},
        {"items": (), "total": 0, "limit": 20, "offset": 100, "has_more": False},
        {"items": (), "total": 50, "limit": 20, "offset": 100, "has_more": False},
    ]
    for params in valid_cases:
        page = ResearchSearchPageDTO(**params)
        assert page.has_more == (page.offset + len(page.items) < page.total)
        if params["offset"] > params["total"]:
            assert page.has_more is False

    invalid_cases = [
        {"items": items, "total": 5, "limit": 1, "offset": 0, "has_more": False},
        {"items": items, "total": 1, "limit": 20, "offset": 0, "has_more": True},
        {
            "items": (items[0], item_variation),
            "total": 10,
            "limit": 1,
            "offset": 0,
            "has_more": True,
            "match": "len\\(items\\)",
        },
        {
            "items": items,
            "total": 0,
            "limit": 20,
            "offset": 0,
            "has_more": False,
            "match": "offset \\+ len\\(items\\)",
        },
        {
            "items": items,
            "total": 5,
            "limit": 20,
            "offset": 5,
            "has_more": False,
            "match": "offset \\+ len\\(items\\)",
        },
        {
            "items": items,
            "total": 2,
            "limit": 1,
            "offset": 2,
            "has_more": True,
            "match": "offset \\+ len\\(items\\)",
        },
        {
            "items": (),
            "total": 50,
            "limit": 20,
            "offset": 100,
            "has_more": True,
            "match": "has_more",
        },
    ]
    for params in invalid_cases:
        match = params.get("match", "has_more")
        kwargs = {k: v for k, v in params.items() if k != "match"}
        with pytest.raises(ValidationError, match=match):
            ResearchSearchPageDTO(**kwargs)


def test_search_page_extra_and_frozen() -> None:
    page = ResearchSearchPageDTO(items=(), total=0, limit=20, offset=0, has_more=False)
    with pytest.raises(ValidationError):
        ResearchSearchPageDTO.model_validate({**page.model_dump(), "extra": 1})
    with pytest.raises(ValidationError):
        page.total = 1  # type: ignore[misc]


# --- JournalSearchPageDTO ---


def test_journal_search_page_pagination_invariants() -> None:
    entry = JournalEntryDTO.from_domain(_journal())
    valid_cases = [
        {"items": (entry,), "total": 3, "limit": 1, "offset": 0, "has_more": True},
        {"items": (), "total": 3, "limit": 10, "offset": 99, "has_more": False},
    ]
    for params in valid_cases:
        page = JournalSearchPageDTO(**params)
        assert page.has_more == (page.offset + len(page.items) < page.total)
        if params["offset"] > params["total"]:
            assert page.has_more is False

    invalid_cases = [
        {"items": (entry,), "total": 1, "limit": 10, "offset": 0, "has_more": True},
        {
            "items": (entry,),
            "total": 0,
            "limit": 10,
            "offset": 0,
            "has_more": False,
            "match": "offset \\+ len\\(items\\)",
        },
        {
            "items": (entry,),
            "total": 2,
            "limit": 10,
            "offset": 2,
            "has_more": False,
            "match": "offset \\+ len\\(items\\)",
        },
    ]
    for params in invalid_cases:
        match = params.get("match", "has_more")
        kwargs = {k: v for k, v in params.items() if k != "match"}
        with pytest.raises(ValidationError, match=match):
            JournalSearchPageDTO(**kwargs)


# --- ResearchTimelineItemDTO / ResearchTimelineDTO ---


def test_timeline_item_happy_and_json_enum() -> None:
    item = _timeline_item(
        entity_type=ResearchTimelineEntityType.THESIS_REVISION,
        entity_id=REV_ID,
    )
    assert item.entity_type == ResearchTimelineEntityType.THESIS_REVISION.value
    assert item.model_dump(mode="json")["entity_type"] == "thesis_revision"
    cand = _timeline_item(
        entity_type=ResearchTimelineEntityType.CANDIDATE_RESOLUTION,
        entity_id=RUN_ID,
        instrument_ids=(),
        source_name=None,
    )
    assert cand.entity_id == RUN_ID


def test_timeline_item_rejects_wrong_id_prefix_and_naive() -> None:
    with pytest.raises(ValidationError, match="entity_id"):
        _timeline_item(
            entity_type=ResearchTimelineEntityType.CANDIDATE_RESOLUTION,
            entity_id=REV_ID,
        )
    with pytest.raises(ValidationError):
        _timeline_item(occurred_at=NAIVE)
    with pytest.raises(ValidationError):
        _timeline_item(visible_at=NAIVE)


def test_timeline_dto_happy_order_and_rejects_misorder() -> None:
    a = _timeline_item(
        entity_id=EVIDENCE_ID,
        occurred_at=LATER,
        visible_at=LATER,
    )
    b = _timeline_item(
        entity_type=ResearchTimelineEntityType.REPORT,
        entity_id=REPORT_ID,
        occurred_at=NOW,
        visible_at=NOW,
        instrument_ids=(),
        source_name=None,
    )
    c = _timeline_item(
        entity_type=ResearchTimelineEntityType.EVENT,
        entity_id=EVENT_ID,
        occurred_at=NOW,
        visible_at=EARLIER,
        instrument_ids=(US_INSTRUMENT,),
        source_name="sec",
    )
    # same occurred/visible → entity_id ASC
    d1 = _timeline_item(
        entity_type=ResearchTimelineEntityType.DECISION,
        entity_id=DECISION_ID,
        occurred_at=EARLIER,
        visible_at=EARLIER,
        instrument_ids=(),
        source_name=None,
    )
    d2_id = "decision_00000000-0000-7000-8000-000000000002"
    d2 = _timeline_item(
        entity_type=ResearchTimelineEntityType.DECISION,
        entity_id=d2_id,
        occurred_at=EARLIER,
        visible_at=EARLIER,
        instrument_ids=(),
        source_name=None,
    )
    ok = ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(a, b, c, d1, d2), total=5)
    assert ok.total == 5
    assert [i.entity_id for i in ok.items] == [
        EVIDENCE_ID,
        REPORT_ID,
        EVENT_ID,
        DECISION_ID,
        d2_id,
    ]

    with pytest.raises(ValidationError, match="ordered"):
        ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(b, a), total=2)
    with pytest.raises(ValidationError, match="ordered"):
        ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(c, b), total=2)
    with pytest.raises(ValidationError, match="ordered"):
        ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(d2, d1), total=2)


def test_timeline_dto_rejects_naive_as_of_and_extra() -> None:
    with pytest.raises(ValidationError):
        ResearchTimelineDTO(case_id=CASE_ID, as_of=NAIVE, items=(), total=0)
    dto = ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(), total=0)
    with pytest.raises(ValidationError):
        ResearchTimelineDTO.model_validate({**dto.model_dump(), "extra": 1})
    with pytest.raises(ValidationError):
        dto.total = 1  # type: ignore[misc]


def test_timeline_dto_total_must_be_gte_len_items() -> None:
    """§17.1 C1b item 12: ResearchTimelineDTO.total >= len(items)."""
    item = _timeline_item()
    ok = ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(item,), total=1)
    assert ok.total == 1
    ok_more = ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(item,), total=5)
    assert ok_more.total == 5
    with pytest.raises(ValidationError, match="total must be >= len\\(items\\)"):
        ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(item,), total=0)
    empty = ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=(), total=0)
    assert empty.total == 0


def test_timeline_preserves_caller_order_without_silent_sort() -> None:
    """DTO only validates order; does not re-sort valid sequences."""
    items = (
        _timeline_item(occurred_at=LATER, visible_at=LATER),
        _timeline_item(
            entity_type=ResearchTimelineEntityType.JOURNAL,
            entity_id=JOURNAL_ID,
            occurred_at=EARLIER,
            visible_at=EARLIER,
            instrument_ids=(),
            source_name=None,
        ),
    )
    dto = ResearchTimelineDTO(case_id=CASE_ID, as_of=NOW, items=items, total=2)
    assert dto.items[0].entity_id == EVIDENCE_ID
    assert dto.items[1].entity_id == JOURNAL_ID


# --- Cross-cutting domain conversion order preservation ---


def test_from_domain_preserves_tuple_order_and_content() -> None:
    domain = _evidence(
        instrument_ids=(US_INSTRUMENT, A_SHARE_INSTRUMENT),
        topic_tags=("z-tag", "a-tag"),
    )
    dto = EvidenceDTO.from_domain(domain)
    assert dto.instrument_ids == (US_INSTRUMENT, A_SHARE_INSTRUMENT)
    assert dto.topic_tags == ("z-tag", "a-tag")
    assert dto.title == domain.title
    assert dto.content_sha256 == domain.content_sha256
