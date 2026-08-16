"""MCP input schema contract tests (Phase 1A + Phase 1B + Phase 1C + Phase 1D)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from domain.common.enums import (
    AssetType,
    ConfirmationMode,
    DecisionType,
    EvidenceStance,
    JournalEntryType,
    Market,
    ResearchSearchEntityType,
)
from interfaces.mcp.schemas import (
    DecisionRecordAppendInput,
    InstrumentResolveInput,
    JournalAppendInput,
    JournalSearchInput,
    MarketGetMockSnapshotInput,
    ResearchReportGetInput,
    ResearchSearchInput,
    ResearchStateUpdateInput,
    ResearchSubjectArchiveInput,
    ResearchSubjectCreateInput,
    ResearchSubjectGetInput,
    ResearchSubjectUpdateInput,
    ResearchTimelineGetInput,
    ThesisHistoryGetInput,
    ThesisRevisionConfirmInput,
    ThesisRevisionProposeInput,
)
from interfaces.mcp.server import (
    FORBIDDEN_PUBLIC_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    RETIRED_PUBLIC_TOOL_NAMES,
)

CASE_ID = "case_00000000-0000-7000-8000-000000000001"
REPORT_ID = "report_00000000-0000-7000-8000-000000000001"
AWARE = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_schema_accepts_iso_8601_as_of_string() -> None:
    payload = MarketGetMockSnapshotInput.model_validate(
        {
            "market": "US",
            "symbol": "NVDA",
            "as_of": "2026-07-16T16:00:00+00:00",
        }
    )
    assert payload.market is Market.US
    assert payload.as_of is not None
    assert payload.as_of.utcoffset() is not None
    assert payload.as_of.utcoffset().total_seconds() == 0


def test_schema_rejects_wrong_market_case() -> None:
    with pytest.raises(ValidationError):
        MarketGetMockSnapshotInput.model_validate({"market": "us", "symbol": "NVDA"})


def test_schema_rejects_naive_as_of_string() -> None:
    with pytest.raises(ValidationError):
        MarketGetMockSnapshotInput.model_validate(
            {
                "market": "US",
                "symbol": "NVDA",
                "as_of": "2026-07-16T16:00:00",
            }
        )


def test_public_tool_surface_excludes_forbidden_and_retired_names() -> None:
    assert len(PUBLIC_TOOL_NAMES) == 27
    assert PUBLIC_TOOL_NAMES.isdisjoint(FORBIDDEN_PUBLIC_TOOL_NAMES)
    assert PUBLIC_TOOL_NAMES.isdisjoint(RETIRED_PUBLIC_TOOL_NAMES)


def test_instrument_resolve_input_validation() -> None:
    ok = InstrumentResolveInput.model_validate({"market": "US", "query": "NVDA"})
    assert ok.market == "US" or ok.market is Market.US
    normalized = InstrumentResolveInput.model_validate(
        {"market": " us ", "query": "NVDA", "asset_type": " ETF "}
    )
    assert normalized.market == "US" or normalized.market is Market.US
    assert normalized.asset_type == "etf" or normalized.asset_type is AssetType.ETF

    ok = InstrumentResolveInput.model_validate({"market": "A_SHARE", "query": "  600519  "})
    assert ok.query == "600519"
    with pytest.raises(ValidationError):
        InstrumentResolveInput.model_validate({"market": "US", "query": "NVDA", "extra": 1})
    with pytest.raises(ValidationError):
        InstrumentResolveInput.model_validate({"market": "US", "query": "   "})


def test_research_subject_create_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        ResearchSubjectCreateInput.model_validate(
            {
                "case_type": "company",
                "title": "NVDA",
                "summary": "GPU demand",
                "primary_instrument_id": "equity:US:NVDA",
                "confirmed_by": "user",
                "idempotency_key": "k1",
                "extra_field": True,
            }
        )


def test_research_subject_create_company_requires_primary_instrument() -> None:
    with pytest.raises(ValidationError, match="primary_instrument_id"):
        ResearchSubjectCreateInput.model_validate(
            {
                "case_type": "company",
                "title": "NVDA",
                "summary": "GPU demand",
                "confirmed_by": "user",
                "idempotency_key": "k1",
            }
        )
    with pytest.raises(ValidationError, match="primary_instrument_id"):
        ResearchSubjectCreateInput.model_validate(
            {
                "case_type": "catalyst",
                "title": "Event",
                "summary": "Near-term catalyst",
                "primary_instrument_id": "   ",
                "confirmed_by": "user",
                "idempotency_key": "k2",
            }
        )
    # THEME / MACRO may omit instrument
    theme = ResearchSubjectCreateInput.model_validate(
        {
            "case_type": "theme",
            "title": "AI theme",
            "summary": "Multi-name theme",
            "confirmed_by": "user",
            "idempotency_key": "k3",
        }
    )
    assert theme.primary_instrument_id is None


def test_research_subject_create_rejects_codex_confirmed_by() -> None:
    with pytest.raises(ValidationError):
        ResearchSubjectCreateInput.model_validate(
            {
                "case_type": "theme",
                "title": "AI",
                "summary": "theme",
                "confirmed_by": "codex",
                "idempotency_key": "k",
            }
        )


def test_research_subject_get_requires_uuid7_case_id() -> None:
    ok = ResearchSubjectGetInput.model_validate(
        {"case_id": "case_00000000-0000-7000-8000-000000000001"}
    )
    assert ok.case_id.startswith("case_")
    with pytest.raises(ValidationError):
        ResearchSubjectGetInput.model_validate({"case_id": "case_not-a-uuid"})
    with pytest.raises(ValidationError):
        ResearchSubjectGetInput.model_validate(
            {"case_id": "run_00000000-0000-7000-8000-000000000001"}
        )


def test_research_subject_archive_rejects_codex_and_bad_ids() -> None:
    ok = ResearchSubjectArchiveInput.model_validate(
        {
            # Archive accepts any UUID structure (design flex), not only uuid7.
            "case_id": "case_00000000-0000-4000-8000-000000000001",
            "archived_reason": "Policy shift",
            "reviewed_by": "user",
            "idempotency_key": "arch-1",
        }
    )
    assert ok.reviewed_by == "user"
    with pytest.raises(ValidationError):
        ResearchSubjectArchiveInput.model_validate(
            {
                "case_id": "case_00000000-0000-7000-8000-000000000001",
                "archived_reason": "x",
                "reviewed_by": "codex",
                "idempotency_key": "arch-2",
            }
        )
    with pytest.raises(ValidationError):
        ResearchSubjectArchiveInput.model_validate(
            {
                "case_id": "case_not-a-uuid",
                "archived_reason": "x",
                "reviewed_by": "user",
                "idempotency_key": "arch-3",
            }
        )


def test_research_subject_update_accepts_partial_metadata_and_rejects_empty_or_extra() -> None:
    update = ResearchSubjectUpdateInput.model_validate(
        {
            "case_id": CASE_ID,
            "summary": "Updated research scope",
            "reviewed_by": "user",
            "idempotency_key": "case-update-1",
        }
    )
    assert update.summary == "Updated research scope"
    assert update.title is None

    with pytest.raises(ValidationError, match="at least one field"):
        ResearchSubjectUpdateInput.model_validate(
            {
                "case_id": CASE_ID,
                "reviewed_by": "user",
                "idempotency_key": "case-update-empty",
            }
        )
    with pytest.raises(ValidationError):
        ResearchSubjectUpdateInput.model_validate(
            {
                "case_id": CASE_ID,
                "title": "Updated",
                "reviewed_by": "user",
                "idempotency_key": "case-update-extra",
                "unexpected": True,
            }
        )


def test_thesis_revision_confirm_action_rules() -> None:
    confirm = ThesisRevisionConfirmInput.model_validate(
        {
            "candidate_id": "run_00000000-0000-7000-8000-000000000001",
            "action": "confirm",
            "reviewed_by": "user",
        }
    )
    assert confirm.action == "confirm"

    relayed = ThesisRevisionConfirmInput.model_validate(
        {
            "candidate_id": "run_00000000-0000-7000-8000-000000000001",
            "action": "confirm",
            "reviewed_by": "user",
            "submitted_via": "codex_chat",
            "authorization_note": "我确认这个候选",
        }
    )
    assert relayed.reviewed_by == "user"
    assert relayed.submitted_via == "codex_chat"

    with pytest.raises(ValidationError, match="authorization_note"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "confirm",
                "reviewed_by": "user",
                "submitted_via": "codex_chat",
            }
        )

    with pytest.raises(ValidationError, match="reviewed_by=user"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "confirm",
                "reviewed_by": "external_agent",
                "submitted_via": "codex_chat",
                "authorization_note": "approve",
            }
        )

    with pytest.raises(ValidationError, match="submitted_via=codex_chat"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "confirm",
                "reviewed_by": "user",
                "authorization_note": "approve",
            }
        )

    with pytest.raises(ValidationError, match="rejection_reason"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "reject",
                "reviewed_by": "user",
            }
        )

    with pytest.raises(ValidationError, match="review_note"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "withdraw",
                "reviewed_by": "codex",
            }
        )

    with pytest.raises(ValidationError, match="withdraw"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "confirm",
                "reviewed_by": "codex",
            }
        )

    with pytest.raises(ValidationError, match="withdraw"):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "run_00000000-0000-7000-8000-000000000001",
                "action": "reject",
                "reviewed_by": "codex",
                "rejection_reason": "no",
            }
        )

    # candidate_id must be run_<uuid>, not case_/thesis_
    with pytest.raises(ValidationError):
        ThesisRevisionConfirmInput.model_validate(
            {
                "candidate_id": "case_00000000-0000-7000-8000-000000000001",
                "action": "confirm",
                "reviewed_by": "user",
            }
        )


def test_thesis_history_get_requires_uuid7_thesis_id() -> None:
    ok = ThesisHistoryGetInput.model_validate(
        {"thesis_id": "thesis_00000000-0000-7000-8000-000000000001"}
    )
    assert ok.thesis_id.startswith("thesis_")
    with pytest.raises(ValidationError):
        ThesisHistoryGetInput.model_validate(
            {"thesis_id": "thesis_00000000-0000-4000-8000-000000000001"}
        )


def test_research_state_update_requires_case_for_non_watchlist() -> None:
    with pytest.raises(ValidationError, match="case_id"):
        ResearchStateUpdateInput.model_validate(
            {
                "case_id": None,
                "payload": {
                    "kind": "open_question",
                    "action": "create",
                    "text": "What is the catalyst?",
                },
                "proposed_by": "codex",
                "proposed_by_rationale": "track",
                "idempotency_key": "k",
            }
        )

    wl = ResearchStateUpdateInput.model_validate(
        {
            "case_id": None,
            "payload": {
                "kind": "watchlist_item",
                "action": "create",
                "market": "US",
                "symbol": "NVDA",
                "display_name": "NVIDIA",
                "triggers": ["EPS miss"],
            },
            "confirmation_mode": "normal",
            "proposed_by": "user",
            "proposed_by_rationale": "watch",
            "idempotency_key": "wl-1",
        }
    )
    assert wl.case_id is None
    assert wl.payload.kind == "watchlist_item"
    assert wl.payload.thesis_hint is None
    assert wl.confirmation_mode is ConfirmationMode.NORMAL


def test_research_state_schema_documents_direct_instrument_attachment() -> None:
    schema = str(ResearchStateUpdateInput.model_json_schema())

    assert "Use create for the normal Instrument attachment flow" in schema
    assert "Legacy Instrument Selection transition" in schema
    assert "Canonical Instrument proposed for attachment" in schema


def test_thesis_revision_propose_payload_closed() -> None:
    inp = ThesisRevisionProposeInput.model_validate(
        {
            "case_id": "case_00000000-0000-7000-8000-000000000001",
            "payload": {
                "kind": "thesis_revision",
                "title": "Primary",
                "statement": "Demand structural",
                "rationale": "Capex cycle",
                "confidence_band": "high",
                "rating": "buy",
                "invalidation_check_note": "GM",
                "thesis_role": "primary",
            },
            "proposed_by": "codex",
            "proposed_by_rationale": "init",
            "idempotency_key": "p1",
        }
    )
    assert inp.payload.kind == "thesis_revision"
    assert inp.case_id.startswith("case_")

    with pytest.raises(ValidationError):
        ThesisRevisionProposeInput.model_validate(
            {
                "case_id": "case_00000000-0000-7000-8000-000000000001",
                "payload": {
                    "kind": "thesis_revision",
                    "title": "Primary",
                    "statement": "Demand structural",
                    "rationale": "Capex cycle",
                    "confidence_band": "high",
                    "rating": "buy",
                    "invalidation_check_note": "GM",
                    "unknown_field": 1,
                },
                "proposed_by": "codex",
                "proposed_by_rationale": "init",
                "idempotency_key": "p1",
            }
        )


# ---------------------------------------------------------------------------
# Phase 1C C5 input schemas
# ---------------------------------------------------------------------------


def test_research_search_input_accepts_filters_and_domain_enums() -> None:
    ok = ResearchSearchInput.model_validate(
        {
            "text": "  茅台  ",
            "case_id": CASE_ID,
            "entity_types": ["evidence", ResearchSearchEntityType.REPORT],
            "stances": [EvidenceStance.CONTRADICTS],
            "limit": 10,
        }
    )
    assert ok.text == "茅台"
    assert ok.entity_types[0] is ResearchSearchEntityType.EVIDENCE
    assert ok.stances == (EvidenceStance.CONTRADICTS,)


def test_research_search_input_rejects_unconstrained_blank_and_stances_without_scope() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ResearchSearchInput.model_validate({})
    with pytest.raises(ValidationError, match="at least one"):
        ResearchSearchInput.model_validate({"text": "   "})
    with pytest.raises(ValidationError, match="stances"):
        ResearchSearchInput.model_validate({"text": "x", "stances": ["supports"]})
    with pytest.raises(ValidationError):
        ResearchSearchInput.model_validate({"case_id": CASE_ID, "extra": True})


def test_research_search_input_rejects_naive_datetime_and_bad_ids() -> None:
    with pytest.raises(ValidationError):
        ResearchSearchInput.model_validate({"case_id": CASE_ID, "as_of": "2026-07-16T12:00:00"})
    with pytest.raises(ValidationError):
        ResearchSearchInput.model_validate({"case_id": "case_not-uuid"})


def test_research_report_get_input_strict_prefix() -> None:
    ok = ResearchReportGetInput.model_validate({"report_id": REPORT_ID})
    assert ok.report_id == REPORT_ID
    with pytest.raises(ValidationError):
        ResearchReportGetInput.model_validate(
            {"report_id": "evidence_00000000-0000-7000-8000-000000000001"}
        )


def test_research_timeline_get_input_entity_types_and_window() -> None:
    ok = ResearchTimelineGetInput.model_validate(
        {
            "case_id": CASE_ID,
            "entity_types": ["evidence", "decision"],
            "occurred_from": AWARE.isoformat(),
            "occurred_to": AWARE.isoformat(),
        }
    )
    assert len(ok.entity_types) == 2
    with pytest.raises(ValidationError, match="occurred_to"):
        ResearchTimelineGetInput.model_validate(
            {
                "case_id": CASE_ID,
                "occurred_from": "2026-07-16T12:00:00+00:00",
                "occurred_to": "2026-07-15T12:00:00+00:00",
            }
        )


def test_journal_search_input_requires_filter() -> None:
    ok = JournalSearchInput.model_validate({"case_id": CASE_ID})
    assert ok.case_id == CASE_ID
    with pytest.raises(ValidationError, match="at least one"):
        JournalSearchInput.model_validate({"text": "  "})


def _journal_append_base(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "case_id": CASE_ID,
        "entry_type": JournalEntryType.NOTE,
        "title": "Note",
        "body_markdown": "Body",
        "authored_by": "codex",
        "confirmed_by": "user",
        "idempotency_key": "j1",
    }
    payload.update(overrides)
    return payload


# Frozen Journal related type → prefix (v1.22 schema contract).
_JOURNAL_RELATED_VALID_IDS: tuple[tuple[str, str], ...] = (
    ("case", "case_00000000-0000-7000-8000-000000000001"),
    ("thesis", "thesis_00000000-0000-7000-8000-000000000002"),
    ("thesis_revision", "rev_00000000-0000-7000-8000-000000000003"),
    ("evidence", "evidence_00000000-0000-7000-8000-000000000004"),
    ("report", "report_00000000-0000-7000-8000-000000000005"),
    ("event", "event_00000000-0000-7000-8000-000000000006"),
    ("decision", "decision_00000000-0000-7000-8000-000000000007"),
    ("journal", "journal_00000000-0000-7000-8000-000000000008"),
)


def test_journal_append_input_related_entity_matrix() -> None:
    base = _journal_append_base()
    ok = JournalAppendInput.model_validate(base)
    assert ok.entry_type is JournalEntryType.NOTE
    assert ok.related_entity_type is None
    assert ok.related_entity_id is None
    with pytest.raises(ValidationError, match="related_entity"):
        JournalAppendInput.model_validate(
            _journal_append_base(
                related_entity_type="decision",
                idempotency_key="j2",
            )
        )
    with pytest.raises(ValidationError):
        JournalAppendInput.model_validate(
            _journal_append_base(
                confirmed_by="codex",
                idempotency_key="j3",
            )
        )
    with pytest.raises(ValidationError):
        JournalAppendInput.model_validate(
            _journal_append_base(
                title="  ",
                idempotency_key="j4",
            )
        )
    for related_type, related_id in _JOURNAL_RELATED_VALID_IDS:
        ok = JournalAppendInput.model_validate(
            {
                **base,
                "related_entity_type": related_type,
                "related_entity_id": related_id,
            }
        )
        assert ok.related_entity_type == related_type
        assert ok.related_entity_id == related_id
        assert ok.idempotency_key == "j1"

    with pytest.raises(ValidationError):
        JournalAppendInput.model_validate(
            {
                **base,
                "related_entity_type": "position",
                "related_entity_id": "position_00000000-0000-7000-8000-000000000001",
                "idempotency_key": "unknown-type",
            }
        )

    for related_type, wrong_id in (
        ("case", "thesis_00000000-0000-7000-8000-000000000001"),
        ("thesis", "case_00000000-0000-7000-8000-000000000001"),
        ("thesis_revision", "thesis_00000000-0000-7000-8000-000000000001"),
        ("evidence", "report_00000000-0000-7000-8000-000000000001"),
        ("report", "evidence_00000000-0000-7000-8000-000000000001"),
        ("event", "decision_00000000-0000-7000-8000-000000000001"),
        ("decision", "event_00000000-0000-7000-8000-000000000001"),
        ("journal", "case_00000000-0000-7000-8000-000000000001"),
    ):
        with pytest.raises(ValidationError, match="related_entity_id"):
            JournalAppendInput.model_validate(
                {
                    **base,
                    "related_entity_type": related_type,
                    "related_entity_id": wrong_id,
                    "idempotency_key": "unknown-type",
                }
            )

    for related_type, malformed_id in (
        ("case", "case_not-a-uuid"),
        ("thesis", "thesis_00000000-0000-4000-8000-000000000001"),
        ("thesis_revision", "rev_00000000-0000-7000-c000-000000000001"),
        ("evidence", "evidence_"),
        ("report", "report_00000000-0000-7000-8000-00000000000"),
        ("event", "event_00000000000070008000000000000001"),
        ("decision", "decision_00000000-0000-7000-8000-000000000001-extra"),
        ("journal", "JOURNAL_00000000-0000-7000-8000-000000000001"),
    ):
        with pytest.raises(ValidationError, match="related_entity_id"):
            JournalAppendInput.model_validate(
                {
                    **base,
                    "related_entity_type": related_type,
                    "related_entity_id": malformed_id,
                    "idempotency_key": "bad-id",
                }
            )


def test_journal_append_input_rejects_related_half_pair() -> None:
    with pytest.raises(ValidationError, match="related_entity"):
        JournalAppendInput.model_validate(
            _journal_append_base(
                related_entity_type="decision",
                idempotency_key="half-type",
            )
        )
    with pytest.raises(ValidationError, match="related_entity"):
        JournalAppendInput.model_validate(
            _journal_append_base(
                related_entity_id="decision_00000000-0000-7000-8000-000000000007",
                idempotency_key="half-id",
            )
        )


def test_journal_append_input_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        JournalAppendInput.model_validate(
            _journal_append_base(extra_field=True, idempotency_key="extra")
        )


def test_decision_record_append_input_enums_ids_aware() -> None:
    ok = DecisionRecordAppendInput.model_validate(
        {
            "case_id": CASE_ID,
            "decision_type": DecisionType.WATCH,
            "title": "Watch",
            "rationale": "Need more data",
            "decided_at": AWARE.isoformat(),
            "decided_by": "user",
            "confirmation_mode": ConfirmationMode.NORMAL,
            "idempotency_key": "d1",
        }
    )
    assert ok.decision_type is DecisionType.WATCH
    with pytest.raises(ValidationError):
        DecisionRecordAppendInput.model_validate(
            {
                "case_id": CASE_ID,
                "decision_type": "watch",
                "title": "Watch",
                "rationale": "Need more data",
                "decided_at": "2026-07-16T12:00:00",
                "decided_by": "user",
                "idempotency_key": "d2",
            }
        )
    with pytest.raises(ValidationError):
        DecisionRecordAppendInput.model_validate(
            {
                "case_id": CASE_ID,
                "decision_type": "watch",
                "title": "Watch",
                "rationale": "Need more data",
                "decided_at": AWARE.isoformat(),
                "decided_by": "codex",
                "idempotency_key": "d3",
            }
        )
    with pytest.raises(ValidationError):
        DecisionRecordAppendInput.model_validate(
            {
                "case_id": CASE_ID,
                "decision_type": "watch",
                "title": "Watch",
                "rationale": "Need more data",
                "decided_at": AWARE.isoformat(),
                "decided_by": "user",
                "evidence_ids": ["rev_00000000-0000-7000-8000-000000000001"],
                "idempotency_key": "d4",
            }
        )
