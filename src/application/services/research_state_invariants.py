"""Cross-entity invariants for live research judgment state.

The domain models validate each entity in isolation.  These guards keep the
Research Subject/Thesis/Trade Plan lifecycle coherent without coupling the domain layer to
repositories or persistence.
"""

from __future__ import annotations

from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.enums import ResearchSubjectStatus, ThesisStatus
from domain.common.errors import ResearchStateConflict
from domain.research.models import ResearchSubject, Thesis
from domain.trade_plan.enums import TradePlanStatus

# These are intentionally public and stable.  Other read-only services can use
# them when classifying durable state without duplicating lifecycle literals.
TRACKING_SUBJECT_STATUSES = frozenset(
    {
        ResearchSubjectStatus.ACTIVE,
        ResearchSubjectStatus.STRENGTHENED,
        ResearchSubjectStatus.WEAKENED,
    }
)
LIVE_THESIS_STATUSES = frozenset(
    {
        ThesisStatus.ACTIVE,
        ThesisStatus.STRENGTHENED,
        ThesisStatus.WEAKENED,
    }
)
LIVE_TRADE_PLAN_STATUSES = frozenset(
    {
        TradePlanStatus.ACTIVE,
        TradePlanStatus.PAUSED,
    }
)


def _status_value(status: object) -> str:
    if isinstance(status, (ResearchSubjectStatus, ThesisStatus, TradePlanStatus)):
        return status.value
    return str(status)


def ensure_subject_can_host_live_thesis(
    subject: ResearchSubject,
    *,
    attempted_child_status: ThesisStatus,
) -> None:
    """Require a tracking Research Subject before creating/confirming a live Thesis."""

    if (
        attempted_child_status in LIVE_THESIS_STATUSES
        and subject.status not in TRACKING_SUBJECT_STATUSES
    ):
        raise ResearchStateConflict(
            "live Thesis requires a tracking Research Subject",
            details={
                "subject_id": subject.subject_id,
                "subject_status": _status_value(subject.status),
                "attempted_child_status": _status_value(attempted_child_status),
            },
        )


def ensure_active_trade_plan_dependencies(
    subject: ResearchSubject,
    thesis: Thesis,
    *,
    attempted_child_status: TradePlanStatus,
) -> None:
    """Require tracking Research Subject and live Thesis for an ACTIVE Trade Plan."""

    if attempted_child_status is not TradePlanStatus.ACTIVE:
        return

    if subject.status not in TRACKING_SUBJECT_STATUSES:
        raise ResearchStateConflict(
            "ACTIVE Trade Plan requires a tracking Research Subject",
            details={
                "subject_id": subject.subject_id,
                "subject_status": _status_value(subject.status),
                "attempted_child_status": _status_value(attempted_child_status),
            },
        )
    if thesis.status not in LIVE_THESIS_STATUSES:
        raise ResearchStateConflict(
            "ACTIVE Trade Plan requires a live Thesis",
            details={
                "subject_id": subject.subject_id,
                "subject_status": _status_value(subject.status),
                "attempted_child_status": _status_value(attempted_child_status),
                "thesis_id": thesis.thesis_id,
                "thesis_status": _status_value(thesis.status),
            },
        )


def ensure_thesis_status_transition(
    uow: ResearchUnitOfWork,
    subject: ResearchSubject,
    thesis: Thesis,
    *,
    attempted_child_status: ThesisStatus,
) -> None:
    """Validate an existing Thesis status transition against its Subject/Plan."""

    if attempted_child_status in LIVE_THESIS_STATUSES:
        ensure_subject_can_host_live_thesis(
            subject,
            attempted_child_status=attempted_child_status,
        )

    if thesis.status not in LIVE_THESIS_STATUSES or attempted_child_status in LIVE_THESIS_STATUSES:
        return

    current_plan = uow.trade_plans.get_current_by_subject(subject.subject_id)
    if (
        current_plan is None
        or current_plan.thesis_id != thesis.thesis_id
        or current_plan.status not in LIVE_TRADE_PLAN_STATUSES
    ):
        return

    raise ResearchStateConflict(
        "live Thesis cannot retire while its live Trade Plan remains",
        details={
            "subject_id": subject.subject_id,
            "subject_status": _status_value(subject.status),
            "attempted_child_status": _status_value(attempted_child_status),
            "thesis_id": thesis.thesis_id,
            "thesis_status": _status_value(thesis.status),
            "live_trade_plan_id": current_plan.plan_id,
            "live_trade_plan_status": _status_value(current_plan.status),
        },
    )


def ensure_subject_can_leave_tracking(
    uow: ResearchUnitOfWork,
    subject: ResearchSubject,
    *,
    attempted_subject_status: ResearchSubjectStatus,
) -> None:
    """Reject leaving tracking while live judgment or a live plan remains.

    The check is deliberately read-only and must run before callers mutate the
    Research Subject. No child is implicitly archived or otherwise cascaded.
    """

    if attempted_subject_status in TRACKING_SUBJECT_STATUSES:
        return
    # A no-op/non-status update on an already non-tracking legacy Subject should not
    # be blocked, but moving that Subject to another non-tracking terminal state
    # must not preserve live children (for example DRAFT -> ARCHIVED).
    if (
        subject.status not in TRACKING_SUBJECT_STATUSES
        and attempted_subject_status is subject.status
    ):
        return

    live_theses = tuple(
        thesis
        for thesis in uow.theses.list_by_subject(subject.subject_id)
        if thesis.status in LIVE_THESIS_STATUSES
    )
    current_plan = uow.trade_plans.get_current_by_subject(subject.subject_id)
    live_plan = (
        current_plan
        if current_plan is not None and current_plan.status in LIVE_TRADE_PLAN_STATUSES
        else None
    )
    if not live_theses and live_plan is None:
        return

    raise ResearchStateConflict(
        "Research Subject cannot leave tracking while live judgment remains",
        details={
            "subject_id": subject.subject_id,
            "subject_status": _status_value(subject.status),
            "attempted_subject_status": _status_value(attempted_subject_status),
            "live_thesis_ids": tuple(thesis.thesis_id for thesis in live_theses),
            "live_trade_plan_id": live_plan.plan_id if live_plan is not None else None,
            "live_trade_plan_status": (
                _status_value(live_plan.status) if live_plan is not None else None
            ),
        },
    )
