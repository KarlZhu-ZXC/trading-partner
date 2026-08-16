"""Typed post-confirmation appliers for Research Subject candidates.

The candidate lifecycle service owns proposal, authorization, and transaction
boundaries.  This collaborator owns only the append-only domain writes that
follow an already validated confirmation.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.research import (
    AssumptionCandidatePayload,
    InvalidationCandidatePayload,
    OpenQuestionCandidatePayload,
    SubjectUpdateCandidatePayload,
    ThesisRevisionCandidatePayload,
    TradePlanCandidatePayload,
    WatchlistCandidatePayload,
    parse_candidate_payload,
)
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.research_state_invariants import (
    ensure_active_trade_plan_dependencies,
    ensure_no_live_monitors,
    ensure_single_live_primary_thesis,
    ensure_subject_can_host_live_thesis,
    ensure_subject_can_leave_tracking,
    ensure_thesis_relationship_dependencies,
    ensure_thesis_status_transition,
)
from application.services.subject_metadata_policy import validate_subject_metadata
from domain.common.enums import (
    AssumptionStatus,
    CandidateKind,
    ConfirmationMode,
    InvalidationSeverity,
    InvalidationStatus,
    Market,
    OpenQuestionStatus,
    ResearchSubjectStatus,
    ThesisRole,
    ThesisStatus,
    WatchlistItemStatus,
)
from domain.common.errors import (
    DataContractError,
    InputValidationError,
    ResearchStateConflict,
    StrictReviewRequired,
)
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    OpenQuestion,
    ResearchSubject,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlan, TradePlanCondition


class ThesisRevisionAppliers:
    """Apply one confirmed candidate inside the caller's active UOW.

    No method commits or changes candidate status.  The caller remains the
    authority for the Propose → Confirm state machine and commits the candidate
    status together with the affected append-only records.
    """

    def __init__(self, id_generator: IdGenerator) -> None:
        self._id_generator = id_generator

    def apply(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        *,
        reviewed_by: str,
        now: datetime,
    ) -> tuple[str, str | None]:
        payload = parse_candidate_payload(candidate.payload_json)
        kind = candidate.kind

        if kind is CandidateKind.THESIS_REVISION:
            assert isinstance(payload, ThesisRevisionCandidatePayload)
            return self._apply_thesis_revision(
                uow, candidate, payload, reviewed_by=reviewed_by, now=now
            )
        if kind is CandidateKind.ASSUMPTION:
            assert isinstance(payload, AssumptionCandidatePayload)
            return self._apply_assumption(uow, candidate, payload, reviewed_by=reviewed_by, now=now)
        if kind is CandidateKind.INVALIDATION_CONDITION:
            assert isinstance(payload, InvalidationCandidatePayload)
            return self._apply_invalidation(
                uow, candidate, payload, reviewed_by=reviewed_by, now=now
            )
        if kind is CandidateKind.OPEN_QUESTION:
            assert isinstance(payload, OpenQuestionCandidatePayload)
            return self._apply_open_question(
                uow, candidate, payload, reviewed_by=reviewed_by, now=now
            )
        if kind is CandidateKind.WATCHLIST_ITEM:
            assert isinstance(payload, WatchlistCandidatePayload)
            return self._apply_watchlist(uow, candidate, payload, now=now)
        if kind is CandidateKind.SUBJECT_STATUS_CHANGE:
            assert isinstance(payload, SubjectUpdateCandidatePayload)
            return self._apply_subject_update(uow, candidate, payload, now=now)
        if kind is CandidateKind.TRADE_PLAN:
            assert isinstance(payload, TradePlanCandidatePayload)
            return self._apply_trade_plan(uow, candidate, payload, reviewed_by=reviewed_by, now=now)
        raise DataContractError(f"unsupported candidate kind: {kind}")

    @staticmethod
    def validate_thesis_relationships(
        uow: ResearchUnitOfWork,
        *,
        subject_id: str,
        thesis_id: str | None,
        payload: ThesisRevisionCandidatePayload,
    ) -> tuple[ThesisRole, str | None, tuple[str, ...]]:
        current = uow.theses.get(thesis_id) if thesis_id is not None else None
        role = (
            ThesisRole(payload.thesis_role)
            if payload.thesis_role is not None
            else current.role
            if current is not None
            else ThesisRole.PRIMARY
        )
        if payload.thesis_role is not None:
            parent_id = payload.parent_thesis_id if role is ThesisRole.SUB else None
        elif "parent_thesis_id" in payload.model_fields_set:
            parent_id = payload.parent_thesis_id
        else:
            parent_id = current.parent_thesis_id if current is not None else None
        rival_ids = (
            payload.rival_thesis_ids
            if payload.rival_thesis_ids is not None
            else current.rival_thesis_ids
            if current is not None
            else ()
        )

        if role is ThesisRole.SUB:
            if parent_id is None:
                raise InputValidationError("SUB Thesis requires parent_thesis_id")
            if parent_id == thesis_id:
                raise InputValidationError("Thesis cannot be its own parent")
            parent = uow.theses.get(parent_id)
            if parent.subject_id != subject_id:
                raise InputValidationError(
                    "parent_thesis_id does not belong to subject_id",
                    details={"parent_thesis_id": parent_id, "subject_id": subject_id},
                )
            if parent.role is not ThesisRole.PRIMARY:
                raise InputValidationError(
                    "SUB Thesis parent must be a PRIMARY Thesis",
                    details={"parent_thesis_id": parent_id, "parent_role": parent.role.value},
                )
        elif parent_id is not None:
            raise InputValidationError("Only SUB Thesis may set parent_thesis_id")

        if parent_id is not None and parent_id in rival_ids:
            raise InputValidationError("parent_thesis_id cannot also be a rival")
        for rival_id in rival_ids:
            if rival_id == thesis_id:
                raise InputValidationError("Thesis cannot rival itself")
            rival = uow.theses.get(rival_id)
            if rival.subject_id != subject_id:
                raise InputValidationError(
                    "rival_thesis_id does not belong to subject_id",
                    details={"rival_thesis_id": rival_id, "subject_id": subject_id},
                )
        attempted_status = (
            ThesisStatus(payload.thesis_status)
            if payload.thesis_status is not None
            else current.status
            if current is not None
            else ThesisStatus.ACTIVE
        )
        ensure_thesis_relationship_dependencies(
            uow,
            subject_id=subject_id,
            thesis_id=thesis_id,
            attempted_role=role,
            attempted_status=attempted_status,
            parent_thesis_id=parent_id,
        )
        return role, parent_id, rival_ids

    def _apply_trade_plan(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: TradePlanCandidatePayload,
        *,
        reviewed_by: str,
        now: datetime,
    ) -> tuple[str, str | None]:
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("trade_plan candidate requires subject_id")
        subject = uow.subjects.get(subject_id)

        thesis = uow.theses.get(payload.thesis_id)
        if thesis.subject_id != subject_id:
            raise DataContractError("Trade Plan thesis does not belong to Subject")
        status = TradePlanStatus(payload.status)
        ensure_active_trade_plan_dependencies(
            subject,
            thesis,
            attempted_child_status=status,
        )
        if (
            subject.primary_instrument_id is not None
            and subject.primary_instrument_id != payload.instrument_id
        ):
            raise DataContractError("Trade Plan instrument cannot diverge from Subject")

        current = uow.trade_plans.get_current_by_subject(subject_id)
        if payload.plan_id is None:
            if current is not None:
                raise DataContractError("Subject already has a Trade Plan")
            plan_id = self._id_generator.new(EntityIdPrefix.TRADE_PLAN)
            version = 1
        else:
            if current is None or current.plan_id != payload.plan_id:
                raise DataContractError("Trade Plan identity is not current for Subject")
            if payload.expected_version != current.version:
                from domain.common.errors import TradePlanVersionConflict

                raise TradePlanVersionConflict(
                    "Trade Plan expected_version does not match current version",
                    details={"current_version": current.version},
                )
            plan_id = current.plan_id
            version = current.version + 1

        if (
            current is not None
            and current.status in {TradePlanStatus.ACTIVE, TradePlanStatus.PAUSED}
            and status not in {TradePlanStatus.ACTIVE, TradePlanStatus.PAUSED}
        ):
            ensure_no_live_monitors(
                uow,
                trade_plan_id=current.plan_id,
                action="Trade Plan retirement",
            )

        conditions = tuple(
            TradePlanCondition(
                condition_code=item.condition_code,
                phase=TradePlanConditionPhase(item.phase),
                mode=TradePlanConditionMode(item.mode),
                description=item.description,
                severity=item.severity,
                fact_type=(TradePlanFactType(item.fact_type) if item.fact_type else None),
                metric_key=item.metric_key,
                comparator=(
                    TradePlanComparator(item.comparator) if item.comparator else None
                ),
                threshold=item.threshold,
                unit=item.unit,
                instrument_id=item.instrument_id,
                max_fact_age_seconds=item.max_fact_age_seconds,
                event_after=item.event_after,
            )
            for item in payload.conditions
        )
        plan = TradePlan(
            plan_id=plan_id,
            version=version,
            subject_id=subject_id,
            thesis_id=payload.thesis_id,
            instrument_id=payload.instrument_id,
            status=status,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            currency=payload.currency,
            reference_price=payload.reference_price,
            reference_price_at=payload.reference_price_at,
            target_position_percent=payload.target_position_percent,
            max_position_percent=payload.max_position_percent,
            risk_budget_percent=payload.risk_budget_percent,
            stop_price=payload.stop_price,
            conditions=conditions,
            notes=payload.notes,
            confirmed_by=reviewed_by,
            created_at=now,
            idempotency_key=candidate.idempotency_key,
        )
        uow.trade_plans.append(plan)
        self._touch_subject(uow, subject_id, now)
        return "trade_plan", plan.plan_id

    def _touch_subject(
        self,
        uow: ResearchUnitOfWork,
        subject_id: str,
        now: datetime,
    ) -> None:
        subject = uow.subjects.get(subject_id)
        updated = ResearchSubject(
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            title=subject.title,
            summary=subject.summary,
            status=subject.status,
            primary_instrument_id=subject.primary_instrument_id,
            topic_tags=subject.topic_tags,
            created_at=subject.created_at,
            updated_at=now,
            created_by=subject.created_by,
            archived_at=subject.archived_at,
            archived_reason=subject.archived_reason,
            linked_subject_ids=subject.linked_subject_ids,
            evidence_ids=subject.evidence_ids,
            report_ids=subject.report_ids,
            event_ids=subject.event_ids,
            decision_ids=subject.decision_ids,
            schema_version=subject.schema_version,
        )
        uow.subjects.update(updated)

    def _apply_thesis_revision(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: ThesisRevisionCandidatePayload,
        *,
        reviewed_by: str,
        now: datetime,
    ) -> tuple[str, str | None]:
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("thesis_revision candidate requires subject_id")

        subject = uow.subjects.get(subject_id)

        role, parent_thesis_id, rival_thesis_ids = self.validate_thesis_relationships(
            uow,
            subject_id=subject_id,
            thesis_id=candidate.thesis_id,
            payload=payload,
        )

        thesis_status = (
            ThesisStatus(payload.thesis_status)
            if payload.thesis_status is not None
            else ThesisStatus.ACTIVE
        )

        if candidate.thesis_id is None:
            if thesis_status in {ThesisStatus.INVALIDATED, ThesisStatus.ARCHIVED}:
                raise InputValidationError(
                    "new Thesis must start as DRAFT or a live status",
                    details={
                        "attempted_child_status": thesis_status.value,
                    },
                )
            ensure_subject_can_host_live_thesis(
                subject,
                attempted_child_status=thesis_status,
            )
            ensure_single_live_primary_thesis(
                uow,
                subject_id=subject_id,
                thesis_role=role,
                attempted_child_status=thesis_status,
            )
            thesis_id = self._id_generator.new(EntityIdPrefix.THESIS)
            revision_id = self._id_generator.new(EntityIdPrefix.REV)
            revision_no = 1
            supersedes = None
            thesis = Thesis(
                thesis_id=thesis_id,
                subject_id=subject_id,
                title=payload.title,
                role=role,
                status=thesis_status,
                current_revision_no=1,
                latest_revision_id=revision_id,
                parent_thesis_id=parent_thesis_id,
                rival_thesis_ids=rival_thesis_ids,
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
            uow.theses.add(thesis)
        else:
            thesis_id = candidate.thesis_id
            thesis = uow.theses.get(thesis_id)
            if thesis.subject_id != subject_id:
                raise DataContractError(
                    "Thesis does not belong to candidate Subject",
                    details={
                        "thesis_id": thesis.thesis_id,
                        "subject_id": subject_id,
                        "thesis_subject_id": thesis.subject_id,
                    },
                )
            thesis_status = (
                ThesisStatus(payload.thesis_status)
                if payload.thesis_status is not None
                else thesis.status
            )
            if (
                thesis_status is not thesis.status
                and candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW
            ):
                raise StrictReviewRequired(
                    "changing Thesis status requires STRICT_REVIEW",
                    details={
                        "thesis_id": thesis.thesis_id,
                        "from": thesis.status.value,
                        "to": thesis_status.value,
                    },
                )
            ensure_thesis_status_transition(
                uow,
                subject,
                thesis,
                attempted_child_status=thesis_status,
                attempted_role=role,
            )
            revision_no = uow.revisions.next_revision_no(thesis_id)
            supersedes = thesis.current_revision_no
            revision_id = self._id_generator.new(EntityIdPrefix.REV)

        from domain.common.enums import ConfidenceBand, InvestmentRating

        revision = ThesisRevision(
            revision_id=revision_id,
            thesis_id=thesis_id,
            subject_id=subject_id,
            revision_no=revision_no,
            supersedes_revision_no=supersedes,
            statement=payload.statement,
            rationale=payload.rationale,
            confidence_band=ConfidenceBand(payload.confidence_band),
            rating=InvestmentRating(payload.rating),
            confirmation_mode=candidate.confirmation_mode,
            proposed_by=candidate.proposed_by,
            confirmed_by=reviewed_by,
            proposed_at=candidate.proposed_at,
            confirmed_at=now,
            observation_window_start=payload.observation_window_start,
            observation_window_end=payload.observation_window_end,
            invalidation_check_note=payload.invalidation_check_note,
            schema_version=RESEARCH_SCHEMA_VERSION,
        )
        uow.revisions.append(revision)
        if candidate.thesis_id is not None:
            uow.theses.advance_current_revision(
                thesis_id,
                new_revision_no=revision_no,
                new_latest_revision_id=revision_id,
            )
            uow.theses.update_metadata(
                thesis_id,
                title=payload.title,
                role=role,
                parent_thesis_id=parent_thesis_id,
                rival_thesis_ids=rival_thesis_ids,
            )
            if thesis_status is not thesis.status:
                uow.theses.update_status(
                    thesis_id,
                    new_status=thesis_status,
                    archived_at=(now if thesis_status is ThesisStatus.ARCHIVED else None),
                )

        for ap in payload.assumptions:
            uow.assumptions.add(
                Assumption(
                    assumption_id=self._id_generator.new(EntityIdPrefix.REV),
                    thesis_id=thesis_id,
                    subject_id=subject_id,
                    revision_no=revision_no,
                    statement=ap.statement,
                    basis=ap.basis,
                    falsifiability=ap.falsifiability,
                    status=AssumptionStatus.ACCEPTED,
                    proposed_at=candidate.proposed_at,
                    confirmed_at=now,
                    proposed_by=candidate.proposed_by,
                    confirmed_by=reviewed_by,
                    retired_at=None,
                    retired_reason=None,
                )
            )

        for inv in payload.invalidations:
            severity = InvalidationSeverity(inv.severity)
            status = InvalidationStatus.ARMED
            if severity is InvalidationSeverity.HARD and status is not InvalidationStatus.ARMED:
                raise DataContractError("HARD invalidation must be created as ARMED")
            uow.invalidations.add(
                InvalidationCondition(
                    invalidation_id=self._id_generator.new(EntityIdPrefix.REV),
                    thesis_id=thesis_id,
                    subject_id=subject_id,
                    revision_no=revision_no,
                    description=inv.description,
                    observable=inv.observable,
                    severity=severity,
                    status=status,
                    proposed_at=candidate.proposed_at,
                    confirmed_at=now,
                    last_checked_at=None,
                    triggered_at=None,
                    triggered_reason=None,
                    proposed_by=candidate.proposed_by,
                    confirmed_by=reviewed_by,
                )
            )

        self._touch_subject(uow, subject_id, now)
        return "thesis_revision", revision_id

    def _apply_assumption(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: AssumptionCandidatePayload,
        *,
        reviewed_by: str,
        now: datetime,
    ) -> tuple[str, str | None]:
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("assumption candidate requires subject_id")
        assumption_id = self._id_generator.new(EntityIdPrefix.REV)
        uow.assumptions.add(
            Assumption(
                assumption_id=assumption_id,
                thesis_id=payload.thesis_id,
                subject_id=subject_id,
                revision_no=payload.revision_no,
                statement=payload.statement,
                basis=payload.basis,
                falsifiability=payload.falsifiability,
                status=AssumptionStatus.ACCEPTED,
                proposed_at=candidate.proposed_at,
                confirmed_at=now,
                proposed_by=candidate.proposed_by,
                confirmed_by=reviewed_by,
                retired_at=None,
                retired_reason=None,
            )
        )
        self._touch_subject(uow, subject_id, now)
        return "assumption", assumption_id

    def _apply_invalidation(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: InvalidationCandidatePayload,
        *,
        reviewed_by: str,
        now: datetime,
    ) -> tuple[str, str | None]:
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("invalidation candidate requires subject_id")
        severity = InvalidationSeverity(payload.severity)
        status = InvalidationStatus.ARMED
        if severity is InvalidationSeverity.HARD and status is not InvalidationStatus.ARMED:
            raise DataContractError("HARD invalidation must be created as ARMED")

        if payload.relaxes_invalidation_id is not None:
            if candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW:
                raise StrictReviewRequired(
                    "HARD relaxation requires STRICT_REVIEW",
                    details={"invalidation_id": payload.relaxes_invalidation_id},
                )
            old = uow.invalidations.get(payload.relaxes_invalidation_id)
            if old.severity is InvalidationSeverity.HARD and severity is InvalidationSeverity.SOFT:
                uow.invalidations.transition_status(
                    payload.relaxes_invalidation_id,
                    new_status=InvalidationStatus.RETIRED,
                    triggered_at=None,
                    triggered_reason=None,
                    last_checked_at=now,
                )

        inv_id = self._id_generator.new(EntityIdPrefix.REV)
        uow.invalidations.add(
            InvalidationCondition(
                invalidation_id=inv_id,
                thesis_id=payload.thesis_id,
                subject_id=subject_id,
                revision_no=payload.revision_no,
                description=payload.description,
                observable=payload.observable,
                severity=severity,
                status=status,
                proposed_at=candidate.proposed_at,
                confirmed_at=now,
                last_checked_at=None,
                triggered_at=None,
                triggered_reason=None,
                proposed_by=candidate.proposed_by,
                confirmed_by=reviewed_by,
            )
        )
        self._touch_subject(uow, subject_id, now)
        return "invalidation_condition", inv_id

    def _apply_open_question(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: OpenQuestionCandidatePayload,
        *,
        reviewed_by: str,
        now: datetime,
    ) -> tuple[str, str | None]:
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("open_question candidate requires subject_id")

        if payload.action == "create":
            qid = self._id_generator.new(EntityIdPrefix.REV)
            uow.questions.add(
                OpenQuestion(
                    question_id=qid,
                    subject_id=subject_id,
                    text=payload.text or "",
                    status=OpenQuestionStatus.OPEN,
                    asked_at=now,
                    answered_at=None,
                    answer_summary=None,
                    closed_without_answer_reason=None,
                    proposed_by=candidate.proposed_by,
                )
            )
            self._touch_subject(uow, subject_id, now)
            return "open_question", qid
        if payload.action == "answer":
            assert payload.question_id is not None
            assert payload.answer_summary is not None
            uow.questions.answer(
                payload.question_id,
                answered_at=now,
                answer_summary=payload.answer_summary,
            )
            self._touch_subject(uow, subject_id, now)
            return "open_question", payload.question_id
        if payload.action == "mark_stale":
            assert payload.question_id is not None
            uow.questions.mark_stale(payload.question_id)
            self._touch_subject(uow, subject_id, now)
            return "open_question", payload.question_id
        if payload.action == "close":
            assert payload.question_id is not None
            assert payload.closed_reason is not None
            uow.questions.close_without_answer(
                payload.question_id,
                closed_reason=payload.closed_reason,
            )
            self._touch_subject(uow, subject_id, now)
            return "open_question", payload.question_id
        raise DataContractError(f"unknown open_question action: {payload.action}")

    def _apply_watchlist(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: WatchlistCandidatePayload,
        *,
        now: datetime,
    ) -> tuple[str, str | None]:
        if payload.action == "create":
            market_value = payload.market
            symbol = (payload.symbol or "").strip()
            if payload.instrument_id is not None:
                _, instrument_market, instrument_symbol = parse_instrument_id(payload.instrument_id)
                market_value = instrument_market
                symbol = instrument_symbol
            if market_value is None:
                raise DataContractError(
                    "create watchlist payload requires market or instrument_id"
                )
            item_id = self._id_generator.new(EntityIdPrefix.SNAPSHOT)
            item = WatchlistItem(
                item_id=item_id,
                market=Market(str(market_value)),
                symbol=symbol,
                display_name=(payload.display_name or "").strip(),
                thesis_hint=(payload.thesis_hint or "").strip(),
                triggers=payload.triggers,
                subject_id=(
                    payload.subject_id
                    if payload.subject_id is not None
                    else candidate.subject_id
                ),
                status=WatchlistItemStatus.WATCHING,
                created_at=now,
                updated_at=now,
                expires_at=payload.expires_at,
                promoted_to_subject_id=None,
                triggered_at=None,
                triggered_reason=None,
                instrument_id=payload.instrument_id,
                selection_reason=None,
            )
            uow.watchlist.add(item)
            return "watchlist_item", item_id

        assert payload.item_id is not None
        assert payload.new_status is not None
        new_status = WatchlistItemStatus(payload.new_status)
        current_item = uow.watchlist.get(payload.item_id)
        if candidate.subject_id is not None and current_item.subject_id != candidate.subject_id:
            raise DataContractError("candidate item does not belong to Research Subject")
        if new_status is WatchlistItemStatus.SELECTED:
            if current_item.subject_id is None:
                raise DataContractError("selected candidate requires Research Subject")
            selected = uow.watchlist.list(
                subject_id=current_item.subject_id,
                status=WatchlistItemStatus.SELECTED,
                limit=2,
            )
            if any(item.item_id != current_item.item_id for item in selected):
                raise ResearchStateConflict(
                    "Research Subject already has a selected Instrument candidate; "
                    "reject or archive it before selecting another"
                )
        triggered_at = now if new_status is WatchlistItemStatus.TRIGGERED else None
        uow.watchlist.update_status(
            payload.item_id,
            new_status=new_status,
            triggered_at=triggered_at,
            triggered_reason=payload.triggered_reason,
            promoted_to_subject_id=payload.promoted_to_subject_id,
            expires_at=payload.expires_at,
            selection_reason=payload.selection_reason,
        )
        return "watchlist_item", payload.item_id

    def _apply_subject_update(
        self,
        uow: ResearchUnitOfWork,
        candidate: CandidateThesisRevision,
        payload: SubjectUpdateCandidatePayload,
        *,
        now: datetime,
    ) -> tuple[str, str | None]:
        subject_id = candidate.subject_id
        if subject_id is None:
            raise DataContractError("subject_status_change candidate requires subject_id")
        subject = uow.subjects.get(subject_id)

        if payload.action == "create":
            raise DataContractError(
                "subject create candidates are confirmed at creation time, "
                "not via confirm_candidate"
            )
        if payload.action == "archive":
            if candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW:
                raise StrictReviewRequired("archive requires STRICT_REVIEW")
            ensure_subject_can_leave_tracking(
                uow,
                subject,
                attempted_subject_status=ResearchSubjectStatus.ARCHIVED,
            )
            reason = payload.archived_reason or "archived"
            archived = ResearchSubject(
                subject_id=subject.subject_id,
                subject_type=subject.subject_type,
                title=subject.title,
                summary=subject.summary,
                status=ResearchSubjectStatus.ARCHIVED,
                primary_instrument_id=subject.primary_instrument_id,
                topic_tags=subject.topic_tags,
                created_at=subject.created_at,
                updated_at=now,
                created_by=subject.created_by,
                archived_at=now,
                archived_reason=reason,
                linked_subject_ids=subject.linked_subject_ids,
                evidence_ids=subject.evidence_ids,
                report_ids=subject.report_ids,
                event_ids=subject.event_ids,
                decision_ids=subject.decision_ids,
                schema_version=subject.schema_version,
            )
            uow.subjects.update(archived)
            return "research_subject", subject_id

        if payload.subject_type is not None or payload.primary_instrument_id is not None:
            raise InputValidationError(
                "Subject type and primary instrument are immutable after creation",
            )
        new_status = (
            ResearchSubjectStatus(payload.new_status)
            if payload.new_status is not None
            else subject.status
        )
        metadata = validate_subject_metadata(
            title=payload.title if payload.title is not None else subject.title,
            summary=payload.summary if payload.summary is not None else subject.summary,
        )
        ensure_subject_can_leave_tracking(
            uow,
            subject,
            attempted_subject_status=new_status,
        )
        if (
            subject.status == ResearchSubjectStatus.ACTIVE
            and new_status != ResearchSubjectStatus.ACTIVE
            and candidate.confirmation_mode != ConfirmationMode.STRICT_REVIEW
        ):
            raise StrictReviewRequired(
                "leaving ACTIVE subject status requires STRICT_REVIEW",
                details={"from": subject.status.value, "to": new_status.value},
            )
        is_archived = new_status == ResearchSubjectStatus.ARCHIVED
        updated = ResearchSubject(
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            title=metadata.title,
            summary=metadata.summary,
            status=new_status,
            primary_instrument_id=subject.primary_instrument_id,
            topic_tags=payload.topic_tags if payload.topic_tags is not None else subject.topic_tags,
            created_at=subject.created_at,
            updated_at=now,
            created_by=subject.created_by,
            archived_at=now if is_archived else None,
            archived_reason=(
                (payload.archived_reason or subject.archived_reason or "archived")
                if is_archived
                else None
            ),
            linked_subject_ids=(
                payload.linked_subject_ids
                if payload.linked_subject_ids is not None
                else subject.linked_subject_ids
            ),
            evidence_ids=subject.evidence_ids,
            report_ids=subject.report_ids,
            event_ids=subject.event_ids,
            decision_ids=subject.decision_ids,
            schema_version=subject.schema_version,
        )
        uow.subjects.update(updated)
        return "research_subject", subject_id
