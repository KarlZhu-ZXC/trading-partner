"""Session-bound repositories for source snapshots and evidence impacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from domain.common.errors import DataContractError, InvalidResearchLink
from domain.common.time import require_aware_datetime
from domain.research.evidence_impact import (
    EvidenceImpact,
    ResearchSourceRef,
    ResearchSourceSnapshot,
)
from infrastructure.persistence.orm import (
    AssumptionRow,
    CandidateThesisRevisionRow,
    EvidenceImpactRow,
    ExternalNoteIdentityRow,
    ExternalNoteRevisionRow,
    MonitorRunObservationRow,
    MonitorRunRow,
    MonitorVersionRow,
    ResearchEvidenceRow,
    ResearchSubjectRow,
    SubjectEvidenceLinkRow,
    ThesisRevisionRow,
)
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_to_db,
)


def _source_key(ref: ResearchSourceRef) -> tuple[str, str, str, str]:
    return (ref.kind, ref.source_id, ref.monitor_id or "", ref.rule_code or "")


def _warning_codes(*groups: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for code in group or ():
            if code not in seen:
                result.append(code)
                seen.add(code)
    return tuple(result)


class SqlAlchemyResearchSourceRepository:
    """Resolve only source metadata visible within one existing SQL transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _as_of(as_of: datetime) -> str:
        require_aware_datetime(as_of, field_name="as_of")
        return dt_to_db(as_of)

    @staticmethod
    def _at_or_before(column: Any, as_of_text: str) -> Any:
        """Compare ISO timestamps by instant, including mixed UTC offsets.

        SQLite TEXT ordering is not chronological when offsets differ.  Its
        ``julianday`` parser handles the aware ISO values used by persistence;
        each result is checked again after decoding below for a fail-closed
        boundary.
        """
        return func.julianday(column) <= func.julianday(as_of_text)

    def _evidence(
        self, subject_id: str, ref: ResearchSourceRef, *, as_of: datetime
    ) -> ResearchSourceSnapshot | None:
        if ref.kind != "EVIDENCE":
            return None
        as_of_text = self._as_of(as_of)
        stmt = (
            select(ResearchEvidenceRow, SubjectEvidenceLinkRow)
            .join(
                SubjectEvidenceLinkRow,
                SubjectEvidenceLinkRow.evidence_id == ResearchEvidenceRow.evidence_id,
            )
            .where(
                SubjectEvidenceLinkRow.subject_id == subject_id,
                ResearchEvidenceRow.evidence_id == ref.source_id,
                self._at_or_before(SubjectEvidenceLinkRow.linked_at, as_of_text),
                self._at_or_before(ResearchEvidenceRow.observed_at, as_of_text),
                or_(
                    ResearchEvidenceRow.published_at.is_(None),
                    self._at_or_before(ResearchEvidenceRow.published_at, as_of_text),
                ),
            )
            .limit(1)
        )
        pair = self._session.execute(stmt).first()
        if pair is None:
            return None
        evidence, link = pair
        observed_at = dt_from_db(evidence.observed_at, field_name="observed_at")
        published_at = dt_opt_from_db(evidence.published_at, field_name="published_at")
        linked_at = dt_from_db(link.linked_at, field_name="linked_at")
        if (
            observed_at > as_of
            or linked_at > as_of
            or (published_at is not None and published_at > as_of)
        ):
            return None
        return ResearchSourceSnapshot(
            source=ref,
            title=evidence.title,
            fact_at=published_at or observed_at,
            recorded_at=observed_at,
            origin=evidence.origin,
            warning_codes=(),
            source_name=evidence.source_name,
            source_url=evidence.source_url,
        )

    def _observation(
        self, subject_id: str, ref: ResearchSourceRef, *, as_of: datetime
    ) -> ResearchSourceSnapshot | None:
        if ref.kind != "OBSERVATION":
            return None
        subject = self._session.get(ResearchSubjectRow, subject_id)
        if subject is None or subject.primary_instrument_id is None:
            return None
        as_of_text = self._as_of(as_of)
        stmt = (
            select(ExternalNoteRevisionRow, ExternalNoteIdentityRow)
            .join(
                ExternalNoteIdentityRow,
                ExternalNoteIdentityRow.note_id == ExternalNoteRevisionRow.note_id,
            )
            .where(
                ExternalNoteRevisionRow.note_revision_id == ref.source_id,
                ExternalNoteRevisionRow.coverage == "FULL",
                ExternalNoteIdentityRow.primary_instrument_id == subject.primary_instrument_id,
                self._at_or_before(ExternalNoteRevisionRow.observed_at, as_of_text),
                self._at_or_before(ExternalNoteIdentityRow.created_at, as_of_text),
                or_(
                    ExternalNoteRevisionRow.source_timestamp.is_(None),
                    self._at_or_before(ExternalNoteRevisionRow.source_timestamp, as_of_text),
                ),
            )
            .limit(1)
        )
        pair = self._session.execute(stmt).first()
        if pair is None:
            return None
        revision, identity = pair
        observed_at = dt_from_db(revision.observed_at, field_name="observed_at")
        created_at = dt_from_db(identity.created_at, field_name="created_at")
        fact_at = dt_opt_from_db(revision.source_timestamp, field_name="source_timestamp")
        if observed_at > as_of or created_at > as_of or (fact_at is not None and fact_at > as_of):
            return None
        return ResearchSourceSnapshot(
            source=ref,
            title=revision.title,
            fact_at=fact_at,
            recorded_at=observed_at,
            origin="EXTERNAL_OBSERVATION",
            warning_codes=("SOURCE_ATTRIBUTION_REQUIRES_REVIEW",),
        )

    def _monitor(
        self, subject_id: str, ref: ResearchSourceRef, *, as_of: datetime
    ) -> ResearchSourceSnapshot | None:
        if ref.kind != "MONITOR":
            return None
        assert ref.monitor_id is not None
        assert ref.rule_code is not None
        as_of_text = self._as_of(as_of)
        stmt = (
            select(MonitorRunObservationRow, MonitorRunRow, MonitorVersionRow)
            .join(MonitorRunRow, MonitorRunRow.run_id == MonitorRunObservationRow.run_id)
            .join(
                MonitorVersionRow,
                (MonitorVersionRow.monitor_id == MonitorRunObservationRow.monitor_id)
                & (MonitorVersionRow.version == MonitorRunObservationRow.monitor_version),
            )
            .where(
                MonitorRunObservationRow.run_id == ref.source_id,
                MonitorRunObservationRow.monitor_id == ref.monitor_id,
                MonitorRunObservationRow.rule_code == ref.rule_code,
                MonitorVersionRow.subject_id == subject_id,
                self._at_or_before(MonitorRunRow.completed_at, as_of_text),
                MonitorRunObservationRow.state != "NOT_EVALUATED",
                or_(
                    MonitorRunObservationRow.fact_as_of.is_(None),
                    self._at_or_before(MonitorRunObservationRow.fact_as_of, as_of_text),
                ),
            )
            .limit(1)
        )
        result = self._session.execute(stmt).first()
        if result is None:
            return None
        observation, run, monitor = result
        completed_at = dt_from_db(run.completed_at, field_name="completed_at")
        fact_at = dt_opt_from_db(observation.fact_as_of, field_name="fact_as_of")
        if completed_at > as_of or (fact_at is not None and fact_at > as_of):
            return None
        warning_codes = _warning_codes(observation.warning_codes, observation.error_codes)
        return ResearchSourceSnapshot(
            source=ref,
            title=f"{monitor.name} · {ref.rule_code}",
            fact_at=fact_at,
            recorded_at=completed_at,
            origin="SYSTEM_DERIVED",
            warning_codes=warning_codes,
        )

    def get(
        self, subject_id: str, ref: ResearchSourceRef, *, as_of: datetime
    ) -> ResearchSourceSnapshot | None:
        self._as_of(as_of)
        if ref.kind == "EVIDENCE":
            return self._evidence(subject_id, ref, as_of=as_of)
        if ref.kind == "OBSERVATION":
            return self._observation(subject_id, ref, as_of=as_of)
        return self._monitor(subject_id, ref, as_of=as_of)

    def _list_evidence(
        self, subject_id: str, *, as_of: datetime, bound: int
    ) -> list[ResearchSourceSnapshot]:
        as_of_text = self._as_of(as_of)
        stmt = (
            select(ResearchEvidenceRow, SubjectEvidenceLinkRow)
            .join(
                SubjectEvidenceLinkRow,
                SubjectEvidenceLinkRow.evidence_id == ResearchEvidenceRow.evidence_id,
            )
            .where(
                SubjectEvidenceLinkRow.subject_id == subject_id,
                self._at_or_before(SubjectEvidenceLinkRow.linked_at, as_of_text),
                self._at_or_before(ResearchEvidenceRow.observed_at, as_of_text),
                or_(
                    ResearchEvidenceRow.published_at.is_(None),
                    self._at_or_before(ResearchEvidenceRow.published_at, as_of_text),
                ),
            )
            .order_by(
                func.julianday(ResearchEvidenceRow.observed_at).desc(),
                ResearchEvidenceRow.evidence_id.desc(),
            )
            .limit(bound)
        )
        result: list[ResearchSourceSnapshot] = []
        for evidence, link in self._session.execute(stmt).all():
            observed_at = dt_from_db(evidence.observed_at, field_name="observed_at")
            published_at = dt_opt_from_db(evidence.published_at, field_name="published_at")
            linked_at = dt_from_db(link.linked_at, field_name="linked_at")
            if (
                observed_at > as_of
                or linked_at > as_of
                or (published_at is not None and published_at > as_of)
            ):
                continue
            ref = ResearchSourceRef("EVIDENCE", evidence.evidence_id)
            result.append(
                ResearchSourceSnapshot(
                    source=ref,
                    title=evidence.title,
                    fact_at=published_at or observed_at,
                    recorded_at=observed_at,
                    origin=evidence.origin,
                    source_name=evidence.source_name,
                    source_url=evidence.source_url,
                )
            )
        return result

    def _list_observations(
        self, subject_id: str, *, as_of: datetime, bound: int
    ) -> list[ResearchSourceSnapshot]:
        subject = self._session.get(ResearchSubjectRow, subject_id)
        if subject is None or subject.primary_instrument_id is None:
            return []
        as_of_text = self._as_of(as_of)
        stmt = (
            select(ExternalNoteRevisionRow, ExternalNoteIdentityRow)
            .join(
                ExternalNoteIdentityRow,
                ExternalNoteIdentityRow.note_id == ExternalNoteRevisionRow.note_id,
            )
            .where(
                ExternalNoteIdentityRow.primary_instrument_id == subject.primary_instrument_id,
                ExternalNoteRevisionRow.coverage == "FULL",
                self._at_or_before(ExternalNoteRevisionRow.observed_at, as_of_text),
                self._at_or_before(ExternalNoteIdentityRow.created_at, as_of_text),
                or_(
                    ExternalNoteRevisionRow.source_timestamp.is_(None),
                    self._at_or_before(ExternalNoteRevisionRow.source_timestamp, as_of_text),
                ),
            )
            .order_by(
                func.julianday(ExternalNoteRevisionRow.observed_at).desc(),
                ExternalNoteRevisionRow.note_revision_id.desc(),
            )
            .limit(bound)
        )
        result: list[ResearchSourceSnapshot] = []
        for revision, identity in self._session.execute(stmt).all():
            observed_at = dt_from_db(revision.observed_at, field_name="observed_at")
            created_at = dt_from_db(identity.created_at, field_name="created_at")
            fact_at = dt_opt_from_db(revision.source_timestamp, field_name="source_timestamp")
            if (
                observed_at > as_of
                or created_at > as_of
                or (fact_at is not None and fact_at > as_of)
            ):
                continue
            ref = ResearchSourceRef("OBSERVATION", revision.note_revision_id)
            result.append(
                ResearchSourceSnapshot(
                    source=ref,
                    title=revision.title,
                    fact_at=fact_at,
                    recorded_at=observed_at,
                    origin="EXTERNAL_OBSERVATION",
                    warning_codes=("SOURCE_ATTRIBUTION_REQUIRES_REVIEW",),
                )
            )
        return result

    def _list_monitors(
        self, subject_id: str, *, as_of: datetime, bound: int
    ) -> list[ResearchSourceSnapshot]:
        as_of_text = self._as_of(as_of)
        stmt = (
            select(MonitorRunObservationRow, MonitorRunRow, MonitorVersionRow)
            .join(MonitorRunRow, MonitorRunRow.run_id == MonitorRunObservationRow.run_id)
            .join(
                MonitorVersionRow,
                (MonitorVersionRow.monitor_id == MonitorRunObservationRow.monitor_id)
                & (MonitorVersionRow.version == MonitorRunObservationRow.monitor_version),
            )
            .where(
                MonitorVersionRow.subject_id == subject_id,
                self._at_or_before(MonitorRunRow.completed_at, as_of_text),
                MonitorRunObservationRow.state != "NOT_EVALUATED",
                or_(
                    MonitorRunObservationRow.fact_as_of.is_(None),
                    self._at_or_before(MonitorRunObservationRow.fact_as_of, as_of_text),
                ),
            )
            .order_by(
                func.julianday(MonitorRunRow.completed_at).desc(),
                MonitorRunObservationRow.run_id.desc(),
                MonitorRunObservationRow.rule_code.desc(),
            )
            .limit(bound)
        )
        result: list[ResearchSourceSnapshot] = []
        for observation, run, monitor in self._session.execute(stmt).all():
            completed_at = dt_from_db(run.completed_at, field_name="completed_at")
            fact_at = dt_opt_from_db(observation.fact_as_of, field_name="fact_as_of")
            if completed_at > as_of or (fact_at is not None and fact_at > as_of):
                continue
            ref = ResearchSourceRef(
                "MONITOR",
                observation.run_id,
                observation.monitor_id,
                observation.rule_code,
            )
            result.append(
                ResearchSourceSnapshot(
                    source=ref,
                    title=f"{monitor.name} · {observation.rule_code}",
                    fact_at=fact_at,
                    recorded_at=completed_at,
                    origin="SYSTEM_DERIVED",
                    warning_codes=_warning_codes(
                        observation.warning_codes, observation.error_codes
                    ),
                )
            )
        return result

    def list_for_subject(
        self,
        subject_id: str,
        *,
        as_of: datetime,
        limit: int = 200,
    ) -> tuple[ResearchSourceSnapshot, ...]:
        if type(limit) is not int or limit < 1 or limit > 200:
            raise DataContractError("Research source limit must be between 1 and 200")
        # Each source query is scoped to the subject before applying its bound;
        # the final merge is capped at limit+1 so callers can expose truncation.
        per_source_bound = limit + 1
        values = (
            self._list_evidence(subject_id, as_of=as_of, bound=per_source_bound)
            + self._list_observations(subject_id, as_of=as_of, bound=per_source_bound)
            + self._list_monitors(subject_id, as_of=as_of, bound=per_source_bound)
        )
        values.sort(
            key=lambda item: (item.recorded_at, _source_key(item.source)), reverse=True
        )
        return tuple(values[: limit + 1])


def _to_domain(row: EvidenceImpactRow) -> EvidenceImpact:
    return EvidenceImpact(
        impact_id=row.impact_id,
        version=row.version,
        subject_id=row.subject_id,
        thesis_id=row.thesis_id,
        thesis_revision_id=row.thesis_revision_id,
        assumption_id=row.assumption_id,
        source=ResearchSourceRef(
            row.source_kind,
            row.source_id,
            row.source_monitor_id,
            row.source_rule_code,
        ),
        stance=row.stance,
        rationale=row.rationale,
        state=row.state,
        confirmed_at=dt_from_db(row.confirmed_at, field_name="confirmed_at"),
        confirmed_by=row.confirmed_by,
        candidate_id=row.candidate_id,
    )


def _to_row(value: EvidenceImpact) -> EvidenceImpactRow:
    return EvidenceImpactRow(
        impact_id=value.impact_id,
        version=value.version,
        subject_id=value.subject_id,
        thesis_id=value.thesis_id,
        thesis_revision_id=value.thesis_revision_id,
        assumption_id=value.assumption_id,
        source_kind=value.source.kind,
        source_id=value.source.source_id,
        source_monitor_id=value.source.monitor_id,
        source_rule_code=value.source.rule_code,
        stance=value.stance,
        rationale=value.rationale,
        state=value.state,
        confirmed_at=dt_to_db(value.confirmed_at),
        confirmed_by=value.confirmed_by,
        candidate_id=value.candidate_id,
    )


class SqlAlchemyEvidenceImpactRepository:
    """Append-only version repository using optimistic version checks."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._sources = SqlAlchemyResearchSourceRepository(session)

    def get(self, impact_id: str, version: int | None = None) -> EvidenceImpact | None:
        stmt = select(EvidenceImpactRow).where(EvidenceImpactRow.impact_id == impact_id)
        if version is not None:
            stmt = stmt.where(EvidenceImpactRow.version == version)
        else:
            stmt = stmt.order_by(EvidenceImpactRow.version.desc()).limit(1)
        row = self._session.scalars(stmt).first()
        return None if row is None else _to_domain(row)

    def list_by_subject(
        self,
        subject_id: str,
        *,
        include_history: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[EvidenceImpact, ...]:
        # Application readers request a bounded sentinel page (201 or 501) to
        # distinguish a complete result from truncation; the public default
        # remains 200.
        if type(limit) is not int or limit < 1 or limit > 501:
            raise DataContractError("Evidence impact limit must be between 1 and 501")
        if type(offset) is not int or offset < 0:
            raise DataContractError("Evidence impact offset must be nonnegative")
        if include_history:
            stmt = (
                select(EvidenceImpactRow)
                .where(EvidenceImpactRow.subject_id == subject_id)
                .order_by(
                    func.julianday(EvidenceImpactRow.confirmed_at).desc(),
                    EvidenceImpactRow.impact_id.desc(),
                    EvidenceImpactRow.version.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        else:
            latest = (
                select(
                    EvidenceImpactRow.impact_id,
                    func.max(EvidenceImpactRow.version).label("latest_version"),
                )
                .where(EvidenceImpactRow.subject_id == subject_id)
                .group_by(EvidenceImpactRow.impact_id)
                .subquery()
            )
            stmt = (
                select(EvidenceImpactRow)
                .join(
                    latest,
                    (latest.c.impact_id == EvidenceImpactRow.impact_id)
                    & (latest.c.latest_version == EvidenceImpactRow.version),
                )
                .where(EvidenceImpactRow.subject_id == subject_id)
                .order_by(
                    func.julianday(EvidenceImpactRow.confirmed_at).desc(),
                    EvidenceImpactRow.impact_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def append(self, value: EvidenceImpact, *, expected_version: int | None) -> None:
        latest_version = self._session.scalar(
            select(func.max(EvidenceImpactRow.version)).where(
                EvidenceImpactRow.impact_id == value.impact_id
            )
        )
        if latest_version is None:
            if value.version != 1 or expected_version not in (None, 0):
                raise DataContractError(
                    "new EvidenceImpact must start at version 1 with expected_version 0"
                )
        else:
            latest = int(latest_version)
            if expected_version != latest or value.version != latest + 1:
                raise DataContractError(
                    "EvidenceImpact expected_version does not match latest version",
                    details={
                        "impact_id": value.impact_id,
                        "expected_version": expected_version,
                        "latest_version": latest,
                        "new_version": value.version,
                    },
                )

        candidate = self._session.get(CandidateThesisRevisionRow, value.candidate_id)
        if candidate is None:
            raise InvalidResearchLink(
                "EvidenceImpact candidate does not exist",
                details={"candidate_id": value.candidate_id},
            )
        # Confirmation applies the append-only impact and the candidate status
        # in one UoW transaction.  The lifecycle service therefore presents a
        # PROPOSED candidate while this method runs; a direct confirmed replay
        # is also valid for recovery paths.
        if candidate.kind != "evidence_impact" or candidate.status not in {
            "proposed",
            "confirmed",
        }:
            raise InvalidResearchLink(
                "EvidenceImpact requires a confirmed evidence_impact candidate",
                details={"candidate_id": value.candidate_id, "kind": candidate.kind},
            )
        if candidate.subject_id != value.subject_id or candidate.thesis_id != value.thesis_id:
            raise InvalidResearchLink(
                "EvidenceImpact candidate scope does not match the record",
                details={"candidate_id": value.candidate_id},
            )
        if self._session.scalar(
            select(EvidenceImpactRow.impact_id).where(
                EvidenceImpactRow.candidate_id == value.candidate_id
            )
        ) is not None:
            raise DataContractError("candidate_id may be used by only one EvidenceImpact version")

        # A Subject may have only one currently active interpretation for the
        # same exact source/assumption pair.  The latest version of a retracted
        # impact is intentionally excluded, so that pair can be researched
        # again under a new impact identity later.
        latest_for_pair = (
            select(
                EvidenceImpactRow.impact_id,
                func.max(EvidenceImpactRow.version).label("latest_version"),
            )
            .where(EvidenceImpactRow.subject_id == value.subject_id)
            .group_by(EvidenceImpactRow.impact_id)
            .subquery()
        )
        active_pair = self._session.scalar(
            select(EvidenceImpactRow.impact_id)
            .join(
                latest_for_pair,
                (latest_for_pair.c.impact_id == EvidenceImpactRow.impact_id)
                & (latest_for_pair.c.latest_version == EvidenceImpactRow.version),
            )
            .where(
                EvidenceImpactRow.subject_id == value.subject_id,
                EvidenceImpactRow.assumption_id == value.assumption_id,
                EvidenceImpactRow.source_kind == value.source.kind,
                EvidenceImpactRow.source_id == value.source.source_id,
                EvidenceImpactRow.source_monitor_id == value.source.monitor_id,
                EvidenceImpactRow.source_rule_code == value.source.rule_code,
                EvidenceImpactRow.state == "ACTIVE",
            )
        )
        if active_pair is not None and active_pair != value.impact_id:
            raise DataContractError(
                "an active EvidenceImpact already exists for this source and assumption",
                details={
                    "subject_id": value.subject_id,
                    "assumption_id": value.assumption_id,
                    "source_id": value.source.source_id,
                    "impact_id": active_pair,
                },
            )

        revision = self._session.get(ThesisRevisionRow, value.thesis_revision_id)
        assumption = self._session.get(AssumptionRow, value.assumption_id)
        if (
            revision is None
            or revision.thesis_id != value.thesis_id
            or revision.subject_id != value.subject_id
            or assumption is None
            or assumption.thesis_id != value.thesis_id
            or assumption.subject_id != value.subject_id
            or assumption.revision_no != revision.revision_no
        ):
            raise InvalidResearchLink(
                "EvidenceImpact thesis, revision, and assumption must share the Subject",
                details={"impact_id": value.impact_id},
            )
        if value.state == "ACTIVE" and self._sources.get(
            value.subject_id, value.source, as_of=value.confirmed_at
        ) is None:
            raise InvalidResearchLink(
                "EvidenceImpact source is unavailable or outside the confirmation scope",
                details={"impact_id": value.impact_id, "source_id": value.source.source_id},
            )
        self._session.add(_to_row(value))
        self._session.flush()


__all__ = [
    "SqlAlchemyEvidenceImpactRepository",
    "SqlAlchemyResearchSourceRepository",
]
