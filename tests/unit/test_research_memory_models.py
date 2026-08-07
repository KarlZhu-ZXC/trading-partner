"""Phase 1C C1a research-memory domain models, enums, errors, and hash helpers."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest

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
)
from domain.common.errors import (
    DataContractError,
    HistoricalVisibilityViolation,
    ImmutableResearchRecord,
    InvalidResearchLink,
    ResearchMemoryNotFound,
    SearchBackendUnavailable,
    UnauthorizedResearchWrite,
)
from domain.research.models import (
    FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES,
    FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES,
    FROZEN_RESEARCH_MODEL_NAMES,
    RESEARCH_SCHEMA_VERSION,
    DecisionRecord,
    Evidence,
    EvidenceAssessment,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    SubjectEvidenceLink,
    canonicalize_research_json_object,
    compute_evidence_content_sha256,
    compute_report_content_sha256,
)

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
LATER = NOW + timedelta(hours=1)

CASE_ID = "case_00000000-0000-7000-8000-000000000001"
EVIDENCE_ID = "evidence_00000000-0000-7000-8000-000000000001"
EVIDENCE_ID_2 = "evidence_00000000-0000-7000-8000-000000000002"
REV_ID = "rev_00000000-0000-7000-8000-000000000001"
REV_ID_2 = "rev_00000000-0000-7000-8000-000000000002"
THESIS_ID = "thesis_00000000-0000-7000-8000-000000000001"
REPORT_ID = "report_00000000-0000-7000-8000-000000000001"
REPORT_ID_2 = "report_00000000-0000-7000-8000-000000000002"
EVENT_ID = "event_00000000-0000-7000-8000-000000000001"
DECISION_ID = "decision_00000000-0000-7000-8000-000000000001"
DECISION_ID_2 = "decision_00000000-0000-7000-8000-000000000002"
JOURNAL_ID = "journal_00000000-0000-7000-8000-000000000001"
JOURNAL_ID_2 = "journal_00000000-0000-7000-8000-000000000002"
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
        "content_sha256": "",  # filled below
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
        "subject_id": CASE_ID,
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
        "subject_id": CASE_ID,
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
            subject_id=base["subject_id"],
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
        "subject_id": CASE_ID,
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


def _link(**overrides: Any) -> SubjectEvidenceLink:
    base: dict[str, Any] = {
        "link_id": REV_ID,
        "subject_id": CASE_ID,
        "evidence_id": EVIDENCE_ID,
        "linked_at": NOW,
        "linked_by": "user",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return SubjectEvidenceLink(**base)


def _event(**overrides: Any) -> ResearchEvent:
    base: dict[str, Any] = {
        "event_id": EVENT_ID,
        "subject_id": CASE_ID,
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
        "subject_id": CASE_ID,
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
        "subject_id": CASE_ID,
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


# --- Registry / enums / errors ---


def test_frozen_research_registry_non_regression() -> None:
    assert FROZEN_RESEARCH_MODEL_NAMES == (
        "ResearchSubject",
        "Thesis",
        "ThesisRevision",
        "Assumption",
        "InvalidationCondition",
        "Evidence",
        "EvidenceAssessment",
        "ResearchReport",
        "ResearchEvent",
        "DecisionRecord",
        "JournalEntry",
        "WatchlistItem",
    )
    assert len(FROZEN_RESEARCH_MODEL_NAMES) == 12
    assert FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES == (
        "OpenQuestion",
        "CandidateThesisRevision",
    )
    assert FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES == ("SubjectEvidenceLink",)
    assert "SubjectEvidenceLink" not in FROZEN_RESEARCH_MODEL_NAMES


def test_phase1c_enum_wire_values_complete() -> None:
    def values(enum_cls: type[StrEnum]) -> set[str]:
        return {m.value for m in enum_cls}

    assert values(EvidenceType) == {
        "market_snapshot",
        "fundamental_snapshot",
        "financial_statement",
        "company_action",
        "company_news",
        "global_news",
        "research_report",
        "technical_signal",
        "sentiment",
        "macro",
        "account_snapshot",
        "portfolio_snapshot",
        "user_observation",
        "a_share_announcement",
        "a_share_interactive_qa",
        "a_share_analyst_report",
        "a_share_consensus_estimate",
        "a_share_capital_flow",
        "a_share_northbound_flow",
        "a_share_chip_distribution",
        "a_share_dragon_tiger",
        "a_share_margin_financing",
        "a_share_block_trade",
        "a_share_shareholder_count",
        "a_share_unlock",
        "a_share_dividend",
        "a_share_order_book",
        "a_share_tick",
        "a_share_limit_ecology",
        "a_share_market_heat",
        "a_share_concept_heat",
        "a_share_option_snapshot",
        "sec_filing",
        "sec_company_fact",
        "us_insider_activity",
        "us_10b5_1",
        "us_pre_post_market",
        "us_news_sentiment",
        "fred_macro",
        "stocktwits_sentiment",
        "reddit_sentiment",
        "prediction_market",
        "correction",
    }
    assert values(EvidenceOrigin) == {
        "external_fact",
        "user_observation",
        "system_derived",
    }
    assert values(EvidenceStance) == {
        "supports",
        "contradicts",
        "neutral",
        "uncertain",
    }
    assert values(EvidenceQuality) == {
        "primary",
        "secondary",
        "tertiary",
        "unverified",
    }
    assert values(ReliabilityLevel) == {"high", "medium", "low", "unknown"}
    assert values(ResearchReportType) == {
        "deep_dive",
        "catalyst_review",
        "a_share_market_review",
        "us_market_review",
        "portfolio_review",
        "ad_hoc",
    }
    assert values(ResearchEventType) == {
        "company",
        "earnings",
        "regulatory",
        "corporate_action",
        "industry",
        "macro",
        "policy",
        "market_structure",
        "capital_market",
        "other",
    }
    assert values(DecisionType) == {
        "watch",
        "no_action",
        "initiate_intent",
        "add_intent",
        "hold",
        "reduce_intent",
        "exit_intent",
        "avoid",
        "research_more",
    }
    assert values(JournalEntryType) == {
        "note",
        "observation",
        "reflection",
        "postmortem",
        "question",
    }


def test_phase1c_error_codes_and_retryability() -> None:
    subjects: list[tuple[type, str, bool]] = [
        (ResearchMemoryNotFound, "RESEARCH_MEMORY_NOT_FOUND", False),
        (ImmutableResearchRecord, "IMMUTABLE_RESEARCH_RECORD", False),
        (InvalidResearchLink, "INVALID_RESEARCH_LINK", False),
        (SearchBackendUnavailable, "SEARCH_BACKEND_UNAVAILABLE", True),
        (HistoricalVisibilityViolation, "HISTORICAL_VISIBILITY_VIOLATION", False),
        (UnauthorizedResearchWrite, "UNAUTHORIZED_RESEARCH_WRITE", False),
    ]
    for cls, code, retryable in subjects:
        exc = cls("msg")
        assert exc.code == code
        assert exc.retryable is retryable


# --- Happy paths ---


def test_evidence_happy_paths() -> None:
    structured = canonicalize_research_json_object('{"close":"1505.00","currency":"CNY"}')
    subjects = [
        dict(
            evidence_type=EvidenceType.A_SHARE_ANNOUNCEMENT,
            evidence_id=EVIDENCE_ID,
            title="茅台年报摘要",
            summary="营收与净利同比增长",
            content_text="全文摘要",
            structured_data_json=structured,
            source_name="eastmoney",
            source_vendor="eastmoney",
            source_record_id="ann-600519-2025",
            instrument_ids=(A_SHARE_INSTRUMENT,),
            topic_tags=("a-share", "announcement"),
            recorded_by="provider:eastmoney",
        ),
        dict(
            evidence_id=EVIDENCE_ID_2,
            evidence_type=EvidenceType.SEC_FILING,
            title="NVDA 10-K excerpt",
            summary="Data center revenue growth",
            source_name="sec_edgar",
            source_vendor="sec_edgar",
            instrument_ids=(US_INSTRUMENT,),
            topic_tags=("us", "filing"),
            recorded_by="provider:sec_edgar",
        ),
    ]
    for overrides in subjects:
        ev = _evidence(**overrides)
        assert ev.instrument_ids == overrides["instrument_ids"]
        assert ev.evidence_id.startswith("evidence_")
        assert len(ev.content_sha256) == 64
        if ev.evidence_type is EvidenceType.A_SHARE_ANNOUNCEMENT:
            assert ev.structured_data_json == structured


def test_all_memory_models_smoke_checks() -> None:
    assert _evidence().schema_version == RESEARCH_SCHEMA_VERSION
    assert _link().link_id.startswith("rev_")
    assert _assessment().stance is EvidenceStance.SUPPORTS
    assert _report().report_type is ResearchReportType.DEEP_DIVE
    assert _event().event_type is ResearchEventType.EARNINGS
    assert _decision().decision_type is DecisionType.WATCH
    assert _journal().entry_type is JournalEntryType.NOTE


# --- JSON canonicalization ---


def test_canonicalize_research_json_object_normalizes() -> None:
    subjects = (
        ('{\n  "b": 2,\n  "a": 1\n}', '{"a":1,"b":2}'),
        ('{"name":"贵州茅台"}', '{"name":"贵州茅台"}'),
    )
    for raw, expected in subjects:
        assert canonicalize_research_json_object(raw) == expected, raw


def test_canonicalize_research_json_object_rejects_invalid_inputs() -> None:
    subjects = (
        ('{"a":1,"a":2}', "duplicate keys|strict_json"),
        ("[1,2]", "JSON object"),
        ('"x"', "JSON object"),
        ("1", "JSON object"),
        ('{"x":NaN}', "duplicate keys, NaN, or Infinity"),
        ('{"x":Infinity}', "duplicate keys, NaN, or Infinity"),
        ("{", "malformed_json|is not valid JSON"),
    )
    for raw, match in subjects:
        with pytest.raises(DataContractError, match=match):
            canonicalize_research_json_object(raw)


# --- Hash determinism / mismatch ---


def test_hash_helpers_deterministic_and_mismatch() -> None:
    # Evidence: order-independent and timezone stable.
    h1 = _evidence_hash(instrument_ids=(A_SHARE_INSTRUMENT, US_INSTRUMENT))
    h2 = _evidence_hash(instrument_ids=(US_INSTRUMENT, A_SHARE_INSTRUMENT))
    assert h1 == h2
    assert len(h1) == 64
    assert h1 == h1.lower()
    assert h1 == _evidence_hash(
        instrument_ids=(A_SHARE_INSTRUMENT, US_INSTRUMENT),
        published_at=EARLIER.astimezone(timezone(timedelta(hours=8))),
    )

    # Report: order-independent set inputs.
    r1 = _report_hash(
        evidence_ids=(EVIDENCE_ID, EVIDENCE_ID_2),
        thesis_revision_ids=(REV_ID, REV_ID_2),
    )
    r2 = _report_hash(
        evidence_ids=(EVIDENCE_ID_2, EVIDENCE_ID),
        thesis_revision_ids=(REV_ID_2, REV_ID),
    )
    assert r1 == r2

    # Hash mismatch remains a hard fail.
    with pytest.raises(DataContractError, match="content hash"):
        _evidence(content_sha256="0" * 64)
    with pytest.raises(DataContractError, match="content hash"):
        _report(content_sha256="a" * 64)

    canonical = canonicalize_research_json_object('{"b":1,"a":2}')
    noncanonical = '{"b":1,"a":2}'
    # Non-canonical input is canonicalized inside the hash helper.
    assert _evidence_hash(structured_data_json=canonical) == _evidence_hash(
        structured_data_json=noncanonical
    )


def test_evidence_requires_structured_data_already_canonical() -> None:
    with pytest.raises(DataContractError, match="canonical"):
        _evidence(structured_data_json='{"b":1,"a":2}')


# --- ID / tuple / string / time / reviewer / decision / supersedes / schema ---


def test_identity_and_fk_prefix_validation() -> None:
    for kwargs in (
        {"evidence_id": "evidence_not-a-uuid"},
        {"evidence_id": "evidence_00000000-0000-4000-8000-000000000001"},
    ):
        with pytest.raises(DataContractError, match="evidence_id"):
            _evidence(**kwargs)
    with pytest.raises(DataContractError, match="link_id"):
        _link(link_id="link_00000000-0000-7000-8000-000000000001")
    with pytest.raises(DataContractError, match="assessment_id"):
        _assessment(assessment_id="assessment_00000000-0000-7000-8000-000000000001")
    with pytest.raises(DataContractError, match="research_run_id"):
        _report(research_run_id="run_not-uuid")
    with pytest.raises(DataContractError, match="position_context_snapshot_id"):
        _decision(
            decision_type=DecisionType.INITIATE_INTENT,
            confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            position_context_snapshot_id="snap_00000000-0000-7000-8000-000000000001",
        )
    with pytest.raises(DataContractError, match="subject_id"):
        _assessment(subject_id="not-a-case")
    with pytest.raises(DataContractError, match="thesis_id"):
        _assessment(thesis_id="rev_00000000-0000-7000-8000-000000000099")
    with pytest.raises(DataContractError, match="evidence_ids"):
        _report(evidence_ids=("report_00000000-0000-7000-8000-000000000001",))


def test_tuple_and_text_boundaries_are_enforced() -> None:
    with pytest.raises(DataContractError, match="tuple"):
        _evidence(instrument_ids=[A_SHARE_INSTRUMENT])  # type: ignore[arg-type]
    with pytest.raises(DataContractError, match="duplicates"):
        _evidence(topic_tags=("ai", "ai"))
    with pytest.raises(DataContractError, match="non-empty"):
        _evidence(topic_tags=("",))
    # Model preserves caller order (no silent sort/dedupe).
    ev = _evidence(instrument_ids=(US_INSTRUMENT, A_SHARE_INSTRUMENT))
    assert ev.instrument_ids == (US_INSTRUMENT, A_SHARE_INSTRUMENT)


def test_text_length_boundaries_are_enforced() -> None:
    with pytest.raises(DataContractError, match="non-blank|title"):
        _evidence(title="   ")
    with pytest.raises(DataContractError, match="length"):
        _evidence(title="x" * 301)
    with pytest.raises(DataContractError, match="length"):
        _evidence(summary="x" * 8001)
    with pytest.raises(DataContractError, match="length"):
        _evidence(content_text="x" * 200_001)
    with pytest.raises(DataContractError, match="length"):
        _assessment(rationale="x" * 8001)
    with pytest.raises(DataContractError, match="length"):
        _report(content_markdown="x" * 2_000_001)
    with pytest.raises(DataContractError, match="length"):
        _report(summary="x" * 8001)
    with pytest.raises(DataContractError, match="length"):
        _evidence(source_name="x" * 201)
    with pytest.raises(DataContractError, match="length"):
        _event(title="x" * 301)
    with pytest.raises(DataContractError, match="length"):
        _event(summary="x" * 8001)
    with pytest.raises(DataContractError, match="length"):
        _event(source_name="x" * 201)
    with pytest.raises(DataContractError, match="length"):
        _decision(title="x" * 301)
    with pytest.raises(DataContractError, match="length"):
        _decision(rationale="x" * 20_001)
    with pytest.raises(DataContractError, match="length"):
        _journal(title="x" * 301)
    with pytest.raises(DataContractError, match="length"):
        _journal(body_markdown="x" * 200_001)

    # Exact maxima accepted.
    assert _event(title="t" * 300).title == "t" * 300
    assert _event(summary="s" * 8000).summary == "s" * 8000
    assert _event(source_name="n" * 200).source_name == "n" * 200
    assert _decision(title="t" * 300).title == "t" * 300
    assert _decision(rationale="r" * 20_000).rationale == "r" * 20_000
    assert _journal(title="t" * 300).title == "t" * 300
    assert _journal(body_markdown="b" * 200_000).body_markdown == "b" * 200_000


def test_aware_datetime_and_time_order_invariants() -> None:
    naive = datetime(2026, 7, 16, 12, 0, 0)
    with pytest.raises(DataContractError, match="timezone-aware"):
        _evidence(observed_at=naive)
    with pytest.raises(DataContractError, match="effective_to"):
        _evidence(effective_from=NOW, effective_to=EARLIER)
    with pytest.raises(DataContractError, match="as_of"):
        _report(as_of=LATER, created_at=NOW)
    with pytest.raises(DataContractError, match="decided_at"):
        _decision(decided_at=LATER, recorded_at=NOW)
    # as_of equal to created_at is allowed
    assert _report(as_of=NOW, created_at=NOW).as_of == NOW


def test_schema_version_restrictions() -> None:
    with pytest.raises(DataContractError, match="schema_version"):
        _evidence(schema_version=2)
    with pytest.raises(DataContractError, match="schema_version"):
        _link(schema_version=0)
    # Python bools must not pass as ints.
    for creator in (
        _evidence,
        _link,
        _assessment,
        _report,
        _event,
        _decision,
        _journal,
    ):
        with pytest.raises(DataContractError, match="schema_version|exact int"):
            creator(schema_version=True)  # type: ignore[arg-type]
        with pytest.raises(DataContractError, match="schema_version|exact int"):
            creator(schema_version=False)  # type: ignore[arg-type]


def _assert_no_secret_in_exception(exc: BaseException, secret: str) -> None:
    """Secret-shaped caller content must not appear in message/details/repr/chain."""
    surfaces = [str(exc), repr(exc)]
    if isinstance(exc, DataContractError):
        surfaces.append(str(exc.details))
        surfaces.append(repr(exc.details))
        surfaces.append(exc.message)
    for text in surfaces:
        assert secret not in text
    assert exc.__cause__ is None
    # Prefer no retained context; accept suppressed context only if empty of secret.
    assert exc.__context__ is None or exc.__suppress_context__
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        nxt: BaseException | None = current.__cause__
        if nxt is None and current.__context__ is not None and not current.__suppress_context__:
            nxt = current.__context__
        # Even suppressed context objects must not retain secret-shaped caller content.
        if current.__context__ is not None:
            ctx = current.__context__
            assert secret not in str(ctx)
            assert secret not in repr(ctx)
            doc = getattr(ctx, "doc", None)
            if isinstance(doc, str):
                assert secret not in doc
        current = nxt
    for link in chain:
        assert secret not in str(link)
        assert secret not in repr(link)


def test_malformed_input_and_actor_secrets_do_not_leak() -> None:
    secret = "test-secret-json-leak-probe"
    bad_inputs = [
        '{"api_key":"' + secret + '"',
        '{"api_key":"' + secret + '","api_key":"x"}',
        '"' + secret + '"',
    ]
    for bad in bad_inputs:
        with pytest.raises(DataContractError) as caught:
            canonicalize_research_json_object(bad)
        _assert_no_secret_in_exception(caught.value, secret)

    actor_cases = [
        (_assessment, dict(assessed_by=secret)),
        (_decision, dict(decided_by=secret)),
        (_journal, dict(authored_by=secret)),
        (_journal, dict(confirmed_by=secret)),
        (_evidence, dict(recorded_by=secret)),
    ]
    for builder, bad_kwargs in actor_cases:
        with pytest.raises(DataContractError) as caught:
            builder(**bad_kwargs)
        _assert_no_secret_in_exception(caught.value, secret)


def test_confidence_and_materiality_finite_decimal_unit_interval() -> None:
    with pytest.raises(DataContractError, match="Decimal"):
        _evidence(confidence=0.5)  # type: ignore[arg-type]
    with pytest.raises(DataContractError, match="finite"):
        _evidence(confidence=Decimal("NaN"))
    with pytest.raises(DataContractError, match="finite"):
        _assessment(materiality=Decimal("Infinity"))
    with pytest.raises(DataContractError, match=r"\[0, 1\]"):
        _assessment(materiality=Decimal("1.01"))
    with pytest.raises(DataContractError, match=r"\[0, 1\]"):
        _evidence(confidence=Decimal("-0.01"))
    assert _evidence(confidence=Decimal("0")).confidence == Decimal("0")
    assert _assessment(materiality=Decimal("1")).materiality == Decimal("1")


def test_content_sha256_format() -> None:
    with pytest.raises(DataContractError, match="64-character lowercase"):
        _evidence(content_sha256="ABC" + "0" * 61)
    with pytest.raises(DataContractError, match="64-character lowercase"):
        _report(content_sha256="0" * 63)


def test_correction_requires_supersedes_non_correction_allows() -> None:
    with pytest.raises(DataContractError, match="CORRECTION"):
        _evidence(
            evidence_type=EvidenceType.CORRECTION,
            supersedes_evidence_id=None,
        )
    corrected = _evidence(
        evidence_type=EvidenceType.CORRECTION,
        supersedes_evidence_id=EVIDENCE_ID_2,
    )
    assert corrected.supersedes_evidence_id == EVIDENCE_ID_2
    # Non-correction may still supersede.
    versioned = _evidence(
        evidence_type=EvidenceType.COMPANY_NEWS,
        supersedes_evidence_id=EVIDENCE_ID_2,
    )
    assert versioned.supersedes_evidence_id == EVIDENCE_ID_2


def test_supersedes_must_not_equal_self() -> None:
    with pytest.raises(DataContractError, match="own id"):
        _evidence(supersedes_evidence_id=EVIDENCE_ID)
    with pytest.raises(DataContractError, match="own id"):
        _report(supersedes_report_id=REPORT_ID)
    with pytest.raises(DataContractError, match="own id"):
        _decision(
            decision_type=DecisionType.WATCH,
            confirmation_mode=ConfirmationMode.NORMAL,
            supersedes_decision_id=DECISION_ID,
        )
    with pytest.raises(DataContractError, match="own id"):
        _journal(supersedes_journal_id=JOURNAL_ID)


def test_reviewer_and_actor_rules() -> None:
    with pytest.raises(DataContractError, match="USER_OBSERVATION"):
        _evidence(
            origin=EvidenceOrigin.USER_OBSERVATION,
            recorded_by="system",
        )
    assert (
        _evidence(
            origin=EvidenceOrigin.USER_OBSERVATION,
            recorded_by="user",
        ).recorded_by
        == "user"
    )
    with pytest.raises(DataContractError, match="assessed_by"):
        _assessment(assessed_by="system")
    with pytest.raises(DataContractError, match="confirmed_by"):
        _assessment(confirmed_by="codex")
    with pytest.raises(DataContractError, match="decided_by"):
        _decision(decided_by="codex")
    with pytest.raises(DataContractError, match="authored_by"):
        _journal(authored_by="system")
    with pytest.raises(DataContractError, match="confirmed_by"):
        _journal(confirmed_by="codex")
    # Journal authored_by=codex is allowed when confirmed_by is user/agent.
    assert _journal(authored_by="codex", confirmed_by="external_agent").authored_by == ("codex")


def test_decision_confirmation_mode_matrix() -> None:
    strict_types = (
        DecisionType.INITIATE_INTENT,
        DecisionType.ADD_INTENT,
        DecisionType.HOLD,
        DecisionType.REDUCE_INTENT,
        DecisionType.EXIT_INTENT,
        DecisionType.AVOID,
    )
    for decision_type in strict_types:
        with pytest.raises(DataContractError, match="STRICT_REVIEW"):
            _decision(
                decision_type=decision_type,
                confirmation_mode=ConfirmationMode.NORMAL,
            )
        assert (
            _decision(
                decision_type=decision_type,
                confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            ).decision_type
            is decision_type
        )

    normal_only = (
        DecisionType.WATCH,
        DecisionType.NO_ACTION,
        DecisionType.RESEARCH_MORE,
    )
    for decision_type in normal_only:
        with pytest.raises(DataContractError, match="NORMAL"):
            _decision(
                decision_type=decision_type,
                confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            )
        assert (
            _decision(
                decision_type=decision_type,
                confirmation_mode=ConfirmationMode.NORMAL,
            ).decision_type
            is decision_type
        )


def test_related_entity_pair_and_optional_ids() -> None:
    with pytest.raises(DataContractError, match="related_entity"):
        _event(related_entity_type="thesis", related_entity_id=None)
    with pytest.raises(DataContractError, match="related_entity"):
        _journal(related_entity_type=None, related_entity_id=THESIS_ID)
    event = _event(related_entity_type="thesis", related_entity_id=THESIS_ID)
    assert event.related_entity_id == THESIS_ID
    report = _report(research_run_id=RUN_ID)
    assert report.research_run_id == RUN_ID
    decision = _decision(
        decision_type=DecisionType.ADD_INTENT,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        position_context_snapshot_id=SNAPSHOT_ID,
        supersedes_decision_id=DECISION_ID_2,
    )
    assert decision.position_context_snapshot_id == SNAPSHOT_ID


# --- Frozen / immutability ---


def test_models_are_frozen_slotted_dataclasses() -> None:
    models = (
        _evidence(),
        _link(),
        _assessment(),
        _report(),
        _event(),
        _decision(),
        _journal(),
    )
    for model in models:
        assert model.__slots__  # type: ignore[attr-defined]
        with pytest.raises(FrozenInstanceError):
            model.schema_version = 99  # type: ignore[misc]


def test_source_vendor_and_recorded_by_vendor_validation() -> None:
    with pytest.raises(DataContractError, match="source_vendor"):
        _evidence(source_vendor="not_a_vendor")
    with pytest.raises(DataContractError, match="recorded_by"):
        _evidence(recorded_by="provider:not_a_vendor")
    assert _evidence(source_vendor=None, recorded_by="system").source_vendor is None
