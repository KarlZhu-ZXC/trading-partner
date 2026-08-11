"""Deterministic Thesis Judgment Scorecard S1 orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from application.dto.error_mapper import to_error_info_from_exception
from application.dto.judgment_scorecard import (
    JudgmentScorecardHistoryDTO,
    JudgmentScorecardHistoryInput,
    JudgmentScorecardRunDTO,
)
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.judgment_scorecard_repository import JudgmentScorecardRepository
from application.ports.monitor_repository import MonitorRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.ports.trade_retro_repository import TradeRetroRepository
from application.services._research_support import UowFactory
from domain.catalyst_agenda.enums import AgendaItemStatus
from domain.catalyst_agenda.models import CatalystAgendaVersion
from domain.common.enums import DecisionType, EvidenceStance, Freshness, InvalidationStatus
from domain.common.errors import DataContractError, IdempotencyConflict
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    Assumption,
    DecisionRecord,
    Evidence,
    EvidenceAssessment,
    InvalidationCondition,
    ThesisRevision,
)
from domain.retro.enums import TradeRetroStatus
from domain.scorecard.enums import ScorecardDimensionStatus, ScorecardStatus
from domain.scorecard.models import (
    JUDGMENT_SCORECARD_ALGORITHM_VERSION,
    JUDGMENT_SCORECARD_SCHEMA_VERSION,
    JudgmentScorecardRun,
    ScorecardDimension,
    ScorecardSourceRef,
    scorecard_input_fingerprint,
)
from domain.trade_plan.models import TradePlan

_ACTION_INTENTS = frozenset(
    {
        DecisionType.INITIATE_INTENT,
        DecisionType.ADD_INTENT,
        DecisionType.REDUCE_INTENT,
        DecisionType.EXIT_INTENT,
    }
)
_T = TypeVar("_T")

_AGENDA_IDENTITY_CAP = 50
_AGENDA_SOURCE_KIND = "AGENDA"


@dataclass(frozen=True, slots=True)
class _AgendaCalibrationFacts:
    """Bounded machine facts used by the S1 catalyst dimension."""

    current: CatalystAgendaVersion
    pre_outcome: CatalystAgendaVersion | None
    outcome_time: datetime
    outcome_in_window: bool | None
    expected_question_before_outcome: bool
    exact_report_ids: tuple[str, ...]
    exact_evidence_ids: tuple[str, ...]
    exact_assessments: tuple[EvidenceAssessment, ...]
    source_refs: tuple[ScorecardSourceRef, ...]


class JudgmentScorecardService:
    def __init__(
        self,
        repository: JudgmentScorecardRepository,
        catalyst_agenda_repository: CatalystAgendaRepository,
        research_uow_factory: UowFactory,
        monitor_repository: MonitorRepository,
        trade_retro_repository: TradeRetroRepository,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._repository = repository
        self._agendas = catalyst_agenda_repository
        self._uow_factory = research_uow_factory
        self._monitors = monitor_repository
        self._retros = trade_retro_repository
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    def run(
        self, subject_id: str, thesis_id: str, idempotency_key: str
    ) -> ToolEnvelope[JudgmentScorecardRunDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        generated_at = self._clock.now()
        try:
            existing = self._repository.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.subject_id != subject_id or existing.thesis_id != thesis_id:
                    raise IdempotencyConflict(
                        "Judgment Scorecard idempotency key was reused"
                    )
                return self._success(
                    request_id,
                    existing.generated_at,
                    JudgmentScorecardRunDTO.from_domain(existing),
                    ("DUPLICATE_IDEMPOTENCY_KEY",),
                )
            with self._uow_factory() as uow:
                subject = uow.subjects.get(subject_id)
                thesis = uow.theses.get(thesis_id)
                if thesis.subject_id != subject_id:
                    raise DataContractError("thesis does not belong to subject")
                revision = uow.revisions.get(thesis.latest_revision_id)
                if revision.thesis_id != thesis_id or revision.subject_id != subject_id:
                    raise DataContractError("latest Thesis revision is outside requested scope")
                dimensions = self._dimensions(
                    uow,
                    subject_id=subject_id,
                    primary_instrument_id=getattr(subject, "primary_instrument_id", None),
                    thesis_id=thesis_id,
                    revision=revision,
                    as_of=generated_at,
                )
                fingerprint = scorecard_input_fingerprint(
                    {
                        "subject_id": subject_id,
                        "thesis_id": thesis_id,
                        "thesis_revision_id": revision.revision_id,
                        "thesis_revision_no": revision.revision_no,
                        "generated_at": generated_at.isoformat(),
                        "dimensions": [
                            {
                                "code": item.code,
                                "status": item.status.value,
                                "result_code": item.result_code,
                                "facts": item.facts,
                                "source_refs": [
                                    (ref.kind, ref.entity_id, ref.version)
                                    for ref in item.source_refs
                                ],
                            }
                            for item in dimensions
                        ],
                        "catalyst_agenda": {
                            "dimension_code": "CATALYST_OUTCOME_CALIBRATION",
                            "result_code": next(
                                item.result_code
                                for item in dimensions
                                if item.code == "CATALYST_OUTCOME_CALIBRATION"
                            ),
                            "status": next(
                                item.status.value
                                for item in dimensions
                                if item.code == "CATALYST_OUTCOME_CALIBRATION"
                            ),
                            "facts": next(
                                item.facts
                                for item in dimensions
                                if item.code == "CATALYST_OUTCOME_CALIBRATION"
                            ),
                            "source_refs": [
                                (ref.kind, ref.entity_id, ref.version)
                                for ref in next(
                                    item.source_refs
                                    for item in dimensions
                                    if item.code == "CATALYST_OUTCOME_CALIBRATION"
                                )
                            ],
                        },
                    }
                )
                status = self._overall_status(dimensions)
                warning_codes = self._warning_codes(status, dimensions)
                value = JudgmentScorecardRun(
                    scorecard_id=self._ids.new(EntityIdPrefix.SCORECARD),
                    subject_id=subject_id,
                    subject_title=subject.title,
                    thesis_id=thesis_id,
                    thesis_title=thesis.title,
                    thesis_revision_id=revision.revision_id,
                    thesis_revision_no=revision.revision_no,
                    generated_at=generated_at,
                    status=status,
                    dimensions=dimensions,
                    warning_codes=warning_codes,
                    input_fingerprint=fingerprint,
                    idempotency_key=idempotency_key,
                    algorithm_version=JUDGMENT_SCORECARD_ALGORITHM_VERSION,
                    schema_version=JUDGMENT_SCORECARD_SCHEMA_VERSION,
                )
            self._repository.append(value)
            return self._success(
                request_id,
                generated_at,
                JudgmentScorecardRunDTO.from_domain(value),
                tuple(WarningInfo(code=code, message=code) for code in warning_codes),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, generated_at, exc)

    def history(
        self, request: JudgmentScorecardHistoryInput
    ) -> ToolEnvelope[JudgmentScorecardHistoryDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            values, total = self._repository.list(
                subject_id=request.subject_id,
                thesis_id=request.thesis_id,
                limit=request.limit,
                offset=request.offset,
            )
            data = JudgmentScorecardHistoryDTO(
                runs=tuple(JudgmentScorecardRunDTO.from_domain(item) for item in values),
                total=total,
                has_more=request.offset + len(values) < total,
            )
            return self._success(request_id, as_of, data, ())
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def _dimensions(
        self,
        uow: ResearchUnitOfWork,
        *,
        subject_id: str,
        primary_instrument_id: str | None,
        thesis_id: str,
        revision: ThesisRevision,
        as_of: datetime,
    ) -> tuple[ScorecardDimension, ...]:
        revision_id = revision.revision_id
        revision_no = revision.revision_no
        revision_ref = ScorecardSourceRef("THESIS_REVISION", revision_id, revision_no)
        assumptions = uow.assumptions.list_by_revision(thesis_id, revision_no)
        invalidations = uow.invalidations.list_by_revision(thesis_id, revision_no)
        assessments = tuple(
            item
            for item in uow.evidence_assessments.list_for_thesis(thesis_id, as_of=as_of)
            if item.thesis_revision_id == revision_id
        )
        latest_assessments = self._latest_assessments(assessments)
        evidence = tuple(uow.evidence.get(item.evidence_id) for item in latest_assessments)
        decisions = uow.decisions.list_by_subject(subject_id, as_of=as_of)
        plan = uow.trade_plans.get_current_by_subject(subject_id)
        if plan is not None and plan.thesis_id != thesis_id:
            plan = None
        plan_versions = (
            uow.trade_plans.list_versions(plan.plan_id) if plan is not None else ()
        )

        return (
            self._revision_definition(revision, revision_ref, assumptions, invalidations),
            self._evidence_balance(latest_assessments, evidence, revision_ref),
            self._evidence_recency(latest_assessments, evidence, revision_ref, as_of),
            self._assumption_outcome(assumptions, revision_ref),
            self._invalidation_outcome(invalidations, revision_ref),
            self._plan_monitor_coverage(plan, revision_ref),
            self._plan_before_action_intent(plan_versions, decisions, revision_ref),
            self._trade_retro_discipline(plan, revision_ref),
            self._catalyst_outcome_calibration(
                uow,
                subject_id=subject_id,
                primary_instrument_id=primary_instrument_id,
                thesis_id=thesis_id,
                revision=revision,
                assessments=latest_assessments,
                revision_ref=revision_ref,
                as_of=as_of,
            ),
        )

    @staticmethod
    def _latest_assessments(
        assessments: Iterable[EvidenceAssessment],
    ) -> tuple[EvidenceAssessment, ...]:
        latest: dict[str, EvidenceAssessment] = {}
        for item in assessments:
            current = latest.get(item.evidence_id)
            if current is None or item.assessed_at > current.assessed_at:
                latest[item.evidence_id] = item
        return tuple(sorted(latest.values(), key=lambda value: value.evidence_id))

    @staticmethod
    def _revision_definition(
        revision: ThesisRevision,
        revision_ref: ScorecardSourceRef,
        assumptions: tuple[Assumption, ...],
        invalidations: tuple[InvalidationCondition, ...],
    ) -> ScorecardDimension:
        statement = revision.statement
        rationale = revision.rationale
        has_definition = bool(statement.strip() and rationale.strip())
        limitations = () if has_definition else ("REVISION_DEFINITION_MISSING",)
        return ScorecardDimension(
            code="REVISION_DEFINITION_COVERAGE",
            status=(
                ScorecardDimensionStatus.EVALUATED
                if has_definition
                else ScorecardDimensionStatus.PARTIAL
            ),
            result_code="DEFINED" if has_definition else "INCOMPLETE",
            title="Thesis revision definition",
            summary=(
                "The exact Thesis revision has a statement and rationale."
                if has_definition
                else "The exact Thesis revision is missing a bounded statement or rationale."
            ),
            facts=(
                ("REVISION_NO", str(revision.revision_no)),
                ("ASSUMPTION_COUNT", str(len(assumptions))),
                ("INVALIDATION_COUNT", str(len(invalidations))),
            ),
            source_refs=(revision_ref,),
            limitation_codes=limitations,
        )

    @staticmethod
    def _evidence_balance(
        assessments: tuple[EvidenceAssessment, ...],
        evidence: tuple[Evidence, ...],
        revision_ref: ScorecardSourceRef,
    ) -> ScorecardDimension:
        if not assessments:
            return ScorecardDimension(
                code="REVISION_EVIDENCE_BALANCE",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_EXACT_REVISION_ASSESSMENTS",
                title="Evidence balance",
                summary="No evidence assessments are linked to this exact Thesis revision.",
                source_refs=(revision_ref,),
                limitation_codes=("NO_EXACT_REVISION_ASSESSMENTS",),
            )
        stances = {item.stance for item in assessments}
        has_support = EvidenceStance.SUPPORTS in stances
        has_contradict = EvidenceStance.CONTRADICTS in stances
        if has_support and has_contradict:
            result = "MIXED"
        elif has_support:
            result = "SUPPORTS"
        elif has_contradict:
            result = "CONTRADICTS"
        elif EvidenceStance.NEUTRAL in stances:
            result = "NEUTRAL"
        else:
            result = "UNCERTAIN"
        status = (
            ScorecardDimensionStatus.PARTIAL
            if result == "UNCERTAIN"
            else ScorecardDimensionStatus.EVALUATED
        )
        evidence_cap = 40
        capped_evidence = evidence[:evidence_cap]
        balance_limitations = (
            ("EVIDENCE_REFS_TRUNCATED",) if len(evidence) > evidence_cap else ()
        )
        return ScorecardDimension(
            code="REVISION_EVIDENCE_BALANCE",
            status=status,
            result_code=result,
            title="Evidence balance",
            summary=f"Latest assessments for this revision are {result.lower()}.",
            facts=(("ASSESSMENT_COUNT", str(len(assessments))),),
            source_refs=(
                revision_ref,
                *tuple(
                    ScorecardSourceRef("EVIDENCE", item.evidence_id)
                    for item in capped_evidence
                ),
            ),
            limitation_codes=balance_limitations
            + (("UNCERTAIN_STANCE",) if result == "UNCERTAIN" else ()),
        )

    @staticmethod
    def _evidence_recency(
        assessments: tuple[EvidenceAssessment, ...],
        evidence: tuple[Evidence, ...],
        revision_ref: ScorecardSourceRef,
        as_of: datetime,
    ) -> ScorecardDimension:
        if not assessments:
            return ScorecardDimension(
                code="EVIDENCE_RECENCY",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_EXACT_REVISION_EVIDENCE",
                title="Evidence recency",
                summary="No exact-revision evidence is available for an age calculation.",
                source_refs=(revision_ref,),
                limitation_codes=("NO_EXACT_REVISION_EVIDENCE",),
            )
        evidence_cap = 10
        capped_assessments = assessments[:evidence_cap]
        capped_evidence = evidence[:evidence_cap]
        facts: list[tuple[str, str]] = []
        refs: list[ScorecardSourceRef] = [revision_ref]
        for index, (assessment, item) in enumerate(
            zip(capped_assessments, capped_evidence, strict=True), 1
        ):
            observed_at = item.observed_at
            age = max(0, int((as_of - observed_at).total_seconds()))
            facts.extend(
                (
                    (f"EVIDENCE_{index}_OBSERVED_AT", observed_at.isoformat()),
                    (f"EVIDENCE_{index}_ASSESSED_AT", assessment.assessed_at.isoformat()),
                    (f"EVIDENCE_{index}_AGE_SECONDS", str(age)),
                )
            )
            refs.append(ScorecardSourceRef("EVIDENCE", item.evidence_id))
        return ScorecardDimension(
            code="EVIDENCE_RECENCY",
            status=ScorecardDimensionStatus.EVALUATED,
            result_code="OBSERVED_AGE_REPORTED",
            title="Evidence recency",
            summary=(
                "Observed and assessed timestamps are reported without a subjective "
                "freshness threshold."
            ),
            facts=tuple(facts),
            source_refs=tuple(refs),
            limitation_codes=(
                ("EVIDENCE_RECENCY_TRUNCATED",)
                if len(assessments) > evidence_cap
                else ()
            ),
        )

    @staticmethod
    def _assumption_outcome(
        assumptions: tuple[Assumption, ...], revision_ref: ScorecardSourceRef
    ) -> ScorecardDimension:
        return ScorecardDimension(
            code="ASSUMPTION_OUTCOME",
            status=ScorecardDimensionStatus.NOT_EVALUATED,
            result_code="NO_ASSUMPTION_EVIDENCE_LINK",
            title="Assumption outcome",
            summary="Assumptions are present, but S0 has no assumption-to-evidence outcome link.",
            facts=(("ASSUMPTION_COUNT", str(len(assumptions))),),
            source_refs=(revision_ref,),
            limitation_codes=("ASSUMPTION_EVIDENCE_LINK_UNAVAILABLE",),
        )

    @staticmethod
    def _invalidation_outcome(
        invalidations: tuple[InvalidationCondition, ...], revision_ref: ScorecardSourceRef
    ) -> ScorecardDimension:
        triggered = tuple(
            item
            for item in invalidations
            if item.status is InvalidationStatus.TRIGGERED
        )
        checked = tuple(item for item in invalidations if item.last_checked_at is not None)
        if triggered:
            status = ScorecardDimensionStatus.EVALUATED
            result = "TRIGGERED"
            limitation_codes: tuple[str, ...] = ()
        elif checked:
            status = ScorecardDimensionStatus.PARTIAL
            result = "CURRENT_STATE_ONLY"
            limitation_codes = ("HISTORICAL_INVALIDATION_OUTCOME_UNAVAILABLE",)
        else:
            status = ScorecardDimensionStatus.NOT_EVALUATED
            result = "NEVER_CHECKED"
            limitation_codes = ("INVALIDATION_NEVER_CHECKED",)
        refs = (revision_ref,) + tuple(
            ScorecardSourceRef("INVALIDATION", item.invalidation_id)
            for item in invalidations
        )
        return ScorecardDimension(
            code="THESIS_INVALIDATION_OUTCOME",
            status=status,
            result_code=result,
            title="Thesis invalidation outcome",
            summary=(
                "At least one current invalidation condition is triggered."
                if triggered
                else (
                    "Only current invalidation state is available; historical outcome "
                    "is not inferred."
                )
                if checked
                else "No invalidation condition has a recorded check."
            ),
            facts=(("INVALIDATION_COUNT", str(len(invalidations))),),
            source_refs=refs,
            limitation_codes=limitation_codes,
        )

    def _plan_monitor_coverage(
        self, plan: TradePlan | None, revision_ref: ScorecardSourceRef
    ) -> ScorecardDimension:
        if plan is None:
            return ScorecardDimension(
                code="PLAN_MONITOR_COVERAGE",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_TRADE_PLAN",
                title="Trade Plan monitor coverage",
                summary="No current Trade Plan is available for this Research Subject.",
                source_refs=(revision_ref,),
                limitation_codes=("NO_TRADE_PLAN",),
            )
        monitor_defs = tuple(
            value
            for value in self._monitors.list_current()
            if value.trade_plan_id == plan.plan_id and value.trade_plan_version == plan.version
        )
        monitorable = tuple(value for value in plan.conditions if value.mode.value == "MONITORABLE")
        manual = tuple(value for value in plan.conditions if value.mode.value == "MANUAL")
        matched_codes: set[str] = set()
        observed_codes: set[str] = set()
        refs: list[ScorecardSourceRef] = [
            revision_ref,
            ScorecardSourceRef("TRADE_PLAN", plan.plan_id, plan.version),
        ]
        for monitor in monitor_defs:
            exact = self._monitors.get_version(monitor.monitor_id, monitor.version)
            if exact is None:
                continue
            refs.append(ScorecardSourceRef("MONITOR", exact.monitor_id, exact.version))
            rule_codes = {item.rule_code for item in exact.rules}
            matched_codes.update(
                value.condition_code
                for value in monitorable
                if value.condition_code in rule_codes
            )
            run = self._monitors.latest_run_for_monitor_version(exact.monitor_id, exact.version)
            if run is not None:
                observed_codes.update(
                    item.rule_code
                    for item in run.observations
                    if item.rule_code in {value.condition_code for value in monitorable}
                )
                refs.append(ScorecardSourceRef("MONITOR_RUN", run.run_id))
        total = len(monitorable)
        limitations: tuple[str, ...]
        facts = (
            ("MONITORABLE_CONDITION_COUNT", str(total)),
            ("MATCHED_CONDITION_COUNT", str(len(matched_codes))),
            ("OBSERVED_CONDITION_COUNT", str(len(observed_codes))),
            ("MANUAL_CONDITION_COUNT", str(len(manual))),
        )
        if total == 0:
            status = ScorecardDimensionStatus.NOT_EVALUATED
            result = "NO_MONITORABLE_CONDITIONS"
            limitations = ("NO_MONITORABLE_CONDITIONS",)
        elif len(matched_codes) == total and len(observed_codes) == total and not manual:
            status = ScorecardDimensionStatus.EVALUATED
            result = "COVERED"
            limitations = ()
        else:
            status = ScorecardDimensionStatus.PARTIAL
            result = "INCOMPLETE"
            limitations = ("MANUAL_OR_UNOBSERVED_CONDITIONS",)
        return ScorecardDimension(
            code="PLAN_MONITOR_COVERAGE",
            status=status,
            result_code=result,
            title="Trade Plan monitor coverage",
            summary=(
                "Exact Trade Plan conditions are compared with exact bound Monitor "
                "versions and latest observations."
            ),
            facts=facts,
            source_refs=tuple(refs),
            limitation_codes=limitations,
        )

    @staticmethod
    def _plan_before_action_intent(
        plan_versions: tuple[TradePlan, ...],
        decisions: tuple[DecisionRecord, ...],
        revision_ref: ScorecardSourceRef,
    ) -> ScorecardDimension:
        if not plan_versions:
            return ScorecardDimension(
                code="PLAN_BEFORE_ACTION_INTENT",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_TRADE_PLAN",
                title="Plan before action intent",
                summary="No Trade Plan exists for a before-intent ordering check.",
                source_refs=(revision_ref,),
                limitation_codes=("NO_TRADE_PLAN",),
            )
        action_decisions = tuple(
            item for item in decisions if item.decision_type in _ACTION_INTENTS
        )
        refs = (revision_ref,) + tuple(
            ScorecardSourceRef("TRADE_PLAN", plan.plan_id, plan.version)
            for plan in plan_versions
        )
        if not action_decisions:
            return ScorecardDimension(
                code="PLAN_BEFORE_ACTION_INTENT",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_ACTION_INTENT",
                title="Plan before action intent",
                summary="No action intent is recorded; this dimension does not infer an execution.",
                source_refs=refs,
                limitation_codes=("NO_ACTION_INTENT",),
            )
        def eligible(item: DecisionRecord) -> TradePlan | None:
            candidates = tuple(
                plan
                for plan in plan_versions
                if plan.status.value == "ACTIVE"
                and plan.created_at <= item.decided_at
                and plan.valid_from <= item.decided_at
                and (plan.valid_until is None or item.decided_at < plan.valid_until)
            )
            return max(candidates, key=lambda plan: plan.version) if candidates else None

        before = tuple(item for item in action_decisions if eligible(item) is not None)
        status = (
            ScorecardDimensionStatus.EVALUATED
            if len(before) == len(action_decisions)
            else ScorecardDimensionStatus.PARTIAL
        )
        result = (
            "INTENT_AFTER_PLAN"
            if status is ScorecardDimensionStatus.EVALUATED
            else "INTENT_ORDER_UNCLEAR"
        )
        return ScorecardDimension(
            code="PLAN_BEFORE_ACTION_INTENT",
            status=status,
            result_code=result,
            title="Plan before action intent",
            summary=(
                "Action intents are checked against the exact Trade Plan creation time; "
                "fills are not inferred."
            ),
            facts=(("ACTION_INTENT_COUNT", str(len(action_decisions))),),
            source_refs=refs
            + tuple(ScorecardSourceRef("DECISION", item.decision_id) for item in action_decisions),
            limitation_codes=(
                "EXECUTION_RECORD_NOT_EVALUATED",
                *(
                    ("ACTION_INTENT_NOT_PRECEDED_BY_ACTIVE_PLAN",)
                    if len(before) != len(action_decisions)
                    else ()
                ),
            ),
        )

    def _trade_retro_discipline(
        self, plan: TradePlan | None, revision_ref: ScorecardSourceRef
    ) -> ScorecardDimension:
        if plan is None:
            return ScorecardDimension(
                code="TRADE_RETRO_DISCIPLINE",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_TRADE_PLAN",
                title="Trade Retro discipline",
                summary="No Trade Plan exists for a plan-tagged retro finding check.",
                source_refs=(revision_ref,),
                limitation_codes=("NO_TRADE_PLAN",),
            )
        runs = self._retros.list_runs(limit=100)
        matching = tuple(
            run for run in runs if any(item.plan_id == plan.plan_id for item in run.findings)
        )
        refs = (revision_ref, ScorecardSourceRef("TRADE_PLAN", plan.plan_id, plan.version)) + tuple(
            ScorecardSourceRef("TRADE_RETRO", run.run_id) for run in matching
        )
        if not matching:
            return ScorecardDimension(
                code="TRADE_RETRO_DISCIPLINE",
                status=ScorecardDimensionStatus.NOT_EVALUATED,
                result_code="NO_PLAN_TAGGED_FINDINGS",
                title="Trade Retro discipline",
                summary=(
                    "No immutable Trade Retro finding is tagged to this Trade Plan; "
                    "this is not a pass."
                ),
                source_refs=refs,
                limitation_codes=("NO_PLAN_TAGGED_RETRO_FINDING",),
            )
        limitations: tuple[str, ...]
        if any(run.status is TradeRetroStatus.INCOMPLETE for run in matching):
            status = ScorecardDimensionStatus.PARTIAL
            result = "INCOMPLETE_RETRO"
            limitations = ("TRADE_RETRO_INCOMPLETE",)
        else:
            status = ScorecardDimensionStatus.EVALUATED
            result = "PLAN_TAGGED_FINDINGS_REUSED"
            limitations = tuple()
        return ScorecardDimension(
            code="TRADE_RETRO_DISCIPLINE",
            status=status,
            result_code=result,
            title="Trade Retro discipline",
            summary=(
                "Only immutable, plan-tagged Trade Retro findings are reused; no new "
                "execution judgment is inferred."
            ),
            facts=(("MATCHING_RETRO_RUN_COUNT", str(len(matching))),),
            source_refs=refs,
            limitation_codes=limitations,
        )

    def _catalyst_outcome_calibration(
        self,
        uow: ResearchUnitOfWork,
        *,
        subject_id: str,
        primary_instrument_id: str | None,
        thesis_id: str,
        revision: ThesisRevision,
        assessments: tuple[EvidenceAssessment, ...],
        revision_ref: ScorecardSourceRef,
        as_of: datetime,
    ) -> ScorecardDimension:
        """Evaluate visible Agenda outcomes without interpreting free text."""

        grouped: dict[str, list[CatalystAgendaVersion]] = {}
        for item in self._agendas.list_visible(as_of=as_of):
            if not self._s1_visible_at(item, as_of):
                continue
            if getattr(item, "subject_id", None) != subject_id and (
                primary_instrument_id is None
                or getattr(item, "instrument_id", None) != primary_instrument_id
            ):
                continue
            agenda_id = str(getattr(item, "agenda_item_id", ""))
            if agenda_id:
                grouped.setdefault(agenda_id, []).append(item)

        identity_count = len(grouped)
        agenda_groups = tuple(
            sorted(
                (
                    tuple(sorted(values, key=self._s1_version_key))
                    for values in grouped.values()
                ),
                key=lambda values: str(getattr(values[-1], "agenda_item_id", "")),
            )[:_AGENDA_IDENTITY_CAP]
        )
        identity_truncated = identity_count > len(agenda_groups)
        ref_values: list[ScorecardSourceRef] = [revision_ref]
        ref_seen = {(revision_ref.kind, revision_ref.entity_id, revision_ref.version)}
        refs_truncated = False

        def add_ref(ref: ScorecardSourceRef) -> None:
            nonlocal refs_truncated
            key = (ref.kind, ref.entity_id, ref.version)
            if key in ref_seen:
                return
            if len(ref_values) >= 50:
                refs_truncated = True
                return
            ref_values.append(ref)
            ref_seen.add(key)

        occurred_count = cancelled_count = upcoming_count = overdue_count = 0
        expected_question_count = closure_only_count = no_calibratable_count = 0
        supports_count = contradicts_count = neutral_count = uncertain_count = 0
        calibratable_agenda_count = 0
        in_window_count = outside_window_count = timing_unavailable_count = 0

        for versions in agenda_groups:
            current = versions[-1]
            current_status = self._s1_status(current)
            add_ref(
                ScorecardSourceRef(
                    _AGENDA_SOURCE_KIND,
                    str(current.agenda_item_id),
                    int(current.version),
                )
            )
            pre_outcome = self._s1_pre_outcome_version(versions, current)
            if pre_outcome is not None:
                add_ref(
                    ScorecardSourceRef(
                        _AGENDA_SOURCE_KIND,
                        str(pre_outcome.agenda_item_id),
                        int(pre_outcome.version),
                    )
                )
            if current_status == AgendaItemStatus.UPCOMING.value:
                upcoming_count += 1
                window_end = getattr(current, "window_end", None)
                if window_end is not None and window_end < as_of:
                    overdue_count += 1
                continue
            if current_status == AgendaItemStatus.CANCELLED.value:
                cancelled_count += 1
                continue
            if current_status != AgendaItemStatus.OCCURRED.value:
                continue

            occurred_count += 1
            outcome_time = self._s1_outcome_time(uow, current, as_of)
            if getattr(current, "outcome_occurred_at", None) is None:
                timing_unavailable_count += 1
            timing = self._s1_within_window(pre_outcome or current, outcome_time)
            if timing is True:
                in_window_count += 1
            elif timing is False:
                outside_window_count += 1
            expected_before = bool(
                pre_outcome is not None
                and isinstance(getattr(pre_outcome, "expected_question", None), str)
                and bool(getattr(pre_outcome, "expected_question", "").strip())
                and getattr(pre_outcome, "recorded_at", outcome_time) <= outcome_time
            )
            if expected_before:
                expected_question_count += 1
            outcome = self._s1_agenda_outcome_facts(
                uow,
                current=current,
                pre_outcome=pre_outcome,
                revision_id=revision.revision_id,
                thesis_id=thesis_id,
                subject_id=subject_id,
                assessments=assessments,
                outcome_time=outcome_time,
                expected_before=expected_before,
                as_of=as_of,
            )
            for ref in outcome.source_refs:
                add_ref(ref)
            if not outcome.exact_assessments and not outcome.exact_report_ids:
                closure_only_count += 1
                continue
            stances = {item.stance for item in outcome.exact_assessments}
            if EvidenceStance.SUPPORTS in stances:
                supports_count += 1
            if EvidenceStance.CONTRADICTS in stances:
                contradicts_count += 1
            if EvidenceStance.NEUTRAL in stances:
                neutral_count += 1
            if EvidenceStance.UNCERTAIN in stances:
                uncertain_count += 1
            if outcome.exact_assessments:
                calibratable_agenda_count += 1
            else:
                no_calibratable_count += 1

        facts = {
            "AGENDA_IDENTITY_COUNT": identity_count,
            "AGENDA_SELECTED_IDENTITY_COUNT": len(agenda_groups),
            "AGENDA_TRUNCATED_COUNT": max(0, identity_count - len(agenda_groups)),
            "OCCURRED_COUNT": occurred_count,
            "CANCELLED_COUNT": cancelled_count,
            "UPCOMING_COUNT": upcoming_count,
            "OVERDUE_COUNT": overdue_count,
            "EXPECTED_QUESTION_BEFORE_OUTCOME_COUNT": expected_question_count,
            "CALIBRATABLE_OUTCOME_COUNT": calibratable_agenda_count,
            "CLOSURE_ONLY_COUNT": closure_only_count,
            "NO_CALIBRATABLE_OUTCOME_COUNT": no_calibratable_count,
            "SUPPORTS_COUNT": supports_count,
            "CONTRADICTS_COUNT": contradicts_count,
            "NEUTRAL_COUNT": neutral_count,
            "UNCERTAIN_COUNT": uncertain_count,
            "OUTCOME_IN_WINDOW_COUNT": in_window_count,
            "OUTCOME_OUTSIDE_WINDOW_COUNT": outside_window_count,
            "OUTCOME_TIME_UNAVAILABLE_COUNT": timing_unavailable_count,
        }
        limitations: list[str] = []
        if not agenda_groups:
            limitations.append("NO_RELEVANT_AGENDA")
        if identity_truncated:
            limitations.append("AGENDA_IDENTITIES_TRUNCATED")
        if refs_truncated:
            limitations.append("AGENDA_SOURCE_REFS_TRUNCATED")
        if overdue_count:
            limitations.append("AGENDA_OUTCOMES_OVERDUE")
        if closure_only_count:
            limitations.append("AGENDA_OUTCOME_CLOSURE_ONLY")
        if no_calibratable_count:
            limitations.append("NO_CALIBRATABLE_OUTCOME")

        if not agenda_groups:
            result_code = "NO_RELEVANT_AGENDA"
        elif occurred_count == 0:
            result_code = "NO_OCCURRED_OUTCOME"
        else:
            result_code = self._s1_result_code(
                supports_count=supports_count,
                contradicts_count=contradicts_count,
                neutral_count=neutral_count,
                uncertain_count=uncertain_count,
                no_calibratable_count=no_calibratable_count,
                closure_only_count=closure_only_count,
            )
        unresolved = (
            closure_only_count
            + no_calibratable_count
            + overdue_count
        )
        if not agenda_groups or occurred_count == 0:
            dimension_status = ScorecardDimensionStatus.NOT_EVALUATED
        elif unresolved:
            dimension_status = ScorecardDimensionStatus.PARTIAL
        else:
            dimension_status = ScorecardDimensionStatus.EVALUATED
        summary = (
            "No visible Agenda identity is scoped to this Research Subject."
            if not agenda_groups
            else f"Visible OCCURRED Agenda outcomes are {result_code.lower()} "
            "only where exact-revision machine facts exist."
        )
        return self._s1_dimension(
            status=dimension_status,
            result_code=result_code,
            summary=summary,
            facts=facts,
            source_refs=ref_values,
            limitation_codes=tuple(limitations),
        )

    @staticmethod
    def _s1_visible_at(item: object, as_of: datetime) -> bool:
        for field in ("source_visible_at", "recorded_at"):
            value = getattr(item, field, None)
            if isinstance(value, datetime) and value > as_of:
                return False
        return True

    @staticmethod
    def _s1_status(item: object) -> str:
        value = getattr(item, "status", "")
        return str(getattr(value, "value", value))

    @staticmethod
    def _s1_version_key(item: object) -> int:
        return int(getattr(item, "version", 0))

    @classmethod
    def _s1_pre_outcome_version(
        cls, versions: tuple[CatalystAgendaVersion, ...], current: CatalystAgendaVersion
    ) -> CatalystAgendaVersion | None:
        candidates = tuple(
            value
            for value in versions
            if int(getattr(value, "version", 0)) < int(getattr(current, "version", 0))
            and cls._s1_status(value) == AgendaItemStatus.UPCOMING.value
        )
        return max(candidates, key=cls._s1_version_key) if candidates else None

    @staticmethod
    def _s1_safe_get(repository: Any, entity_id: str | None) -> Any | None:
        if not entity_id or repository is None or not hasattr(repository, "get"):
            return None
        try:
            return repository.get(entity_id)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _s1_outcome_time(
        cls, uow: ResearchUnitOfWork, current: CatalystAgendaVersion, as_of: datetime
    ) -> datetime:
        explicit = getattr(current, "outcome_occurred_at", None)
        if isinstance(explicit, datetime):
            return explicit
        event = cls._s1_safe_get(
            getattr(uow, "events", None),
            getattr(current, "linked_event_id", None),
        )
        event_time = getattr(event, "occurred_at", None)
        if isinstance(event_time, datetime):
            return event_time
        report = cls._s1_safe_get(
            getattr(uow, "reports", None), getattr(current, "linked_report_id", None)
        )
        report_time = getattr(report, "as_of", None)
        if isinstance(report_time, datetime):
            return report_time
        recorded_at = getattr(current, "recorded_at", None)
        return recorded_at if isinstance(recorded_at, datetime) else as_of

    @staticmethod
    def _s1_within_window(item: CatalystAgendaVersion, outcome_time: datetime) -> bool | None:
        window_start = getattr(item, "window_start", None)
        window_end = getattr(item, "window_end", None)
        if not isinstance(window_start, datetime) or not isinstance(window_end, datetime):
            return None
        return window_start <= outcome_time <= window_end

    @classmethod
    def _s1_agenda_outcome_facts(
        cls,
        uow: ResearchUnitOfWork,
        *,
        current: CatalystAgendaVersion,
        pre_outcome: CatalystAgendaVersion | None,
        revision_id: str,
        thesis_id: str,
        subject_id: str,
        assessments: tuple[EvidenceAssessment, ...],
        outcome_time: datetime,
        expected_before: bool,
        as_of: datetime,
    ) -> _AgendaCalibrationFacts:
        refs: list[ScorecardSourceRef] = []
        refs.append(
            ScorecardSourceRef(
                _AGENDA_SOURCE_KIND,
                str(current.agenda_item_id),
                int(current.version),
            )
        )
        if pre_outcome is not None:
            refs.append(
                ScorecardSourceRef(
                    _AGENDA_SOURCE_KIND,
                    str(pre_outcome.agenda_item_id),
                    int(pre_outcome.version),
                )
            )

        event_ids: list[str] = []
        report_ids: list[str] = []
        evidence_ids: list[str] = []
        linked_event_id = getattr(current, "linked_event_id", None)
        linked_report_id = getattr(current, "linked_report_id", None)
        linked_evidence_id = getattr(current, "linked_evidence_id", None)
        if linked_event_id:
            event_ids.append(str(linked_event_id))
            refs.append(ScorecardSourceRef("EVENT", str(linked_event_id)))
        if linked_report_id:
            report_ids.append(str(linked_report_id))
            refs.append(ScorecardSourceRef("REPORT", str(linked_report_id)))
        if linked_evidence_id:
            evidence_ids.append(str(linked_evidence_id))
            refs.append(ScorecardSourceRef("EVIDENCE", str(linked_evidence_id)))

        for event_id in tuple(event_ids):
            event = cls._s1_safe_get(getattr(uow, "events", None), event_id)
            if event is None or not cls._s1_visible_at(event, as_of):
                continue
            evidence_ids.extend(str(value) for value in getattr(event, "evidence_ids", ()) if value)
            report_ids.extend(str(value) for value in getattr(event, "report_ids", ()) if value)
        exact_report_ids: list[str] = []
        for report_id in tuple(dict.fromkeys(report_ids)):
            if report_id != linked_report_id:
                refs.append(ScorecardSourceRef("REPORT", report_id))
            report = cls._s1_safe_get(getattr(uow, "reports", None), report_id)
            if report is None or not cls._s1_visible_at(report, as_of):
                continue
            revision_ids = tuple(getattr(report, "thesis_revision_ids", ()))
            if revision_id not in revision_ids:
                continue
            exact_report_ids.append(report_id)
            evidence_ids.extend(
                str(value) for value in getattr(report, "evidence_ids", ()) if value
            )

        unique_evidence_ids = tuple(dict.fromkeys(evidence_ids))
        for evidence_id in unique_evidence_ids:
            if not any(
                item.evidence_id == evidence_id
                and item.thesis_revision_id == revision_id
                and item.thesis_id == thesis_id
                and item.subject_id == subject_id
                for item in assessments
            ):
                refs.append(ScorecardSourceRef("EVIDENCE", evidence_id))
        exact_assessments = cls._latest_assessments_for_ids(
            assessments,
            evidence_ids=unique_evidence_ids,
            revision_id=revision_id,
            thesis_id=thesis_id,
            subject_id=subject_id,
        )
        for assessment in exact_assessments:
            refs.append(ScorecardSourceRef("EVIDENCE_ASSESSMENT", assessment.assessment_id))
        return _AgendaCalibrationFacts(
            current=current,
            pre_outcome=pre_outcome,
            outcome_time=outcome_time,
            outcome_in_window=cls._s1_within_window(pre_outcome or current, outcome_time),
            expected_question_before_outcome=expected_before,
            exact_report_ids=tuple(exact_report_ids),
            exact_evidence_ids=unique_evidence_ids,
            exact_assessments=exact_assessments,
            source_refs=tuple(refs),
        )

    @staticmethod
    def _latest_assessments_for_ids(
        assessments: tuple[EvidenceAssessment, ...],
        *,
        evidence_ids: tuple[str, ...],
        revision_id: str,
        thesis_id: str,
        subject_id: str,
    ) -> tuple[EvidenceAssessment, ...]:
        return JudgmentScorecardService._latest_assessments(
            tuple(
                item
                for item in assessments
                if item.evidence_id in evidence_ids
                and item.thesis_revision_id == revision_id
                and item.thesis_id == thesis_id
                and item.subject_id == subject_id
            )
        )

    @staticmethod
    def _s1_result_code(
        *,
        supports_count: int,
        contradicts_count: int,
        neutral_count: int,
        uncertain_count: int,
        no_calibratable_count: int,
        closure_only_count: int,
    ) -> str:
        if supports_count and contradicts_count:
            return "MIXED"
        if supports_count:
            return "SUPPORTS"
        if contradicts_count:
            return "CONTRADICTS"
        if no_calibratable_count:
            return "NO_CALIBRATABLE_OUTCOME"
        if neutral_count:
            return "NEUTRAL"
        if uncertain_count:
            return "UNCERTAIN"
        if closure_only_count:
            return "CLOSURE_ONLY"
        return "NO_CALIBRATABLE_OUTCOME"

    @staticmethod
    def _s1_dimension(
        *,
        status: ScorecardDimensionStatus,
        result_code: str,
        summary: str,
        facts: dict[str, int],
        source_refs: list[ScorecardSourceRef],
        limitation_codes: tuple[str, ...],
    ) -> ScorecardDimension:
        return ScorecardDimension(
            code="CATALYST_OUTCOME_CALIBRATION",
            status=status,
            result_code=result_code,
            title="Catalyst Agenda outcome calibration",
            summary=summary,
            facts=tuple((key, str(value)) for key, value in facts.items()),
            source_refs=tuple(source_refs),
            limitation_codes=limitation_codes,
        )

    @staticmethod
    def _overall_status(dimensions: tuple[ScorecardDimension, ...]) -> ScorecardStatus:
        statuses = {item.status for item in dimensions}
        if statuses == {ScorecardDimensionStatus.EVALUATED}:
            return ScorecardStatus.COMPLETE
        if statuses == {ScorecardDimensionStatus.NOT_EVALUATED}:
            return ScorecardStatus.NOT_EVALUATED
        return ScorecardStatus.PARTIAL

    @staticmethod
    def _warning_codes(
        status: ScorecardStatus, dimensions: tuple[ScorecardDimension, ...]
    ) -> tuple[str, ...]:
        codes: list[str] = []
        if status is ScorecardStatus.PARTIAL:
            codes.append("JUDGMENT_SCORECARD_PARTIAL")
        if status is ScorecardStatus.NOT_EVALUATED:
            codes.append("JUDGMENT_SCORECARD_NOT_EVALUATED")
        for item in dimensions:
            codes.extend(item.limitation_codes)
        return tuple(dict.fromkeys(codes))

    @staticmethod
    def _success(
        request_id: str,
        as_of: datetime,
        data: _T,
        warnings: tuple[WarningInfo | str, ...],
    ) -> ToolEnvelope[_T]:
        normalized = tuple(
            warning
            if isinstance(warning, WarningInfo)
            else WarningInfo(code=warning, message=warning)
            for warning in warnings
        )
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=as_of,
            freshness=Freshness.FRESH,
            sources=(),
            data=data,
            degraded=bool(normalized),
            warnings=normalized,
        )

    def _failure(
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[_T]:
        error = to_error_info_from_exception(exc, self._redactor)
        return ToolEnvelope.failure(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=as_of,
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=(error,),
            degraded=True,
        )
