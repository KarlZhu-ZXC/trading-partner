"""Read-only durable context builder for fresh Codex threads."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from application.dto.research import InvestmentCaseDTO, ResearchStateDTO
from application.dto.research_context import (
    ContextBudgetDTO,
    ContextDecisionDTO,
    ContextEventDTO,
    ContextEvidenceDTO,
    ContextJournalDTO,
    ContextPositionDTO,
    ContextReportDTO,
    ResearchContextBuildInput,
    ResearchContextDTO,
)
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    build_research_state,
    envelope_failure,
)
from domain.common.enums import EvidenceStance, Freshness
from domain.common.errors import InputValidationError
from domain.common.ids import EntityIdPrefix
from domain.research.models import InvestmentCase

_LIVE_TOOLS = (
    "market_get_snapshot_or_a_share_get_snapshot",
    "fundamental_get_snapshot_or_a_share_get_snapshot",
    "events_search_or_research_get_company_updates",
)


def _clip(value: str, maximum: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= maximum else compact[: maximum - 1] + "…"


class ResearchContextBuilder:
    def __init__(
        self,
        uow_factory: UowFactory,
        accounts: AccountSnapshotRepository,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._accounts = accounts
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    def build(self, request: ResearchContextBuildInput) -> ToolEnvelope[ResearchContextDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            if request.since is not None and request.since > now:
                raise InputValidationError("since must not be in the future")
            with self._uow_factory() as uow:
                case = self._select_case(uow, request)
                state = build_research_state(uow, case.case_id)
                evidence = self._evidence(uow, case.case_id, now)
                reports = tuple(
                    ContextReportDTO(
                        report_id=item.report_id,
                        report_type=item.report_type,
                        title=_clip(item.title, 200),
                        summary=_clip(item.summary, 1_000),
                        as_of=item.as_of,
                    )
                    for item in uow.reports.list_by_case(case.case_id, as_of=now)[:10]
                )
                events = tuple(
                    ContextEventDTO(
                        event_id=item.event_id,
                        event_type=item.event_type,
                        title=_clip(item.title, 200),
                        summary=_clip(item.summary, 800),
                        occurred_at=item.occurred_at,
                    )
                    for item in uow.events.list_timeline(
                        case.case_id,
                        start=request.since,
                        end=now,
                        as_of=now,
                        event_types=(),
                    )[-20:]
                )
                decisions = tuple(
                    ContextDecisionDTO(
                        decision_id=item.decision_id,
                        decision_type=item.decision_type,
                        title=_clip(item.title, 200),
                        rationale=_clip(item.rationale, 1_000),
                        decided_at=item.decided_at,
                        confirmation_mode=item.confirmation_mode,
                        execution_effect=False,
                    )
                    for item in uow.decisions.list_by_case(case.case_id, as_of=now)[-10:]
                    if request.since is None or item.decided_at >= request.since
                )
                journals = tuple(
                    ContextJournalDTO(
                        journal_id=item.journal_id,
                        entry_type=item.entry_type,
                        title=_clip(item.title, 200),
                        body=_clip(item.body_markdown, 1_000),
                        created_at=item.created_at,
                    )
                    for item in uow.journal.list(
                        case_id=case.case_id, as_of=now, limit=20, offset=0
                    )
                    if request.since is None or item.created_at >= request.since
                )

            positions, degraded = self._positions()
            conflicts = tuple(
                f"EVIDENCE_STANCE_CONFLICT:{item.evidence_id}"
                for item in evidence
                if EvidenceStance.SUPPORTS in item.stances
                and EvidenceStance.CONTRADICTS in item.stances
            )
            missing: list[str] = []
            if not state.invalidations:
                missing.append("NO_INVALIDATION_CONDITIONS")
            if not evidence:
                missing.append("NO_LINKED_EVIDENCE")
            if not reports:
                missing.append("NO_RESEARCH_REPORTS")
            if not positions:
                missing.append("NO_DURABLE_PORTFOLIO_CONTEXT")
            context, budget_warnings = self._budget(
                request.token_budget,
                case=InvestmentCaseDTO.from_domain(case),
                state=state,
                evidence=evidence,
                reports=reports,
                events=events,
                decisions=decisions,
                journals=journals,
                positions=positions,
                conflicts=conflicts,
                missing=tuple(missing),
                degraded=degraded,
            )
            warning_codes = list(budget_warnings) + missing
            if degraded:
                warning_codes.append("PORTFOLIO_SOURCE_DEGRADED")
            warnings = tuple(
                WarningInfo(code=code, message="Research context warning.", details={})
                for code in dict.fromkeys(warning_codes)
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=context,
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    @staticmethod
    def _select_case(
        uow: ResearchUnitOfWork, request: ResearchContextBuildInput
    ) -> InvestmentCase:
        cases = uow.cases
        if request.case_id is not None:
            return cases.get(request.case_id)
        matches = cases.list(
            primary_instrument_id=request.instrument_id,
            include_archived=False,
            limit=10,
            offset=0,
        )
        if len(matches) != 1:
            raise InputValidationError(
                "instrument_id must resolve to exactly one active Investment Case",
                details={"candidate_case_ids": tuple(item.case_id for item in matches)},
            )
        return matches[0]

    @staticmethod
    def _evidence(
        uow: ResearchUnitOfWork, case_id: str, now: datetime
    ) -> tuple[ContextEvidenceDTO, ...]:
        linked = uow.case_evidence_links.list_evidence(case_id, as_of=now)
        items: list[ContextEvidenceDTO] = []
        for evidence in linked:
            assessments = tuple(
                item
                for item in uow.evidence_assessments.list_for_evidence(
                    evidence.evidence_id, as_of=now
                )
                if item.case_id == case_id
            )
            stances = tuple(dict.fromkeys(item.stance for item in assessments))
            materiality = max((item.materiality for item in assessments), default=None)
            items.append(
                ContextEvidenceDTO(
                    evidence_id=evidence.evidence_id,
                    title=_clip(evidence.title, 200),
                    summary=_clip(evidence.summary, 1_000),
                    source_name=evidence.source_name,
                    observed_at=evidence.observed_at,
                    stances=stances,
                    materiality=materiality,
                    assessment_rationales=tuple(
                        _clip(item.rationale, 500) for item in assessments[:3]
                    ),
                )
            )
        rank = {
            EvidenceStance.CONTRADICTS: 0,
            EvidenceStance.SUPPORTS: 1,
            EvidenceStance.UNCERTAIN: 2,
            EvidenceStance.NEUTRAL: 3,
        }
        items.sort(
            key=lambda item: (
                min((rank[stance] for stance in item.stances), default=4),
                -(item.materiality or Decimal(0)),
                item.evidence_id,
            )
        )
        contrary_count = sum(
            EvidenceStance.CONTRADICTS in item.stances for item in items
        )
        return tuple(items[: max(20, contrary_count)])

    def _positions(self) -> tuple[tuple[ContextPositionDTO, ...], tuple[str, ...]]:
        positions: list[ContextPositionDTO] = []
        degraded: list[str] = []
        for snapshot in self._accounts.latest_accounts():
            degraded.extend(f"{snapshot.provider.value}:{code}" for code in snapshot.warning_codes)
            for item in snapshot.positions:
                positions.append(
                    ContextPositionDTO(
                        snapshot_id=snapshot.snapshot_id,
                        account_ref=snapshot.account_ref,
                        provider=snapshot.provider,
                        account_as_of=snapshot.account_as_of,
                        instrument_id=item.instrument_id,
                        side=item.side,
                        quantity=item.quantity,
                        market_value=item.market_value,
                        currency=item.currency,
                    )
                )
        return tuple(positions[:50]), tuple(dict.fromkeys(degraded))

    def _budget(
        self,
        requested: int,
        *,
        case: InvestmentCaseDTO,
        state: ResearchStateDTO,
        evidence: tuple[ContextEvidenceDTO, ...],
        reports: tuple[ContextReportDTO, ...],
        events: tuple[ContextEventDTO, ...],
        decisions: tuple[ContextDecisionDTO, ...],
        journals: tuple[ContextJournalDTO, ...],
        positions: tuple[ContextPositionDTO, ...],
        conflicts: tuple[str, ...],
        missing: tuple[str, ...],
        degraded: tuple[str, ...],
    ) -> tuple[ResearchContextDTO, tuple[str, ...]]:
        evidence_items = list(evidence)
        report_items = list(reports)
        event_items = list(events)
        decision_items = list(decisions)
        journal_items = list(journals)
        position_items = list(positions)
        truncated: list[str] = []

        def package(estimated: int = 0) -> ResearchContextDTO:
            return ResearchContextDTO(
                case=case,
                research_state=state,
                evidence=tuple(evidence_items),
                reports=tuple(report_items),
                events=tuple(event_items),
                decisions=tuple(decision_items),
                journals=tuple(journal_items),
                positions=tuple(position_items),
                conflicts=conflicts,
                missing_information=missing,
                degraded_sources=degraded,
                live_fact_tools_required=_LIVE_TOOLS,
                budget=ContextBudgetDTO(
                    requested_tokens=requested,
                    estimated_tokens=estimated,
                    truncated=bool(truncated),
                    truncated_collections=tuple(dict.fromkeys(truncated)),
                ),
            )

        context = package()
        estimate = (len(context.model_dump_json()) + 3) // 4
        while estimate > requested:
            if journal_items:
                journal_items.pop()
                truncated.append("journals")
            elif event_items:
                event_items.pop()
                truncated.append("events")
            elif report_items:
                report_items.pop()
                truncated.append("reports")
            elif decision_items:
                decision_items.pop()
                truncated.append("decisions")
            elif position_items:
                position_items.pop()
                truncated.append("positions")
            else:
                removable = next(
                    (
                        index
                        for index in range(len(evidence_items) - 1, -1, -1)
                        if EvidenceStance.CONTRADICTS not in evidence_items[index].stances
                    ),
                    None,
                )
                if removable is None:
                    break
                evidence_items.pop(removable)
                truncated.append("evidence")
            context = package()
            estimate = (len(context.model_dump_json()) + 3) // 4
        context = context.model_copy(
            update={
                "budget": context.budget.model_copy(update={"estimated_tokens": estimate})
            }
        )
        warnings = ["CONTEXT_TRUNCATED"] if truncated else []
        if estimate > requested:
            warnings.append("CONTEXT_REQUIRED_STATE_EXCEEDS_BUDGET")
        return context, tuple(warnings)
