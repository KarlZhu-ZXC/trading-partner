"""Group durable reminders without acknowledging or mutating their source records."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from application.dto.today_review import (
    TodayReviewDTO,
    TodayReviewGroupDTO,
    TodayReviewReasonDTO,
    TodayReviewSubjectDTO,
)
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.monitor_repository import MonitorRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.review_item_repository import ReviewItemRepository
from domain.monitoring.models import MonitorEvent
from domain.research.models import DecisionRecord, ResearchSubject


@dataclass(frozen=True)
class _Entry:
    reason: TodayReviewReasonDTO
    deferred: bool = False
    excluded: bool = False


def _href(subject_id: str) -> str:
    return f"/research?section=quick-review#subject-{quote(subject_id, safe='')}"


def _safe_href(value: str) -> str:
    if (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not any(ord(char) < 32 for char in value)
    ):
        parsed = urlsplit(value)
        if not parsed.scheme and not parsed.netloc:
            return value
    return "/decision-workbench#reviews"


def _reason(
    kind: str,
    identity: str,
    subject: str | None,
    title: str,
    detail: str,
    occurred: datetime,
    due: datetime | None,
    href: str,
    severity: str = "ATTENTION",
) -> TodayReviewReasonDTO:
    return TodayReviewReasonDTO(
        reason_id=f"{kind}:{identity}",
        source_type=kind,
        source_id=identity,
        subject_id=subject,
        title=title,
        detail=detail,
        occurred_at=occurred,
        due_at=due,
        href=_safe_href(href),
        severity=severity,
    )


class TodayReviewService:
    def __init__(
        self,
        research_uow_factory: Callable[[], ResearchUnitOfWork],
        review_items: ReviewItemRepository,
        observation_reviews: ExternalNoteReviewRepository,
        notes: ExternalNoteRepository,
        monitors: MonitorRepository,
        agenda: CatalystAgendaRepository,
        clock: Clock,
        timezone: str,
    ) -> None:
        ZoneInfo(timezone)
        self._uow = research_uow_factory
        self._items = review_items
        self._reviews = observation_reviews
        self._notes = notes
        self._monitors = monitors
        self._agenda = agenda
        self._clock = clock
        self._timezone = timezone

    def get(self) -> TodayReviewDTO:
        now = self._clock.now()
        subjects: dict[str, ResearchSubject] = {}
        decisions: dict[str, DecisionRecord] = {}
        coverage: dict[str, str] = {}
        warnings: list[str] = []
        entries: dict[str, _Entry] = {}
        try:
            with self._uow() as uow:
                for offset in (0, 200, 400):
                    page = uow.subjects.list(limit=min(200, 500 - offset), offset=offset)
                    subjects.update((s.subject_id, s) for s in page)
                    if len(page) < min(200, 500 - offset):
                        break
                coverage["SUBJECTS"] = "PARTIAL" if len(subjects) >= 500 else "COMPLETE"
                if len(subjects) >= 500:
                    warnings.append("SUBJECT_HISTORY_BOUNDED")
                for subject_id in subjects:
                    subject_decisions = [
                        d
                        for d in uow.decisions.list_by_subject(subject_id, as_of=now)
                        if d.subject_id == subject_id
                        and d.decided_by == "user"
                        and d.recorded_at <= now
                    ]
                    if subject_decisions:
                        decisions[subject_id] = max(
                            subject_decisions, key=lambda d: (d.recorded_at, d.decision_id)
                        )
            coverage["DECISIONS"] = coverage["SUBJECTS"]
        except Exception:
            coverage["SUBJECTS"] = coverage["DECISIONS"] = "UNAVAILABLE"
            warnings.append("RESEARCH_SOURCE_UNAVAILABLE")
            decisions.clear()
        for decision in decisions.values():
            if decision.review_due_at is not None:
                entries[f"DECISION_REVIEW_DUE:{decision.decision_id}"] = _Entry(
                    _reason(
                        "DECISION_REVIEW_DUE",
                        decision.decision_id,
                        decision.subject_id,
                        "Reviewed Decision follow-up",
                        decision.title,
                        decision.recorded_at,
                        decision.review_due_at,
                        _href(decision.subject_id),
                    ),
                    decision.review_due_at > now,
                )
        # Materialized reminders are read first; authoritative live-source projections
        # replace duplicate source identities without changing their persisted status.
        readers: tuple[tuple[str, Callable[[], tuple[list[_Entry], bool]]], ...] = (
            ("REVIEW_ITEMS", lambda: self._review_items(now)),
            (
                "OBSERVATIONS",
                lambda: self._observations(now, subjects, coverage.get("SUBJECTS") == "COMPLETE"),
            ),
            ("MONITORS", lambda: self._monitor_reasons(now)),
            ("NOTE_PROCESSING", lambda: self._note_failures(now, subjects)),
            ("AGENDA", lambda: self._agenda_reasons(now)),
        )
        for name, reader in readers:
            try:
                values, bounded = reader()
                for value in values:
                    if value.excluded:
                        entries.pop(value.reason.reason_id, None)
                    else:
                        previous = entries.get(value.reason.reason_id)
                        if previous is not None and (
                            value.reason.source_type == "DECISION_REVIEW_DUE"
                            or previous.reason.severity == "ERROR"
                        ):
                            continue
                        entries[value.reason.reason_id] = value
                coverage[name] = "PARTIAL" if bounded else "COMPLETE"
                if bounded:
                    warnings.append(f"{name}_HISTORY_BOUNDED")
            except Exception:
                coverage[name] = "UNAVAILABLE"
                warnings.append(f"{name}_SOURCE_UNAVAILABLE")
        current_decisions = {d.decision_id for d in decisions.values()}
        entries = {
            key: entry
            for key, entry in entries.items()
            if coverage.get("DECISIONS") != "COMPLETE"
            or entry.reason.source_type != "DECISION_REVIEW_DUE"
            or entry.reason.source_id in current_decisions
        }
        grouped: dict[str, list[_Entry]] = {}
        unscoped = []
        for entry in entries.values():
            reason = entry.reason
            if reason.subject_id not in subjects or reason.source_type in {
                "BROKER_ORDER_INTENT",
                "AGENT_PENDING_ACTION",
            }:
                unscoped.append(reason)
                continue
            subject = subjects[reason.subject_id]
            key = subject.primary_instrument_id or f"subject:{subject.subject_id}"
            grouped.setdefault(key, []).append(entry)
        groups = []
        for key, values in grouped.items():
            refs = sorted({e.reason.subject_id for e in values if e.reason.subject_id})
            statuses = []
            for entry in values:
                r = entry.reason
                reviewed_decision = decisions.get(r.subject_id or "")
                if r.severity == "ERROR":
                    statuses.append("ACTIVE")
                elif entry.deferred and r.due_at is not None and r.due_at > now:
                    statuses.append("DEFERRED")
                elif r.source_type == "DECISION_REVIEW_DUE":
                    statuses.append(
                        "ACTIVE" if r.due_at is not None and r.due_at <= now else "DEFERRED"
                    )
                elif (
                    reviewed_decision is not None and reviewed_decision.recorded_at >= r.occurred_at
                ):
                    statuses.append("REVIEWED")
                else:
                    statuses.append("ACTIVE")
            state: Literal["ACTIVE", "DEFERRED", "REVIEWED"] = (
                "ACTIVE"
                if "ACTIVE" in statuses
                else "DEFERRED"
                if "DEFERRED" in statuses
                else "REVIEWED"
            )
            priority: Literal["ERROR", "DUE", "ATTENTION"] = (
                "ERROR"
                if any(e.reason.severity == "ERROR" for e in values)
                else "DUE"
                if any(e.reason.due_at is not None and e.reason.due_at <= now for e in values)
                else "ATTENTION"
            )
            due = [
                decisions[s].review_due_at
                for s in refs
                if s in decisions and decisions[s].review_due_at is not None
            ]
            groups.append(
                TodayReviewGroupDTO(
                    group_id=key,
                    instrument_id=subjects[refs[0]].primary_instrument_id,
                    title=(
                        (subjects[refs[0]].primary_instrument_id or "").rsplit(":", 1)[-1]
                        if subjects[refs[0]].primary_instrument_id
                        else subjects[refs[0]].title
                    ),
                    status=state,
                    priority=priority,
                    subjects=tuple(
                        TodayReviewSubjectDTO(subject_id=s, title=subjects[s].title, href=_href(s))
                        for s in refs
                    ),
                    reasons=tuple(
                        sorted(
                            (e.reason for e in values),
                            key=lambda r: (r.occurred_at, r.reason_id),
                            reverse=True,
                        )
                    ),
                    review_due_at=min(d for d in due if d is not None) if due else None,
                )
            )
        rank = {"ACTIVE": 0, "DEFERRED": 1, "REVIEWED": 2}
        priority_rank = {"ERROR": 0, "DUE": 1, "ATTENTION": 2}
        groups.sort(key=lambda g: (rank[g.status], priority_rank[g.priority], g.group_id))
        return TodayReviewDTO(
            as_of=now,
            timezone=self._timezone,
            groups=tuple(groups),
            unscoped=tuple(sorted(unscoped, key=lambda r: (r.severity != "ERROR", r.reason_id))),
            coverage=coverage,
            warnings=tuple(warnings),
        )

    def _review_items(self, now: datetime) -> tuple[list[_Entry], bool]:
        rows = self._items.list(limit=1000)
        result = []
        for row in rows:
            if (
                not row.active_at_source
                or row.status.value in {"RESOLVED", "AUTO_RESOLVED"}
                or row.first_seen_at > now
            ):
                continue
            result.append(
                _Entry(
                    _reason(
                        row.source_type.value,
                        row.source_ref,
                        row.subject_id,
                        row.title,
                        row.detail,
                        row.first_seen_at,
                        row.due_at,
                        row.href,
                        row.severity.value,
                    ),
                    row.due_at is not None and row.due_at > now,
                )
            )
        return result, len(rows) >= 1000

    def _observations(
        self, now: datetime, subjects: dict[str, ResearchSubject], allow_instrument_mapping: bool
    ) -> tuple[list[_Entry], bool]:
        rows = self._reviews.list_latest(limit=1000)
        result = []
        for row in rows:
            if row.created_at > now:
                continue
            if row.status.value not in {"PENDING", "DEFERRED"}:
                result.append(
                    _Entry(
                        _reason(
                            "OBSERVATION_REVIEW_DUE",
                            row.note_revision_id,
                            row.subject_id,
                            "Reviewed Observation",
                            "Source review resolved.",
                            row.created_at,
                            None,
                            "/decision-workbench#notes",
                        ),
                        excluded=True,
                    )
                )
                continue
            revision = self._notes.revision_by_id(row.note_revision_id)
            identity = self._notes.get(row.note_id)
            if revision is None or identity is None or revision.observed_at > now:
                continue
            subject_id = row.subject_id
            if (
                allow_instrument_mapping
                and subject_id is None
                and identity.primary_instrument_id is not None
            ):
                matches = [
                    s.subject_id
                    for s in subjects.values()
                    if s.primary_instrument_id == identity.primary_instrument_id
                ]
                if len(matches) == 1:
                    subject_id = matches[0]
            if (
                subject_id in subjects
                and subjects[subject_id].primary_instrument_id != identity.primary_instrument_id
            ):
                subject_id = None
            result.append(
                _Entry(
                    _reason(
                        "OBSERVATION_REVIEW_DUE",
                        revision.note_revision_id,
                        subject_id,
                        revision.title,
                        "Observation awaiting explicit review.",
                        revision.observed_at,
                        row.due_at,
                        _href(subject_id) if subject_id else "/decision-workbench#notes",
                    ),
                    row.status.value == "DEFERRED" and row.due_at is not None and row.due_at > now,
                )
            )
        return result, len(rows) >= 1000

    def _note_failures(
        self,
        now: datetime,
        subjects: dict[str, ResearchSubject],
    ) -> tuple[list[_Entry], bool]:
        rows = self._notes.list_latest(limit=500)
        result = []
        for identity, revision in rows:
            if revision.observed_at > now or str(revision.coverage) != "FULL":
                continue
            interpretation = self._notes.interpretation_for_revision(revision.note_revision_id)
            if (
                interpretation is None
                or interpretation.status != "FAILED"
                or interpretation.created_at > now
            ):
                continue
            review = self._reviews.latest_for_revision(revision.note_revision_id)
            subject_id = review.subject_id if review else None
            if subject_id not in subjects:
                matches = [
                    s.subject_id
                    for s in subjects.values()
                    if s.primary_instrument_id
                    and s.primary_instrument_id == identity.primary_instrument_id
                ]
                subject_id = matches[0] if len(matches) == 1 and len(subjects) < 500 else None
            result.append(
                _Entry(
                    _reason(
                        "NOTE_PROCESSING",
                        revision.note_revision_id,
                        subject_id,
                        "Note interpretation failed",
                        "Note saved; interpretation failed. Open the note to retry failed stages.",
                        interpretation.created_at,
                        None,
                        _href(subject_id) if subject_id else "/decision-workbench#notes",
                        "ERROR",
                    )
                )
            )
        return result, len(rows) >= 500

    def _agenda_reasons(self, now: datetime) -> tuple[list[_Entry], bool]:
        rows = self._agenda.list_visible(as_of=now)
        result = []
        for row in rows[:1000]:
            if row.recorded_at > now:
                continue
            if row.status.value in {"CANCELLED", "SUPERSEDED"}:
                result.append(
                    _Entry(
                        _reason(
                            "CATALYST_AGENDA",
                            row.agenda_item_id,
                            row.subject_id,
                            row.title,
                            "Agenda item inactive.",
                            row.recorded_at,
                            None,
                            "/agenda",
                        ),
                        excluded=True,
                    )
                )
                continue
            if row.status.value == "UPCOMING" and (
                row.window_start is None or row.window_start > now
            ):
                continue
            due = row.window_start if row.status.value == "UPCOMING" else None
            result.append(
                _Entry(
                    _reason(
                        "CATALYST_AGENDA",
                        row.agenda_item_id,
                        row.subject_id,
                        row.title,
                        row.outcome_note or row.expected_question or "Agenda event needs review.",
                        row.recorded_at,
                        due,
                        "/agenda",
                    )
                )
            )
        return result, len(rows) >= 1000

    def _monitor_reasons(self, now: datetime) -> tuple[list[_Entry], bool]:
        monitors = self._monitors.list_current()
        result = []
        bounded = len(monitors) >= 500
        for monitor in monitors[:500]:
            if (
                monitor.status.value != "ACTIVE"
                or monitor.created_at > now
                or (monitor.valid_until is not None and monitor.valid_until <= now)
            ):
                continue
            events = self._monitors.list_events_for_monitor_version(
                monitor.monitor_id, monitor.version, limit=100
            )
            bounded |= len(events) >= 100
            latest: dict[str, MonitorEvent] = {}
            for event in sorted(events, key=lambda e: (e.created_at, e.event_id)):
                if event.created_at <= now and event.monitor_version == monitor.version:
                    previous = latest.get(event.rule_code)
                    if previous is None or previous.event_type != event.event_type:
                        latest[event.rule_code] = event
            states = {
                r.rule_code: r
                for r in self._monitors.get_rule_states(monitor.monitor_id)
                if r.monitor_version == monitor.version and r.updated_at <= now
            }
            judgment = self._monitors.latest_judgment(monitor.monitor_id)
            for event in latest.values():
                state = states.get(event.rule_code)
                if (
                    state
                    and state.updated_at >= event.created_at
                    and (
                        (event.event_type.value == "TRIGGERED" and state.state.value != "TRIGGERED")
                        or (
                            event.event_type.value == "NOT_EVALUATED"
                            and state.state.value != "NOT_EVALUATED"
                        )
                    )
                ):
                    continue
                if (
                    event.event_type.value == "JUDGMENT_UNAVAILABLE"
                    and judgment is not None
                    and judgment.monitor_version == monitor.version
                    and judgment.status == "SUCCEEDED"
                    and event.created_at <= judgment.created_at <= now
                ):
                    continue
                if event.event_type.value == "RECOVERED":
                    continue
                resolution = self._monitors.latest_resolution(event.event_id)
                if (
                    resolution is not None
                    and resolution.created_at <= now
                    and resolution.action.value == "RESOLVE"
                ):
                    continue
                result.append(
                    _Entry(
                        _reason(
                            "MONITOR",
                            event.event_id,
                            monitor.subject_id,
                            monitor.name,
                            event.message,
                            event.created_at,
                            None,
                            f"/monitors#monitor-{quote(monitor.monitor_id, safe='')}",
                            "ERROR"
                            if event.event_type.value in {"NOT_EVALUATED", "JUDGMENT_UNAVAILABLE"}
                            else "ATTENTION",
                        )
                    )
                )
        return result, bounded
