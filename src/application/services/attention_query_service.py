"""Read-only Attention digest over durable collaborators. Never reconciles."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from application.dto.attention import (
    AttentionCoverageDTO,
    AttentionDigestDTO,
    AttentionHealthSummaryDTO,
    AttentionItemDTO,
    AttentionQueryInput,
)
from application.dto.catalyst_agenda import AgendaQueryFilters, AgendaQueryInput
from application.dto.judgment_scorecard import JudgmentScorecardHistoryInput
from application.dto.research import CandidateRevisionDTO
from application.dto.trade_retro import TradeRetroHistoryInput
from application.ports.agent_pending_action_repository import AgentPendingActionRepository
from application.ports.clock import Clock
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.attention_projection import (
    assemble_attention_digest,
    coverage,
    project_agenda_overdue,
    project_data_quality_issue,
    project_monitor_blind_spot,
    project_pending_candidate,
    project_review_item,
    project_scorecard_gaps,
    project_trade_retro,
    project_unresolved_agent,
    project_unresolved_broker,
)
from application.services.broker_order_service import BrokerOrderService
from application.services.catalyst_agenda_service import CatalystAgendaService
from application.services.data_quality_service import DataQualityService
from application.services.judgment_scorecard_service import JudgmentScorecardService
from application.services.review_item_service import ReviewItemService
from application.services.trade_retro_service import TradeRetroService
from domain.attention.enums import (
    AttentionCoverageSource,
    AttentionCoverageState,
    AttentionSeverity,
)
from domain.common.enums import CandidateStatus

ResearchUowFactory = Callable[[], ResearchUnitOfWork]
_LOGGER = logging.getLogger(__name__)
_MATERIALIZATION_STALE_AFTER = timedelta(hours=24)


def _log_source_failure(source: AttentionCoverageSource, error: BaseException) -> None:
    """Emit only bounded source/type metadata; never exception text or payloads."""

    _LOGGER.warning(
        "Attention durable source unavailable: source=%s error_type=%s",
        source.value,
        type(error).__name__,
    )


class AttentionQueryService:
    def __init__(
        self,
        clock: Clock,
        review_items: ReviewItemService,
        research_uow_factory: ResearchUowFactory,
        catalyst_agenda: CatalystAgendaService,
        trade_retro: TradeRetroService,
        scorecards: JudgmentScorecardService,
        data_quality: DataQualityService,
        broker_orders: BrokerOrderService,
        agent_pending_actions: AgentPendingActionRepository,
    ) -> None:
        self._clock = clock
        self._review_items = review_items
        self._research_uow_factory = research_uow_factory
        self._catalyst_agenda = catalyst_agenda
        self._trade_retro = trade_retro
        self._scorecards = scorecards
        self._data_quality = data_quality
        self._broker_orders = broker_orders
        self._agent_pending_actions = agent_pending_actions

    def list_digest(self, request: AttentionQueryInput) -> AttentionDigestDTO:
        now = self._clock.now()
        live: list[AttentionItemDTO] = []
        review_items: list[AttentionItemDTO] = []
        coverages: list[AttentionCoverageDTO] = []
        extra_limitations: list[str] = []

        review_items.extend(self._collect_review_items(request.case_id, coverages, now))
        live.extend(self._collect_candidates(request.case_id, coverages, now))
        live.extend(self._collect_agenda(request.case_id, coverages, extra_limitations, now))
        live.extend(self._collect_retro(request.case_id, coverages, now))
        live.extend(self._collect_scorecards(request.case_id, coverages, now))
        live.extend(self._collect_quality(coverages, extra_limitations, now))
        live.extend(self._collect_broker(coverages, now))
        live.extend(self._collect_agent(coverages, now))
        return assemble_attention_digest(
            generated_at=now,
            request=request,
            review_items=tuple(review_items),
            live_items=tuple(live),
            coverage=tuple(coverages),
            extra_limitations=tuple(extra_limitations),
        )

    def health_summary(self) -> AttentionHealthSummaryDTO:
        now = self._clock.now()
        materialized_at = self._review_items.latest_observed_at()
        metrics = self._review_items.metrics()
        open_items = self._review_items.list_open(limit=500)
        open_count = metrics.open_count
        acknowledged_count = metrics.acknowledged_count
        limitation_codes: list[str] = []
        if open_count + acknowledged_count > len(open_items):
            limitation_codes.append("ATTENTION_REVIEW_ITEMS_TRUNCATED")
        quality_available = False
        try:
            quality = self._data_quality.check()
            quality_data = quality.data if quality.ok else None
            quality_available = quality_data is not None
            limitations = quality_data.limitations if quality_data is not None else ()
        except Exception as error:  # noqa: BLE001 — health summary must not fail the process
            _log_source_failure(AttentionCoverageSource.DATA_QUALITY, error)
            limitations = ()
        if not quality_available:
            limitation_codes.append("ATTENTION_DATA_QUALITY_UNAVAILABLE")
        highest = None
        for item in open_items:
            try:
                severity = AttentionSeverity(item.severity)
            except ValueError:
                continue
            if highest is None or (
                severity is AttentionSeverity.ERROR
                or (
                    severity is AttentionSeverity.ATTENTION
                    and highest is not AttentionSeverity.ERROR
                )
            ):
                highest = severity
        materialization_stale = (
            materialized_at is not None
            and now - materialized_at > _MATERIALIZATION_STALE_AFTER
        )
        if materialization_stale:
            limitation_codes.append("ATTENTION_REVIEW_ITEMS_STALE")
        if materialized_at is None:
            coverage_status = AttentionCoverageState.UNKNOWN
        elif not quality_available or limitation_codes:
            coverage_status = AttentionCoverageState.PARTIAL
        else:
            coverage_status = AttentionCoverageState.COMPLETE
        return AttentionHealthSummaryDTO(
            generated_at=now,
            materialized_at=materialized_at,
            open_review_item_count=open_count,
            acknowledged_review_item_count=acknowledged_count,
            highest_severity=highest,
            catalyst_sync_receipt_missing=(
                "CATALYST_AGENDA_SYNC_RECEIPT_MISSING" in limitations
                if quality_available
                else None
            ),
            coverage_status=coverage_status,
            limitation_codes=tuple(limitation_codes),
        )

    def _collect_review_items(
        self,
        subject_id: str | None,
        coverages: list[AttentionCoverageDTO],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            values = self._review_items.list_open(subject_id=subject_id, limit=500)
            metrics = self._review_items.metrics(subject_id=subject_id)
            items = tuple(
                item for value in values if (item := project_review_item(value)) is not None
            )
            truncated = metrics.open_count + metrics.acknowledged_count > len(values)
            coverages.append(
                coverage(
                    AttentionCoverageSource.REVIEW_ITEMS,
                    "PARTIAL" if truncated else "COMPLETE",
                    observed_at=now,
                    limitation_codes=(
                        ("ATTENTION_REVIEW_ITEMS_TRUNCATED",) if truncated else ()
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001 — source failure stays local
            _log_source_failure(AttentionCoverageSource.REVIEW_ITEMS, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.REVIEW_ITEMS,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_REVIEW_ITEMS_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_candidates(
        self,
        subject_id: str | None,
        coverages: list[AttentionCoverageDTO],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            with self._research_uow_factory() as uow:
                pending = uow.candidates.list(
                    subject_id=subject_id,
                    status=CandidateStatus.PROPOSED,
                    limit=201,
                    offset=0,
                )
            items = tuple(
                item
                for value in CandidateRevisionDTO.from_domain_list(pending[:200])
                if (item := project_pending_candidate(value, now=now)) is not None
            )
            truncated = len(pending) > 200
            coverages.append(
                coverage(
                    AttentionCoverageSource.RESEARCH_CANDIDATES,
                    "PARTIAL" if truncated else "COMPLETE",
                    observed_at=now,
                    limitation_codes=(
                        ("ATTENTION_CANDIDATES_TRUNCATED",) if truncated else ()
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.RESEARCH_CANDIDATES, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.RESEARCH_CANDIDATES,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_CANDIDATES_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_agenda(
        self,
        subject_id: str | None,
        coverages: list[AttentionCoverageDTO],
        extra_limitations: list[str],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            filters = AgendaQueryFilters(
                subject_ids=(subject_id,) if subject_id is not None else (),
            )
            envelope = self._catalyst_agenda.query(
                AgendaQueryInput(filters=filters, limit=200)
            )
            if not envelope.ok or envelope.data is None:
                coverages.append(
                    coverage(
                        AttentionCoverageSource.CATALYST_AGENDA,
                        "UNAVAILABLE",
                        observed_at=now,
                        limitation_codes=("ATTENTION_AGENDA_UNAVAILABLE",),
                    )
                )
                return ()
            extra_limitations.extend(envelope.data.limitation_codes)
            items = tuple(
                item
                for value in envelope.data.items
                if (item := project_agenda_overdue(value)) is not None
            )
            state = (
                "PARTIAL"
                if envelope.data.limitation_codes or envelope.data.has_more
                else "COMPLETE"
            )
            coverages.append(
                coverage(
                    AttentionCoverageSource.CATALYST_AGENDA,
                    state,
                    observed_at=now,
                    limitation_codes=tuple(
                        dict.fromkeys(
                            (
                                *envelope.data.limitation_codes,
                                *(
                                    ("ATTENTION_AGENDA_TRUNCATED",)
                                    if envelope.data.has_more
                                    else ()
                                ),
                            )
                        )
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.CATALYST_AGENDA, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.CATALYST_AGENDA,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_AGENDA_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_retro(
        self,
        subject_id: str | None,
        coverages: list[AttentionCoverageDTO],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            envelope = self._trade_retro.history(TradeRetroHistoryInput(limit=100))
            if not envelope.ok or envelope.data is None:
                coverages.append(
                    coverage(
                        AttentionCoverageSource.TRADE_RETRO,
                        "UNAVAILABLE",
                        observed_at=now,
                        limitation_codes=("ATTENTION_RETRO_UNAVAILABLE",),
                    )
                )
                return ()
            runs = envelope.data.runs
            if subject_id is not None:
                runs = tuple(
                    run for run in runs if subject_id in run.subject_ids or not run.subject_ids
                )
            items = tuple(item for run in runs for item in project_trade_retro(run))
            truncated = len(envelope.data.runs) >= 100
            coverages.append(
                coverage(
                    AttentionCoverageSource.TRADE_RETRO,
                    "PARTIAL" if truncated else "COMPLETE",
                    observed_at=now,
                    limitation_codes=(
                        ("ATTENTION_RETRO_LIMIT_REACHED",) if truncated else ()
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.TRADE_RETRO, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.TRADE_RETRO,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_RETRO_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_scorecards(
        self,
        subject_id: str | None,
        coverages: list[AttentionCoverageDTO],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            envelope = self._scorecards.history(
                JudgmentScorecardHistoryInput(subject_id=subject_id, limit=50)
            )
            if not envelope.ok or envelope.data is None:
                coverages.append(
                    coverage(
                        AttentionCoverageSource.SCORECARD,
                        "UNAVAILABLE",
                        observed_at=now,
                        limitation_codes=("ATTENTION_SCORECARD_UNAVAILABLE",),
                    )
                )
                return ()
            items = project_scorecard_gaps(envelope.data.runs)
            state = "PARTIAL" if envelope.data.has_more else "COMPLETE"
            coverages.append(
                coverage(
                    AttentionCoverageSource.SCORECARD,
                    state,
                    observed_at=now,
                    limitation_codes=(
                        ("ATTENTION_SCORECARD_TRUNCATED",)
                        if envelope.data.has_more
                        else ()
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.SCORECARD, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.SCORECARD,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_SCORECARD_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_quality(
        self,
        coverages: list[AttentionCoverageDTO],
        extra_limitations: list[str],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            envelope = self._data_quality.check()
            if not envelope.ok or envelope.data is None:
                coverages.append(
                    coverage(
                        AttentionCoverageSource.DATA_QUALITY,
                        "UNAVAILABLE",
                        observed_at=now,
                        limitation_codes=("ATTENTION_DATA_QUALITY_UNAVAILABLE",),
                    )
                )
                coverages.append(
                    coverage(
                        AttentionCoverageSource.MONITORS,
                        "UNAVAILABLE",
                        observed_at=now,
                        limitation_codes=("ATTENTION_MONITORS_UNAVAILABLE",),
                    )
                )
                return ()
            extra_limitations.extend(envelope.data.limitations)
            items = [project_data_quality_issue(issue) for issue in envelope.data.issues]
            items.extend(
                item
                for monitor in envelope.data.monitors
                if (item := project_monitor_blind_spot(monitor)) is not None
            )
            coverages.append(
                coverage(
                    AttentionCoverageSource.DATA_QUALITY,
                    "COMPLETE",
                    observed_at=now,
                    limitation_codes=envelope.data.limitations,
                )
            )
            coverages.append(
                coverage(AttentionCoverageSource.MONITORS, "COMPLETE", observed_at=now)
            )
            return tuple(items)
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.DATA_QUALITY, error)
            _log_source_failure(AttentionCoverageSource.MONITORS, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.DATA_QUALITY,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_DATA_QUALITY_UNAVAILABLE",),
                )
            )
            coverages.append(
                coverage(
                    AttentionCoverageSource.MONITORS,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_MONITORS_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_broker(
        self,
        coverages: list[AttentionCoverageDTO],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            intents = self._broker_orders.list_unresolved(limit=101)
            items = tuple(
                project_unresolved_broker(
                    order_intent_id=intent.order_intent_id,
                    status=intent.status,
                    symbol=intent.symbol,
                    provider_status=intent.provider_status,
                )
                for intent in intents[:100]
            )
            truncated = len(intents) > 100
            coverages.append(
                coverage(
                    AttentionCoverageSource.BROKER_ORDERS,
                    "PARTIAL" if truncated else "COMPLETE",
                    observed_at=now,
                    limitation_codes=(
                        ("ATTENTION_BROKER_TRUNCATED",) if truncated else ()
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.BROKER_ORDERS, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.BROKER_ORDERS,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_BROKER_UNAVAILABLE",),
                )
            )
            return ()

    def _collect_agent(
        self,
        coverages: list[AttentionCoverageDTO],
        now: datetime,
    ) -> tuple[AttentionItemDTO, ...]:
        try:
            actions = self._agent_pending_actions.list_unresolved(now=now, limit=101)
            items = tuple(project_unresolved_agent(action) for action in actions[:100])
            truncated = len(actions) > 100
            coverages.append(
                coverage(
                    AttentionCoverageSource.AGENT_PENDING_ACTIONS,
                    "PARTIAL" if truncated else "COMPLETE",
                    observed_at=now,
                    limitation_codes=(
                        ("ATTENTION_AGENT_ACTIONS_TRUNCATED",) if truncated else ()
                    ),
                )
            )
            return items
        except Exception as error:  # noqa: BLE001
            _log_source_failure(AttentionCoverageSource.AGENT_PENDING_ACTIONS, error)
            coverages.append(
                coverage(
                    AttentionCoverageSource.AGENT_PENDING_ACTIONS,
                    "UNAVAILABLE",
                    observed_at=now,
                    limitation_codes=("ATTENTION_AGENT_UNAVAILABLE",),
                )
            )
            return ()
