"""Compose one model-independent, deterministic Observation review package."""

from __future__ import annotations

import json
from collections.abc import Callable

from application.dto.external_note_review import (
    ExternalNoteReviewDraftDTO,
    ExternalNoteReviewDTO,
)
from application.dto.view_review import (
    CurrentViewDTO,
    ExternalViewpointDTO,
    ViewDecisionBaselineDTO,
    ViewInboxDTO,
    ViewInboxItemDTO,
    ViewMonitorBaselineDTO,
    ViewPlanBaselineDTO,
    ViewPositionContextDTO,
    ViewReviewPackageDTO,
    ViewScenarioDTO,
    ViewThesisBaselineDTO,
)
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.monitor_repository import MonitorRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services._research_support import build_research_state
from domain.common.errors import DataContractError, ExternalNoteReviewNotFound
from domain.external_note.enums import ExternalNoteReviewStatus

ResearchUowFactory = Callable[[], ResearchUnitOfWork]
_LIVE_THESIS = {"active", "strengthened", "weakened"}


def _text(value: object, fallback: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


class ViewReviewService:
    def __init__(
        self,
        notes: ExternalNoteRepository,
        reviews: ExternalNoteReviewRepository,
        research_uow_factory: ResearchUowFactory,
        account_snapshots: AccountSnapshotRepository,
        monitors: MonitorRepository,
    ) -> None:
        self._notes = notes
        self._reviews = reviews
        self._research_uow_factory = research_uow_factory
        self._account_snapshots = account_snapshots
        self._monitors = monitors

    def get(
        self,
        note_revision_id: str,
        *,
        explicit_review: bool = False,
    ) -> ViewReviewPackageDTO:
        revision = self._notes.revision_by_id(note_revision_id.strip())
        if revision is None:
            raise ExternalNoteReviewNotFound("Observation revision was not found")
        identity = self._notes.get(revision.note_id)
        review = self._reviews.latest_for_revision(revision.note_revision_id)
        deep_review = self._reviews.latest_successful_draft(revision.note_revision_id)
        if deep_review is None:
            deep_review = self._reviews.latest_draft(revision.note_revision_id)
        interpretation = self._notes.interpretation_for_revision(revision.note_revision_id)
        if identity is None or review is None:
            raise ExternalNoteReviewNotFound("Observation review is not materialized")
        if interpretation is None or interpretation.status != "SUCCEEDED":
            raise ExternalNoteReviewNotFound("Observation interpretation is unavailable")
        try:
            raw = json.loads(interpretation.payload_json)
        except (TypeError, ValueError) as exc:
            raise DataContractError("Observation interpretation payload is invalid") from exc
        if not isinstance(raw, dict):
            raise DataContractError("Observation interpretation payload must be an object")

        scenarios = self._scenarios(raw.get("user_scenarios"))
        if {item.scenario for item in scenarios} != {
            "UPSIDE",
            "SIDEWAYS",
            "PULLBACK",
            "INVALIDATION",
        }:
            raise DataContractError("Observation review requires all four USER scenarios")
        external_viewpoints = self._external_viewpoints(raw.get("viewpoints"))
        subject_id = review.subject_id
        subject_title: str | None = None
        subject_status: str | None = None
        thesis: ViewThesisBaselineDTO | None = None
        plan: ViewPlanBaselineDTO | None = None
        latest_decision: ViewDecisionBaselineDTO | None = None
        flags: list[str] = []
        coverage = {
            "observation": "COMPLETE",
            "interpretation": "COMPLETE",
            "research": "NOT_MAPPED" if subject_id is None else "COMPLETE",
            "portfolio": "COMPLETE",
            "monitors": "COMPLETE",
        }

        if subject_id is None:
            flags.append("UNMAPPED_RESEARCH_SUBJECT")
        else:
            try:
                with self._research_uow_factory() as uow:
                    state = build_research_state(
                        uow,
                        subject_id,
                        include_archived_theses=True,
                        include_watchlist=False,
                    )
                    decisions = uow.decisions.list_by_subject(subject_id)
            except Exception:  # noqa: BLE001 - one context source degrades independently
                coverage["research"] = "UNAVAILABLE"
                flags.append("RESEARCH_CONTEXT_UNAVAILABLE")
            else:
                subject_title = state.subject.title
                subject_status = str(state.subject.status)
                if subject_status.lower() == "archived":
                    flags.append("SUBJECT_ARCHIVED")
                live = [
                    item
                    for item in state.theses
                    if str(item.status).lower() in _LIVE_THESIS
                ]
                primary = next(
                    (item for item in live if str(item.role).lower() == "primary"),
                    live[0] if live else None,
                )
                if primary is None:
                    flags.append("NO_LIVE_THESIS")
                else:
                    latest = next(
                        (
                            item
                            for item in state.latest_revisions
                            if item.thesis_id == primary.thesis_id
                        ),
                        None,
                    )
                    if latest is not None:
                        thesis = ViewThesisBaselineDTO(
                            thesis_id=primary.thesis_id,
                            revision_id=latest.revision_id,
                            title=primary.title,
                            statement=latest.statement,
                            status=str(primary.status),
                            rating=str(latest.rating),
                            confidence_band=str(latest.confidence_band),
                        )
                current_plan = state.current_trade_plan
                if current_plan is not None:
                    plan = ViewPlanBaselineDTO(
                        plan_id=current_plan.plan_id,
                        version=current_plan.version,
                        status=str(current_plan.status),
                        instrument_id=current_plan.instrument_id,
                    )
                baseline_decision = next(
                    (
                        item
                        for item in decisions
                        if item.external_note_revision_id != revision.note_revision_id
                    ),
                    None,
                )
                if baseline_decision is not None:
                    latest_decision = ViewDecisionBaselineDTO(
                        decision_id=baseline_decision.decision_id,
                        decision_type=str(baseline_decision.decision_type),
                        title=baseline_decision.title,
                        rationale=baseline_decision.rationale,
                        decided_at=baseline_decision.decided_at,
                        external_note_revision_id=(
                            baseline_decision.external_note_revision_id
                        ),
                    )

        relation = _text(raw.get("change_relation"), "UNKNOWN").upper()
        if thesis is not None and relation in {
            "CORRECTION",
            "REVISION",
            "SUPERSEDES",
            "INVALIDATES",
            "REMOVED_FROM_NOTE",
        }:
            flags.append("REVIEW_THESIS_IMPACT")
        suggested = _text(raw.get("suggested_next_step"), "REVIEW").upper()
        if subject_id is not None and suggested == "PROPOSE_PLAN" and plan is None:
            flags.append("NO_ACTIVE_TRADE_PLAN")

        instrument_id = identity.primary_instrument_id
        positions: list[ViewPositionContextDTO] = []
        if instrument_id is None:
            coverage["portfolio"] = "NOT_APPLICABLE"
        try:
            accounts = self._account_snapshots.latest_accounts()
        except Exception:  # noqa: BLE001 - one context source degrades independently
            accounts = ()
            coverage["portfolio"] = "UNAVAILABLE"
        for account in accounts:
            for position in account.positions:
                if instrument_id is not None and position.instrument_id == instrument_id:
                    positions.append(
                        ViewPositionContextDTO(
                            account_ref=account.account_ref,
                            provider=account.provider.value,
                            instrument_id=position.instrument_id,
                            quantity=position.quantity,
                            currency=position.currency,
                            account_as_of=account.account_as_of,
                        )
                    )

        monitor_values: list[ViewMonitorBaselineDTO] = []
        try:
            current_monitors = self._monitors.list_current()
        except Exception:  # noqa: BLE001 - one context source degrades independently
            current_monitors = ()
            coverage["monitors"] = "UNAVAILABLE"
        for monitor in current_monitors:
            if subject_id is not None and monitor.subject_id == subject_id:
                monitor_values.append(
                    ViewMonitorBaselineDTO(
                        monitor_id=monitor.monitor_id,
                        version=monitor.version,
                        name=monitor.name,
                        status=monitor.status.value,
                    )
                )

        escalation_reasons: list[str] = []
        if relation in {"SUPERSEDES", "INVALIDATES"}:
            escalation_reasons.append(f"CHANGE_{relation}")
        scenario_actions = {item.action for item in scenarios}
        for action in ("ADD", "REDUCE", "EXIT"):
            if action in scenario_actions:
                escalation_reasons.append(f"ACTION_{action}")
        if "REVIEW_THESIS_IMPACT" in flags:
            escalation_reasons.append("THESIS_IMPACT")
        if suggested in {"PROPOSE_DECISION", "PROPOSE_PLAN"}:
            escalation_reasons.append(f"NEXT_STEP_{suggested}")
        if explicit_review:
            escalation_reasons.append("EXPLICIT_USER_REVIEW")

        open_review = review.status in {
            ExternalNoteReviewStatus.PENDING,
            ExternalNoteReviewStatus.DEFERRED,
        }
        actions = ["DEFER"] if open_review else []
        if (
            subject_id is not None
            and open_review
            and (subject_status or "").lower() != "archived"
            and coverage["research"] == "COMPLETE"
        ):
            actions.extend(("RECORD_DECISION", "RECORD_NO_ACTION"))
            if (subject_status or "").lower() == "active":
                actions.append("PROPOSE_THESIS_REVISION")
                if thesis is not None:
                    actions.extend(("PROPOSE_TRADE_PLAN", "PREFILL_MONITOR"))

        return ViewReviewPackageDTO(
            review=ExternalNoteReviewDTO.from_domain(review),
            deep_review=(
                ExternalNoteReviewDraftDTO.from_domain(deep_review)
                if deep_review is not None
                else None
            ),
            note_id=revision.note_id,
            note_revision_id=revision.note_revision_id,
            note_version=revision.version,
            source=identity.source,
            title=revision.title,
            instrument_id=instrument_id,
            observed_at=revision.observed_at,
            change_relation=relation,
            material_change_summary=_text(
                raw.get("material_change_summary"), revision.summary
            ),
            user_scenarios=scenarios,
            external_viewpoints=external_viewpoints,
            contradictions=_string_tuple(raw.get("contradictions")),
            missing_evidence=_string_tuple(raw.get("missing_evidence")),
            suggested_next_step=suggested,
            subject_id=subject_id,
            subject_title=subject_title,
            subject_status=subject_status,
            thesis=thesis,
            trade_plan=plan,
            latest_decision=latest_decision,
            monitors=tuple(monitor_values),
            positions=tuple(positions),
            deterministic_flags=tuple(dict.fromkeys(flags)),
            escalation_reasons=tuple(dict.fromkeys(escalation_reasons)),
            requires_deep_review=bool(escalation_reasons),
            coverage=coverage,
            allowed_actions=tuple(actions),
        )

    def current(self, subject_id: str) -> CurrentViewDTO | None:
        normalized = subject_id.strip()
        if not normalized:
            raise DataContractError("Current View requires a Research Subject")
        reviews = self._reviews.list_latest(
            statuses=frozenset(
                {
                    ExternalNoteReviewStatus.ADOPTED,
                    ExternalNoteReviewStatus.NO_ACTION,
                }
            ),
            subject_id=normalized,
            limit=1,
        )
        if not reviews:
            return None
        review = reviews[0]
        if review.decision_id is None:
            raise DataContractError("Confirmed Observation review is missing its Decision")
        package = self.get(review.note_revision_id)
        if package.subject_title is None or package.subject_status is None:
            raise ExternalNoteReviewNotFound(
                "Current View Research Subject context is unavailable"
            )
        with self._research_uow_factory() as uow:
            decision = uow.decisions.get(review.decision_id)
        if (
            decision.subject_id != normalized
            or decision.external_note_revision_id != review.note_revision_id
        ):
            raise DataContractError(
                "Current View Decision does not match its exact Subject and Observation"
            )
        decision_dto = ViewDecisionBaselineDTO(
            decision_id=decision.decision_id,
            decision_type=str(decision.decision_type),
            title=decision.title,
            rationale=decision.rationale,
            decided_at=decision.decided_at,
            external_note_revision_id=decision.external_note_revision_id,
        )
        return CurrentViewDTO(
            subject_id=normalized,
            subject_title=package.subject_title,
            subject_status=package.subject_status,
            instrument_id=package.instrument_id,
            source_note_revision_id=review.note_revision_id,
            source_title=package.title,
            source_note_version=package.note_version,
            review=ExternalNoteReviewDTO.from_domain(review),
            decision=decision_dto,
            thesis=package.thesis,
            trade_plan=package.trade_plan,
            coverage=package.coverage,
        )

    def inbox(self, *, limit: int = 50) -> ViewInboxDTO:
        if not 1 <= limit <= 100:
            raise DataContractError("View Inbox limit must be between 1 and 100")
        reviews = self._reviews.list_latest(
            statuses=frozenset(
                {
                    ExternalNoteReviewStatus.PENDING,
                    ExternalNoteReviewStatus.DEFERRED,
                }
            ),
            limit=limit + 1,
        )
        items: list[ViewInboxItemDTO] = []
        for review in reviews[:limit]:
            revision = self._notes.revision_by_id(review.note_revision_id)
            if revision is None:
                raise ExternalNoteReviewNotFound(
                    "View Inbox source revision was not found"
                )
            identity = self._notes.get(revision.note_id)
            interpretation = self._notes.interpretation_for_revision(
                revision.note_revision_id
            )
            if identity is None or interpretation is None:
                raise ExternalNoteReviewNotFound(
                    "View Inbox source identity or interpretation is unavailable"
                )
            try:
                payload = json.loads(interpretation.payload_json)
            except (TypeError, ValueError) as exc:
                raise DataContractError(
                    "View Inbox interpretation payload is invalid"
                ) from exc
            if not isinstance(payload, dict):
                raise DataContractError(
                    "View Inbox interpretation payload must be an object"
                )
            items.append(
                ViewInboxItemDTO(
                    review=ExternalNoteReviewDTO.from_domain(review),
                    note_revision_id=revision.note_revision_id,
                    note_version=revision.version,
                    title=revision.title,
                    instrument_id=identity.primary_instrument_id,
                    observed_at=revision.observed_at,
                    change_relation=_text(
                        payload.get("change_relation"), "UNKNOWN"
                    ).upper(),
                    material_change_summary=_text(
                        payload.get("material_change_summary"), revision.summary
                    ),
                    suggested_next_step=_text(
                        payload.get("suggested_next_step"), "REVIEW"
                    ).upper(),
                    subject_id=review.subject_id,
                    allowed_actions=(
                        ("REVIEW_VIEW_CHANGE", "DEFER")
                        if review.subject_id is not None
                        else ("MAP_RESEARCH_SUBJECT", "DEFER")
                    ),
                )
            )
        return ViewInboxDTO(
            items=tuple(items),
            returned_count=len(items),
            has_more=len(reviews) > limit,
        )

    @staticmethod
    def _scenarios(value: object) -> tuple[ViewScenarioDTO, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(
            ViewScenarioDTO(
                scenario=_text(item.get("scenario")).upper(),
                action=_text(item.get("action"), "REVIEW").upper(),
                condition=_text(item.get("condition"), "Not stated"),
                confirmation=_text(item.get("confirmation"), "Not stated"),
                loss_boundary=_text(item.get("loss_boundary"), "Not stated"),
            )
            for item in value
            if isinstance(item, dict)
        )

    @staticmethod
    def _external_viewpoints(value: object) -> tuple[ExternalViewpointDTO, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(
            ExternalViewpointDTO(
                speaker_label=_text(item.get("speaker_label"), "UNKNOWN"),
                summary=_text(item.get("summary"), "Not stated"),
                direction=_text(item.get("direction"), "UNKNOWN").upper(),
            )
            for item in value
            if isinstance(item, dict)
            and _text(item.get("speaker_kind")).upper() != "USER"
        )
