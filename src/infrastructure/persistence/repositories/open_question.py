"""SQLAlchemy OpenQuestion repository (session-bound)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.common.enums import OpenQuestionStatus
from domain.common.errors import DataContractError, OpenQuestionNotFound
from domain.common.time import ensure_utc, require_aware_datetime
from domain.research.evidence_impact import ResearchSourceRef
from domain.research.models import OpenQuestion
from infrastructure.persistence.orm import OpenQuestionRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_to_db,
)

_ANSWERABLE = frozenset({OpenQuestionStatus.OPEN})
_CLOSABLE = frozenset({OpenQuestionStatus.OPEN})
_STALEABLE = frozenset({OpenQuestionStatus.OPEN})


def _due_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    # New due dates are stored in a canonical UTC representation so SQLite
    # text bounds remain deterministic; aware datetime equality still retains
    # the caller's instant semantics on read.
    return ensure_utc(value).isoformat(timespec="microseconds")


def _source_ref_to_json(value: ResearchSourceRef) -> dict[str, str]:
    result: dict[str, str] = {"kind": value.kind, "source_id": value.source_id}
    if value.monitor_id is not None:
        result["monitor_id"] = value.monitor_id
    if value.rule_code is not None:
        result["rule_code"] = value.rule_code
    return result


def _source_refs_to_db(values: tuple[ResearchSourceRef, ...]) -> str:
    return json.dumps(
        [_source_ref_to_json(value) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _source_refs_from_db(value: str | None) -> tuple[ResearchSourceRef, ...]:
    if value is None or value == "":
        return ()
    try:
        parsed: Any = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise DataContractError("OpenQuestion source_refs_json is invalid") from exc
    if not isinstance(parsed, list):
        raise DataContractError("OpenQuestion source_refs_json must be a JSON array")
    refs: list[ResearchSourceRef] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise DataContractError("OpenQuestion source_refs_json contains an invalid item")
        kind = item.get("kind")
        source_id = item.get("source_id")
        monitor_id = item.get("monitor_id")
        rule_code = item.get("rule_code")
        if not isinstance(kind, str) or not isinstance(source_id, str):
            raise DataContractError("OpenQuestion source_refs_json contains an invalid reference")
        if monitor_id is not None and not isinstance(monitor_id, str):
            raise DataContractError("OpenQuestion source_refs_json contains an invalid monitor_id")
        if rule_code is not None and not isinstance(rule_code, str):
            raise DataContractError("OpenQuestion source_refs_json contains an invalid rule_code")
        refs.append(
            ResearchSourceRef(
                kind=kind,
                source_id=source_id,
                monitor_id=monitor_id,
                rule_code=rule_code,
            )
        )
    return tuple(refs)


def _to_domain(row: OpenQuestionRow) -> OpenQuestion:
    return OpenQuestion(
        question_id=row.question_id,
        subject_id=row.subject_id,
        text=row.text,
        status=OpenQuestionStatus(row.status),
        asked_at=dt_from_db(row.asked_at, field_name="asked_at"),
        answered_at=dt_opt_from_db(row.answered_at, field_name="answered_at"),
        answer_summary=row.answer_summary,
        closed_without_answer_reason=row.closed_without_answer_reason,
        proposed_by=row.proposed_by,
        version=row.version,
        evidence_requirements=tuple(row.evidence_requirements_json),
        due_at=dt_opt_from_db(row.due_at, field_name="due_at"),
        progress_note=row.progress_note,
        source_refs=_source_refs_from_db(row.source_refs_json),
        decision_ids=tuple(row.decision_ids_json),
    )


def _apply_domain(row: OpenQuestionRow, question: OpenQuestion) -> None:
    """Write a fully validated domain shape onto the ORM row."""
    row.status = question.status.value
    row.answered_at = None if question.answered_at is None else dt_to_db(question.answered_at)
    row.answer_summary = question.answer_summary
    row.closed_without_answer_reason = question.closed_without_answer_reason
    row.version = question.version
    row.evidence_requirements_json = question.evidence_requirements
    row.due_at = _due_to_db(question.due_at)
    row.progress_note = question.progress_note
    row.source_refs_json = _source_refs_to_db(question.source_refs)
    row.decision_ids_json = question.decision_ids


def _to_row(question: OpenQuestion) -> OpenQuestionRow:
    return OpenQuestionRow(
        question_id=question.question_id,
        subject_id=question.subject_id,
        text=question.text,
        status=question.status.value,
        asked_at=dt_to_db(question.asked_at),
        answered_at=None if question.answered_at is None else dt_to_db(question.answered_at),
        answer_summary=question.answer_summary,
        closed_without_answer_reason=question.closed_without_answer_reason,
        proposed_by=question.proposed_by,
        version=question.version,
        evidence_requirements_json=question.evidence_requirements,
        due_at=_due_to_db(question.due_at),
        progress_note=question.progress_note,
        source_refs_json=_source_refs_to_db(question.source_refs),
        decision_ids_json=question.decision_ids,
    )


class SqlAlchemyOpenQuestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_subject(self, subject_id: str) -> tuple[OpenQuestion, ...]:
        stmt = (
            select(OpenQuestionRow)
            .where(OpenQuestionRow.subject_id == subject_id)
            .order_by(
                func.julianday(OpenQuestionRow.asked_at).asc(),
                OpenQuestionRow.question_id.asc(),
            )
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def get(self, question_id: str) -> OpenQuestion:
        row = self._session.get(OpenQuestionRow, question_id)
        if row is None:
            raise OpenQuestionNotFound(
                f"OpenQuestion not found: {question_id}",
                details={"question_id": question_id},
            )
        return _to_domain(row)

    def add(self, question: OpenQuestion) -> None:
        self._session.add(_to_row(question))
        self._session.flush()

    def _locked(self, question_id: str, expected_version: int | None) -> OpenQuestionRow:
        row = self._session.get(OpenQuestionRow, question_id, with_for_update=True)
        if row is None:
            raise OpenQuestionNotFound(
                f"OpenQuestion not found: {question_id}",
                details={"question_id": question_id},
            )
        if expected_version is not None and row.version != expected_version:
            raise DataContractError(
                "OpenQuestion expected_version does not match current version",
                details={
                    "question_id": question_id,
                    "expected_version": expected_version,
                    "actual_version": row.version,
                },
            )
        return row

    @staticmethod
    def _next_version(current: OpenQuestion) -> int:
        return current.version + 1

    def answer(
        self,
        question_id: str,
        *,
        answered_at: datetime,
        answer_summary: str,
        expected_version: int | None = None,
        source_refs: tuple[ResearchSourceRef, ...] | None = None,
    ) -> None:
        row = self._locked(question_id, expected_version)
        current = OpenQuestionStatus(row.status)
        if current not in _ANSWERABLE:
            raise DataContractError(
                "OpenQuestion.answer only allowed from OPEN",
                details={
                    "question_id": question_id,
                    "status": current.value,
                    "allowed_from": sorted(s.value for s in _ANSWERABLE),
                },
            )
        current_domain = _to_domain(row)
        next_domain = OpenQuestion(
            question_id=current_domain.question_id,
            subject_id=current_domain.subject_id,
            text=current_domain.text,
            status=OpenQuestionStatus.ANSWERED,
            asked_at=current_domain.asked_at,
            answered_at=answered_at,
            answer_summary=answer_summary,
            closed_without_answer_reason=None,
            proposed_by=current_domain.proposed_by,
            version=self._next_version(current_domain),
            evidence_requirements=current_domain.evidence_requirements,
            due_at=current_domain.due_at,
            progress_note=current_domain.progress_note,
            source_refs=current_domain.source_refs if source_refs is None else source_refs,
            decision_ids=current_domain.decision_ids,
        )
        _apply_domain(row, next_domain)

    def close_without_answer(
        self,
        question_id: str,
        *,
        closed_reason: str,
        expected_version: int | None = None,
    ) -> None:
        row = self._locked(question_id, expected_version)
        current = OpenQuestionStatus(row.status)
        if current not in _CLOSABLE:
            raise DataContractError(
                "OpenQuestion.close_without_answer only allowed from OPEN",
                details={
                    "question_id": question_id,
                    "status": current.value,
                    "allowed_from": sorted(s.value for s in _CLOSABLE),
                },
            )
        current_domain = _to_domain(row)
        next_domain = OpenQuestion(
            question_id=current_domain.question_id,
            subject_id=current_domain.subject_id,
            text=current_domain.text,
            status=OpenQuestionStatus.CLOSED_WITHOUT_ANSWER,
            asked_at=current_domain.asked_at,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=closed_reason,
            proposed_by=current_domain.proposed_by,
            version=self._next_version(current_domain),
            evidence_requirements=current_domain.evidence_requirements,
            due_at=current_domain.due_at,
            progress_note=current_domain.progress_note,
            source_refs=current_domain.source_refs,
            decision_ids=current_domain.decision_ids,
        )
        _apply_domain(row, next_domain)

    def mark_stale(self, question_id: str, *, expected_version: int | None = None) -> None:
        row = self._locked(question_id, expected_version)
        current = OpenQuestionStatus(row.status)
        if current not in _STALEABLE:
            raise DataContractError(
                "OpenQuestion.mark_stale only allows OPEN→STALE",
                details={
                    "question_id": question_id,
                    "status": current.value,
                    "allowed_from": sorted(s.value for s in _STALEABLE),
                },
            )
        current_domain = _to_domain(row)
        next_domain = OpenQuestion(
            question_id=current_domain.question_id,
            subject_id=current_domain.subject_id,
            text=current_domain.text,
            status=OpenQuestionStatus.STALE,
            asked_at=current_domain.asked_at,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=None,
            proposed_by=current_domain.proposed_by,
            version=self._next_version(current_domain),
            evidence_requirements=current_domain.evidence_requirements,
            due_at=current_domain.due_at,
            progress_note=current_domain.progress_note,
            source_refs=current_domain.source_refs,
            decision_ids=current_domain.decision_ids,
        )
        _apply_domain(row, next_domain)

    def update_research(
        self,
        question_id: str,
        *,
        expected_version: int,
        evidence_requirements: tuple[str, ...],
        due_at: datetime | None,
        progress_note: str | None,
        source_refs: tuple[ResearchSourceRef, ...],
    ) -> None:
        row = self._locked(question_id, expected_version)
        current_domain = _to_domain(row)
        if current_domain.status is not OpenQuestionStatus.OPEN:
            raise DataContractError(
                "OpenQuestion.update_research only allowed from OPEN",
                details={"question_id": question_id, "status": current_domain.status.value},
            )
        next_domain = OpenQuestion(
            question_id=current_domain.question_id,
            subject_id=current_domain.subject_id,
            text=current_domain.text,
            status=current_domain.status,
            asked_at=current_domain.asked_at,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=None,
            proposed_by=current_domain.proposed_by,
            version=self._next_version(current_domain),
            evidence_requirements=evidence_requirements,
            due_at=due_at,
            progress_note=progress_note,
            source_refs=source_refs,
            decision_ids=current_domain.decision_ids,
        )
        _apply_domain(row, next_domain)

    def link_decisions(
        self,
        question_id: str,
        *,
        expected_version: int,
        decision_ids: tuple[str, ...],
    ) -> None:
        row = self._locked(question_id, expected_version)
        current_domain = _to_domain(row)
        if current_domain.status is not OpenQuestionStatus.ANSWERED:
            raise DataContractError(
                "OpenQuestion.link_decisions only allowed from ANSWERED",
                details={"question_id": question_id, "status": current_domain.status.value},
            )
        next_domain = OpenQuestion(
            question_id=current_domain.question_id,
            subject_id=current_domain.subject_id,
            text=current_domain.text,
            status=current_domain.status,
            asked_at=current_domain.asked_at,
            answered_at=current_domain.answered_at,
            answer_summary=current_domain.answer_summary,
            closed_without_answer_reason=None,
            proposed_by=current_domain.proposed_by,
            version=self._next_version(current_domain),
            evidence_requirements=current_domain.evidence_requirements,
            due_at=current_domain.due_at,
            progress_note=current_domain.progress_note,
            source_refs=current_domain.source_refs,
            decision_ids=decision_ids,
        )
        _apply_domain(row, next_domain)

    def list_due(
        self,
        *,
        now: datetime,
        subject_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[OpenQuestion, ...]:
        require_aware_datetime(now, field_name="now")
        if type(limit) is not int or limit < 1 or limit > 100:
            raise DataContractError("OpenQuestion due limit must be between 1 and 100")
        if type(offset) is not int or offset < 0:
            raise DataContractError("OpenQuestion due offset must be non-negative")
        stmt = (
            select(OpenQuestionRow)
            .where(OpenQuestionRow.status == OpenQuestionStatus.OPEN.value)
            .where(OpenQuestionRow.due_at.is_not(None))
            .where(func.julianday(OpenQuestionRow.asked_at) <= func.julianday(_due_to_db(now)))
            .where(func.julianday(OpenQuestionRow.due_at) <= func.julianday(_due_to_db(now)))
            .order_by(
                func.julianday(OpenQuestionRow.due_at).asc(),
                OpenQuestionRow.question_id.asc(),
            )
        )
        if subject_id is not None:
            stmt = stmt.where(OpenQuestionRow.subject_id == subject_id)
        # SQLite's Julian-day arithmetic is deliberately only a SQL candidate
        # filter.  Decode and compare aware timestamps in Python, paging until
        # the requested bound is filled so a near-boundary rounding artefact
        # cannot make a complete due page look short.
        result: list[OpenQuestion] = []
        query_offset = offset
        while len(result) < limit:
            page = self._session.scalars(stmt.offset(query_offset).limit(limit)).all()
            if not page:
                break
            query_offset += len(page)
            for row in page:
                question = _to_domain(row)
                if (
                    question.asked_at <= now
                    and question.due_at is not None
                    and question.due_at <= now
                ):
                    result.append(question)
                    if len(result) >= limit:
                        break
            if len(page) < limit:
                break
        return tuple(result)


__all__ = ["SqlAlchemyOpenQuestionRepository"]
