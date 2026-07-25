"""FastMCP stdio server for Trading Partner Phase 1 tools."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ImageContent, TextContent
from pydantic import ValidationError

from application.dto.a_share import (
    AShareGetCapitalSnapshotInput,
    AShareGetCompanyOperatingMetricsInput,
    AShareGetEtfOptionSnapshotInput,
    AShareGetFinancialStatementsInput,
    AShareGetIndustryCycleInput,
    AShareGetLimitUpContextInput,
    AShareGetMarketStructureInput,
    AShareGetSentimentSnapshotInput,
    AShareGetSnapshotInput,
    ResearchSearchReportsInput,
)
from application.dto.account_transactions import AccountGetTransactionsInput
from application.dto.challenge import (
    ChallengeReviewGetInput,
    ChallengeReviewResolveInput,
    ChallengeReviewStartInput,
)
from application.dto.error_mapper import to_error_info_from_exception
from application.dto.monitoring import (
    MonitorCadenceInput,
    MonitorCreateInput,
    MonitorEvaluateInput,
    MonitorEventActionInput,
    MonitorEventListInput,
    MonitorEventResolveInput,
    MonitorGetInput,
    MonitorListInput,
    MonitorRuleInput,
    MonitorStatusInput,
    MonitorUpdateInput,
)
from application.dto.portfolio import (
    AccountGetPositionsInput,
    AccountGetSnapshotInput,
    PortfolioAnalyzeInput,
    PortfolioSimulateAdditionInput,
)
from application.dto.research_context import ResearchContextBuildInput
from application.dto.research_memory import ResearchSearchQuery
from application.dto.risk import RiskCheckInput, RiskPolicyUpdateInput
from application.dto.technical import TechnicalAnalysisInput, TechnicalChartInput
from application.dto.tool_envelope import ToolEnvelope
from application.dto.us_context import (
    DEFAULT_MACRO_SERIES,
    MarketGetLiveNewsInput,
    USGetMacroContextInput,
    USGetPredictionMarketContextInput,
    USGetSentimentSnapshotInput,
)
from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetContextInput,
    MarketGetSnapshotInput,
    USGetSnapshotInput,
)
from application.dto.us_research import (
    EventsSearchInput,
    FundamentalGetSnapshotInput,
    FundamentalGetStatementsInput,
    ResearchGetCompanyUpdatesInput,
    USGetFilingsInput,
    USGetInsiderActivityInput,
)
from application.dto.watchlist_hub import (
    WatchlistAddInput,
    WatchlistGetGroupsInput,
    WatchlistGetItemsInput,
    WatchlistRemoveInput,
)
from application.dto.workflow import (
    AShareRunMarketReviewInput,
    PortfolioRunReviewInput,
    ResearchRunCatalystReviewInput,
    ResearchRunDeepDiveInput,
    USRunMarketReviewInput,
)
from bootstrap import ApplicationContainer, build_default_application
from domain.a_share.enums import FinancialStatementType
from domain.common.enums import AssetType, Freshness, Market
from domain.common.ids import EntityIdPrefix
from domain.monitoring.enums import MonitorCadence
from interfaces.mcp.chart_artifacts import persist_chart_png
from interfaces.mcp.schemas import (
    DecisionRecordAppendInput,
    InstrumentResolveInput,
    InvestmentCaseArchiveInput,
    InvestmentCaseCreateInput,
    InvestmentCaseGetInput,
    InvestmentCaseListInput,
    JournalAppendInput,
    ResearchReportGetInput,
    ResearchSearchInput,
    ResearchStateGetInput,
    ResearchStateUpdateInput,
    ResearchTimelineGetInput,
    ThesisHistoryGetInput,
    ThesisRevisionConfirmInput,
    ThesisRevisionProposeInput,
)

# Exact public tool surface after the pre-v0.1 façade consolidation: 52 tools.
PHASE1A_TOOL_NAMES: frozenset[str] = frozenset({"system_health"})
PHASE1B_RESEARCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "investment_case_create",
        "investment_case_query",
        "investment_case_archive",
        "research_state_get",
        "research_state_update",
        "thesis_revision_propose",
        "thesis_revision_confirm",
        "thesis_history_get",
    }
)
PHASE1C_RESEARCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "research_search",
        "research_report_get",
        "research_timeline_get",
        "journal_append",
        "decision_record_append",
    }
)
PHASE1D_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "instrument_resolve",
    }
)
PHASE1E_A_SHARE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "a_share_get_facts",
        "research_search_reports",
    }
)
PHASE1F_US_MARKET_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "market_get_bars",
        "market_get_context",
        "technical_get_snapshot",
        "us_get_market",
    }
)
PHASE1G_US_RESEARCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "us_get_fundamentals",
        "us_get_company_research",
    }
)
PHASE1H_US_CONTEXT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "market_get_live_news",
        "us_get_macro_context",
        "us_get_sentiment_snapshot",
        "us_get_prediction_market_context",
    }
)
PHASE1I_PORTFOLIO_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "account_get",
        "portfolio_analyze",
        "portfolio_simulate_addition",
    }
)
PHASE1J_CONTEXT_TOOL_NAMES: frozenset[str] = frozenset({"research_context_build"})
PHASE1K_CHALLENGE_TOOL_NAMES: frozenset[str] = frozenset(
    {"challenge_review_start", "challenge_review_get", "challenge_review_resolve"}
)
PHASE1L_WORKFLOW_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "research_run_deep_dive",
        "research_run_catalyst_review",
        "a_share_run_market_review",
        "us_run_market_review",
        "portfolio_run_review",
    }
)
PHASE2_WATCHLIST_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "watchlist_get",
        "watchlist_add",
        "watchlist_remove",
    }
)
PHASE2B_RISK_TOOL_NAMES: frozenset[str] = frozenset(
    {"risk_policy_get", "risk_policy_update", "risk_check"}
)
PHASE2C_MONITORING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "monitor_create",
        "monitor_query",
        "monitor_update",
        "monitor_evaluate",
        "monitor_event_list",
        "monitor_event_resolve",
    }
)
PHASE2D_TECHNICAL_TOOL_NAMES: frozenset[str] = frozenset({"technical_render_chart"})
LEGACY_PUBLIC_TOOL_NAMES: frozenset[str] = (
    PHASE1A_TOOL_NAMES
    | PHASE1B_RESEARCH_TOOL_NAMES
    | PHASE1C_RESEARCH_TOOL_NAMES
    | PHASE1D_TOOL_NAMES
)
PUBLIC_TOOL_NAMES: frozenset[str] = (
    LEGACY_PUBLIC_TOOL_NAMES
    | PHASE1E_A_SHARE_TOOL_NAMES
    | PHASE1F_US_MARKET_TOOL_NAMES
    | PHASE1G_US_RESEARCH_TOOL_NAMES
    | PHASE1H_US_CONTEXT_TOOL_NAMES
    | PHASE1I_PORTFOLIO_TOOL_NAMES
    | PHASE1J_CONTEXT_TOOL_NAMES
    | PHASE1K_CHALLENGE_TOOL_NAMES
    | PHASE1L_WORKFLOW_TOOL_NAMES
    | PHASE2_WATCHLIST_TOOL_NAMES
    | PHASE2B_RISK_TOOL_NAMES
    | PHASE2C_MONITORING_TOOL_NAMES
    | PHASE2D_TECHNICAL_TOOL_NAMES
)

# Internal write surfaces — must never appear as public MCP tools.
FORBIDDEN_PUBLIC_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "evidence_create",
        "evidence_update",
        "report_create",
        "event_create",
        "decision_update",
        "journal_update",
        "journal_delete",
        "order_place",
        "order_modify",
        "order_cancel",
        "trade_unlock",
    }
)

# Pre-v0.1 names removed by the breaking façade consolidation. Keeping this
# inventory makes accidental re-registration visible without supporting aliases.
RETIRED_PUBLIC_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "market_get_mock_snapshot",
        "investment_case_get",
        "investment_case_list",
        "journal_search",
        "a_share_get_snapshot",
        "a_share_get_market_structure",
        "a_share_get_capital_snapshot",
        "a_share_get_limit_up_context",
        "a_share_get_sentiment_snapshot",
        "a_share_get_etf_option_snapshot",
        "market_get_snapshot",
        "us_get_snapshot",
        "fundamental_get_snapshot",
        "fundamental_get_statements",
        "us_get_filings",
        "us_get_insider_activity",
        "research_get_company_updates",
        "events_search",
        "account_get_snapshot",
        "account_get_positions",
        "account_get_transactions",
        "watchlist_get_groups",
        "watchlist_get_items",
        "monitor_get",
        "monitor_list",
    }
)


def _unexpected_failure(
    container: ApplicationContainer,
    exc: BaseException,
) -> dict[str, Any]:
    """Map unexpected tool-handler exceptions to a Tool Envelope (never raise)."""
    request_id = container.id_generator.new(EntityIdPrefix.REQ)
    now = container.clock.now()
    err = to_error_info_from_exception(exc, container.secret_redactor)
    envelope: ToolEnvelope[None] = ToolEnvelope.failure(
        request_id=request_id,
        market=None,
        as_of=now,
        fetched_at=now,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(err,),
        degraded=True,
    )
    return envelope.model_dump(mode="json")


def create_mcp_server(container: ApplicationContainer) -> FastMCP:
    """Build a FastMCP server bound to the given application container."""
    server = FastMCP(container.settings.mcp_server_name)

    # ------------------------------------------------------------------ Phase 1A

    @server.tool(name="system_health")
    def system_health() -> dict[str, Any]:
        """Return application and database health as a Tool Envelope."""
        try:
            envelope = container.health_service.check()
            return envelope.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 — MCP must return ToolEnvelope
            return _unexpected_failure(container, exc)

    # ---------------------------------------------------------- Phase 1B research

    @server.tool(name="investment_case_create")
    def investment_case_create(
        case_type: str,
        title: str,
        summary: str,
        confirmed_by: str,
        idempotency_key: str,
        primary_instrument_id: str | None = None,
        topic_tags: list[str] | None = None,
        linked_case_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a user-confirmed research file (Investment Case).

        COMPANY and CATALYST files are anchored to an objective Instrument. The Case
        is the durable research file around it; creating one does not confirm a Thesis.
        """
        try:
            inp = InvestmentCaseCreateInput.model_validate(
                {
                    "case_type": case_type,
                    "title": title,
                    "summary": summary,
                    "primary_instrument_id": primary_instrument_id,
                    "topic_tags": tuple(topic_tags or ()),
                    "linked_case_ids": tuple(linked_case_ids or ()),
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.investment_case_service.create_case(
                case_type=inp.case_type,
                title=inp.title,
                summary=inp.summary,
                primary_instrument_id=inp.primary_instrument_id,
                topic_tags=inp.topic_tags,
                linked_case_ids=inp.linked_case_ids,
                confirmed_by=inp.confirmed_by,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="investment_case_query")
    def investment_case_query(
        case_id: str | None = None,
        case_type: str | None = None,
        status: str | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get one research file, or list research files with filters."""
        try:
            if case_id is not None:
                get_input = InvestmentCaseGetInput.model_validate({"case_id": case_id})
                return container.investment_case_service.get_case(get_input.case_id).model_dump(
                    mode="json"
                )
            list_input = InvestmentCaseListInput.model_validate(
                {
                    "case_type": case_type,
                    "status": status,
                    "primary_instrument_id": primary_instrument_id,
                    "topic_tag": topic_tag,
                    "include_archived": include_archived,
                    "limit": limit,
                    "offset": offset,
                }
            )
            envelope = container.investment_case_service.list_cases(
                case_type=list_input.case_type,
                status=list_input.status,
                primary_instrument_id=list_input.primary_instrument_id,
                topic_tag=list_input.topic_tag,
                include_archived=list_input.include_archived,
                limit=list_input.limit,
                offset=list_input.offset,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="investment_case_archive")
    def investment_case_archive(
        case_id: str,
        archived_reason: str,
        reviewed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Archive a research file without deleting its linked Instrument."""
        try:
            inp = InvestmentCaseArchiveInput.model_validate(
                {
                    "case_id": case_id,
                    "archived_reason": archived_reason,
                    "reviewed_by": reviewed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.investment_case_service.archive_case(
                inp.case_id,
                archived_reason=inp.archived_reason,
                reviewed_by=inp.reviewed_by,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_state_get")
    def research_state_get(
        case_id: str,
        include_archived_theses: bool = False,
        include_watchlist: bool = True,
    ) -> dict[str, Any]:
        """Return a research file's current judgments, assumptions, and open questions."""
        try:
            inp = ResearchStateGetInput.model_validate(
                {
                    "case_id": case_id,
                    "include_archived_theses": include_archived_theses,
                    "include_watchlist": include_watchlist,
                }
            )
            envelope = container.research_state_query_service.get_state(
                inp.case_id,
                include_archived_theses=inp.include_archived_theses,
                include_watchlist=inp.include_watchlist,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_state_update")
    def research_state_update(
        payload: dict[str, Any],
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        case_id: str | None = None,
        confirmation_mode: str = "strict_review",
    ) -> dict[str, Any]:
        """Propose Assumption / Invalidation / OpenQuestion / Watchlist / Case updates."""
        try:
            inp = ResearchStateUpdateInput.model_validate(
                {
                    "case_id": case_id,
                    "payload": payload,
                    "confirmation_mode": confirmation_mode,
                    "proposed_by": proposed_by,
                    "proposed_by_rationale": proposed_by_rationale,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.thesis_revision_service.propose_state_update(
                case_id=inp.case_id,
                payload=inp.payload,
                confirmation_mode=inp.confirmation_mode,
                proposed_by=inp.proposed_by,
                proposed_by_rationale=inp.proposed_by_rationale,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="thesis_revision_propose")
    def thesis_revision_propose(
        case_id: str,
        payload: dict[str, Any],
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        thesis_id: str | None = None,
        confirmation_mode: str = "strict_review",
    ) -> dict[str, Any]:
        """Propose a revision to an investment judgment in a research file."""
        try:
            inp = ThesisRevisionProposeInput.model_validate(
                {
                    "case_id": case_id,
                    "thesis_id": thesis_id,
                    "payload": payload,
                    "confirmation_mode": confirmation_mode,
                    "proposed_by": proposed_by,
                    "proposed_by_rationale": proposed_by_rationale,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.thesis_revision_service.propose_revision(
                case_id=inp.case_id,
                thesis_id=inp.thesis_id,
                payload=inp.payload,
                confirmation_mode=inp.confirmation_mode,
                proposed_by=inp.proposed_by,
                proposed_by_rationale=inp.proposed_by_rationale,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="thesis_revision_confirm")
    def thesis_revision_confirm(
        candidate_id: str,
        reviewed_by: str,
        action: str = "confirm",
        review_note: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict[str, Any]:
        """Confirm, reject, or withdraw a candidate (action=confirm|reject|withdraw)."""
        try:
            inp = ThesisRevisionConfirmInput.model_validate(
                {
                    "candidate_id": candidate_id,
                    "action": action,
                    "reviewed_by": reviewed_by,
                    "review_note": review_note,
                    "rejection_reason": rejection_reason,
                }
            )
            if inp.action == "confirm":
                return container.thesis_revision_service.confirm_candidate(
                    inp.candidate_id,
                    reviewed_by=inp.reviewed_by,
                    review_note=inp.review_note,
                ).model_dump(mode="json")
            if inp.action == "reject":
                assert inp.rejection_reason is not None
                return container.thesis_revision_service.reject_candidate(
                    inp.candidate_id,
                    reviewed_by=inp.reviewed_by,
                    rejection_reason=inp.rejection_reason,
                ).model_dump(mode="json")
            assert inp.review_note is not None
            return container.thesis_revision_service.withdraw_candidate(
                inp.candidate_id,
                reviewed_by=inp.reviewed_by,
                review_note=inp.review_note,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="thesis_history_get")
    def thesis_history_get(thesis_id: str) -> dict[str, Any]:
        """Return append-only history for one investment judgment (Thesis)."""
        try:
            inp = ThesisHistoryGetInput.model_validate({"thesis_id": thesis_id})
            envelope = container.thesis_revision_service.get_revision_history(inp.thesis_id)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ---------------------------------------------------------- Phase 1C research memory

    @server.tool(name="research_search")
    def research_search(
        text: str | None = None,
        case_id: str | None = None,
        thesis_id: str | None = None,
        instrument_id: str | None = None,
        entity_types: list[str] | None = None,
        evidence_types: list[str] | None = None,
        journal_entry_types: list[str] | None = None,
        stances: list[str] | None = None,
        topic_tags: list[str] | None = None,
        visible_from: datetime | None = None,
        visible_to: datetime | None = None,
        as_of: datetime | None = None,
        include_superseded: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Full-text + structured research-memory search (no Evidence create)."""
        try:
            inp = ResearchSearchInput.model_validate(
                {
                    "text": text,
                    "case_id": case_id,
                    "thesis_id": thesis_id,
                    "instrument_id": instrument_id,
                    "entity_types": tuple(entity_types or ()),
                    "evidence_types": tuple(evidence_types or ()),
                    "journal_entry_types": tuple(journal_entry_types or ()),
                    "stances": tuple(stances or ()),
                    "topic_tags": tuple(topic_tags or ()),
                    "visible_from": visible_from,
                    "visible_to": visible_to,
                    "as_of": as_of,
                    "include_superseded": include_superseded,
                    "limit": limit,
                    "offset": offset,
                }
            )
            query = ResearchSearchQuery(
                text=inp.text,
                case_id=inp.case_id,
                thesis_id=inp.thesis_id,
                instrument_id=inp.instrument_id,
                entity_types=inp.entity_types,
                evidence_types=inp.evidence_types,
                journal_entry_types=inp.journal_entry_types,
                stances=inp.stances,
                topic_tags=inp.topic_tags,
                visible_from=inp.visible_from,
                visible_to=inp.visible_to,
                as_of=inp.as_of,
                include_superseded=inp.include_superseded,
                limit=inp.limit,
                offset=inp.offset,
            )
            envelope = container.research_search_service.search(query)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_report_get")
    def research_report_get(report_id: str) -> dict[str, Any]:
        """Fetch one immutable ResearchReport by id."""
        try:
            inp = ResearchReportGetInput.model_validate({"report_id": report_id})
            envelope = container.research_archive_service.get_report(inp.report_id)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_timeline_get")
    def research_timeline_get(
        case_id: str,
        entity_types: list[str] | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Unified case research timeline projection."""
        try:
            inp = ResearchTimelineGetInput.model_validate(
                {
                    "case_id": case_id,
                    "entity_types": tuple(entity_types or ()),
                    "occurred_from": occurred_from,
                    "occurred_to": occurred_to,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = container.research_timeline_service.get_timeline(
                case_id=inp.case_id,
                entity_types=inp.entity_types,
                occurred_from=inp.occurred_from,
                occurred_to=inp.occurred_to,
                as_of=inp.as_of,
                limit=inp.limit,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="journal_append")
    def journal_append(
        entry_type: str,
        title: str,
        body_markdown: str,
        authored_by: str,
        confirmed_by: str,
        idempotency_key: str,
        case_id: str | None = None,
        instrument_ids: list[str] | None = None,
        topic_tags: list[str] | None = None,
        related_entity_type: str | None = None,
        related_entity_id: str | None = None,
        supersedes_journal_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a user-confirmed journal entry (never auto-writes chat)."""
        try:
            inp = JournalAppendInput.model_validate(
                {
                    "case_id": case_id,
                    "entry_type": entry_type,
                    "title": title,
                    "body_markdown": body_markdown,
                    "authored_by": authored_by,
                    "confirmed_by": confirmed_by,
                    "instrument_ids": tuple(instrument_ids or ()),
                    "topic_tags": tuple(topic_tags or ()),
                    "related_entity_type": related_entity_type,
                    "related_entity_id": related_entity_id,
                    "supersedes_journal_id": supersedes_journal_id,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.journal_service.append(
                case_id=inp.case_id,
                entry_type=inp.entry_type,
                title=inp.title,
                body_markdown=inp.body_markdown,
                authored_by=inp.authored_by,
                confirmed_by=inp.confirmed_by,
                instrument_ids=inp.instrument_ids,
                topic_tags=inp.topic_tags,
                related_entity_type=inp.related_entity_type,
                related_entity_id=inp.related_entity_id,
                supersedes_journal_id=inp.supersedes_journal_id,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="decision_record_append")
    def decision_record_append(
        case_id: str,
        decision_type: str,
        title: str,
        rationale: str,
        decided_at: datetime,
        decided_by: str,
        idempotency_key: str,
        confirmation_mode: str = "strict_review",
        primary_instrument_id: str | None = None,
        thesis_revision_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        report_ids: list[str] | None = None,
        supersedes_decision_id: str | None = None,
        position_context_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a research/position intent DecisionRecord (no order/fill writes)."""
        try:
            inp = DecisionRecordAppendInput.model_validate(
                {
                    "case_id": case_id,
                    "decision_type": decision_type,
                    "title": title,
                    "rationale": rationale,
                    "decided_at": decided_at,
                    "decided_by": decided_by,
                    "confirmation_mode": confirmation_mode,
                    "primary_instrument_id": primary_instrument_id,
                    "thesis_revision_ids": tuple(thesis_revision_ids or ()),
                    "evidence_ids": tuple(evidence_ids or ()),
                    "report_ids": tuple(report_ids or ()),
                    "supersedes_decision_id": supersedes_decision_id,
                    "position_context_snapshot_id": position_context_snapshot_id,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.decision_record_service.append(
                case_id=inp.case_id,
                decision_type=inp.decision_type,
                title=inp.title,
                rationale=inp.rationale,
                decided_at=inp.decided_at,
                decided_by=inp.decided_by,
                confirmation_mode=inp.confirmation_mode,
                primary_instrument_id=inp.primary_instrument_id,
                thesis_revision_ids=inp.thesis_revision_ids,
                evidence_ids=inp.evidence_ids,
                report_ids=inp.report_ids,
                supersedes_decision_id=inp.supersedes_decision_id,
                position_context_snapshot_id=inp.position_context_snapshot_id,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ---------------------------------------------------------- Phase 1D instrument

    @server.tool(name="instrument_resolve")
    async def instrument_resolve(
        market: str,
        query: str,
        asset_type: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve locally, then discover and cache a validated external instrument."""
        try:
            inp = InstrumentResolveInput.model_validate(
                {
                    "market": market,
                    "query": query,
                    "asset_type": asset_type,
                    "as_of": as_of,
                }
            )
            market_enum = inp.market if isinstance(inp.market, Market) else Market(inp.market)
            asset_hint: AssetType | None
            if inp.asset_type is None:
                asset_hint = None
            elif isinstance(inp.asset_type, AssetType):
                asset_hint = inp.asset_type
            else:
                asset_hint = AssetType(inp.asset_type)
            envelope = await container.instrument_resolve_service.resolve_dynamic(
                market=market_enum,
                query=inp.query,
                asset_type_hint=asset_hint,
                as_of=inp.as_of,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ---------------------------------------------------------- Phase 1E A-share

    @server.tool(name="a_share_get_facts")
    async def a_share_get_facts(
        operation: Literal[
            "snapshot",
            "market_structure",
            "capital",
            "limit_up",
            "sentiment",
            "etf_option",
            "financials",
            "industry_cycle",
            "company_operating_metrics",
        ] = "snapshot",
        instrument_id: str | None = None,
        as_of: datetime | None = None,
        detail: str = "summary",
        scope: str = "instrument",
        trade_date: date | None = None,
        start: date | None = None,
        end: date | None = None,
        interval: str = "1d",
        adjustment: str = "forward_adjusted",
        include_bars: bool | None = None,
        include_order_book: bool | None = None,
        include_ticks: bool = False,
        include_industries: bool | None = None,
        include_market_board: bool | None = None,
        industry_limit: int = 20,
        tick_limit: int = 100,
        metrics: list[str] | None = None,
        pools: list[str] | None = None,
        sentiment_sources: list[str] | None = None,
        expiry: date | None = None,
        strike_center: str | None = None,
        strike_count_each_side: int = 5,
        cycle: Literal["hog"] = "hog",
        lookback_months: int = 12,
        document_limit: int = 10,
        view: Literal["compact", "series"] = "compact",
        metric_codes: list[str] | None = None,
        statement_types: list[str] | None = None,
        periods: int = 8,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read one A-share fact family selected by a closed operation name."""
        if operation == "snapshot":
            if instrument_id is None:
                raise ValueError("instrument_id is required for operation=snapshot")
            return await a_share_get_snapshot(instrument_id, as_of, detail)
        if operation == "market_structure":
            return await a_share_get_market_structure(
                scope,
                instrument_id,
                trade_date,
                start,
                end,
                interval,
                adjustment,
                include_bars,
                include_order_book,
                include_ticks,
                include_industries,
                include_market_board,
                industry_limit,
                tick_limit,
                as_of,
            )
        if operation == "capital":
            return await a_share_get_capital_snapshot(instrument_id, metrics, start, end, as_of)
        if operation == "limit_up":
            if trade_date is None:
                raise ValueError("trade_date is required for operation=limit_up")
            return await a_share_get_limit_up_context(trade_date, pools, as_of)
        if operation == "sentiment":
            return await a_share_get_sentiment_snapshot(
                instrument_id, sentiment_sources, trade_date, as_of
            )
        if operation == "etf_option":
            return await a_share_get_etf_option_snapshot(
                instrument_id or "", expiry, strike_center, strike_count_each_side, as_of
            )
        if operation == "financials":
            if instrument_id is None:
                raise ValueError("instrument_id is required for operation=financials")
            try:
                financial_input = AShareGetFinancialStatementsInput.model_validate(
                    {
                        "instrument_id": instrument_id,
                        "statement_types": tuple(statement_types or FinancialStatementType),
                        "periods": periods,
                        "metric_codes": tuple(metric_codes or ()),
                        "as_of": as_of,
                    }
                )
                return (
                    await container.a_share_tool_coordinator.get_financial_statements(
                        financial_input
                    )
                ).model_dump(mode="json")
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                return _unexpected_failure(container, exc)
        if operation == "industry_cycle":
            try:
                industry_cycle_input = AShareGetIndustryCycleInput.model_validate(
                    {
                        "cycle": cycle,
                        "lookback_months": lookback_months,
                        "view": view,
                        "metric_codes": tuple(metric_codes or ()),
                        "offset": offset,
                        "limit": limit,
                        "as_of": as_of,
                    }
                )
                return (
                    await container.a_share_tool_coordinator.get_industry_cycle(
                        industry_cycle_input
                    )
                ).model_dump(mode="json")
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                return _unexpected_failure(container, exc)
        if operation == "company_operating_metrics":
            if instrument_id is None:
                raise ValueError(
                    "instrument_id is required for operation=company_operating_metrics"
                )
            try:
                company_metrics_input = AShareGetCompanyOperatingMetricsInput.model_validate(
                    {
                        "instrument_id": instrument_id,
                        "lookback_months": lookback_months,
                        "document_limit": document_limit,
                        "metric_codes": tuple(metric_codes or ()),
                        "as_of": as_of,
                    }
                )
                return (
                    await container.a_share_tool_coordinator.get_company_operating_metrics(
                        company_metrics_input
                    )
                ).model_dump(mode="json")
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                return _unexpected_failure(container, exc)
        raise ValueError(
            "operation must be snapshot, market_structure, capital, limit_up, "
            "sentiment, etf_option, financials, industry_cycle, or "
            "company_operating_metrics"
        )

    async def a_share_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        detail: str = "summary",
    ) -> dict[str, Any]:
        try:
            inp = AShareGetSnapshotInput.model_validate(
                {"instrument_id": instrument_id, "as_of": as_of, "detail": detail}
            )
            return (await container.a_share_tool_coordinator.get_snapshot(inp)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_market_structure(
        scope: str = "instrument",
        instrument_id: str | None = None,
        trade_date: date | None = None,
        start: date | None = None,
        end: date | None = None,
        interval: str = "1d",
        adjustment: str = "forward_adjusted",
        include_bars: bool | None = None,
        include_order_book: bool | None = None,
        include_ticks: bool = False,
        include_industries: bool | None = None,
        include_market_board: bool | None = None,
        industry_limit: int = 20,
        tick_limit: int = 100,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share market-structure snapshot Tool Envelope."""
        try:
            inp = AShareGetMarketStructureInput.model_validate(
                {
                    "scope": scope,
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "start": start,
                    "end": end,
                    "interval": interval,
                    "adjustment": adjustment,
                    "include_bars": include_bars,
                    "include_order_book": include_order_book,
                    "include_ticks": include_ticks,
                    "include_industries": include_industries,
                    "include_market_board": include_market_board,
                    "industry_limit": industry_limit,
                    "tick_limit": tick_limit,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_market_structure(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_capital_snapshot(
        instrument_id: str | None = None,
        metrics: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share capital/flow snapshot Tool Envelope."""
        try:
            inp = AShareGetCapitalSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "metrics": tuple(metrics or ()),
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_capital_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_limit_up_context(
        trade_date: date,
        pools: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return A-share limit-up / limit-down context Tool Envelope."""
        try:
            inp = AShareGetLimitUpContextInput.model_validate(
                {
                    "trade_date": trade_date,
                    "pools": tuple(pools or ()),
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_limit_up_context(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_sentiment_snapshot(
        instrument_id: str | None = None,
        sources: list[str] | None = None,
        trade_date: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share sentiment/heat snapshot Tool Envelope."""
        try:
            inp = AShareGetSentimentSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "sources": tuple(sources or ()),
                    "trade_date": trade_date,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_sentiment_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def a_share_get_etf_option_snapshot(
        underlying_instrument_id: str,
        expiry: date | None = None,
        strike_center: str | None = None,
        strike_count_each_side: int = 5,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an A-share ETF option chain snapshot Tool Envelope."""
        try:
            inp = AShareGetEtfOptionSnapshotInput.model_validate(
                {
                    "underlying_instrument_id": underlying_instrument_id,
                    "expiry": expiry,
                    "strike_center": strike_center,
                    "strike_count_each_side": strike_count_each_side,
                    "as_of": as_of,
                }
            )
            envelope = await container.a_share_tool_coordinator.get_etf_option_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_search_reports")
    async def research_search_reports(
        text: str | None = None,
        instrument_id: str | None = None,
        industry_code: str | None = None,
        published_from: date | None = None,
        published_to: date | None = None,
        include_consensus: bool = True,
        as_of: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search external A-share research reports (no ResearchReport archive write)."""
        try:
            inp = ResearchSearchReportsInput.model_validate(
                {
                    "text": text,
                    "instrument_id": instrument_id,
                    "industry_code": industry_code,
                    "published_from": published_from,
                    "published_to": published_to,
                    "include_consensus": include_consensus,
                    "as_of": as_of,
                    "limit": limit,
                    "offset": offset,
                }
            )
            envelope = await container.a_share_tool_coordinator.search_reports(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ---------------------------------------------------------- Phase 1F US market

    @server.tool(name="us_get_market")
    async def us_get_market(
        instrument_id: str,
        as_of: datetime | None = None,
        operation: Literal["quote", "composite"] = "quote",
        lookback_sessions: int = 260,
    ) -> dict[str, Any]:
        """Return a lightweight quote or the full US composite snapshot."""
        if operation == "composite":
            return await us_get_snapshot(instrument_id, as_of, lookback_sessions)
        if operation != "quote":
            raise ValueError("operation must be quote or composite")
        try:
            inp = MarketGetSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                }
            )
            envelope = await container.us_tool_coordinator.get_market_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="market_get_bars")
    async def market_get_bars(
        instrument_id: str,
        start: date,
        end: date,
        interval: str = "1d",
        adjustment: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return US equity/index/futures OHLCV; futures default to unadjusted."""
        try:
            inp = MarketGetBarsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "start": start,
                    "end": end,
                    "interval": interval,
                    "adjustment": adjustment,
                    "as_of": as_of,
                }
            )
            envelope = await container.us_tool_coordinator.get_market_bars(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="market_get_context")
    async def market_get_context(
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return US proxy, best-effort breadth, and sector-rotation context."""
        try:
            inp = MarketGetContextInput.model_validate({"as_of": as_of})
            envelope = await container.us_tool_coordinator.get_market_context(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="technical_get_snapshot")
    async def technical_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        lookback_sessions: int = 260,
        intervals: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return professional daily/weekly technical facts for an A-share or US instrument."""
        try:
            inp = TechnicalAnalysisInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_sessions": lookback_sessions,
                    "intervals": tuple(intervals or ("1d", "1w")),
                }
            )
            envelope = await container.technical_tool_coordinator.get_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="technical_render_chart")
    async def technical_render_chart(
        instrument_id: str,
        as_of: datetime | None = None,
        interval: str = "1d",
        lookback_sessions: int = 160,
    ) -> list[TextContent | ImageContent]:
        """Return an auditable technical-analysis envelope followed by a PNG chart."""
        inp = TechnicalChartInput.model_validate(
            {
                "instrument_id": instrument_id,
                "as_of": as_of,
                "interval": interval,
                "lookback_sessions": lookback_sessions,
            }
        )
        artifact = await container.technical_tool_coordinator.render_chart(inp)
        content: list[TextContent | ImageContent] = [
            TextContent(
                type="text",
                text=json.dumps(
                    artifact.envelope.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        ]
        if artifact.png is not None:
            local = persist_chart_png(
                artifact.png,
                request_id=artifact.envelope.request_id,
            )
            content.append(
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "chart_artifact": {
                                "path": str(local.path),
                                "mime_type": local.mime_type,
                                "display_markdown": local.markdown,
                                "instruction": (
                                    "Embed display_markdown in the assistant response so the "
                                    "local chart is visible in Codex."
                                ),
                            }
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
            content.append(Image(data=artifact.png, format="png").to_image_content())
        return content

    async def us_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        lookback_sessions: int = 260,
    ) -> dict[str, Any]:
        """Return a US composite snapshot (quote/bars/technical/context) Tool Envelope."""
        try:
            inp = USGetSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_sessions": lookback_sessions,
                }
            )
            envelope = await container.us_tool_coordinator.get_us_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # -------------------------------------------------------- Phase 1G US research

    @server.tool(name="us_get_fundamentals")
    async def us_get_fundamentals(
        instrument_id: str,
        as_of: datetime | None = None,
        operation: Literal["snapshot", "statements"] = "snapshot",
        frequency: str = "quarterly",
        limit: int = 8,
        view: Literal["latest", "vintages"] = "latest",
    ) -> dict[str, Any]:
        """Return a US fundamental snapshot or normalized statements."""
        if operation == "statements":
            return await fundamental_get_statements(
                instrument_id, frequency, as_of, limit, view
            )
        if operation != "snapshot":
            raise ValueError("operation must be snapshot or statements")
        try:
            inp = FundamentalGetSnapshotInput.model_validate(
                {"instrument_id": instrument_id, "as_of": as_of}
            )
            envelope = await container.us_research_tool_coordinator.get_fundamental_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def fundamental_get_statements(
        instrument_id: str,
        frequency: str = "quarterly",
        as_of: datetime | None = None,
        limit: int = 8,
        view: str = "latest",
    ) -> dict[str, Any]:
        """Return normalized US income, balance-sheet, and cash-flow periods."""
        try:
            inp = FundamentalGetStatementsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "frequency": frequency,
                    "as_of": as_of,
                    "limit": limit,
                    "view": view,
                }
            )
            envelope = await container.us_research_tool_coordinator.get_fundamental_statements(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="us_get_company_research")
    async def us_get_company_research(
        operation: Literal["filings", "insider_activity", "company_updates", "events"] = "filings",
        instrument_id: str | None = None,
        forms: tuple[str, ...] = (),
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        include_sections: bool = False,
        limit: int = 20,
        since: datetime | None = None,
        event_types: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Read filings, insider activity, company updates, or external events."""
        if operation == "insider_activity":
            if instrument_id is None:
                raise ValueError("instrument_id is required for insider_activity")
            return await us_get_insider_activity(instrument_id, start, end, as_of, limit)
        if operation == "company_updates":
            if instrument_id is None:
                raise ValueError("instrument_id is required for company_updates")
            return await research_get_company_updates(instrument_id, since, as_of, limit)
        if operation == "events":
            return await events_search(instrument_id, event_types, start, end, as_of, limit)
        if operation != "filings":
            raise ValueError(
                "operation must be filings, insider_activity, company_updates, or events"
            )
        if instrument_id is None:
            raise ValueError("instrument_id is required for filings")
        try:
            inp = USGetFilingsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "forms": forms,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "include_sections": include_sections,
                    "limit": limit,
                }
            )
            envelope = await container.us_research_tool_coordinator.get_filings(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def us_get_insider_activity(
        instrument_id: str,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return visible-at-as_of SEC/Alpha insider transactions."""
        try:
            inp = USGetInsiderActivityInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.us_research_tool_coordinator.get_insider_activity(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def research_get_company_updates(
        instrument_id: str,
        since: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Merge recent filings, insider activity, and corporate actions."""
        try:
            inp = ResearchGetCompanyUpdatesInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "since": since,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.us_research_tool_coordinator.get_company_updates(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def events_search(
        instrument_id: str | None = None,
        event_types: tuple[str, ...] = (),
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search the Phase 1G external-event view."""
        try:
            inp = EventsSearchInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "event_types": event_types,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.us_research_tool_coordinator.search_events(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # --------------------------------------------------------- Phase 1H US context

    @server.tool(name="market_get_live_news")
    async def market_get_live_news(
        instrument_id: str | None = None,
        query: str | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return dated US company or global market news."""
        try:
            inp = MarketGetLiveNewsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "query": query,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.us_context_tool_coordinator.get_live_news(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="us_get_macro_context")
    async def us_get_macro_context(
        series_ids: tuple[str, ...] = DEFAULT_MACRO_SERIES,
        lookback_days: int = 365,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return FRED macro series with historical-vintage cutoffs."""
        try:
            inp = USGetMacroContextInput.model_validate(
                {
                    "series_ids": series_ids,
                    "lookback_days": lookback_days,
                    "as_of": as_of,
                }
            )
            envelope = await container.us_context_tool_coordinator.get_macro_context(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="us_get_sentiment_snapshot")
    async def us_get_sentiment_snapshot(
        instrument_id: str,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit_per_source: int = 20,
    ) -> dict[str, Any]:
        """Return explicit and inferred US discussion sentiment by source."""
        try:
            inp = USGetSentimentSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit_per_source": limit_per_source,
                }
            )
            envelope = await container.us_context_tool_coordinator.get_sentiment_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="us_get_prediction_market_context")
    async def us_get_prediction_market_context(
        topic: str,
        as_of: datetime | None = None,
        limit: int = 6,
    ) -> dict[str, Any]:
        """Return current open Polymarket probabilities for a topic."""
        try:
            inp = USGetPredictionMarketContextInput.model_validate(
                {"topic": topic, "as_of": as_of, "limit": limit}
            )
            envelope = await container.us_context_tool_coordinator.get_prediction_market_context(
                inp
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # --------------------------------------------------- Phase 1I account/portfolio

    @server.tool(name="account_get")
    async def account_get(
        operation: Literal["positions", "refresh", "transactions"] = "positions",
        providers: tuple[str, ...] = (),
        as_of: datetime | None = None,
        snapshot_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read durable positions, explicitly refresh accounts, or fetch transactions."""
        if operation == "positions":
            return account_get_positions(snapshot_id)
        if operation == "transactions":
            return await account_get_transactions(providers, start, end, limit)
        if operation != "refresh":
            raise ValueError("operation must be positions, refresh, or transactions")
        try:
            inp = AccountGetSnapshotInput.model_validate({"providers": providers, "as_of": as_of})
            envelope = await container.portfolio_tool_coordinator.get_account_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def account_get_positions(snapshot_id: str | None = None) -> dict[str, Any]:
        """Return positions from one snapshot or the latest durable accounts."""
        try:
            inp = AccountGetPositionsInput.model_validate({"snapshot_id": snapshot_id})
            envelope = container.portfolio_tool_coordinator.get_account_positions(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="portfolio_analyze")
    def portfolio_analyze(
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> dict[str, Any]:
        """Compute deterministic gross exposure without implicit FX conversion."""
        try:
            inp = PortfolioAnalyzeInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "base_currency": base_currency,
                }
            )
            envelope = container.portfolio_tool_coordinator.analyze_portfolio(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="portfolio_simulate_addition")
    def portfolio_simulate_addition(
        instrument_id: str,
        quantity: Decimal,
        assumed_price: Decimal,
        currency: str,
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> dict[str, Any]:
        """Compare gross exposure before/after a hypothetical non-executing addition."""
        try:
            inp = PortfolioSimulateAdditionInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "instrument_id": instrument_id,
                    "quantity": quantity,
                    "assumed_price": assumed_price,
                    "currency": currency,
                    "base_currency": base_currency,
                }
            )
            envelope = container.portfolio_tool_coordinator.simulate_addition(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ----------------------------------------------- Phase 2B Portfolio Risk Engine

    @server.tool(name="risk_policy_get")
    def risk_policy_get() -> dict[str, Any]:
        """Return the current versioned portfolio risk policy."""
        try:
            return container.risk_tool_coordinator.get_policy().model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="risk_policy_update")
    def risk_policy_update(
        single_position_max_percent: Decimal,
        gross_exposure_max_percent: Decimal,
        minimum_cash_percent: Decimal,
        margin_usage_max_percent: Decimal,
        max_account_age_seconds: int,
        max_price_age_seconds: int,
        expected_version: int,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Append a confirmed risk-policy version; this never executes an order."""
        try:
            inp = RiskPolicyUpdateInput.model_validate(
                {
                    "single_position_max_percent": single_position_max_percent,
                    "gross_exposure_max_percent": gross_exposure_max_percent,
                    "minimum_cash_percent": minimum_cash_percent,
                    "margin_usage_max_percent": margin_usage_max_percent,
                    "max_account_age_seconds": max_account_age_seconds,
                    "max_price_age_seconds": max_price_age_seconds,
                    "expected_version": expected_version,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.risk_tool_coordinator.update_policy(inp).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="risk_check")
    async def risk_check(
        account_snapshot_ids: tuple[str, ...] = (),
        refresh_accounts: bool = False,
        providers: tuple[str, ...] = (),
        hypothetical_instrument_id: str | None = None,
        hypothetical_quantity: Decimal | None = None,
        hypothetical_assumed_price: Decimal | None = None,
        hypothetical_currency: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Use durable accounts by default; refresh only on an explicit user request."""
        try:
            inp = RiskCheckInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "refresh_accounts": refresh_accounts,
                    "providers": providers,
                    "hypothetical_instrument_id": hypothetical_instrument_id,
                    "hypothetical_quantity": hypothetical_quantity,
                    "hypothetical_assumed_price": hypothetical_assumed_price,
                    "hypothetical_currency": hypothetical_currency,
                    "as_of": as_of,
                }
            )
            return (await container.risk_tool_coordinator.check(inp)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # --------------------------------------------------------- Phase 2C Monitoring

    @server.tool(name="monitor_create")
    def monitor_create(
        name: str,
        rules: tuple[MonitorRuleInput, ...],
        confirmed_by: str,
        idempotency_key: str,
        case_id: str | None = None,
        primary_instrument_id: str | None = None,
        cadence: MonitorCadenceInput = MonitorCadence.ON_DEMAND,
        valid_until: datetime | None = None,
    ) -> dict[str, Any]:
        """Create one confirmed, versioned, non-executing monitor."""
        try:
            request = MonitorCreateInput.model_validate(
                {
                    "name": name,
                    "case_id": case_id,
                    "primary_instrument_id": primary_instrument_id,
                    "cadence": cadence,
                    "rules": rules,
                    "valid_until": valid_until,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.monitor_tool_coordinator.create(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="monitor_query")
    def monitor_query(
        monitor_id: str | None = None,
        status: MonitorStatusInput | None = None,
    ) -> dict[str, Any]:
        """Restore one monitor, or filter by ACTIVE/PAUSED/ARCHIVED (case-insensitive)."""
        if monitor_id is None:
            return monitor_list(status)
        try:
            request = MonitorGetInput(monitor_id=monitor_id)
            return container.monitor_tool_coordinator.get(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def monitor_list(status: MonitorStatusInput | None = None) -> dict[str, Any]:
        """List current monitor versions, optionally filtered by status."""
        try:
            request = MonitorListInput.model_validate({"status": status})
            return container.monitor_tool_coordinator.list(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="monitor_update")
    def monitor_update(
        monitor_id: str,
        expected_version: int,
        name: str,
        cadence: MonitorCadenceInput,
        status: MonitorStatusInput,
        rules: tuple[MonitorRuleInput, ...],
        confirmed_by: str,
        idempotency_key: str,
        case_id: str | None = None,
        primary_instrument_id: str | None = None,
        valid_until: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a confirmed monitor version, including pause/archive changes."""
        try:
            request = MonitorUpdateInput.model_validate(
                {
                    "monitor_id": monitor_id,
                    "expected_version": expected_version,
                    "name": name,
                    "case_id": case_id,
                    "primary_instrument_id": primary_instrument_id,
                    "cadence": cadence,
                    "status": status,
                    "rules": rules,
                    "valid_until": valid_until,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.monitor_tool_coordinator.update(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="monitor_evaluate")
    async def monitor_evaluate(
        monitor_ids: tuple[str, ...] = (),
        cadence: MonitorCadenceInput | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate active monitors and persist only rule-state transitions."""
        try:
            request = MonitorEvaluateInput.model_validate(
                {"monitor_ids": monitor_ids, "cadence": cadence, "as_of": as_of}
            )
            return (await container.monitor_tool_coordinator.evaluate(request)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="monitor_event_list")
    def monitor_event_list(
        monitor_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List durable monitor transition events with latest resolution."""
        try:
            request = MonitorEventListInput(monitor_id=monitor_id, limit=limit)
            return container.monitor_tool_coordinator.list_events(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="monitor_event_resolve")
    def monitor_event_resolve(
        event_id: str,
        action: MonitorEventActionInput,
        note: str,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Acknowledge or resolve one event; never mutate Thesis or positions."""
        try:
            request = MonitorEventResolveInput.model_validate(
                {
                    "event_id": event_id,
                    "action": action,
                    "note": note,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.monitor_tool_coordinator.resolve_event(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ------------------------------------------------------- Phase 1J context restore

    @server.tool(name="research_context_build")
    def research_context_build(
        case_id: str | None = None,
        instrument_id: str | None = None,
        since: datetime | None = None,
        token_budget: int = 4_000,
    ) -> dict[str, Any]:
        """Build one current durable research context package for a fresh thread."""
        try:
            inp = ResearchContextBuildInput.model_validate(
                {
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "since": since,
                    "token_budget": token_budget,
                }
            )
            envelope = container.research_context_builder.build(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ------------------------------------------------------ Phase 1K challenge mode

    @server.tool(name="challenge_review_start")
    def challenge_review_start(
        case_id: str,
        trigger: str,
        proposed_action: str,
        related_candidate_id: str | None = None,
        related_evidence_ids: tuple[str, ...] = (),
        position_context_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Start a deterministic strict review, or bypass ordinary discussion."""
        try:
            inp = ChallengeReviewStartInput.model_validate(
                {
                    "case_id": case_id,
                    "trigger": trigger,
                    "proposed_action": proposed_action,
                    "related_candidate_id": related_candidate_id,
                    "related_evidence_ids": related_evidence_ids,
                    "position_context_snapshot_id": position_context_snapshot_id,
                }
            )
            return container.challenge_review_service.start(inp).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="challenge_review_get")
    def challenge_review_get(review_id: str) -> dict[str, Any]:
        """Get one persisted Challenge Review."""
        try:
            inp = ChallengeReviewGetInput.model_validate({"review_id": review_id})
            return container.challenge_review_service.get(inp.review_id).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="challenge_review_resolve")
    def challenge_review_resolve(
        review_id: str,
        resolution: str,
        rationale: str,
        confirmed_by: str,
    ) -> dict[str, Any]:
        """Record a user-confirmed non-executing Challenge Review resolution."""
        try:
            inp = ChallengeReviewResolveInput.model_validate(
                {
                    "review_id": review_id,
                    "resolution": resolution,
                    "rationale": rationale,
                    "confirmed_by": confirmed_by,
                }
            )
            return container.challenge_review_service.resolve(inp).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ------------------------------------------- Phase 1L transactions/workflows

    async def account_get_transactions(
        providers: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Refresh and return normalized read-only historical account transactions."""
        try:
            inp = AccountGetTransactionsInput.model_validate(
                {"providers": providers, "start": start, "end": end, "limit": limit}
            )
            return (
                await container.account_transaction_coordinator.get_transactions(inp)
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_run_deep_dive")
    async def research_run_deep_dive(
        case_id: str | None = None,
        instrument_id: str | None = None,
        as_of: datetime | None = None,
        lookback_days: int = 365,
        create_case: bool = True,
        case_title: str | None = None,
        case_summary: str | None = None,
        case_topic_tags: list[str] | None = None,
        case_creation_confirmed_by: str = "user",
        case_creation_idempotency_key: str | None = None,
        industry_cycle: Literal["hog"] | None = None,
        industry_cycle_lookback_months: int = 120,
        company_operating_lookback_months: int = 36,
        company_operating_document_limit: int = 20,
    ) -> dict[str, Any]:
        """Run Deep Research; by default create/reuse a Draft instrument research file."""
        try:
            inp = ResearchRunDeepDiveInput.model_validate(
                {
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_days": lookback_days,
                    "create_case": create_case,
                    "case_title": case_title,
                    "case_summary": case_summary,
                    "case_topic_tags": tuple(case_topic_tags or ()),
                    "case_creation_confirmed_by": case_creation_confirmed_by,
                    "case_creation_idempotency_key": case_creation_idempotency_key,
                    "industry_cycle": industry_cycle,
                    "industry_cycle_lookback_months": industry_cycle_lookback_months,
                    "company_operating_lookback_months": company_operating_lookback_months,
                    "company_operating_document_limit": company_operating_document_limit,
                }
            )
            return (await container.research_workflow_orchestrator.run_deep_dive(inp)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="research_run_catalyst_review")
    async def research_run_catalyst_review(
        case_id: str | None = None,
        instrument_id: str | None = None,
        as_of: datetime | None = None,
        lookback_days: int = 365,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Run the cross-market catalyst fact recipe."""
        try:
            inp = ResearchRunCatalystReviewInput.model_validate(
                {
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_days": lookback_days,
                    "topic": topic,
                }
            )
            return (
                await container.research_workflow_orchestrator.run_catalyst_review(inp)
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="a_share_run_market_review")
    async def a_share_run_market_review(
        trade_date: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Run the A-share market-board and limit-ecology review recipe."""
        try:
            inp = AShareRunMarketReviewInput.model_validate(
                {"trade_date": trade_date, "as_of": as_of}
            )
            return (
                await container.research_workflow_orchestrator.run_a_share_market_review(inp)
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="us_run_market_review")
    async def us_run_market_review(
        as_of: datetime | None = None,
        prediction_topic: str | None = None,
    ) -> dict[str, Any]:
        """Run the US index, macro, news, and portfolio-impact recipe."""
        try:
            inp = USRunMarketReviewInput.model_validate(
                {"as_of": as_of, "prediction_topic": prediction_topic}
            )
            return (
                await container.research_workflow_orchestrator.run_us_market_review(inp)
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="portfolio_run_review")
    async def portfolio_run_review(
        refresh_accounts: bool = False,
        providers: tuple[str, ...] = (),
        account_snapshot_ids: tuple[str, ...] = (),
        as_of: datetime | None = None,
        risk_lookback_sessions: int = 126,
        max_risk_instruments: int = 12,
    ) -> dict[str, Any]:
        """Review durable accounts; refresh only when the user explicitly requests it."""
        try:
            inp = PortfolioRunReviewInput.model_validate(
                {
                    "refresh_accounts": refresh_accounts,
                    "providers": providers,
                    "account_snapshot_ids": account_snapshot_ids,
                    "as_of": as_of,
                    "risk_lookback_sessions": risk_lookback_sessions,
                    "max_risk_instruments": max_risk_instruments,
                }
            )
            return (
                await container.research_workflow_orchestrator.run_portfolio_review(inp)
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ------------------------------------------------------- Phase 2 Watchlist Hub

    @server.tool(name="watchlist_get")
    async def watchlist_get(
        operation: Literal["groups", "items"] = "items",
        group_name: str | None = None,
        refresh: bool = True,
        include_inactive: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List durable Watchlist groups or members from the active source."""
        if operation == "items":
            return await watchlist_get_items(group_name, refresh, include_inactive, limit, offset)
        if operation != "groups":
            raise ValueError("operation must be groups or items")
        try:
            request = WatchlistGetGroupsInput.model_validate(
                {"refresh": refresh, "include_inactive": include_inactive}
            )
            return (await container.watchlist_hub_service.get_groups(request)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def watchlist_get_items(
        group_name: str | None = None,
        refresh: bool = True,
        include_inactive: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List one durable watchlist group with research metadata links."""
        try:
            request = WatchlistGetItemsInput.model_validate(
                {
                    "group_name": group_name,
                    "refresh": refresh,
                    "include_inactive": include_inactive,
                    "limit": limit,
                    "offset": offset,
                }
            )
            return (await container.watchlist_hub_service.get_items(request)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="watchlist_add")
    async def watchlist_add(
        instrument_id: str,
        confirmed_by: str,
        idempotency_key: str,
        group_name: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Add one instrument to the active source after explicit confirmation."""
        try:
            request = WatchlistAddInput.model_validate(
                {
                    "group_name": group_name,
                    "instrument_id": instrument_id,
                    "display_name": display_name,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return (await container.watchlist_hub_service.add(request)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    @server.tool(name="watchlist_remove")
    async def watchlist_remove(
        membership_id: str,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Remove one membership from the active source without deleting research."""
        try:
            request = WatchlistRemoveInput.model_validate(
                {
                    "membership_id": membership_id,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return (await container.watchlist_hub_service.remove(request)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return server


async def _run_stdio() -> None:
    """Build and run the production stdio server in one event loop."""
    _suppress_sensitive_http_client_logs()
    container = build_default_application()
    try:
        server = create_mcp_server(container)
        await server.run_stdio_async()
    finally:
        await container.aclose()


def _suppress_sensitive_http_client_logs() -> None:
    """Keep provider URLs (which may contain credentials) off MCP stdio logs."""
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).disabled = True


def main() -> None:
    """Console entry: run FastMCP and close its container in one event loop.

    Startup sequence (design v4):
    console script → main() → AppSettings.load() → build_application →
    create_mcp_server → FastMCP.run_stdio_async()

    Settings I/O is performed inside the composition root helper so interfaces
    never import infrastructure modules.
    """
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
