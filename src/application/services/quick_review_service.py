"""One read-only review card and explicit, idempotent Decision capture."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import timedelta
from threading import RLock
from typing import Any

from application.dto.quick_review import QuickReviewInput
from application.dto.review_materials import (
    ReviewMaterialRef,
    ReviewMaterials,
    review_materials_key,
)
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.clock import Clock
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.services.decision_record_service import DecisionRecordService
from application.services.quick_review_thinking import LatestThinkingReader
from application.services.research_changes_service import ResearchChangesService
from domain.common.enums import ConfirmationMode, DecisionType
from domain.common.errors import DataContractError, IdempotencyConflict
from domain.external_note.enums import ExternalNoteReviewStatus


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class QuickReviewService:
    def __init__(
        self,
        uow_factory: Callable[[], ResearchUnitOfWork],
        changes: ResearchChangesService,
        thinking: LatestThinkingReader,
        snapshots: AccountSnapshotRepository,
        reviews: ExternalNoteReviewRepository,
        decisions: DecisionRecordService,
        clock: Clock,
        redactor: SecretRedactor,
    ) -> None:
        self._uow, self._changes, self._thinking = uow_factory, changes, thinking
        self._snapshots, self._reviews, self._decisions = snapshots, reviews, decisions
        self._clock, self._redactor = clock, redactor
        self._cards: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _collect(self, subject_id: str) -> dict[str, Any]:
        with self._uow() as uow:
            subject = uow.subjects.get(subject_id)
            if subject is None:
                raise DataContractError("Research Subject not found")
            current_theses = uow.theses.list_by_subject(subject_id)
            formal: dict[str, Any] = {
                "theses": sorted(
                    (t.thesis_id, t.latest_revision_id, str(t.status)) for t in current_theses
                ),
                "plan": None,
            }
            primary = next(
                (
                    t
                    for t in current_theses
                    if str(t.role).lower() == "primary"
                    and str(t.status).lower() in {"active", "strengthened", "weakened"}
                ),
                None,
            )
            draft_baseline = None
            if primary:
                revision = uow.revisions.get(primary.latest_revision_id)
                draft_baseline = {
                    "thesis_id": primary.thesis_id,
                    "revision_id": revision.revision_id,
                    "revision_no": revision.revision_no,
                    "title": primary.title,
                    "statement": revision.statement,
                    "rationale": revision.rationale,
                }
            plan = uow.trade_plans.get_current_by_subject(subject_id)
            if plan is not None:
                formal["plan"] = (plan.plan_id, plan.version, str(plan.status))
        projection = self._changes.get_for_review(subject_id)
        items = list(projection.items)
        more = projection.has_more
        warnings = list(projection.warning_codes)
        if more:
            warnings.append("QUICK_REVIEW_HISTORY_BOUNDED")
        coverage = dict(projection.coverage)
        try:
            thinking = self._thinking.get(subject.primary_instrument_id)
            coverage["THINKING"] = "COMPLETE"
        except Exception:
            thinking = []
            coverage["THINKING"] = "UNAVAILABLE"
            warnings.append("LATEST_THINKING_UNAVAILABLE")
        positions = []
        account_snapshots = []
        try:
            accounts = self._snapshots.latest_accounts()
            for account in accounts:
                account_snapshots.append(
                    {"snapshot_id": account.snapshot_id, "as_of": account.account_as_of.isoformat()}
                )
                for position in account.positions:
                    if position.instrument_id == subject.primary_instrument_id:
                        positions.append(
                            {
                                "account_ref": account.account_ref,
                                "quantity": str(position.quantity),
                                "currency": position.currency,
                                "as_of": account.account_as_of.isoformat(),
                                "snapshot_id": account.snapshot_id,
                            }
                        )
            coverage["PORTFOLIO"] = "COMPLETE"
        except Exception:
            coverage["PORTFOLIO"] = "UNAVAILABLE"
            warnings.append("PORTFOLIO_SNAPSHOT_UNAVAILABLE")
        pending = 0
        pending_sources: dict[str, Any] = {}
        try:
            reviews = self._reviews.list_latest(
                subject_id=subject_id,
                statuses=frozenset(
                    {ExternalNoteReviewStatus.PENDING, ExternalNoteReviewStatus.DEFERRED}
                ),
                limit=500,
            )
            pending_sources = {r.review_id: r for r in reviews}
            pending_ids = set(pending_sources)
            for note in thinking:
                review = self._reviews.latest_for_revision(str(note["note_revision_id"]))
                if review and review.status in {
                    ExternalNoteReviewStatus.PENDING,
                    ExternalNoteReviewStatus.DEFERRED,
                }:
                    pending_ids.add(review.review_id)
                    pending_sources[review.review_id] = review
            pending = len(pending_ids)
            coverage["OBSERVATION_REVIEWS"] = "PARTIAL" if len(reviews) == 500 else "COMPLETE"
        except Exception:
            coverage["OBSERVATION_REVIEWS"] = "UNAVAILABLE"
        # No volatile observation clock in the digest; source identities/times are retained.
        return {
            "subject_id": subject_id,
            "title": subject.title,
            "instrument_id": subject.primary_instrument_id,
            "baseline": projection.baseline.model_dump(mode="json")
            if projection.baseline
            else None,
            "changes": [i.model_dump(mode="json") for i in items],
            "coverage": coverage,
            "warnings": warnings,
            "positions": positions,
            "account_snapshots": account_snapshots,
            "pending_review_sources": [
                {
                    "review_id": r.review_id,
                    "note_revision_id": r.note_revision_id,
                    "version": r.version,
                    "status": str(r.status),
                }
                for r in pending_sources.values()
            ],
            "latest_thinking": thinking,
            "pending_observation_reviews": pending,
            "can_maintain": projection.baseline is not None and not more,
            "current_formal_versions": formal,
            "draft_baseline": draft_baseline,
        }

    @staticmethod
    def _materials(card: dict[str, Any], prepared_at: Any) -> ReviewMaterials:
        refs: list[ReviewMaterialRef] = []
        for _thesis_id, revision_id, _status in card["current_formal_versions"]["theses"]:
            refs.append(ReviewMaterialRef(kind="THESIS", source_id=revision_id))
        plan = card["current_formal_versions"]["plan"]
        if plan:
            refs.append(ReviewMaterialRef(kind="PLAN", source_id=plan[0], version=str(plan[1])))
        for item in card["changes"]:
            refs.append(
                ReviewMaterialRef(
                    kind="CHANGE",
                    source_id=item["source_id"],
                    version=item.get("source_version"),
                    as_of=item["occurred_at"],
                )
            )
        for item in card["latest_thinking"]:
            refs.append(
                ReviewMaterialRef(
                    kind="OBSERVATION_REVISION",
                    source_id=item["note_revision_id"],
                    version=str(item["version"]),
                    as_of=item["observed_at"],
                )
            )
        for item in card["account_snapshots"]:
            refs.append(
                ReviewMaterialRef(
                    kind="ACCOUNT_SNAPSHOT", source_id=item["snapshot_id"], as_of=item["as_of"]
                )
            )
        for item in card["pending_review_sources"]:
            refs.append(
                ReviewMaterialRef(
                    kind="OBSERVATION_REVIEW",
                    source_id=item["review_id"],
                    version=str(item["version"]),
                )
            )
        # Deduplication retains exact versions; bounded manifest is explicitly partial.
        refs = list(dict.fromkeys(refs))
        complete = len(refs) <= 500
        return ReviewMaterials(
            subject_id=card["subject_id"],
            baseline_decision_id=card["baseline"]["decision_id"] if card["baseline"] else None,
            prepared_at=prepared_at,
            references=tuple(refs[:500]),
            reference_count=len(refs),
            references_complete=complete,
            coverage=card["coverage"],
            warning_codes=tuple(card["warnings"])
            + (() if complete else ("REVIEW_MATERIAL_REFERENCES_BOUNDED",)),
            pending_observation_reviews=(
                card["pending_observation_reviews"]
                if card["coverage"].get("OBSERVATION_REVIEWS") != "UNAVAILABLE"
                else None
            ),
        )

    def _receipt(self, decision: Any, key: str) -> dict[str, Any]:
        materials = None
        journal_id = None
        try:
            with self._uow() as uow:
                entry = uow.journal.get_by_idempotency_key(review_materials_key(key))
            if (
                entry is not None
                and entry.related_entity_id == decision.decision_id
                and entry.subject_id == decision.subject_id
            ):
                parsed = ReviewMaterials.model_validate_json(entry.body_markdown)
                if parsed.subject_id == decision.subject_id:
                    materials = parsed.model_dump(mode="json")
                    journal_id = entry.journal_id
        except Exception:  # noqa: BLE001 - a receipt read never retries the Decision write
            pass
        return {
            "status": "RECORDED",
            "decision_id": decision.decision_id,
            "action": "maintain" if decision.decision_type == DecisionType.NO_ACTION else "defer",
            "review_due_at": decision.review_due_at.isoformat() if decision.review_due_at else None,
            "completion": {
                "decision_recorded": True,
                "observation_reviews_changed": False,
                "remaining_observation_reviews_at_review": materials["pending_observation_reviews"]
                if materials
                else None,
                "materials_status": "AVAILABLE" if materials else "UNAVAILABLE",
                "materials_journal_id": journal_id,
                "materials": materials,
            },
        }

    def get(self, subject_id: str) -> dict[str, Any]:
        card = self._collect(subject_id)
        now = self._clock.now()
        token = secrets.token_urlsafe(32)
        materials = self._materials(card, now)
        with self._lock:
            self._cards = {k: v for k, v in self._cards.items() if v["expires"] > now}
            if len(self._cards) >= 64:
                del self._cards[next(iter(self._cards))]
            self._cards[token] = {
                "card": card,
                "digest": _fingerprint(card),
                "expires": now + timedelta(minutes=20),
                "created": now,
                "materials": materials,
            }
        return {
            **card,
            "review_materials": materials.model_dump(mode="json"),
            "review_token": token,
            "expires_at": (now + timedelta(minutes=20)).isoformat(),
        }

    def submission_status(self, subject_id: str, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 128:
            raise DataContractError("Invalid review request identity")
        key = (
            "quick-review:"
            + hashlib.sha256((subject_id + ":" + idempotency_key).encode()).hexdigest()
        )
        with self._lock, self._uow() as uow:
            decision = uow.decisions.get_by_idempotency_key(key)
        if decision is None:
            return {"status": "NOT_FOUND"}
        if decision.subject_id != subject_id:
            raise DataContractError("Review receipt belongs to another Subject")
        return self._receipt(decision, key)

    def submit(self, subject_id: str, request: QuickReviewInput) -> dict[str, Any]:
        key = (
            "quick-review:"
            + hashlib.sha256((subject_id + ":" + request.idempotency_key).encode()).hexdigest()
        )
        kind = (
            DecisionType.NO_ACTION if request.action == "maintain" else DecisionType.RESEARCH_MORE
        )
        reason = self._redactor.redact_text(request.rationale)
        with self._lock:
            with self._uow() as uow:
                existing = uow.decisions.get_by_idempotency_key(key)
            if existing is not None:
                if (
                    existing.subject_id != subject_id
                    or existing.decision_type != kind
                    or existing.rationale != reason
                    or existing.review_due_at != request.review_due_at
                    or existing.supersedes_decision_id != request.baseline_decision_id
                ):
                    raise IdempotencyConflict(
                        "This review request was already used with different intent"
                    )
                return self._receipt(existing, key)
            cached = self._cards.get(request.review_token)
            if (
                not cached
                or cached["expires"] <= self._clock.now()
                or cached["card"]["subject_id"] != subject_id
            ):
                raise DataContractError(
                    "Review expired. Refresh the card; your draft can be retained."
                )
            card = cached["card"]
            materials = cached["materials"]
            baseline_id = card["baseline"]["decision_id"] if card["baseline"] else None
            if baseline_id != request.baseline_decision_id:
                raise DataContractError("The reviewed Decision does not match this card")
            if request.action == "maintain" and (
                not card["can_maintain"] or not materials.references_complete
            ):
                raise DataContractError(
                    "A reviewed baseline and complete change listing are required"
                )
            if request.action == "defer" and (
                request.review_due_at is None or request.review_due_at <= self._clock.now()
            ):
                raise DataContractError("Choose a future follow-up time")
            if request.review_due_at is not None and request.review_due_at <= self._clock.now():
                raise DataContractError("Follow-up time must be in the future")
            if _fingerprint(self._collect(subject_id)) != cached["digest"]:
                raise DataContractError(
                    "Judgment or evidence changed. Refresh the card before confirming."
                )
            with self._uow() as uow:
                baseline = uow.decisions.get(baseline_id) if baseline_id else None
            result = self._decisions.append(
                subject_id=subject_id,
                decision_type=kind,
                title="Quick Review: No Action"
                if request.action == "maintain"
                else "Quick Review: Follow-up",
                rationale=reason,
                decided_at=self._clock.now(),
                decided_by="user",
                confirmation_mode=ConfirmationMode.NORMAL,
                primary_instrument_id=card["instrument_id"],
                thesis_revision_ids=baseline.thesis_revision_ids if baseline else (),
                evidence_ids=baseline.evidence_ids if baseline else (),
                report_ids=baseline.report_ids if baseline else (),
                supersedes_decision_id=baseline_id,
                position_context_snapshot_id=(
                    card["account_snapshots"][0]["snapshot_id"]
                    if len(card["account_snapshots"]) == 1
                    else None
                ),
                review_materials=materials,
                idempotency_key=key,
                strategy_code=baseline.strategy_code if baseline else None,
                strategy_version=baseline.strategy_version if baseline else None,
                scenario=baseline.scenario if baseline else None,
                trade_plan_id=baseline.trade_plan_id if baseline else None,
                trade_plan_version=baseline.trade_plan_version if baseline else None,
                review_due_at=request.review_due_at,
            )
            if not result.ok or result.data is None:
                raise DataContractError(
                    "Review could not be recorded. Retry the same request to recover safely."
                )
            return self._receipt(result.data, key)
