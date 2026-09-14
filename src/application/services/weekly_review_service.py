"""Durable current-week views and current questions, with independent source coverage."""

from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from application.dto.today_review import TodayReviewGroupDTO
from application.dto.weekly_review import (
    WeeklyConfirmedViewDTO,
    WeeklyDecisionDTO,
    WeeklyQuestionDTO,
    WeeklyReviewDTO,
)
from application.ports.clock import Clock
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.today_review_service import TodayReviewService
from domain.research.models import ResearchSubject


class WeeklyReviewService:
    def __init__(
        self,
        uow_factory: Callable[[], ResearchUnitOfWork],
        today_review: TodayReviewService,
        clock: Clock,
        timezone: str,
    ) -> None:
        self._uow = uow_factory
        self._today = today_review
        self._clock = clock
        self._timezone = timezone
        self._zone = ZoneInfo(timezone)

    def get(self) -> WeeklyReviewDTO:
        now = self._clock.now()
        local = now.astimezone(self._zone)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=local.weekday()
        )
        end = start + timedelta(days=7)
        coverage = {
            key: "COMPLETE" for key in ("SUBJECTS", "CONFIRMED_VIEWS", "DECISIONS", "QUESTIONS")
        }
        warnings: list[str] = []
        subjects: list[ResearchSubject] = []
        try:
            with self._uow() as uow:
                for offset in (0, 200, 400):
                    limit = min(200, 500 - offset)
                    page = uow.subjects.list(limit=limit, offset=offset, include_archived=True)
                    subjects.extend(page)
                    if len(page) < limit:
                        break
            if len(subjects) >= 500:
                coverage = {key: "PARTIAL" for key in coverage}
                warnings.append("SUBJECT_HISTORY_BOUNDED")
        except Exception:
            coverage = {key: "UNAVAILABLE" for key in coverage}
            warnings.append("SUBJECT_SOURCE_UNAVAILABLE")
        views: list[WeeklyConfirmedViewDTO] = []
        decisions: list[WeeklyDecisionDTO] = []
        questions: list[WeeklyQuestionDTO] = []
        for subject in subjects[:500]:

            def read_views(current: ResearchSubject = subject) -> None:
                views.extend(self._views(current, start, now, warnings))

            def read_decisions(current: ResearchSubject = subject) -> None:
                decisions.extend(self._decisions(current, start, now))

            def read_questions(current: ResearchSubject = subject) -> None:
                questions.extend(self._questions(current, now))

            readers: tuple[tuple[str, Callable[[], None]], ...] = (
                ("CONFIRMED_VIEWS", read_views),
                ("DECISIONS", read_decisions),
                ("QUESTIONS", read_questions),
            )
            for key, read in readers:
                try:
                    read()
                except Exception:
                    coverage[key] = "PARTIAL"
                    warnings.append(f"{key}_SOURCE_UNAVAILABLE:{subject.subject_id}")
        views.sort(key=lambda row: (row.confirmed_at, row.revision_id), reverse=True)
        decisions.sort(key=lambda row: (row.recorded_at, row.decision_id), reverse=True)
        questions.sort(key=lambda row: (row.asked_at, row.question_id), reverse=True)
        for key, rows in (
            ("CONFIRMED_VIEWS", views),
            ("DECISIONS", decisions),
            ("QUESTIONS", questions),
        ):
            if len(rows) > 300:
                coverage[key] = "PARTIAL"
                warnings.append(f"{key}_RESULTS_TRUNCATED")
        due: tuple[TodayReviewGroupDTO, ...] = ()
        unresolved: tuple[TodayReviewGroupDTO, ...] = ()
        try:
            today = self._today.get()
            due = tuple(
                g
                for g in today.groups
                if g.status == "ACTIVE"
                and (
                    g.priority == "DUE"
                    or any(r.due_at is not None and r.due_at <= now for r in g.reasons)
                )
            )
            unresolved = tuple(g for g in today.groups if g.status == "ACTIVE" and g not in due)
            coverage.update({f"TODAY_{key}": value for key, value in today.coverage.items()})
            warnings.extend(today.warnings)
            if today.unscoped:
                warnings.append("UNSCOPED_ACTIONS_REMAIN_IN_TODAY_REVIEW")
            if len(due) > 300 or len(unresolved) > 300:
                warnings.append("TODAY_GROUPS_TRUNCATED")
                coverage["TODAY"] = "PARTIAL"
            else:
                coverage["TODAY"] = (
                    "COMPLETE"
                    if today.coverage
                    and all(
                        value in {"COMPLETE", "NOT_APPLICABLE"} for value in today.coverage.values()
                    )
                    else "PARTIAL"
                )
        except Exception:
            coverage["TODAY"] = "UNAVAILABLE"
            warnings.append("TODAY_SOURCE_UNAVAILABLE")
        return WeeklyReviewDTO(
            as_of=now,
            week_start=start,
            week_end=end,
            timezone=self._timezone,
            confirmed_views=tuple(views[:300]),
            decisions=tuple(decisions[:300]),
            open_questions=tuple(questions[:300]),
            due_groups=due[:300],
            unresolved_groups=unresolved[:300],
            coverage=coverage,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _views(
        self, subject: ResearchSubject, start: datetime, now: datetime, warnings: list[str]
    ) -> list[WeeklyConfirmedViewDTO]:
        result = []
        with self._uow() as uow:
            for thesis in uow.theses.list_by_subject(subject.subject_id):
                if thesis.subject_id != subject.subject_id:
                    continue
                revisions = [
                    r
                    for r in uow.revisions.list_by_thesis(thesis.thesis_id)
                    if r.subject_id == subject.subject_id
                    and r.thesis_id == thesis.thesis_id
                    and r.confirmed_at <= now
                ]
                by_number = {r.revision_no: r for r in revisions}
                for revision in revisions:
                    if revision.confirmed_at < start:
                        continue
                    previous = (
                        by_number.get(revision.supersedes_revision_no)
                        if revision.supersedes_revision_no is not None
                        else None
                    )
                    if previous is not None and previous.confirmed_at > revision.confirmed_at:
                        previous = None
                    fields = tuple(
                        field
                        for field in ("statement", "rationale", "rating", "confidence_band")
                        if previous is not None
                        and getattr(previous, field) != getattr(revision, field)
                    )
                    comparison_available = previous is not None
                    if previous is not None:
                        try:
                            for label, repository, attributes in (
                                (
                                    "assumptions",
                                    uow.assumptions,
                                    ("statement", "basis", "falsifiability"),
                                ),
                                (
                                    "invalidations",
                                    uow.invalidations,
                                    ("description", "observable", "severity"),
                                ),
                            ):
                                definitions = []
                                for exact in (previous, revision):
                                    rows = repository.list_by_revision(
                                        thesis.thesis_id, exact.revision_no
                                    )
                                    if any(
                                        row.subject_id != subject.subject_id
                                        or row.thesis_id != thesis.thesis_id
                                        or row.revision_no != exact.revision_no
                                        or row.confirmed_at > exact.confirmed_at
                                        for row in rows
                                    ):
                                        raise ValueError("Invalid definition scope")
                                    definitions.append(
                                        sorted(
                                            tuple(str(getattr(row, field)) for field in attributes)
                                            for row in rows
                                        )
                                    )
                                if definitions[0] != definitions[1]:
                                    fields += (label,)
                        except Exception:
                            comparison_available = False
                            warnings.append(
                                f"REVISION_DEFINITIONS_UNAVAILABLE:{revision.revision_id}"
                            )
                    if previous is None and revision.supersedes_revision_no is not None:
                        warnings.append(f"PREVIOUS_REVISION_UNAVAILABLE:{revision.revision_id}")
                    result.append(
                        WeeklyConfirmedViewDTO(
                            subject_id=subject.subject_id,
                            subject_title=subject.title,
                            thesis_id=thesis.thesis_id,
                            revision_id=revision.revision_id,
                            revision_no=revision.revision_no,
                            confirmed_at=revision.confirmed_at,
                            kind="INITIAL"
                            if revision.supersedes_revision_no is None
                            else "COMPARISON_UNAVAILABLE"
                            if not comparison_available
                            else "RECONFIRMED"
                            if not fields
                            else "CHANGED",
                            before_statement=previous.statement if previous else None,
                            after_statement=revision.statement,
                            changed_fields=fields,
                            href=_href(subject.subject_id, revision_id=revision.revision_id),
                        )
                    )
        return result

    def _decisions(
        self, subject: ResearchSubject, start: datetime, now: datetime
    ) -> list[WeeklyDecisionDTO]:
        with self._uow() as uow:
            return [
                WeeklyDecisionDTO(
                    subject_id=subject.subject_id,
                    subject_title=subject.title,
                    decision_id=d.decision_id,
                    decision_type=d.decision_type.value,
                    title=d.title,
                    rationale=d.rationale,
                    recorded_at=d.recorded_at,
                    review_due_at=d.review_due_at,
                    href=_href(subject.subject_id, decision_id=d.decision_id),
                )
                for d in uow.decisions.list_by_subject(subject.subject_id, as_of=now)
                if d.subject_id == subject.subject_id
                and d.decided_by == "user"
                and start <= d.recorded_at <= now
            ]

    def _questions(self, subject: ResearchSubject, now: datetime) -> list[WeeklyQuestionDTO]:
        with self._uow() as uow:
            return [
                WeeklyQuestionDTO(
                    subject_id=subject.subject_id,
                    question_id=q.question_id,
                    text=q.text,
                    asked_at=q.asked_at,
                    status=q.status.value,
                    href=_href(subject.subject_id, question_id=q.question_id),
                )
                for q in uow.questions.list_by_subject(subject.subject_id)
                if q.subject_id == subject.subject_id
                and q.asked_at <= now
                and q.status.value.upper() in {"OPEN", "STALE"}
            ]


def _href(subject_id: str, **references: str) -> str:
    return "/research?" + urlencode(
        {"subject_id": subject_id, "section": "quick-review", **references}
    )
