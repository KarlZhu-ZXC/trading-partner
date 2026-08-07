from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from application.dto.research_context import ResearchContextBuildInput
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.services.research_context_builder import ResearchContextBuilder
from domain.common.enums import (
    EvidenceStance,
    Freshness,
    JournalEntryType,
    ResearchSubjectStatus,
    ResearchSubjectType,
)
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.mcp.server import PUBLIC_TOOL_NAMES, create_mcp_server

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
CASE_ID = "case_00000000-0000-7000-8000-000000000001"
CASE_ID_2 = "case_00000000-0000-7000-8000-000000000002"
IID = "equity:US:NVDA"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def new(self, prefix: object) -> str:
        return "req_00000000-0000-7000-8000-000000000001"


class _Accounts:
    def latest_accounts(self) -> tuple[object, ...]:
        return ()


def _case(subject_id: str = CASE_ID) -> ResearchSubject:
    return ResearchSubject(
        subject_id=subject_id,
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA",
        summary="Long horizon AI case",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=IID,
        topic_tags=("ai",),
        created_at=NOW,
        updated_at=NOW,
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_subject_ids=(),
        evidence_ids=(),
        report_ids=(),
        event_ids=(),
        decision_ids=(),
        schema_version=RESEARCH_SCHEMA_VERSION,
    )


def _uow(subject: ResearchSubject) -> MagicMock:
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = None
    uow.subjects.get.return_value = subject
    uow.theses.list_by_subject.return_value = ()
    uow.questions.list_by_subject.return_value = ()
    uow.watchlist.list.return_value = ()
    uow.candidates.list.return_value = ()
    uow.reports.list_by_subject.return_value = ()
    uow.events.list_timeline.return_value = ()
    uow.decisions.list_by_subject.return_value = ()
    uow.journal.list.return_value = tuple(
        SimpleNamespace(
            journal_id=f"journal_{index}",
            entry_type=JournalEntryType.OBSERVATION,
            title=f"Journal {index}",
            body_markdown="x" * 1_000,
            created_at=NOW,
        )
        for index in range(20)
    )
    support = SimpleNamespace(
        evidence_id="evidence_support",
        title="Support",
        summary="supporting evidence",
        source_name="source",
        observed_at=NOW,
    )
    contrary = SimpleNamespace(
        evidence_id="evidence_contrary",
        title="Contrary",
        summary="contrary evidence",
        source_name="source",
        observed_at=NOW,
    )
    uow.subject_evidence_links.list_evidence.return_value = (support, contrary)
    uow.trade_plans.get_current_by_subject.return_value = None

    def assessments(evidence_id: str, **_kwargs: object) -> tuple[SimpleNamespace, ...]:
        stance = (
            EvidenceStance.CONTRADICTS
            if evidence_id == "evidence_contrary"
            else EvidenceStance.SUPPORTS
        )
        return (
            SimpleNamespace(
                subject_id=subject.subject_id,
                stance=stance,
                materiality=Decimal("0.9"),
                rationale=stance.value,
            ),
        )

    uow.evidence_assessments.list_for_evidence.side_effect = assessments
    return uow


def _service(uow: MagicMock) -> ResearchContextBuilder:
    return ResearchContextBuilder(
        lambda: uow,
        _Accounts(),  # type: ignore[arg-type]
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )


def test_context_is_contrary_first_and_budget_trims_journal_not_evidence() -> None:
    service = _service(_uow(_case()))

    result = service.build(ResearchContextBuildInput(subject_id=CASE_ID, token_budget=2_000))

    assert result.ok is True
    assert result.data is not None
    assert [item.evidence_id for item in result.data.evidence] == [
        "evidence_contrary",
        "evidence_support",
    ]
    assert result.data.budget.truncated is True
    assert "journals" in result.data.budget.truncated_collections
    assert result.data.live_fact_tools_required == (
        "market_data_get/quote",
        "a_share_get_facts/snapshot",
        "us_company_get/fundamentals_snapshot",
        "us_company_get/company_updates",
    )


def test_instrument_selection_requires_explicit_case_disambiguation() -> None:
    uow = _uow(_case())
    uow.subjects.list.return_value = (_case(), _case(CASE_ID_2))
    service = _service(uow)

    result = service.build(ResearchContextBuildInput(instrument_id=IID))

    assert result.ok is False
    assert result.errors[0].code == "INPUT_VALIDATION_ERROR"
    assert result.errors[0].details["candidate_subject_ids"] == [CASE_ID, CASE_ID_2]


@pytest.mark.asyncio
async def test_context_mcp_is_compact_read_operation() -> None:
    container = MagicMock()
    container.settings.mcp_server_name = "phase1j-test"
    envelope = ToolEnvelope.failure(
        request_id="req_context",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(ErrorInfo(code="STUB", message="stub", retryable=False, details={}),),
        degraded=True,
        data=None,
    )
    container.services.research_context.build.return_value = envelope
    manager = create_mcp_server(container)._tool_manager
    listed = {tool.name: tool for tool in manager.list_tools()}

    assert set(listed) == set(PUBLIC_TOOL_NAMES)
    result = await manager.call_tool(
        "investment_case_read",
        {"request": {"operation": "context", "case_id": CASE_ID}},
    )
    assert result["request_id"] == "req_context"
    request = container.services.research_context.build.call_args.args[0]
    assert isinstance(request, ResearchContextBuildInput)
    assert request.subject_id == CASE_ID
