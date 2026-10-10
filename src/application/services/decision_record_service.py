"""DecisionRecordService — confirmed Decision writes (Phase 1C C4b2).

Records research / position *intent* only. ``execution_effect`` is always false;
this service must not import broker, order, or trading adapters.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.research_memory import DecisionRecordDTO
from application.dto.review_materials import ReviewMaterials, review_materials_key
from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY, ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_memory_write_support import (
    audit_summary,
    compute_decision_idempotency_payload_sha256,
    compute_journal_idempotency_payload_sha256,
    redact_required_text,
    schema_version,
    stable_dedupe_strs,
    update_subject_decision_cache,
    validate_decision_references,
)
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
    normalize_idempotency_key,
    require_confirm_reviewer,
)
from domain.common.enums import (
    ConfirmationMode,
    DecisionScenario,
    DecisionType,
    JournalEntryType,
    ResearchSearchEntityType,
)
from domain.common.errors import DuplicateIdempotencyKey, InputValidationError, InvalidResearchLink
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.models import DecisionRecord, JournalEntry


class DecisionRecordService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._redactor = secret_redactor

    def list_review_due(
        self,
        *,
        now: datetime | None = None,
        subject_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[DecisionRecordDTO, ...]:
        """Read the bounded set of currently due, still-live Decisions.

        This is intentionally a direct durable read for the ReviewItem
        materializer.  It does not append an audit record, refresh a Provider,
        or reconcile ReviewItems itself; callers decide whether a successful
        page is complete enough to mark the source fully observed.
        """

        if type(limit) is not int or not 1 <= limit <= 500:
            raise InputValidationError(
                "limit must be an int in [1, 500]",
                details={"field": "limit", "limit": limit},
            )
        subject_id_n: str | None
        if subject_id is None:
            subject_id_n = None
        else:
            subject_id_n = subject_id.strip()
            if not subject_id_n:
                raise InputValidationError(
                    "subject_id must be non-blank when provided",
                    details={"field": "subject_id"},
                )
        if type(offset) is not int or offset < 0:
            raise InputValidationError(
                "offset must be a non-negative int",
                details={"field": "offset", "offset": offset},
            )
        cutoff = require_aware_datetime(
            now if now is not None else self._clock.now(), field_name="now"
        )
        with self._uow_factory() as uow:
            if offset == 0:
                values = uow.decisions.list_review_due(
                    now=cutoff,
                    subject_id=subject_id_n,
                    limit=limit,
                )
            else:
                values = uow.decisions.list_review_due(
                    now=cutoff,
                    subject_id=subject_id_n,
                    limit=limit,
                    offset=offset,
                )
        return tuple(DecisionRecordDTO.from_domain(value) for value in values)

    def append(
        self,
        *,
        subject_id: str,
        decision_type: DecisionType,
        title: str,
        rationale: str,
        decided_at: datetime,
        decided_by: str,
        confirmation_mode: ConfirmationMode,
        primary_instrument_id: str | None,
        thesis_revision_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        report_ids: tuple[str, ...],
        supersedes_decision_id: str | None,
        position_context_snapshot_id: str | None,
        idempotency_key: str,
        strategy_code: str | None = None,
        strategy_version: str | None = None,
        scenario: DecisionScenario | str | None = None,
        trade_plan_id: str | None = None,
        trade_plan_version: int | None = None,
        review_due_at: datetime | None = None,
        external_note_revision_id: str | None = None,
        review_materials: ReviewMaterials | None = None,
    ) -> ToolEnvelope[DecisionRecordDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(decided_by, action="decision_record_append")
            decider = decided_by.strip()

            subject_id_n = subject_id.strip()
            if not subject_id_n:
                raise InputValidationError(
                    "subject_id must be non-blank",
                    details={"field": "subject_id"},
                )

            title_r = redact_required_text(title, self._redactor, field="title")
            rationale_r = redact_required_text(rationale, self._redactor, field="rationale")
            decided = require_aware_datetime(decided_at, field_name="decided_at")

            def optional_text(value: str | None, *, field: str, max_len: int) -> str | None:
                if value is None:
                    return None
                if not isinstance(value, str):
                    raise InputValidationError(
                        f"{field} must be a string when provided",
                        details={"field": field, "type": type(value).__name__},
                    )
                normalized = value.strip()
                if not normalized:
                    raise InputValidationError(
                        f"{field} must be non-blank when provided",
                        details={"field": field},
                    )
                if len(normalized) > max_len:
                    raise InputValidationError(
                        f"{field} length must be <= {max_len}",
                        details={"field": field, "length": len(normalized), "max": max_len},
                    )
                return normalized

            strategy_code_n = optional_text(strategy_code, field="strategy_code", max_len=128)
            strategy_version_n = optional_text(
                strategy_version, field="strategy_version", max_len=128
            )
            if scenario is None:
                scenario_n = None
            else:
                try:
                    scenario_n = (
                        scenario
                        if isinstance(scenario, DecisionScenario)
                        else DecisionScenario(scenario)
                    )
                except (TypeError, ValueError) as exc:
                    raise InputValidationError(
                        "scenario must be one of UPSIDE, SIDEWAYS, PULLBACK, INVALIDATION",
                        details={"field": "scenario", "value": str(scenario)},
                    ) from exc

            plan_id_n: str | None
            if trade_plan_id is None:
                plan_id_n = None
            else:
                plan_id_n = trade_plan_id.strip()
                if not plan_id_n:
                    raise InputValidationError(
                        "trade_plan_id must be non-blank when provided",
                        details={"field": "trade_plan_id"},
                    )
            if (plan_id_n is None) != (trade_plan_version is None):
                raise InputValidationError(
                    "trade_plan_id and trade_plan_version must be provided together",
                    details={"field": "trade_plan_id/trade_plan_version", "rule": "pair"},
                )
            if trade_plan_version is not None and (
                type(trade_plan_version) is not int or trade_plan_version < 1
            ):
                raise InputValidationError(
                    "trade_plan_version must be a positive integer",
                    details={"field": "trade_plan_version"},
                )
            review_due = (
                require_aware_datetime(review_due_at, field_name="review_due_at")
                if review_due_at is not None
                else None
            )

            note_revision_id_n = optional_text(
                external_note_revision_id,
                field="external_note_revision_id",
                max_len=128,
            )

            primary: str | None
            if primary_instrument_id is None:
                primary = None
            else:
                primary = primary_instrument_id.strip()
                if not primary:
                    raise InputValidationError(
                        "primary_instrument_id must be non-blank when provided",
                        details={"field": "primary_instrument_id"},
                    )

            revisions = stable_dedupe_strs(thesis_revision_ids)
            evidence = stable_dedupe_strs(evidence_ids)
            reports = stable_dedupe_strs(report_ids)

            supersedes = (
                supersedes_decision_id.strip() if supersedes_decision_id is not None else None
            )
            if supersedes is not None and not supersedes:
                raise InputValidationError(
                    "supersedes_decision_id must be non-blank when provided",
                    details={"field": "supersedes_decision_id"},
                )

            snapshot_id: str | None
            if position_context_snapshot_id is None:
                snapshot_id = None
            else:
                snapshot_id = position_context_snapshot_id.strip()
                if not snapshot_id:
                    raise InputValidationError(
                        "position_context_snapshot_id must be non-blank when provided",
                        details={"field": "position_context_snapshot_id"},
                    )

            if review_materials is not None:
                if (
                    review_materials.subject_id != subject_id_n
                    or review_materials.baseline_decision_id != supersedes
                    or review_materials.prepared_at > decided
                ):
                    raise InputValidationError("Review materials do not match this Decision")
                if len(review_materials.model_dump_json()) > 180_000:
                    raise InputValidationError("Review materials exceed the record bound")

            key = normalize_idempotency_key(idempotency_key)
            payload_sha = compute_decision_idempotency_payload_sha256(
                subject_id=subject_id_n,
                decision_type=decision_type,
                title=title_r,
                rationale=rationale_r,
                decided_at=decided,
                decided_by=decider,
                confirmation_mode=confirmation_mode,
                primary_instrument_id=primary,
                thesis_revision_ids=revisions,
                evidence_ids=evidence,
                report_ids=reports,
                supersedes_decision_id=supersedes,
                position_context_snapshot_id=snapshot_id,
                strategy_code=strategy_code_n,
                strategy_version=strategy_version_n,
                scenario=scenario_n,
                trade_plan_id=plan_id_n,
                trade_plan_version=trade_plan_version,
                review_due_at=review_due,
                external_note_revision_id=note_revision_id_n,
            )

            with self._uow_factory() as uow:
                existing = uow.decisions.get_by_idempotency_key(key)
                if existing is not None:
                    existing_sha = compute_decision_idempotency_payload_sha256(
                        subject_id=existing.subject_id,
                        decision_type=existing.decision_type,
                        title=existing.title,
                        rationale=existing.rationale,
                        decided_at=existing.decided_at,
                        decided_by=existing.decided_by,
                        confirmation_mode=existing.confirmation_mode,
                        primary_instrument_id=existing.primary_instrument_id,
                        thesis_revision_ids=existing.thesis_revision_ids,
                        evidence_ids=existing.evidence_ids,
                        report_ids=existing.report_ids,
                        supersedes_decision_id=existing.supersedes_decision_id,
                        position_context_snapshot_id=(existing.position_context_snapshot_id),
                        strategy_code=existing.strategy_code,
                        strategy_version=existing.strategy_version,
                        scenario=existing.scenario,
                        trade_plan_id=existing.trade_plan_id,
                        trade_plan_version=existing.trade_plan_version,
                        review_due_at=existing.review_due_at,
                        external_note_revision_id=existing.external_note_revision_id,
                    )
                    materials_match = True
                    if review_materials is not None:
                        prior_materials = uow.journal.get_by_idempotency_key(
                            review_materials_key(key)
                        )
                        materials_match = (
                            prior_materials is not None
                            and prior_materials.related_entity_id == existing.decision_id
                            and prior_materials.body_markdown == review_materials.model_dump_json()
                        )
                    if existing_sha != payload_sha or not materials_match:
                        raise DuplicateIdempotencyKey(
                            "idempotency_key already used with a different payload",
                            details={
                                "idempotency_key": key,
                                "existing_decision_id": existing.decision_id,
                            },
                        )
                    # Same key + same payload: return existing; no re-index/cache.
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=DecisionRecordDTO.from_domain(existing),
                        warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                        degraded=True,
                    )

                recorded_at = self._clock.now()
                if review_materials is not None:
                    # Recheck formal targets inside the Decision UoW as well as
                    # Quick Review's source-fingerprint check before entering it.
                    for reference in review_materials.references:
                        if reference.kind == "THESIS":
                            reviewed_revision = uow.revisions.get(reference.source_id)
                            current_thesis = uow.theses.get(reviewed_revision.thesis_id)
                            if (
                                current_thesis.subject_id != subject_id_n
                                or current_thesis.latest_revision_id != reference.source_id
                            ):
                                raise InvalidResearchLink("Prepared Thesis version changed")
                        elif reference.kind == "PLAN":
                            current_plan = uow.trade_plans.get_current_by_subject(subject_id_n)
                            if (
                                current_plan is None
                                or current_plan.plan_id != reference.source_id
                                or str(current_plan.version) != reference.version
                            ):
                                raise InvalidResearchLink("Prepared Trade Plan version changed")
                if decided > recorded_at:
                    raise InputValidationError(
                        "decided_at must be <= recorded_at",
                        details={
                            "field": "decided_at",
                            "decided_at": decided.isoformat(),
                            "recorded_at": recorded_at.isoformat(),
                        },
                    )

                if plan_id_n is not None and trade_plan_version is not None:
                    plan = uow.trade_plans.get_version(plan_id_n, trade_plan_version)
                    if plan is None:
                        raise InvalidResearchLink(
                            "referenced Trade Plan version does not exist",
                            details={
                                "entity_type": "trade_plan",
                                "trade_plan_id": plan_id_n,
                                "trade_plan_version": trade_plan_version,
                                "subject_id": subject_id_n,
                            },
                        )
                    if plan.subject_id != subject_id_n:
                        raise InvalidResearchLink(
                            "Trade Plan does not belong to the same Research Subject",
                            details={
                                "entity_type": "trade_plan",
                                "trade_plan_id": plan_id_n,
                                "trade_plan_version": trade_plan_version,
                                "subject_id": subject_id_n,
                                "plan_subject_id": plan.subject_id,
                            },
                        )

                validate_decision_references(
                    uow,
                    subject_id=subject_id_n,
                    recorded_at=recorded_at,
                    thesis_revision_ids=revisions,
                    evidence_ids=evidence,
                    report_ids=reports,
                    supersedes_decision_id=supersedes,
                )

                decision_id = self._id_generator.new(EntityIdPrefix.DECISION)
                decision = DecisionRecord(
                    decision_id=decision_id,
                    subject_id=subject_id_n,
                    decision_type=decision_type,
                    title=title_r,
                    rationale=rationale_r,
                    decided_at=decided,
                    recorded_at=recorded_at,
                    decided_by=decider,
                    confirmation_mode=confirmation_mode,
                    primary_instrument_id=primary,
                    thesis_revision_ids=revisions,
                    evidence_ids=evidence,
                    report_ids=reports,
                    supersedes_decision_id=supersedes,
                    position_context_snapshot_id=snapshot_id,
                    schema_version=schema_version(),
                    strategy_code=strategy_code_n,
                    strategy_version=strategy_version_n,
                    scenario=scenario_n,
                    trade_plan_id=plan_id_n,
                    trade_plan_version=trade_plan_version,
                    review_due_at=review_due,
                    external_note_revision_id=note_revision_id_n,
                )
                # Frozen order: business → Research Subject cache → Search → Audit → commit.
                uow.decisions.add(
                    decision,
                    idempotency_key=key,
                    idempotency_payload_sha256=payload_sha,
                )
                if review_materials is not None:
                    entry = JournalEntry(
                        journal_id=self._id_generator.new(EntityIdPrefix.JOURNAL),
                        subject_id=subject_id_n,
                        entry_type=JournalEntryType.NOTE,
                        title="Quick Review material references",
                        body_markdown=review_materials.model_dump_json(),
                        created_at=recorded_at,
                        authored_by="user",
                        confirmed_by=decider,
                        instrument_ids=(primary,) if primary else (),
                        topic_tags=("review_materials_v1",),
                        related_entity_type="decision",
                        related_entity_id=decision_id,
                        supersedes_journal_id=None,
                        schema_version=schema_version(),
                    )
                    uow.journal.add(
                        entry,
                        idempotency_key=review_materials_key(key),
                        idempotency_payload_sha256=compute_journal_idempotency_payload_sha256(
                            subject_id=entry.subject_id,
                            entry_type=entry.entry_type,
                            title=entry.title,
                            body_markdown=entry.body_markdown,
                            authored_by=entry.authored_by,
                            confirmed_by=entry.confirmed_by,
                            instrument_ids=entry.instrument_ids,
                            topic_tags=entry.topic_tags,
                            related_entity_type=entry.related_entity_type,
                            related_entity_id=entry.related_entity_id,
                            supersedes_journal_id=None,
                        ),
                    )
                    uow.search_index.index(ResearchSearchEntityType.JOURNAL, entry.journal_id)
                update_subject_decision_cache(
                    uow,
                    subject_id=subject_id_n,
                    decision_id=decision_id,
                    updated_at=recorded_at,
                )
                uow.search_index.index(ResearchSearchEntityType.DECISION, decision_id)
                linked = list(revisions) + list(evidence) + list(reports)
                if review_materials is not None:
                    linked.append(entry.journal_id)
                if supersedes is not None:
                    linked.append(supersedes)
                if plan_id_n is not None:
                    linked.append(plan_id_n)
                if note_revision_id_n is not None:
                    linked.append(note_revision_id_n)
                uow.audit.append(
                    "phase1c.decision.recorded",
                    audit_summary(
                        action="record",
                        entity_type="decision",
                        entity_id=decision_id,
                        subject_id=subject_id_n,
                        actor=decider,
                        confirmed_by=decider,
                        content_sha256=payload_sha,
                        linked_entity_ids=tuple(linked),
                        idempotency_key=key,
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=DecisionRecordDTO.from_domain(decision),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
