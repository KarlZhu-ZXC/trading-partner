"""Cross-entity invariants for live research judgment state.

The domain models validate each entity in isolation.  These guards keep the
Research Subject/Thesis/Trade Plan lifecycle coherent without coupling the domain layer to
repositories or persistence.
"""

from __future__ import annotations

from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.enums import ResearchSubjectStatus, ThesisRole, ThesisStatus
from domain.common.errors import ResearchStateConflict
from domain.research.models import ResearchSubject, Thesis
from domain.trade_plan.enums import TradePlanStatus

# These are intentionally public and stable.  Other read-only services can use
# them when classifying durable state without duplicating lifecycle literals.
TRACKING_SUBJECT_STATUSES = frozenset(
    {
        ResearchSubjectStatus.ACTIVE,
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


def ensure_subject_can_host_live_monitor(subject: ResearchSubject) -> None:
    """Require an ACTIVE Research Subject for an ACTIVE/PAUSED Monitor."""

    if subject.status in TRACKING_SUBJECT_STATUSES:
        return
    raise ResearchStateConflict(
        "live Monitor requires an ACTIVE Research Subject",
        details={
            "subject_id": subject.subject_id,
            "subject_status": _status_value(subject.status),
        },
    )


def ensure_no_live_monitors(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str | None = None,
    trade_plan_id: str | None = None,
    action: str,
) -> None:
    """Reject parent retirement while linked ACTIVE/PAUSED Monitors remain."""

    monitor_ids = uow.monitor_lifecycle.list_live_ids(
        subject_id=subject_id,
        trade_plan_id=trade_plan_id,
    )
    if not monitor_ids:
        return
    raise ResearchStateConflict(
        f"{action} requires linked Monitors to be archived first",
        details={
            "subject_id": subject_id,
            "trade_plan_id": trade_plan_id,
            "live_monitor_ids": monitor_ids,
        },
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


def ensure_single_live_primary_thesis(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    thesis_role: ThesisRole,
    attempted_child_status: ThesisStatus,
    current_thesis_id: str | None = None,
) -> None:
    """Allow one live PRIMARY while retaining parallel SUB/COMPETITOR/BEAR theses."""

    if thesis_role is not ThesisRole.PRIMARY or attempted_child_status not in LIVE_THESIS_STATUSES:
        return
    existing = tuple(
        thesis_id
        for thesis_id in uow.subjects.list_live_primary_thesis_ids(subject_id)
        if thesis_id != current_thesis_id
    )
    if not existing:
        return
    raise ResearchStateConflict(
        "Research Subject already has a live PRIMARY Thesis",
        details={
            "subject_id": subject_id,
            "attempted_child_status": _status_value(attempted_child_status),
            "current_thesis_id": current_thesis_id,
            "live_primary_thesis_ids": existing,
        },
    )


def ensure_thesis_relationship_dependencies(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    thesis_id: str | None,
    attempted_role: ThesisRole,
    attempted_status: ThesisStatus,
    parent_thesis_id: str | None,
) -> None:
    """Keep PRIMARY/SUB relationships coherent across role and status changes."""

    if attempted_role is ThesisRole.SUB and attempted_status in LIVE_THESIS_STATUSES:
        if parent_thesis_id is None:
            raise ResearchStateConflict(
                "live SUB Thesis requires a parent PRIMARY Thesis",
                details={"subject_id": subject_id, "thesis_id": thesis_id},
            )
        parent = uow.theses.get(parent_thesis_id)
        if parent.status not in LIVE_THESIS_STATUSES:
            raise ResearchStateConflict(
                "live SUB Thesis requires a live PRIMARY Thesis",
                details={
                    "subject_id": subject_id,
                    "thesis_id": thesis_id,
                    "parent_thesis_id": parent_thesis_id,
                    "parent_status": _status_value(parent.status),
                },
            )

    if thesis_id is None:
        return
    children = tuple(
        thesis
        for thesis in uow.theses.list_by_subject(subject_id)
        if thesis.role is ThesisRole.SUB and thesis.parent_thesis_id == thesis_id
    )
    if attempted_role is not ThesisRole.PRIMARY and children:
        raise ResearchStateConflict(
            "PRIMARY Thesis with SUB children cannot change role",
            details={
                "subject_id": subject_id,
                "thesis_id": thesis_id,
                "sub_thesis_ids": tuple(child.thesis_id for child in children),
            },
        )
    live_children = tuple(
        child.thesis_id for child in children if child.status in LIVE_THESIS_STATUSES
    )
    if attempted_status not in LIVE_THESIS_STATUSES and live_children:
        raise ResearchStateConflict(
            "PRIMARY Thesis cannot retire while live SUB Theses remain",
            details={
                "subject_id": subject_id,
                "thesis_id": thesis_id,
                "live_sub_thesis_ids": live_children,
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
    attempted_role: ThesisRole | None = None,
) -> None:
    """Validate an existing Thesis status transition against its Subject/Plan."""

    if attempted_child_status in LIVE_THESIS_STATUSES:
        ensure_subject_can_host_live_thesis(
            subject,
            attempted_child_status=attempted_child_status,
        )
        ensure_single_live_primary_thesis(
            uow,
            subject_id=subject.subject_id,
            thesis_role=attempted_role or thesis.role,
            attempted_child_status=attempted_child_status,
            current_thesis_id=thesis.thesis_id,
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

    ensure_no_live_monitors(
        uow,
        subject_id=subject.subject_id,
        action="Research Subject retirement",
    )

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
