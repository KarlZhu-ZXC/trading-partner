"""Validation and application rules for versioned Research Questions.

Candidate lifecycle and transaction ownership stay in the caller.  These rules
keep the legacy version-1 question payload compatible while requiring an exact
optimistic version once metadata or links have been revised.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

from application.dto.research import OpenQuestionCandidatePayload
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.enums import OpenQuestionStatus
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.evidence_impact import ResearchSourceRef
from domain.research.models import OpenQuestion


def _fail(message: str, **details: object) -> NoReturn:
    raise DataContractError(message, details=details)


def _value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _safe_get(getter: Any, message: str, **details: object) -> Any:
    try:
        value = getter()
    except DataContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - repository adapters vary by deployment
        raise DataContractError(message, details=details) from exc
    if value is None:
        _fail(message, **details)
    return value


def _question_version(question: Any) -> int:
    version = getattr(question, "version", 1)
    if type(version) is not int or version < 1:
        _fail("open question version is invalid", version=version)
    return version


def _field_supplied(payload: OpenQuestionCandidatePayload, field: str) -> bool:
    fields: object = getattr(payload, "model_fields_set", frozenset())
    return isinstance(fields, (set, frozenset)) and field in fields


def _stable_ids(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            _fail(f"{field} must contain non-blank IDs", field=field)
        normalized = value.strip()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    if not result:
        _fail(f"{field} must contain at least one ID", field=field)
    return tuple(result)


def _source_refs(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    payload_refs: tuple[Any, ...],
    now: datetime,
) -> tuple[ResearchSourceRef, ...]:
    refs: list[ResearchSourceRef] = []
    for payload_ref in payload_refs:
        try:
            ref = payload_ref.to_domain()
        except DataContractError:
            raise
        except Exception as exc:  # noqa: BLE001 - DTO adapters may expose ValueError
            raise DataContractError(
                "invalid Research Question source reference",
                details={"subject_id": subject_id},
            ) from exc
        _safe_get(
            lambda ref=ref: uow.research_sources.get(subject_id, ref, as_of=now),
            "source reference is not visible in the Research Subject scope",
            subject_id=subject_id,
            source_id=ref.source_id,
        )
        refs.append(ref)
    return tuple(refs)


def _question_and_expected_version(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    payload: OpenQuestionCandidatePayload,
) -> tuple[Any, int]:
    question_id = payload.question_id
    if not question_id:
        _fail("open question action requires question_id", action=payload.action)
    question = _safe_get(
        lambda: uow.questions.get(question_id),
        "open question does not exist",
        question_id=question_id,
    )
    if question.subject_id != subject_id:
        _fail(
            "open question does not belong to Research Subject",
            question_id=question_id,
            subject_id=subject_id,
        )
    current_version = _question_version(question)
    expected = payload.expected_version
    if expected is None:
        if current_version > 1:
            _fail(
                "open question version is required after the first revision",
                question_id=question_id,
                current_version=current_version,
            )
        expected = current_version
    elif expected != current_version:
        _fail(
            "open question expected_version does not match current version",
            question_id=question_id,
            expected_version=expected,
            current_version=current_version,
        )
    return question, expected


def _require_open(question: Any, *, action: str) -> None:
    if _value(question.status).lower() != OpenQuestionStatus.OPEN.value:
        _fail(
            f"open question {action} is only allowed from OPEN",
            question_id=question.question_id,
            status=_value(question.status),
        )


def _validate_decision_links(
    uow: ResearchUnitOfWork,
    *,
    question: Any,
    subject_id: str,
    decision_ids: tuple[str, ...],
    now: datetime,
) -> tuple[str, ...]:
    ids = _stable_ids(decision_ids, field="decision_ids")
    answered_at = question.answered_at
    if answered_at is None:
        _fail("answered open question must have answered_at", question_id=question.question_id)
    require_aware_datetime(answered_at, field_name="answered_at")
    for decision_id in ids:
        decision = _safe_get(
            lambda decision_id=decision_id: uow.decisions.get(decision_id),
            "Decision does not exist",
            decision_id=decision_id,
        )
        if decision.subject_id != subject_id:
            _fail(
                "Decision does not belong to Research Subject",
                decision_id=decision_id,
                subject_id=subject_id,
            )
        for field in ("decided_at", "recorded_at"):
            timestamp = require_aware_datetime(getattr(decision, field), field_name=field)
            if timestamp < answered_at or timestamp > now:
                _fail(
                    f"Decision {field} must fall between question answer and confirmation",
                    decision_id=decision_id,
                    field=field,
                )
    return ids


def _metadata_source_refs(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    payload: OpenQuestionCandidatePayload,
    now: datetime,
) -> tuple[ResearchSourceRef, ...]:
    return _source_refs(
        uow,
        subject_id=subject_id,
        payload_refs=payload.source_refs,
        now=now,
    )


def prepare_question(
    uow: ResearchUnitOfWork,
    subject_id: str,
    payload: OpenQuestionCandidatePayload,
    now: datetime,
) -> OpenQuestionCandidatePayload:
    """Validate a proposal and pin legacy version-1 mutations to version 1."""

    validate_question(uow, subject_id, payload, now)
    if payload.action == "create" or payload.expected_version is not None:
        return payload
    # ``model_copy`` is used deliberately: this is a trusted internal pin after
    # all structural DTO validation has already completed.
    return payload.model_copy(update={"expected_version": 1})


def validate_question(
    uow: ResearchUnitOfWork,
    subject_id: str,
    payload: OpenQuestionCandidatePayload,
    now: datetime,
) -> None:
    """Validate a question candidate at proposal or confirmation time."""

    require_aware_datetime(now, field_name="now")
    _safe_get(
        lambda: uow.subjects.get(subject_id),
        "Research Subject does not exist",
        subject_id=subject_id,
    )
    action = _value(payload.action).lower()
    if action == "create":
        _metadata_source_refs(uow, subject_id=subject_id, payload=payload, now=now)
        if payload.due_at is not None:
            require_aware_datetime(payload.due_at, field_name="due_at")
        return

    question, _expected = _question_and_expected_version(
        uow,
        subject_id=subject_id,
        payload=payload,
    )
    if payload.due_at is not None:
        require_aware_datetime(payload.due_at, field_name="due_at")

    # Any explicitly carried source refs are resolved in this same transaction;
    # source content is never copied into a question or candidate.
    supplied_refs = _metadata_source_refs(
        uow,
        subject_id=subject_id,
        payload=payload,
        now=now,
    )

    if action == "update_research":
        _require_open(question, action=action)
        if not payload.evidence_requirements:
            _fail("update_research requires evidence_requirements")
        if payload.progress_note is None or not payload.progress_note.strip():
            _fail("update_research requires progress_note")
        del supplied_refs
        return

    if action == "answer":
        _require_open(question, action=action)
        requirements = tuple(getattr(question, "evidence_requirements", ()))
        if requirements:
            if not payload.criteria_met:
                _fail(
                    "answer must set criteria_met when evidence requirements exist",
                    question_id=question.question_id,
                )
            existing_refs = tuple(getattr(question, "source_refs", ()))
            if _field_supplied(payload, "source_refs"):
                answer_refs = supplied_refs
            else:
                answer_refs = existing_refs
            if not answer_refs:
                _fail(
                    "answer requires at least one exact source reference",
                    question_id=question.question_id,
                )
        return

    if action == "link_decision":
        if _value(question.status).lower() != OpenQuestionStatus.ANSWERED.value:
            _fail(
                "link_decision is only allowed from ANSWERED",
                question_id=question.question_id,
                status=_value(question.status),
            )
        _validate_decision_links(
            uow,
            question=question,
            subject_id=subject_id,
            decision_ids=payload.decision_ids,
            now=now,
        )
        return

    if action in {"mark_stale", "close"}:
        # Repository transition guards intentionally remain authoritative for
        # these legacy actions; validation only pins subject/version here.
        return
    _fail("unsupported open question action", action=action)


def apply_question(
    uow: ResearchUnitOfWork,
    candidate: Any,
    payload: OpenQuestionCandidatePayload,
    reviewed_by: str,
    now: datetime,
    id_generator: IdGenerator,
) -> tuple[str, str | None]:
    """Apply one already-confirmed question candidate without changing its lifecycle."""

    subject_id = getattr(candidate, "subject_id", None)
    if not subject_id:
        _fail("open question candidate requires subject_id")
    require_aware_datetime(now, field_name="now")
    validate_question(uow, subject_id, payload, now)
    action = _value(payload.action).lower()

    if action == "create":
        source_refs = _metadata_source_refs(
            uow,
            subject_id=subject_id,
            payload=payload,
            now=now,
        )
        question_id = id_generator.new(EntityIdPrefix.REV)
        question = OpenQuestion(
            question_id=question_id,
            subject_id=subject_id,
            text=(payload.text or "").strip(),
            status=OpenQuestionStatus.OPEN,
            asked_at=now,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=None,
            proposed_by=candidate.proposed_by,
            version=1,
            evidence_requirements=payload.evidence_requirements,
            due_at=payload.due_at,
            progress_note=payload.progress_note,
            source_refs=source_refs,
            decision_ids=(),
        )
        uow.questions.add(question)
        return "open_question", question_id

    question, expected_version = _question_and_expected_version(
        uow,
        subject_id=subject_id,
        payload=payload,
    )
    question_id = question.question_id
    if action == "update_research":
        source_refs = _metadata_source_refs(
            uow,
            subject_id=subject_id,
            payload=payload,
            now=now,
        )
        uow.questions.update_research(
            question_id,
            evidence_requirements=payload.evidence_requirements,
            due_at=payload.due_at,
            progress_note=payload.progress_note,
            source_refs=source_refs,
            expected_version=expected_version,
        )
        return "open_question", question_id

    if action == "answer":
        if _field_supplied(payload, "source_refs"):
            source_refs = _metadata_source_refs(
                uow,
                subject_id=subject_id,
                payload=payload,
                now=now,
            )
        else:
            source_refs = tuple(getattr(question, "source_refs", ()))
        uow.questions.answer(
            question_id,
            answered_at=now,
            answer_summary=(payload.answer_summary or "").strip(),
            expected_version=expected_version,
            source_refs=source_refs,
        )
        return "open_question", question_id

    if action == "close":
        uow.questions.close_without_answer(
            question_id,
            closed_reason=(payload.closed_reason or "").strip(),
            expected_version=expected_version,
        )
        return "open_question", question_id

    if action == "mark_stale":
        uow.questions.mark_stale(question_id, expected_version=expected_version)
        return "open_question", question_id

    if action == "link_decision":
        decision_ids = _validate_decision_links(
            uow,
            question=question,
            subject_id=subject_id,
            decision_ids=payload.decision_ids,
            now=now,
        )
        # The repository receives the complete deduplicated tuple because its
        # append-only version replaces the JSON snapshot for the new version.
        # This preserves links from earlier link_decision candidates.
        decision_ids = _stable_ids(
            tuple(getattr(question, "decision_ids", ())) + decision_ids,
            field="decision_ids",
        )
        uow.questions.link_decisions(
            question_id,
            decision_ids=decision_ids,
            expected_version=expected_version,
        )
        return "open_question", question_id

    _fail("unsupported open question action", action=action)


__all__ = ["apply_question", "prepare_question", "validate_question"]
