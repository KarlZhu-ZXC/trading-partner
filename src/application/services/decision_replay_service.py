"""Historical, read-only replay of one confirmed Decision.

The current research aggregate is intentionally mutable in a few places (most
notably assumptions and open-question state).  This service therefore builds a
small fail-closed projection from the immutable references recorded by the
Decision and the source repository's historical cutoff.  It never consults the
current Thesis metadata and it never returns external-note bodies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Literal, cast

from application.dto.decision_replay import (
    CoverageStatus,
    DecisionReplayAssumptionDTO,
    DecisionReplayDTO,
    DecisionReplayExcludedReferenceDTO,
    DecisionReplayImpactDTO,
    DecisionReplayQuestionDTO,
    DecisionReplayRevisionDTO,
    DecisionReplaySourceDTO,
    ReplayWarningCode,
)
from application.dto.research_links import ResearchSourcePayload
from application.ports.clock import Clock
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services._research_support import UowFactory
from domain.common.enums import AssumptionStatus
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.research.evidence_impact import (
    EvidenceImpact,
    ResearchSourceRef,
    ResearchSourceSnapshot,
)
from domain.research.models import Assumption, DecisionRecord, ThesisRevision

_SOURCE_WARNING: ReplayWarningCode = "SOURCE_NOT_AVAILABLE_AT_CUTOFF"
_MAX_ROWS = 200
_MAX_IMPACT_HISTORY = 500


class DecisionReplayService:
    """Project the facts that were available when a Decision was made."""

    def __init__(self, uow_factory: UowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def get(self, subject_id: str, decision_id: str) -> DecisionReplayDTO:
        """Return a bounded historical projection for ``decision_id``.

        ``Decision.decided_at`` is the strict historical event cutoff.  The
        Decision's own title/rationale and ``recorded_at`` remain visible as
        record metadata, while all referenced research objects are evaluated
        against that cutoff.
        """

        now = require_aware_datetime(self._clock.now(), field_name="now")
        warnings: list[ReplayWarningCode] = []
        excluded: list[DecisionReplayExcludedReferenceDTO] = []
        excluded_keys: set[tuple[str, str]] = set()

        def warn(code: ReplayWarningCode) -> None:
            if code not in warnings:
                warnings.append(code)

        def exclude(reference_id: str, reason: str) -> None:
            key = (reference_id, reason)
            if key in excluded_keys:
                return
            excluded_keys.add(key)
            if len(excluded) < _MAX_ROWS:
                excluded.append(
                    DecisionReplayExcludedReferenceDTO(
                        reference_id=reference_id,
                        reason=reason,
                    )
                )

        with self._uow_factory() as uow:
            # Keep the subject lookup explicit: a Decision ID is not a scope
            # selector and must never be used to disclose another Subject.
            subject = uow.subjects.get(subject_id)
            if subject is None or getattr(subject, "subject_id", subject_id) != subject_id:
                raise DataContractError("Research Subject not found")
            decision = uow.decisions.get(decision_id)
            if decision.subject_id != subject_id:
                raise DataContractError("Decision does not belong to this Research Subject")
            if decision.decided_at > now or decision.recorded_at > now:
                raise DataContractError("Decision replay cannot use a future Decision")
            cutoff = require_aware_datetime(decision.decided_at, field_name="cutoff")

            revisions, visible_revision_ids, assumption_coverage = self._revisions(
                uow,
                decision,
                subject_id=subject_id,
                cutoff=cutoff,
                warn=warn,
                exclude=exclude,
            )

            impact_rows, impact_coverage = self._impacts(
                uow,
                subject_id=subject_id,
                cutoff=cutoff,
                visible_revision_ids=visible_revision_ids,
                revisions=revisions,
                warn=warn,
                exclude=exclude,
            )

            requested_sources = self._requested_source_refs(
                decision,
                impact_rows,
                warn=warn,
                exclude=exclude,
            )
            sources, source_by_ref, source_coverage = self._sources(
                uow,
                subject_id=subject_id,
                cutoff=cutoff,
                requested_sources=requested_sources,
                warn=warn,
                exclude=exclude,
            )

            impacts = [
                self._impact_dto(row, source_by_ref.get(row.source))
                for row in impact_rows[:_MAX_ROWS]
            ]

            questions, question_coverage = self._questions(
                uow,
                subject_id=subject_id,
                cutoff=cutoff,
                warn=warn,
                exclude=exclude,
            )

        coverage: dict[str, CoverageStatus] = {
            "revisions": (
                "PARTIAL"
                if any(item.reason.startswith("REVISION_") for item in excluded)
                else "COMPLETE"
            ),
            "assumptions": assumption_coverage,
            "sources": (
                "PARTIAL"
                if source_coverage == "COMPLETE"
                and any(item.reason.startswith("SOURCE_") for item in excluded)
                else source_coverage
            ),
            "impacts": impact_coverage,
            "questions": question_coverage,
        }
        # The revision coverage is based on exact Decision references; source,
        # impact, and question helpers carry their independent failure state.
        # Avoid exposing internal helper labels in the public exclusion reason.
        return DecisionReplayDTO(
            subject_id=subject_id,
            decision_id=decision.decision_id,
            cutoff=cutoff,
            recorded_at=decision.recorded_at,
            title=decision.title,
            rationale=decision.rationale,
            generated_at=now,
            revisions=revisions,
            sources=sources,
            impacts=impacts,
            questions=questions,
            coverage=coverage,
            warnings=warnings,
            excluded_references=excluded,
        )

    @staticmethod
    def _revisions(
        uow: ResearchUnitOfWork,
        decision: DecisionRecord,
        *,
        subject_id: str,
        cutoff: datetime,
        warn: Callable[[ReplayWarningCode], None],
        exclude: Callable[[str, str], None],
    ) -> tuple[list[DecisionReplayRevisionDTO], set[str], CoverageStatus]:
        """Load only the exact immutable revision IDs stored on the Decision."""

        add_warning = warn
        add_excluded = exclude
        revisions: list[DecisionReplayRevisionDTO] = []
        visible_revision_ids: set[str] = set()
        assumptions_partial = False
        assumptions_unavailable = False
        assumptions_emitted = 0
        revision_ids = tuple(decision.thesis_revision_ids)
        if len(revision_ids) > _MAX_ROWS:
            assumptions_partial = True
            for revision_id in revision_ids[_MAX_ROWS:]:
                add_excluded(revision_id, "REVISION_OUTPUT_CAP_REACHED")
            revision_ids = revision_ids[:_MAX_ROWS]

        for revision_id in revision_ids:
            try:
                revision = uow.revisions.get(revision_id)
            except Exception:  # noqa: BLE001 - one missing exact ref must not hide others
                add_warning("REVISION_UNAVAILABLE")
                add_excluded(revision_id, "REVISION_UNAVAILABLE")
                continue
            if (
                revision.revision_id != revision_id
                or revision.subject_id != subject_id
                or not isinstance(revision.confirmed_at, datetime)
            ):
                add_warning("REVISION_SCOPE_INVALID")
                add_excluded(revision_id, "REVISION_SCOPE_INVALID")
                continue
            try:
                confirmed_at = require_aware_datetime(
                    revision.confirmed_at,
                    field_name="revision.confirmed_at",
                )
            except Exception:  # noqa: BLE001 - malformed storage is unavailable
                add_warning("REVISION_UNAVAILABLE")
                add_excluded(revision_id, "REVISION_UNAVAILABLE")
                continue
            if confirmed_at > cutoff:
                add_warning("REVISION_AFTER_CUTOFF")
                add_excluded(revision_id, "REVISION_AFTER_CUTOFF")
                continue

            assumptions: list[DecisionReplayAssumptionDTO] = []
            try:
                raw_assumptions = uow.assumptions.list_by_revision(
                    revision.thesis_id,
                    revision.revision_no,
                )
            except Exception:  # noqa: BLE001 - per-revision source failure
                add_warning("ASSUMPTIONS_UNAVAILABLE")
                assumptions_unavailable = True
                assumptions_partial = True
                raw_assumptions = ()
            for assumption in sorted(raw_assumptions, key=lambda item: item.assumption_id):
                if (
                    assumption.subject_id != subject_id
                    or assumption.thesis_id != revision.thesis_id
                    or assumption.revision_no != revision.revision_no
                ):
                    add_warning("ASSUMPTION_SCOPE_INVALID")
                    add_excluded(assumption.assumption_id, "ASSUMPTION_SCOPE_INVALID")
                    assumptions_partial = True
                    continue
                try:
                    assumption_confirmed_at = require_aware_datetime(
                        assumption.confirmed_at,
                        field_name="assumption.confirmed_at",
                    )
                except Exception:  # noqa: BLE001 - malformed storage is unavailable
                    add_warning("ASSUMPTION_UNAVAILABLE")
                    add_excluded(assumption.assumption_id, "ASSUMPTION_UNAVAILABLE")
                    assumptions_partial = True
                    continue
                if assumption_confirmed_at > cutoff:
                    add_warning("ASSUMPTION_AFTER_CUTOFF")
                    add_excluded(assumption.assumption_id, "ASSUMPTION_AFTER_CUTOFF")
                    assumptions_partial = True
                    continue
                if assumptions_emitted >= _MAX_ROWS:
                    add_warning("ASSUMPTION_OUTPUT_CAP_REACHED")
                    add_excluded(assumption.assumption_id, "ASSUMPTION_OUTPUT_CAP_REACHED")
                    assumptions_partial = True
                    continue
                rendered_assumption = _assumption_dto(assumption, cutoff, add_warning)
                if rendered_assumption.status is None:
                    assumptions_partial = True
                assumptions.append(rendered_assumption)
                assumptions_emitted += 1

            revisions.append(_revision_dto(revision, assumptions))
            visible_revision_ids.add(revision.revision_id)

        revisions.sort(key=lambda item: (item.revision_no, item.revision_id))
        coverage: CoverageStatus = (
            "UNAVAILABLE"
            if assumptions_unavailable
            else "PARTIAL"
            if assumptions_partial
            else "COMPLETE"
        )
        return revisions, visible_revision_ids, coverage

    @staticmethod
    def _impacts(
        uow: ResearchUnitOfWork,
        *,
        subject_id: str,
        cutoff: datetime,
        visible_revision_ids: set[str],
        revisions: list[DecisionReplayRevisionDTO],
        warn: Callable[[ReplayWarningCode], None],
        exclude: Callable[[str, str], None],
    ) -> tuple[list[EvidenceImpact], CoverageStatus]:
        add_warning = warn
        add_excluded = exclude
        try:
            raw = uow.evidence_impacts.list_by_subject(
                subject_id,
                include_history=True,
                limit=_MAX_IMPACT_HISTORY + 1,
            )
        except Exception:  # noqa: BLE001 - impacts are an independent projection
            add_warning("IMPACTS_UNAVAILABLE")
            return [], "UNAVAILABLE"

        partial = len(raw) > _MAX_IMPACT_HISTORY
        rows = raw[:_MAX_IMPACT_HISTORY]
        latest: dict[str, EvidenceImpact] = {}
        visible_target_refs = {
            (revision.revision_id, revision.thesis_id, assumption.assumption_id)
            for revision in revisions
            for assumption in revision.assumptions
        }
        for row in rows:
            if row.subject_id != subject_id:
                add_warning("IMPACT_SCOPE_INVALID")
                add_excluded(row.impact_id, "IMPACT_SCOPE_INVALID")
                partial = True
                continue
            try:
                confirmed_at = require_aware_datetime(
                    row.confirmed_at,
                    field_name="impact.confirmed_at",
                )
            except Exception:  # noqa: BLE001
                add_warning("IMPACT_UNAVAILABLE")
                add_excluded(row.impact_id, "IMPACT_UNAVAILABLE")
                partial = True
                continue
            if confirmed_at > cutoff:
                # A later version must never hide a version that was visible at
                # the cutoff; simply omit this future version from the vintage.
                continue
            prior = latest.get(row.impact_id)
            if prior is None or row.version > prior.version:
                latest[row.impact_id] = row

        visible: list[EvidenceImpact] = []
        for row in latest.values():
            if row.state != "ACTIVE":
                # A RETRACTED version visible at cutoff is intentionally not
                # projected.  An earlier ACTIVE version wins when a later
                # retraction happened after the cutoff.
                continue
            if row.thesis_revision_id not in visible_revision_ids:
                add_warning("IMPACT_REVISION_UNAVAILABLE")
                add_excluded(row.impact_id, "IMPACT_REVISION_UNAVAILABLE")
                partial = True
                continue
            target_ref = (row.thesis_revision_id, row.thesis_id, row.assumption_id)
            if target_ref not in visible_target_refs:
                add_warning("IMPACT_ASSUMPTION_UNAVAILABLE")
                add_excluded(row.impact_id, "IMPACT_ASSUMPTION_UNAVAILABLE")
                partial = True
                continue
            visible.append(row)

        visible.sort(
            key=lambda item: (item.confirmed_at, item.impact_id, item.version),
            reverse=True,
        )
        if len(visible) > _MAX_ROWS:
            partial = True
            for row in visible[_MAX_ROWS:]:
                add_excluded(row.impact_id, "IMPACT_OUTPUT_CAP_REACHED")
            visible = visible[:_MAX_ROWS]
        if partial:
            add_warning("IMPACT_HISTORY_PARTIAL")
        return visible, "PARTIAL" if partial else "COMPLETE"

    @staticmethod
    def _requested_source_refs(
        decision: DecisionRecord,
        impacts: Iterable[EvidenceImpact],
        *,
        warn: Callable[[ReplayWarningCode], None],
        exclude: Callable[[str, str], None],
    ) -> list[ResearchSourceRef]:
        add_warning = warn
        add_excluded = exclude
        refs: list[ResearchSourceRef] = []
        seen: set[ResearchSourceRef] = set()

        def add(ref: ResearchSourceRef, reference_id: str) -> None:
            if ref in seen:
                return
            if len(refs) >= _MAX_ROWS:
                add_warning("SOURCE_OUTPUT_CAP_REACHED")
                add_excluded(reference_id, "SOURCE_OUTPUT_CAP_REACHED")
                return
            seen.add(ref)
            refs.append(ref)

        for evidence_id in decision.evidence_ids:
            try:
                add(ResearchSourceRef("EVIDENCE", evidence_id), evidence_id)
            except Exception:  # noqa: BLE001 - malformed historical ID is opaque
                add_warning("SOURCE_REFERENCE_INVALID")
                add_excluded(evidence_id, "SOURCE_REFERENCE_INVALID")
        if decision.external_note_revision_id is not None:
            try:
                add(
                    ResearchSourceRef("OBSERVATION", decision.external_note_revision_id),
                    decision.external_note_revision_id,
                )
            except Exception:  # noqa: BLE001
                add_warning("SOURCE_REFERENCE_INVALID")
                add_excluded(decision.external_note_revision_id, "SOURCE_REFERENCE_INVALID")
        for impact in impacts:
            add(impact.source, impact.source.source_id)
        return refs

    @staticmethod
    def _sources(
        uow: ResearchUnitOfWork,
        *,
        subject_id: str,
        cutoff: datetime,
        requested_sources: list[ResearchSourceRef],
        warn: Callable[[ReplayWarningCode], None],
        exclude: Callable[[str, str], None],
    ) -> tuple[
        list[DecisionReplaySourceDTO],
        dict[ResearchSourceRef, DecisionReplaySourceDTO],
        CoverageStatus,
    ]:
        add_warning = warn
        add_excluded = exclude
        list_failed = False
        list_partial = False
        requested_set = set(requested_sources)
        source_by_ref: dict[ResearchSourceRef, ResearchSourceSnapshot] = {}
        try:
            raw_sources = uow.research_sources.list_for_subject(
                subject_id,
                as_of=cutoff,
                limit=_MAX_ROWS,
            )
            list_partial = len(raw_sources) > _MAX_ROWS
            for listed_snapshot in raw_sources[:_MAX_ROWS]:
                # Current Instrument-based observation scope is not historical
                # proof.  Keep observations only when the Decision or a
                # cutoff-visible impact names that exact revision.  Evidence
                # and Monitor rows are already explicitly Subject-scoped by
                # their repository query and are therefore all useful here.
                if (
                    listed_snapshot.source.kind == "OBSERVATION"
                    and listed_snapshot.source not in requested_set
                ):
                    list_partial = True
                    add_warning("OBSERVATION_SCOPE_UNAVAILABLE")
                    continue
                if not _snapshot_visible(listed_snapshot, cutoff):
                    add_warning("SOURCE_AFTER_CUTOFF")
                    list_partial = True
                    continue
                source_by_ref[listed_snapshot.source] = listed_snapshot
        except Exception:  # noqa: BLE001 - exact fallback reads can still work
            list_failed = True
            add_warning("SOURCES_UNAVAILABLE")

        # Exact references can fall outside the bounded list page, so perform
        # an independent lookup for each one.  A source repository must not be
        # able to return metadata for a different reference accidentally.
        for ref in requested_sources:
            candidate_snapshot: ResearchSourceSnapshot | None = source_by_ref.get(ref)
            if candidate_snapshot is None:
                try:
                    candidate_snapshot = uow.research_sources.get(
                        subject_id,
                        ref,
                        as_of=cutoff,
                    )
                except Exception:  # noqa: BLE001 - each source fails independently
                    candidate_snapshot = None
                    add_warning(_SOURCE_WARNING)
                if candidate_snapshot is not None and candidate_snapshot.source != ref:
                    candidate_snapshot = None
                    add_warning("SOURCE_REFERENCE_MISMATCH")
                if candidate_snapshot is not None and not _snapshot_visible(
                    candidate_snapshot,
                    cutoff,
                ):
                    add_warning("SOURCE_AFTER_CUTOFF")
                    candidate_snapshot = None
            if candidate_snapshot is None:
                add_warning(_SOURCE_WARNING)
                add_excluded(ref.source_id, "SOURCE_NOT_AVAILABLE_AT_CUTOFF")
                continue
            source_by_ref[ref] = candidate_snapshot

        # Render every historically scoped EVIDENCE/MONITOR plus exact
        # observation references, in stable newest-first order.
        ordered_snapshots = sorted(
            source_by_ref.values(),
            key=lambda item: (item.recorded_at, _source_key(_source_payload(item.source))),
            reverse=True,
        )
        if len(ordered_snapshots) > _MAX_ROWS:
            list_partial = True
            add_warning("SOURCE_OUTPUT_CAP_REACHED")
            for overflow_snapshot in ordered_snapshots[_MAX_ROWS:]:
                add_excluded(
                    overflow_snapshot.source.source_id,
                    "SOURCE_OUTPUT_CAP_REACHED",
                )
            ordered_snapshots = ordered_snapshots[:_MAX_ROWS]
        rendered = [_source_dto(snapshot) for snapshot in ordered_snapshots]
        rendered_by_ref = {
            snapshot.source: _source_dto(snapshot) for snapshot in ordered_snapshots
        }
        # ``list_failed`` is intentionally visible even when exact fallbacks
        # succeeded: the caller must not mistake a degraded source listing for
        # a complete historical source universe.
        rendered.sort(
            key=lambda item: (item.recorded_at, _source_key(item.source)),
            reverse=True,
        )
        status: CoverageStatus = (
            "PARTIAL"
            if list_failed or list_partial or len(rendered) < len(requested_sources)
            else "COMPLETE"
        )
        if list_failed:
            status = "UNAVAILABLE"
        return rendered[:_MAX_ROWS], rendered_by_ref, status

    @staticmethod
    def _questions(
        uow: ResearchUnitOfWork,
        *,
        subject_id: str,
        cutoff: datetime,
        warn: Callable[[ReplayWarningCode], None],
        exclude: Callable[[str, str], None],
    ) -> tuple[list[DecisionReplayQuestionDTO], CoverageStatus]:
        add_warning = warn
        add_excluded = exclude
        try:
            raw_questions = uow.questions.list_by_subject(subject_id)
        except Exception:  # noqa: BLE001 - question history is independent
            add_warning("QUESTIONS_UNAVAILABLE")
            return [], "UNAVAILABLE"

        # The current OpenQuestion row is enough to prove existence, but its
        # mutable answer/progress/status cannot be reconstructed at a Decision
        # cutoff.  That limitation makes this dimension partial even when the
        # question list is empty and the read itself succeeds.
        questions: list[DecisionReplayQuestionDTO] = []
        for question in sorted(raw_questions, key=lambda item: (item.asked_at, item.question_id)):
            if question.subject_id != subject_id:
                add_warning("QUESTION_SCOPE_INVALID")
                add_excluded(question.question_id, "QUESTION_SCOPE_INVALID")
                continue
            try:
                asked_at = require_aware_datetime(question.asked_at, field_name="question.asked_at")
            except Exception:  # noqa: BLE001
                add_warning("QUESTIONS_UNAVAILABLE")
                add_excluded(question.question_id, "QUESTION_UNAVAILABLE")
                continue
            if asked_at > cutoff:
                add_warning("QUESTION_AFTER_CUTOFF")
                add_excluded(question.question_id, "QUESTION_AFTER_CUTOFF")
                continue
            questions.append(
                DecisionReplayQuestionDTO(
                    question_id=question.question_id,
                    text=question.text,
                    asked_at=asked_at,
                )
            )
        if len(questions) > _MAX_ROWS:
            add_warning("QUESTION_OUTPUT_CAP_REACHED")
            for rendered_question in questions[_MAX_ROWS:]:
                add_excluded(rendered_question.question_id, "QUESTION_OUTPUT_CAP_REACHED")
            questions = questions[:_MAX_ROWS]
        add_warning("QUESTION_STATE_HISTORY_UNAVAILABLE")
        return questions, "PARTIAL"

    @staticmethod
    def _impact_dto(
        impact: EvidenceImpact,
        source: DecisionReplaySourceDTO | None,
    ) -> DecisionReplayImpactDTO:
        source_payload = _source_payload(impact.source)
        return DecisionReplayImpactDTO(
            impact_id=impact.impact_id,
            version=impact.version,
            subject_id=impact.subject_id,
            thesis_id=impact.thesis_id,
            thesis_revision_id=impact.thesis_revision_id,
            assumption_id=impact.assumption_id,
            source=source_payload,
            stance=cast(
                Literal["supports", "contradicts", "uncertain"],
                impact.stance,
            ),
            rationale=impact.rationale,
            state="ACTIVE",
            confirmed_at=impact.confirmed_at,
            confirmed_by=impact.confirmed_by,
            candidate_id=impact.candidate_id,
            source_available=source is not None,
            source_snapshot=source,
        )


def _revision_dto(
    revision: ThesisRevision,
    assumptions: list[DecisionReplayAssumptionDTO],
) -> DecisionReplayRevisionDTO:
    return DecisionReplayRevisionDTO(
        revision_id=revision.revision_id,
        thesis_id=revision.thesis_id,
        subject_id=revision.subject_id,
        revision_no=revision.revision_no,
        supersedes_revision_no=revision.supersedes_revision_no,
        statement=revision.statement,
        rationale=revision.rationale,
        confidence_band=revision.confidence_band,
        rating=revision.rating,
        confirmation_mode=revision.confirmation_mode,
        proposed_by=revision.proposed_by,
        confirmed_by=revision.confirmed_by,
        proposed_at=revision.proposed_at,
        confirmed_at=revision.confirmed_at,
        observation_window_start=revision.observation_window_start,
        observation_window_end=revision.observation_window_end,
        invalidation_check_note=revision.invalidation_check_note,
        schema_version=revision.schema_version,
        assumptions=assumptions,
    )


def _assumption_dto(
    assumption: Assumption,
    cutoff: datetime,
    warn: Callable[[ReplayWarningCode], None],
) -> DecisionReplayAssumptionDTO:
    add_warning = warn
    status: AssumptionStatus | None = assumption.status
    retired_at = assumption.retired_at
    retired_reason = assumption.retired_reason
    if assumption.status is AssumptionStatus.RETIRED:
        if retired_at is not None and retired_at <= cutoff:
            pass
        else:
            # The append-in-place repository does not retain the prior status.
            # Do not leak the later retirement event or fabricate one.
            status = None
            retired_at = None
            retired_reason = None
            add_warning("ASSUMPTION_STATUS_UNAVAILABLE")
    return DecisionReplayAssumptionDTO(
        assumption_id=assumption.assumption_id,
        thesis_id=assumption.thesis_id,
        subject_id=assumption.subject_id,
        revision_no=assumption.revision_no,
        statement=assumption.statement,
        basis=assumption.basis,
        falsifiability=assumption.falsifiability,
        status=status,
        proposed_at=assumption.proposed_at,
        confirmed_at=assumption.confirmed_at,
        proposed_by=assumption.proposed_by,
        confirmed_by=assumption.confirmed_by,
        retired_at=retired_at,
        retired_reason=retired_reason,
    )


def _source_dto(snapshot: ResearchSourceSnapshot) -> DecisionReplaySourceDTO:
    return DecisionReplaySourceDTO(
        source=_source_payload(snapshot.source),
        title=snapshot.title,
        fact_at=snapshot.fact_at,
        recorded_at=snapshot.recorded_at,
        origin=snapshot.origin,
        warning_codes=list(snapshot.warning_codes),
        source_name=getattr(snapshot, "source_name", None),
        source_url=getattr(snapshot, "source_url", None),
    )


def _source_payload(ref: ResearchSourceRef) -> ResearchSourcePayload:
    # Keeping this conversion in one place means no raw provider payload can
    # cross the application boundary, and source IDs remain opaque in DTOs.
    return ResearchSourcePayload(
        kind=cast(Literal["EVIDENCE", "OBSERVATION", "MONITOR"], ref.kind),
        source_id=ref.source_id,
        monitor_id=ref.monitor_id,
        rule_code=ref.rule_code,
    )


def _snapshot_visible(snapshot: ResearchSourceSnapshot, cutoff: datetime) -> bool:
    try:
        recorded_at = require_aware_datetime(snapshot.recorded_at, field_name="source.recorded_at")
        if recorded_at > cutoff:
            return False
        if snapshot.fact_at is not None:
            fact_at = require_aware_datetime(snapshot.fact_at, field_name="source.fact_at")
            if fact_at > cutoff:
                return False
    except Exception:  # noqa: BLE001 - malformed source is unavailable
        return False
    return True


def _source_key(ref: ResearchSourcePayload) -> tuple[str, str, str, str]:
    return (ref.kind, ref.source_id, ref.monitor_id or "", ref.rule_code or "")


__all__ = ["DecisionReplayService"]
