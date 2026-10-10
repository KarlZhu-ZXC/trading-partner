"""Validation and append rules for evidence-to-Thesis impact links.

The candidate lifecycle owns proposal, review, and candidate status changes.  This
module only validates the references carried by an ``evidence_impact`` candidate
and appends the immutable impact version after the caller has authorized it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

from application.dto.research_links import EvidenceImpactCandidatePayload
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.evidence_impact import EvidenceImpact


def _fail(message: str, **details: object) -> NoReturn:
    raise DataContractError(message, details=details)


def _value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _safe_get(getter: Any, message: str, **details: object) -> Any:
    """Read a repository value and normalize missing/repository failures.

    A rule failure must be reported as a data-contract failure at the application
    boundary.  The repositories normally raise a typed domain error; synthetic
    UoWs and compatibility repositories may instead return ``None`` or raise a
    plain lookup exception, so both shapes are normalized here.
    """

    try:
        value = getter()
    except DataContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - repository adapters vary by deployment
        raise DataContractError(message, details=details) from exc
    if value is None:
        _fail(message, **details)
    return value


def _subject(uow: ResearchUnitOfWork, subject_id: str) -> Any:
    return _safe_get(
        lambda: uow.subjects.get(subject_id),
        "Research Subject does not exist",
        subject_id=subject_id,
    )


def _revision_and_thesis(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    revision_id: str | None,
    now: datetime,
) -> tuple[Any, Any]:
    if not revision_id:
        _fail("evidence impact requires thesis_revision_id", subject_id=subject_id)
    revision = _safe_get(
        lambda: uow.revisions.get(revision_id),
        "thesis revision does not exist",
        thesis_revision_id=revision_id,
    )
    if revision.subject_id != subject_id:
        _fail(
            "thesis revision does not belong to Research Subject",
            thesis_revision_id=revision_id,
            subject_id=subject_id,
        )
    if revision.confirmed_at > now:
        _fail(
            "thesis revision is not visible at confirmation time",
            thesis_revision_id=revision_id,
            subject_id=subject_id,
        )
    thesis = _safe_get(
        lambda: uow.theses.get(revision.thesis_id),
        "thesis does not exist",
        thesis_id=revision.thesis_id,
    )
    if thesis.subject_id != subject_id:
        _fail(
            "thesis does not belong to Research Subject",
            thesis_id=revision.thesis_id,
            subject_id=subject_id,
        )
    if (
        thesis.current_revision_no != revision.revision_no
        or thesis.latest_revision_id != revision.revision_id
    ):
        _fail(
            "evidence impact must target the current Thesis revision",
            thesis_id=revision.thesis_id,
            thesis_revision_id=revision_id,
            current_revision_no=thesis.current_revision_no,
            target_revision_no=revision.revision_no,
        )
    return revision, thesis


def _current_impact(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    impact_id: str | None,
    expected_version: int | None,
) -> Any:
    if not impact_id:
        _fail("evidence impact action requires impact_id", subject_id=subject_id)
    current = _safe_get(
        lambda: uow.evidence_impacts.get(impact_id),
        "evidence impact does not exist",
        impact_id=impact_id,
    )
    if current.subject_id != subject_id:
        _fail(
            "evidence impact does not belong to Research Subject",
            impact_id=impact_id,
            subject_id=subject_id,
        )
    if expected_version is None or expected_version != current.version:
        _fail(
            "evidence impact expected_version does not match current version",
            impact_id=impact_id,
            expected_version=expected_version,
            current_version=current.version,
        )
    if _value(current.state).upper() != "ACTIVE":
        _fail(
            "only an ACTIVE evidence impact can be revised or retracted",
            impact_id=impact_id,
            state=_value(current.state),
        )
    return current


def _source_ref(
    payload_source: Any,
    *,
    subject_id: str,
    uow: ResearchUnitOfWork,
    now: datetime,
) -> Any:
    if payload_source is None:
        _fail("evidence impact requires an exact source reference", subject_id=subject_id)
    try:
        ref = payload_source.to_domain()
    except DataContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - DTO adapters may expose ValueError
        raise DataContractError(
            "invalid evidence impact source reference",
            details={"subject_id": subject_id},
        ) from exc
    snapshot = _safe_get(
        lambda: uow.research_sources.get(subject_id, ref, as_of=now),
        "source reference is not visible in the Research Subject scope",
        subject_id=subject_id,
        source_id=getattr(ref, "source_id", None),
    )
    # Keep the snapshot intentionally unused.  The repository is the authority
    # for exact identity, scope, full-text availability, and point-in-time
    # visibility; rules must never copy source content into a candidate.
    del snapshot
    return ref


def validate_impact(
    uow: ResearchUnitOfWork,
    subject_id: str,
    payload: EvidenceImpactCandidatePayload,
    now: datetime,
) -> None:
    """Validate an evidence-impact candidate without mutating the UoW."""

    require_aware_datetime(now, field_name="now")
    _subject(uow, subject_id)
    action = _value(payload.action).lower()
    if action not in {"create", "revise", "retract"}:
        _fail("unsupported evidence impact action", action=action)

    if action == "create":
        if payload.impact_id is not None:
            _fail("create evidence impact must not set impact_id")
        if payload.expected_version is not None:
            _fail("create evidence impact must not set expected_version")
    elif action in {"revise", "retract"}:
        _current_impact(
            uow,
            subject_id=subject_id,
            impact_id=payload.impact_id,
            expected_version=payload.expected_version,
        )

    # A retract copies the previous target and source.  It must remain possible
    # after source retention or visibility changes, so no new source/target is
    # resolved on this path.
    if action == "retract":
        return

    revision, _thesis = _revision_and_thesis(
        uow,
        subject_id=subject_id,
        revision_id=payload.thesis_revision_id,
        now=now,
    )
    if payload.assumption_id is not None:
        assumption = _safe_get(
            lambda: uow.assumptions.get(payload.assumption_id),
            "assumption does not exist",
            assumption_id=payload.assumption_id,
        )
        if (
            assumption.subject_id != subject_id
            or assumption.thesis_id != revision.thesis_id
            or assumption.revision_no != revision.revision_no
        ):
            _fail(
                "assumption does not belong to the targeted Thesis revision",
                assumption_id=payload.assumption_id,
                thesis_revision_id=revision.revision_id,
            )
        if _value(assumption.status).lower() not in {"accepted", "active"}:
            _fail(
                "evidence impact assumption must be active",
                assumption_id=payload.assumption_id,
                status=_value(assumption.status),
            )
    _source_ref(payload.source, subject_id=subject_id, uow=uow, now=now)


def apply_impact(
    uow: ResearchUnitOfWork,
    candidate: Any,
    payload: EvidenceImpactCandidatePayload,
    reviewed_by: str,
    now: datetime,
    id_generator: IdGenerator,
) -> tuple[str, str | None]:
    """Append the immutable evidence-impact version for a confirmed candidate.

    Candidate status and transaction commits remain the caller's responsibility.
    """

    subject_id = getattr(candidate, "subject_id", None)
    if not subject_id:
        _fail("evidence impact candidate requires subject_id")
    require_aware_datetime(now, field_name="now")
    validate_impact(uow, subject_id, payload, now)

    action = _value(payload.action).lower()
    current = (
        _current_impact(
            uow,
            subject_id=subject_id,
            impact_id=payload.impact_id,
            expected_version=payload.expected_version,
        )
        if action in {"revise", "retract"}
        else None
    )

    if action == "retract":
        assert current is not None
        candidate_thesis_id = getattr(candidate, "thesis_id", None)
        if candidate_thesis_id is not None and candidate_thesis_id != current.thesis_id:
            _fail(
                "evidence impact candidate Thesis does not match the prior impact",
                impact_id=current.impact_id,
            )
        impact_id = current.impact_id
        version = current.version + 1
        value = EvidenceImpact(
            impact_id=impact_id,
            version=version,
            subject_id=subject_id,
            thesis_id=current.thesis_id,
            thesis_revision_id=current.thesis_revision_id,
            assumption_id=current.assumption_id,
            source=current.source,
            stance=current.stance,
            rationale=payload.rationale,
            state="RETRACTED",
            confirmed_at=now,
            confirmed_by=reviewed_by,
            candidate_id=candidate.candidate_id,
        )
        uow.evidence_impacts.append(value, expected_version=current.version)
        return "evidence_impact", impact_id

    revision, thesis = _revision_and_thesis(
        uow,
        subject_id=subject_id,
        revision_id=payload.thesis_revision_id,
        now=now,
    )
    candidate_thesis_id = getattr(candidate, "thesis_id", None)
    candidate_revision_no = getattr(candidate, "target_revision_no", None)
    if candidate_thesis_id is not None and candidate_thesis_id != thesis.thesis_id:
        _fail(
            "evidence impact candidate Thesis does not match its target revision",
            thesis_id=thesis.thesis_id,
        )
    if candidate_revision_no is not None and candidate_revision_no != revision.revision_no:
        _fail(
            "evidence impact candidate revision does not match its target revision",
            thesis_revision_id=revision.revision_id,
        )
    if payload.assumption_id is None:
        _fail("evidence impact requires assumption_id")
    source = _source_ref(payload.source, subject_id=subject_id, uow=uow, now=now)
    impact_id = (
        current.impact_id
        if current is not None
        else id_generator.new(EntityIdPrefix.EVIDENCE_IMPACT)
    )
    version = current.version + 1 if current is not None else 1
    value = EvidenceImpact(
        impact_id=impact_id,
        version=version,
        subject_id=subject_id,
        thesis_id=thesis.thesis_id,
        thesis_revision_id=revision.revision_id,
        assumption_id=payload.assumption_id,
        source=source,
        stance=str(payload.stance),
        rationale=payload.rationale,
        state="ACTIVE",
        confirmed_at=now,
        confirmed_by=reviewed_by,
        candidate_id=candidate.candidate_id,
    )
    uow.evidence_impacts.append(
        value,
        expected_version=current.version if current is not None else None,
    )
    return "evidence_impact", impact_id


__all__ = ["apply_impact", "validate_impact"]
