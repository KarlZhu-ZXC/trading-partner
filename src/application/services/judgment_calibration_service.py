"""Read existing outcomes without generating scores, returns, or research writes."""

from collections.abc import Callable
from datetime import datetime

from application.dto.judgment_calibration import (
    CalibrationBaselineDTO,
    CalibrationCardDTO,
    CalibrationDimensionDTO,
    CalibrationObservationDTO,
    JudgmentCalibrationDTO,
)
from application.dto.judgment_scorecard import ScorecardDimensionDTO, ScorecardSourceRefDTO
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.clock import Clock
from application.ports.judgment_scorecard_repository import JudgmentScorecardRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.trade_retro_repository import TradeRetroRepository
from application.services.research_changes_service import ResearchChangesService
from domain.common.errors import DataContractError
from domain.research.models import DecisionRecord

_GROUPS = {
    "FACTUAL_OUTCOME": (
        "事实预测与结果",
        {
            "REVISION_DEFINITION_COVERAGE",
            "REVISION_EVIDENCE_BALANCE",
            "EVIDENCE_RECENCY",
            "ASSUMPTION_OUTCOME",
            "THESIS_INVALIDATION_OUTCOME",
            "CATALYST_OUTCOME_CALIBRATION",
        },
    ),
    "PLAN_CONDITIONS": ("计划条件观察", {"PLAN_MONITOR_COVERAGE"}),
    "ADHERENCE": ("执行纪律", {"PLAN_BEFORE_ACTION_INTENT", "TRADE_RETRO_DISCIPLINE"}),
    "ATTRIBUTABLE_TRADE_OUTCOME": ("可归因交易结果", set()),
}


class JudgmentCalibrationService:
    def __init__(
        self,
        research_uow_factory: Callable[[], ResearchUnitOfWork],
        agenda: CatalystAgendaRepository,
        scorecards: JudgmentScorecardRepository,
        retro: TradeRetroRepository,
        clock: Clock,
        changes: ResearchChangesService | None = None,
    ) -> None:
        self._uow = research_uow_factory
        self._agenda = agenda
        self._scorecards = scorecards
        self._retro = retro
        self._clock = clock
        self._changes = changes

    def get(self, subject_id: str, *, decision_id: str | None = None) -> JudgmentCalibrationDTO:
        now = self._clock.now()
        with self._uow() as uow:
            if uow.subjects.get(subject_id) is None:
                raise DataContractError("Research Subject not found")
            eligible = [
                d
                for d in uow.decisions.list_by_subject(subject_id)
                if d.subject_id == subject_id and d.decided_by == "user" and d.recorded_at <= now
            ]
            if decision_id is not None:
                eligible = [d for d in eligible if d.decision_id == decision_id]
                if not eligible:
                    raise DataContractError("Reviewed Decision does not belong to this Subject")
            decision = max(eligible, key=lambda d: (d.recorded_at, d.decision_id), default=None)
            if decision:
                for revision_id in decision.thesis_revision_ids:
                    revision = uow.revisions.get(revision_id)
                    if (
                        revision.subject_id != subject_id
                        or revision.confirmed_at > decision.recorded_at
                    ):
                        raise DataContractError("Invalid reviewed Thesis revision")
                if decision.trade_plan_id is not None:
                    if decision.trade_plan_version is None:
                        raise DataContractError("Reviewed Trade Plan version is missing")
                    plan = uow.trade_plans.get_version(
                        decision.trade_plan_id, decision.trade_plan_version
                    )
                    if (
                        plan is None
                        or plan.subject_id != subject_id
                        or plan.created_at > decision.recorded_at
                    ):
                        raise DataContractError("Invalid reviewed Trade Plan version")
        cards: dict[str, list[CalibrationCardDTO]] = {key: [] for key in _GROUPS}
        observations: dict[str, list[CalibrationObservationDTO]] = {key: [] for key in _GROUPS}
        coverage: dict[str, str] = {}
        warnings: list[str] = []
        if decision:
            readers: tuple[tuple[str, Callable[[], tuple[str, list[str]]]], ...] = (
                ("SCORECARD", lambda: self._read_cards(decision, now, cards)),
                ("AGENDA", lambda: self._read_agenda(decision, now, observations)),
                ("RETRO", lambda: self._read_retro(decision, now, observations)),
                ("CONDITIONS", lambda: self._read_conditions(decision, now, observations)),
            )
            for name, reader in readers:
                try:
                    coverage[name], codes = reader()
                    warnings.extend(codes)
                except Exception:
                    coverage[name] = "UNAVAILABLE"
                    warnings.append(f"{name}_SOURCE_UNAVAILABLE")
        else:
            warnings.append("NO_REVIEWED_BASELINE")
        review_status = (
            "NO_BASELINE"
            if not decision
            else "UNSCHEDULED"
            if decision.review_due_at is None
            else "NOT_DUE"
            if decision.review_due_at > now
            else "DUE"
        )
        if review_status == "NOT_DUE":
            warnings.append("REVIEW_NOT_DUE")
        dimensions = []
        for code, (title, _) in _GROUPS.items():
            has_evidence = bool(cards[code] or observations[code])
            limitation = "NO_ELIGIBLE_EVIDENCE"
            summary = "尚无匹配该已复核版本的证据；未知不等于失败。"
            if code == "ATTRIBUTABLE_TRADE_OUTCOME":
                limitation = "ATTRIBUTION_NOT_AVAILABLE"
                summary = (
                    "现有持久记录不提供该 Decision 的收益归因；"
                    "不以盈亏代替判断质量，也不将未交易视为失败。"
                )
            elif has_evidence:
                summary = "展示已有记录及其原始限制；不合成胜率或总分。"
            dimensions.append(
                CalibrationDimensionDTO(
                    code=code,
                    title=title,
                    status="OBSERVATIONS" if has_evidence else "UNKNOWN",
                    summary=summary,
                    cards=tuple(cards[code]),
                    observations=tuple(observations[code]),
                    limitation_codes=() if has_evidence else (limitation,),
                )
            )
        return JudgmentCalibrationDTO(
            subject_id=subject_id,
            as_of=now,
            baseline=CalibrationBaselineDTO(
                decision_id=decision.decision_id,
                decision_type=decision.decision_type.value,
                title=decision.title,
                recorded_at=decision.recorded_at,
                thesis_revision_ids=decision.thesis_revision_ids,
                trade_plan_id=decision.trade_plan_id,
                trade_plan_version=decision.trade_plan_version,
                review_due_at=decision.review_due_at,
            )
            if decision
            else None,
            review_status=review_status,
            dimensions=tuple(dimensions),
            coverage=coverage,
            warning_codes=tuple(warnings),
        )

    def _read_conditions(
        self,
        decision: DecisionRecord,
        now: datetime,
        result: dict[str, list[CalibrationObservationDTO]],
    ) -> tuple[str, list[str]]:
        if self._changes is None:
            return "UNAVAILABLE", ["CONDITION_PROJECTION_UNAVAILABLE"]
        projection = self._changes.get(
            decision.subject_id,
            baseline_decision_id=decision.decision_id,
            limit=100,
        )
        for item in projection.items:
            if (
                item.kind != "MONITOR"
                or item.relation != "EXACT_PLAN"
                or item.plan_id != decision.trade_plan_id
                or item.plan_version != decision.trade_plan_version
                or not decision.recorded_at <= item.occurred_at <= now
            ):
                continue
            result["PLAN_CONDITIONS"].append(
                CalibrationObservationDTO(
                    source_id=item.source_id,
                    observed_at=item.recorded_at,
                    occurred_at=item.occurred_at,
                    title=item.title,
                    summary=f"{item.old_value or 'Unknown'} → {item.new_value or 'Unknown'}",
                    status="RECORDED_OBSERVATION",
                    source_refs=(
                        ScorecardSourceRefDTO(
                            kind="MONITOR",
                            entity_id=item.monitor_id or item.source_id,
                            version=int(item.source_version) if item.source_version else None,
                        ),
                        ScorecardSourceRefDTO(
                            kind="TRADE_PLAN",
                            entity_id=item.plan_id or "",
                            version=item.plan_version,
                        ),
                    ),
                    limitation_codes=tuple(item.warning_codes),
                )
            )
        coverage = projection.coverage.get("MONITOR", "UNAVAILABLE")
        if projection.has_more:
            return "PARTIAL", ["CONDITION_HISTORY_BOUNDED"]
        return coverage, projection.warning_codes

    def _read_cards(
        self, decision: DecisionRecord, now: datetime, result: dict[str, list[CalibrationCardDTO]]
    ) -> tuple[str, list[str]]:
        runs, total = self._scorecards.list(subject_id=decision.subject_id, limit=100)
        matching = [
            r
            for r in runs
            if r.subject_id == decision.subject_id
            and r.thesis_revision_id in decision.thesis_revision_ids
            and decision.recorded_at <= r.generated_at <= now
        ]
        # One newest durable run for each exact reviewed revision, never current Thesis.
        latest = {}
        for run in sorted(matching, key=lambda r: (r.generated_at, r.scorecard_id)):
            latest[run.thesis_revision_id] = run
        for run in latest.values():
            for dimension in run.dimensions:
                for code, (_, codes) in _GROUPS.items():
                    if dimension.code not in codes:
                        continue
                    if code in {"PLAN_CONDITIONS", "ADHERENCE"}:
                        plan_refs = [r for r in dimension.source_refs if r.kind == "TRADE_PLAN"]
                        if not plan_refs or any(
                            r.entity_id != decision.trade_plan_id
                            or r.version != decision.trade_plan_version
                            for r in plan_refs
                        ):
                            continue
                    result[code].append(
                        CalibrationCardDTO(
                            scorecard_id=run.scorecard_id,
                            thesis_revision_id=run.thesis_revision_id,
                            generated_at=run.generated_at,
                            dimension=ScorecardDimensionDTO.from_domain(dimension),
                            warning_codes=run.warning_codes,
                        )
                    )
        warnings = [] if latest else ["NO_MATCHING_SCORECARD"]
        if latest and set(latest) != set(decision.thesis_revision_ids):
            warnings.append("REVIEWED_REVISIONS_WITHOUT_SCORECARD")
        if len(matching) != len(runs):
            warnings.append("SCORECARD_REVISION_OR_WINDOW_EXCLUSIONS")
        if total > len(runs):
            warnings.append("SCORECARD_HISTORY_BOUNDED")
        return ("PARTIAL" if total > len(runs) else "COMPLETE", warnings)

    def _read_agenda(
        self,
        decision: DecisionRecord,
        now: datetime,
        result: dict[str, list[CalibrationObservationDTO]],
    ) -> tuple[str, list[str]]:
        rows = [
            r
            for r in self._agenda.list_visible(as_of=now)
            if r.subject_id == decision.subject_id and r.recorded_at <= now
        ]
        for row in sorted(rows, key=lambda r: (r.recorded_at, r.agenda_item_id), reverse=True)[
            :100
        ]:
            happened = row.outcome_occurred_at
            status = (
                "UNKNOWN"
                if happened is None
                else (
                    "OUT_OF_WINDOW"
                    if happened < decision.recorded_at or happened > now
                    else "OBSERVED"
                )
            )
            refs = [
                ScorecardSourceRefDTO(
                    kind="CATALYST_AGENDA", entity_id=row.agenda_item_id, version=row.version
                )
            ]
            for kind, value in (
                ("EVIDENCE", row.linked_evidence_id),
                ("EVENT", row.linked_event_id),
                ("REPORT", row.linked_report_id),
            ):
                if value:
                    refs.append(ScorecardSourceRefDTO(kind=kind, entity_id=value, version=None))
            result["FACTUAL_OUTCOME"].append(
                CalibrationObservationDTO(
                    source_id=row.agenda_item_id,
                    observed_at=row.recorded_at,
                    occurred_at=happened,
                    title=row.title,
                    summary=row.outcome_note or "尚无已链接结果。",
                    status=status,
                    source_refs=tuple(refs),
                    limitation_codes=("SUBJECT_CONTEXT_NOT_CAUSAL_ATTRIBUTION",),
                )
            )
        return ("PARTIAL", ["AGENDA_HISTORY_BOUNDED"]) if len(rows) > 100 else ("COMPLETE", [])

    def _read_retro(
        self,
        decision: DecisionRecord,
        now: datetime,
        result: dict[str, list[CalibrationObservationDTO]],
    ) -> tuple[str, list[str]]:
        runs = self._retro.list_runs(limit=100)
        snapshots = self._retro.get_plan_snapshots(
            tuple({r.plan_snapshot_id for r in runs if r.plan_snapshot_id})
        )
        for run in runs:
            snapshot = snapshots.get(run.plan_snapshot_id or "")
            if (
                snapshot is None
                or run.generated_at > now
                or run.period_end > now
                or run.period_start < decision.recorded_at
                or snapshot.captured_at > run.period_start
            ):
                continue
            if not any(
                e.subject_id == decision.subject_id
                and e.plan_id == decision.trade_plan_id
                and e.plan_version == decision.trade_plan_version
                and any(d[0] == decision.decision_id for d in e.decision_records)
                for e in snapshot.entries
            ):
                continue
            for finding in run.findings:
                if finding.plan_id != decision.trade_plan_id:
                    continue
                result["ADHERENCE"].append(
                    CalibrationObservationDTO(
                        source_id=run.run_id,
                        observed_at=run.generated_at,
                        occurred_at=run.period_end,
                        title=finding.title,
                        summary=finding.detail,
                        status=finding.severity.value,
                        source_refs=(
                            ScorecardSourceRefDTO(
                                kind="TRADE_RETRO", entity_id=run.run_id, version=None
                            ),
                        ),
                        limitation_codes=run.warning_codes
                        + ("GENERATED_FINDING_NOT_USER_VERDICT",),
                    )
                )
        return ("PARTIAL", ["RETRO_HISTORY_BOUNDED"]) if len(runs) == 100 else ("COMPLETE", [])
