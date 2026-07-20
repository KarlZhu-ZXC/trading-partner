"""Shared helpers for Phase 1B research application services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.research import (
    AssumptionDTO,
    CandidateRevisionDTO,
    InvalidationConditionDTO,
    InvestmentCaseDTO,
    OpenQuestionDTO,
    ResearchStateDTO,
    ThesisDTO,
    ThesisRevisionDTO,
    WatchlistItemDTO,
    candidate_payload_to_json,
    parse_candidate_payload,
    payloads_equal_json,
)
from application.dto.tool_envelope import (
    DUPLICATE_IDEMPOTENCY_KEY,
    ToolEnvelope,
    WarningInfo,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import (
    CandidateKind,
    CandidateStatus,
    ConfirmationMode,
    Freshness,
    ThesisStatus,
)
from domain.common.errors import (
    DuplicateIdempotencyKey,
    InputValidationError,
    TradingPartnerError,
    UnauthorizedReviewer,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    ThesisRevision,
)

UowFactory = Callable[[], ResearchUnitOfWork]

CANDIDATE_TTL = timedelta(days=7)

CONFIRM_REVIEWERS = frozenset({"user", "external_agent"})
CODEX_ACTOR = "codex"


def normalize_idempotency_key(key: str) -> str:
    normalized = key.strip().lower()
    if not normalized or len(normalized) > 128:
        raise InputValidationError(
            "idempotency_key must be 1..128 characters after strip/lower",
            details={"length": len(normalized)},
        )
    return normalized


def require_confirm_reviewer(reviewed_by: str, *, action: str) -> None:
    actor = reviewed_by.strip()
    if actor not in CONFIRM_REVIEWERS:
        raise UnauthorizedReviewer(
            f"{action} requires reviewed_by in {{user, external_agent}}; got {actor!r}",
            details={"reviewed_by": actor, "action": action},
        )


def require_non_codex_confirmer(confirmed_by: str, *, action: str) -> None:
    require_confirm_reviewer(confirmed_by, action=action)


def envelope_success[T](
    *,
    request_id: str,
    clock: Clock,
    data: T,
    warnings: tuple[WarningInfo, ...] = (),
    degraded: bool = False,
) -> ToolEnvelope[T]:
    now = clock.now()
    return ToolEnvelope.success(
        request_id=request_id,
        market=None,
        as_of=now,
        fetched_at=now,
        freshness=Freshness.FRESH,
        sources=(),
        data=data,
        degraded=degraded or bool(warnings),
        warnings=warnings,
    )


def envelope_failure[T](
    *,
    request_id: str,
    clock: Clock,
    redactor: SecretRedactor,
    exc: BaseException,
) -> ToolEnvelope[T]:
    now = clock.now()
    if isinstance(exc, TradingPartnerError):
        err = to_error_info(exc, redactor)
    else:
        err = to_error_info_from_exception(exc, redactor)
    return ToolEnvelope.failure(
        request_id=request_id,
        market=None,
        as_of=now,
        fetched_at=now,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(err,),
        degraded=True,
    )


def build_research_state(
    uow: ResearchUnitOfWork,
    case_id: str,
    *,
    include_archived_theses: bool = False,
    include_watchlist: bool = True,
) -> ResearchStateDTO:
    case = uow.cases.get(case_id)
    theses = uow.theses.list_by_case(case_id)
    if not include_archived_theses:
        theses = tuple(t for t in theses if t.status != ThesisStatus.ARCHIVED)

    latest_revisions: list[ThesisRevision] = []
    assumptions: list[Assumption] = []
    invalidations: list[InvalidationCondition] = []
    for thesis in theses:
        rev = uow.revisions.get(thesis.latest_revision_id)
        latest_revisions.append(rev)
        assumptions.extend(
            uow.assumptions.list_by_revision(thesis.thesis_id, thesis.current_revision_no)
        )
        invalidations.extend(
            uow.invalidations.list_by_revision(thesis.thesis_id, thesis.current_revision_no)
        )

    questions = uow.questions.list_by_case(case_id)
    watchlist = (
        uow.watchlist.list(case_id=case_id, limit=500, offset=0) if include_watchlist else ()
    )
    pending = uow.candidates.list(
        case_id=case_id,
        status=CandidateStatus.PROPOSED,
        limit=500,
        offset=0,
    )

    return ResearchStateDTO(
        case=InvestmentCaseDTO.from_domain(case),
        theses=ThesisDTO.from_domain_list(theses),
        latest_revisions=ThesisRevisionDTO.from_domain_list(tuple(latest_revisions)),
        assumptions=AssumptionDTO.from_domain_list(tuple(assumptions)),
        invalidations=InvalidationConditionDTO.from_domain_list(tuple(invalidations)),
        open_questions=OpenQuestionDTO.from_domain_list(questions),
        watchlist_items=WatchlistItemDTO.from_domain_list(watchlist),
        pending_candidates=CandidateRevisionDTO.from_domain_list(pending),
    )


def propose_candidate(
    *,
    uow: ResearchUnitOfWork,
    clock: Clock,
    id_generator: IdGenerator,
    kind: CandidateKind,
    case_id: str | None,
    thesis_id: str | None,
    target_revision_no: int | None,
    payload_model: object,
    confirmation_mode: ConfirmationMode,
    proposed_by: str,
    proposed_by_rationale: str,
    idempotency_key: str,
    status: CandidateStatus = CandidateStatus.PROPOSED,
    reviewed_at: datetime | None = None,
    reviewed_by: str | None = None,
    review_note: str | None = None,
) -> tuple[CandidateThesisRevision, bool, WarningInfo | None]:
    """Insert a candidate or return existing on same-key/same-payload.

    Returns (candidate, is_duplicate, warning_or_none).
    """
    key = normalize_idempotency_key(idempotency_key)
    rationale = proposed_by_rationale.strip()
    if not rationale or len(rationale) > 4000:
        raise InputValidationError(
            "proposed_by_rationale must be 1..4000 characters",
            details={"length": len(rationale)},
        )
    actor = proposed_by.strip()
    if not actor:
        raise InputValidationError("proposed_by must be non-empty")

    payload_json = candidate_payload_to_json(payload_model)  # type: ignore[arg-type]
    # Ensure kind matches payload discriminator.
    parsed = parse_candidate_payload(payload_json)
    if parsed.kind != kind.value:
        raise InputValidationError(
            "CandidateKind must match payload.kind",
            details={"kind": kind.value, "payload_kind": parsed.kind},
        )

    existing = uow.candidates.get_by_idempotency_key(key)
    if existing is not None:
        if payloads_equal_json(existing.payload_json, payload_json):
            return existing, True, DUPLICATE_IDEMPOTENCY_KEY
        raise DuplicateIdempotencyKey(
            "idempotency_key already used with a different payload",
            details={
                "idempotency_key": key,
                "existing_candidate_id": existing.candidate_id,
            },
        )

    now = clock.now()
    candidate = CandidateThesisRevision(
        candidate_id=id_generator.new(EntityIdPrefix.RUN),
        case_id=case_id,
        thesis_id=thesis_id,
        target_revision_no=target_revision_no,
        payload_json=payload_json,
        kind=kind,
        confirmation_mode=confirmation_mode,
        status=status,
        proposed_at=now,
        expires_at=now + CANDIDATE_TTL,
        proposed_by=actor,
        proposed_by_rationale=rationale,
        reviewed_at=reviewed_at,
        reviewed_by=reviewed_by,
        review_note=review_note,
        rejection_reason=None,
        idempotency_key=key,
    )
    uow.candidates.add(candidate)
    return candidate, False, None


def candidate_to_dto(candidate: CandidateThesisRevision) -> CandidateRevisionDTO:
    return CandidateRevisionDTO.from_domain(candidate)
