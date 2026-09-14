"""Bounded graph for the read/review/valuation Console workspace."""

from collections.abc import Callable
from dataclasses import dataclass

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.judgment_scorecard_repository import JudgmentScorecardRepository
from application.ports.monitor_repository import MonitorRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.review_item_repository import ReviewItemRepository
from application.ports.secret_redactor import SecretRedactor
from application.ports.trade_retro_repository import TradeRetroRepository
from application.services.decision_record_service import DecisionRecordService
from application.services.journal_service import JournalService
from application.services.judgment_calibration_service import JudgmentCalibrationService
from application.services.quick_review_service import QuickReviewService
from application.services.quick_review_thinking import LatestThinkingReader
from application.services.research_changes_service import ResearchChangesService
from application.services.today_review_service import TodayReviewService
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from application.services.valuation_service import ValuationService
from application.services.weekly_review_service import WeeklyReviewService


@dataclass(frozen=True)
class ReviewWorkspace:
    changes: ResearchChangesService
    calibration: JudgmentCalibrationService
    valuation: ValuationService
    quick_review: QuickReviewService
    today_review: TodayReviewService
    weekly_review: WeeklyReviewService


def build_review_workspace(
    *,
    notes: ExternalNoteRepository,
    note_reviews: ExternalNoteReviewRepository,
    review_items: ReviewItemRepository,
    snapshots: AccountSnapshotRepository,
    monitors: MonitorRepository,
    agenda: CatalystAgendaRepository,
    scorecards: JudgmentScorecardRepository,
    retro: TradeRetroRepository,
    uow: Callable[[], ResearchUnitOfWork],
    statements: USResearchToolCoordinator,
    journal: JournalService,
    decisions: DecisionRecordService,
    clock: Clock,
    redactor: SecretRedactor,
    timezone: str,
) -> ReviewWorkspace:
    changes = ResearchChangesService(notes, uow, monitors, agenda, clock)
    today = TodayReviewService(
        uow, review_items, note_reviews, notes, monitors, agenda, clock, timezone
    )
    return ReviewWorkspace(
        changes=changes,
        today_review=today,
        weekly_review=WeeklyReviewService(uow, today, clock, timezone),
        calibration=JudgmentCalibrationService(uow, agenda, scorecards, retro, clock, changes),
        valuation=ValuationService(uow, statements, journal, clock),
        quick_review=QuickReviewService(
            uow,
            changes,
            LatestThinkingReader(notes, clock, timezone),
            snapshots,
            note_reviews,
            decisions,
            clock,
            redactor,
        ),
    )
