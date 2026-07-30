"""SQLAlchemy OpenQuestion repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import OpenQuestionStatus
from domain.common.errors import DataContractError, OpenQuestionNotFound
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


def _to_domain(row: OpenQuestionRow) -> OpenQuestion:
    return OpenQuestion(
        question_id=row.question_id,
        case_id=row.case_id,
        text=row.text,
        status=OpenQuestionStatus(row.status),
        asked_at=dt_from_db(row.asked_at, field_name="asked_at"),
        answered_at=dt_opt_from_db(row.answered_at, field_name="answered_at"),
        answer_summary=row.answer_summary,
        closed_without_answer_reason=row.closed_without_answer_reason,
        proposed_by=row.proposed_by,
    )


def _apply_domain(row: OpenQuestionRow, question: OpenQuestion) -> None:
    """Write a fully validated domain shape onto the ORM row."""
    row.status = question.status.value
    row.answered_at = (
        None if question.answered_at is None else dt_to_db(question.answered_at)
    )
    row.answer_summary = question.answer_summary
    row.closed_without_answer_reason = question.closed_without_answer_reason


def _to_row(question: OpenQuestion) -> OpenQuestionRow:
    return OpenQuestionRow(
        question_id=question.question_id,
        case_id=question.case_id,
        text=question.text,
        status=question.status.value,
        asked_at=dt_to_db(question.asked_at),
        answered_at=None if question.answered_at is None else dt_to_db(question.answered_at),
        answer_summary=question.answer_summary,
        closed_without_answer_reason=question.closed_without_answer_reason,
        proposed_by=question.proposed_by,
    )


class SqlAlchemyOpenQuestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_case(self, case_id: str) -> tuple[OpenQuestion, ...]:
        stmt = (
            select(OpenQuestionRow)
            .where(OpenQuestionRow.case_id == case_id)
            .order_by(OpenQuestionRow.asked_at.asc())
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

    def answer(
        self,
        question_id: str,
        *,
        answered_at: datetime,
        answer_summary: str,
    ) -> None:
        row = self._session.get(OpenQuestionRow, question_id, with_for_update=True)
        if row is None:
            raise OpenQuestionNotFound(
                f"OpenQuestion not found: {question_id}",
                details={"question_id": question_id},
            )
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
            case_id=current_domain.case_id,
            text=current_domain.text,
            status=OpenQuestionStatus.ANSWERED,
            asked_at=current_domain.asked_at,
            answered_at=answered_at,
            answer_summary=answer_summary,
            closed_without_answer_reason=None,
            proposed_by=current_domain.proposed_by,
        )
        _apply_domain(row, next_domain)

    def close_without_answer(
        self,
        question_id: str,
        *,
        closed_reason: str,
    ) -> None:
        row = self._session.get(OpenQuestionRow, question_id, with_for_update=True)
        if row is None:
            raise OpenQuestionNotFound(
                f"OpenQuestion not found: {question_id}",
                details={"question_id": question_id},
            )
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
            case_id=current_domain.case_id,
            text=current_domain.text,
            status=OpenQuestionStatus.CLOSED_WITHOUT_ANSWER,
            asked_at=current_domain.asked_at,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=closed_reason,
            proposed_by=current_domain.proposed_by,
        )
        _apply_domain(row, next_domain)

    def mark_stale(self, question_id: str) -> None:
        row = self._session.get(OpenQuestionRow, question_id, with_for_update=True)
        if row is None:
            raise OpenQuestionNotFound(
                f"OpenQuestion not found: {question_id}",
                details={"question_id": question_id},
            )
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
            case_id=current_domain.case_id,
            text=current_domain.text,
            status=OpenQuestionStatus.STALE,
            asked_at=current_domain.asked_at,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=None,
            proposed_by=current_domain.proposed_by,
        )
        _apply_domain(row, next_domain)
