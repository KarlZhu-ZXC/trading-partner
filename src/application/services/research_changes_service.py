"""Read-only projection; each source fails independently without hiding its failure."""

from bisect import bisect_left
from collections.abc import Callable
from datetime import datetime
from functools import cache
from typing import Literal

from application.dto.research_changes import (
    BaselinePlanDTO,
    BaselineThesisDTO,
    ResearchChangeDTO,
    ResearchChangesBaselineDTO,
    ResearchChangesDTO,
)
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.monitor_repository import MonitorRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.catalyst_agenda.models import CatalystAgendaVersion
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import ExternalNoteRevision
from domain.monitoring.enums import MonitorRuleType
from domain.monitoring.models import MonitorDefinition, MonitorEvent, MonitorRunObservation
from domain.research.models import DecisionRecord
from domain.trade_plan.enums import TradePlanConditionMode
from domain.trade_plan.models import TradePlan


class _SourceRows(list[ResearchChangeDTO]):
    partial: bool = False


class ResearchChangesService:
    def __init__(
        self,
        notes: ExternalNoteRepository,
        research_uow_factory: Callable[[], ResearchUnitOfWork],
        monitors: MonitorRepository,
        agenda: CatalystAgendaRepository,
        clock: Clock,
    ) -> None:
        self._notes = notes
        self._uow = research_uow_factory
        self._monitors = monitors
        self._agenda = agenda
        self._clock = clock

    def get(
        self,
        subject_id: str,
        *,
        baseline_decision_id: str | None = None,
        change_id: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> ResearchChangesDTO:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise DataContractError("Invalid changes pagination")
        now = self._clock.now()
        require_aware_datetime(now, field_name="as_of")
        with self._uow() as uow:
            subject = uow.subjects.get(subject_id)
            if subject is None:
                raise DataContractError("Research Subject not found")
            decisions = uow.decisions.list_by_subject(subject_id)
            eligible = [
                d
                for d in decisions
                if d.subject_id == subject_id and d.decided_by == "user" and d.recorded_at <= now
            ]
            if baseline_decision_id == "none":
                eligible = []
            elif baseline_decision_id is not None:
                eligible = [d for d in eligible if d.decision_id == baseline_decision_id]
                if not eligible:
                    raise DataContractError("Reviewed baseline does not belong to this Subject")
            decision = max(eligible, key=lambda d: (d.recorded_at, d.decision_id), default=None)
            baseline = self._baseline(uow, decision) if decision is not None else None
        after = baseline.recorded_at if baseline else None
        coverage: dict[str, Literal["COMPLETE", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"]] = {}
        warnings = [] if baseline else ["NO_REVIEWED_BASELINE"]
        items: list[ResearchChangeDTO] = []
        sources: list[tuple[str, Callable[[], list[ResearchChangeDTO]]]] = [
            ("OBSERVATION", lambda: self._observations(subject.primary_instrument_id, after, now)),
            ("MONITOR", lambda: self._monitor_changes(subject_id, after, now)),
            (
                "AGENDA",
                lambda: self._agenda_changes(subject_id, after, now, subject.primary_instrument_id),
            ),
        ]
        for name, read in sources:
            if name == "OBSERVATION" and subject.primary_instrument_id is None:
                coverage[name] = "NOT_APPLICABLE"
                continue
            try:
                source_items = read()
            except Exception:
                # Never expose private payloads or exception text in closed diagnostics.
                coverage[name] = "UNAVAILABLE"
                warnings.append(f"{name}_SOURCE_UNAVAILABLE")
            else:
                coverage[name] = (
                    "PARTIAL"
                    if isinstance(source_items, _SourceRows) and source_items.partial
                    else "COMPLETE"
                )
                if coverage[name] == "PARTIAL":
                    warnings.append(f"{name}_HISTORY_PARTIAL")
                items.extend(source_items)
        unique = {item.change_id: item for item in items}
        ordered = sorted(unique.values(), key=lambda i: (i.recorded_at, i.change_id), reverse=True)
        if change_id is not None:
            selected_index = next(
                (index for index, item in enumerate(ordered) if item.change_id == change_id),
                None,
            )
            if selected_index is None:
                warnings.append("SELECTED_CHANGE_UNAVAILABLE")
            else:
                offset = (selected_index // limit) * limit
        return ResearchChangesDTO(
            subject_id=subject_id,
            as_of=now,
            baseline=baseline,
            coverage=coverage,
            warning_codes=warnings,
            total=len(ordered),
            offset=offset,
            limit=limit,
            has_more=offset + limit < len(ordered),
            items=ordered[offset : offset + limit],
        )

    @staticmethod
    def _baseline(uow: ResearchUnitOfWork, decision: DecisionRecord) -> ResearchChangesBaselineDTO:
        theses = []
        for revision_id in decision.thesis_revision_ids:
            revision = uow.revisions.get(revision_id)
            if (
                revision.subject_id != decision.subject_id
                or revision.confirmed_at > decision.recorded_at
            ):
                raise DataContractError("Invalid reviewed Thesis revision")
            assumptions = uow.assumptions.list_by_revision(revision.thesis_id, revision.revision_no)
            invalidations = uow.invalidations.list_by_revision(
                revision.thesis_id, revision.revision_no
            )
            if any(
                x.subject_id != decision.subject_id
                or x.thesis_id != revision.thesis_id
                or x.revision_no != revision.revision_no
                for x in assumptions
            ) or any(
                x.subject_id != decision.subject_id
                or x.thesis_id != revision.thesis_id
                or x.revision_no != revision.revision_no
                for x in invalidations
            ):
                raise DataContractError("Invalid reviewed Thesis references")
            theses.append(
                BaselineThesisDTO(
                    thesis_id=revision.thesis_id,
                    revision_id=revision.revision_id,
                    statement=revision.statement,
                    assumptions=[
                        {"assumption_id": x.assumption_id, "statement": x.statement}
                        for x in assumptions
                    ],
                    invalidations=[
                        {"invalidation_id": x.invalidation_id, "description": x.description}
                        for x in invalidations
                    ],
                )
            )
        plan_dto = None
        if decision.trade_plan_id is not None and decision.trade_plan_version is not None:
            plan = uow.trade_plans.get_version(decision.trade_plan_id, decision.trade_plan_version)
            if (
                plan is None
                or plan.subject_id != decision.subject_id
                or plan.created_at > decision.recorded_at
            ):
                raise DataContractError("Invalid reviewed Trade Plan version")
            plan_dto = BaselinePlanDTO(plan_id=plan.plan_id, version=plan.version)
        return ResearchChangesBaselineDTO(
            decision_id=decision.decision_id,
            title=decision.title,
            rationale=decision.rationale,
            external_note_revision_id=decision.external_note_revision_id,
            recorded_at=decision.recorded_at,
            decided_at=decision.decided_at,
            theses=theses,
            plan=plan_dto,
        )

    def _observations(
        self, instrument_id: str | None, after: datetime | None, now: datetime
    ) -> list[ResearchChangeDTO]:
        if instrument_id is None:
            return []
        result = []
        for revision in self._notes.list_revisions_for_instrument(
            instrument_id, observed_after=after, observed_through=now
        ):
            identity = self._notes.get(revision.note_id)
            if identity is None or identity.primary_instrument_id != instrument_id:
                raise DataContractError("Invalid Observation identity scope")
            previous = self._notes.previous_revision(revision.note_id, revision.version)
            if previous is not None and (
                previous.note_id != revision.note_id
                or previous.version >= revision.version
                or previous.observed_at > revision.observed_at
            ):
                raise DataContractError("Invalid Observation predecessor")
            result.append(
                ResearchChangeDTO(
                    change_id=f"observation:{revision.note_revision_id}",
                    kind="OBSERVATION",
                    title=revision.title,
                    occurred_at=revision.source_timestamp or revision.observed_at,
                    recorded_at=revision.observed_at,
                    instrument_id=instrument_id,
                    source_id=revision.note_id,
                    source_version=str(revision.version),
                    previous_occurred_at=(previous.source_timestamp or previous.observed_at)
                    if previous
                    else None,
                    previous_source_id=previous.note_revision_id if previous else None,
                    source_names=[identity.source],
                    old_value=_user_excerpt(previous),
                    new_value=_user_excerpt(revision),
                    relation="SUBJECT_SCOPE",
                    relation_detail="Same Instrument; no inferred Thesis link",
                    note_revision_id=revision.note_revision_id,
                    warning_codes=(
                        ["SUMMARY_ONLY_METADATA"]
                        if revision.coverage is not NoteCoverage.FULL
                        else ["USER_EXCERPT_ONLY"]
                    ),
                )
            )
        return result

    def _monitor_changes(
        self, subject_id: str, after: datetime | None, now: datetime
    ) -> list[ResearchChangeDTO]:
        result = _SourceRows()
        with self._uow() as uow:

            @cache
            def get_monitor(monitor_id: str, version: int) -> MonitorDefinition:
                value = self._monitors.get_version(monitor_id, version)
                if value is None:
                    raise DataContractError("Missing immutable Monitor version")
                return value

            @cache
            def thesis_subject(thesis_id: str) -> str:
                return uow.theses.get(thesis_id).subject_id

            @cache
            def get_plan(plan_id: str, version: int) -> TradePlan | None:
                value = uow.trade_plans.get_version(plan_id, version)
                if value is None or value.subject_id != subject_id:
                    return None
                return value if thesis_subject(value.thesis_id) == subject_id else None

            for current in self._monitors.list_current():
                historical_versions = tuple(
                    get_monitor(current.monitor_id, version)
                    for version in range(1, current.version + 1)
                )
                if not any(version.subject_id == subject_id for version in historical_versions):
                    continue
                events = tuple(
                    event
                    for event in self._monitors.list_events(current.monitor_id, limit=None)
                    if get_monitor(event.monitor_id, event.monitor_version).subject_id == subject_id
                )
                events_by_rule: dict[tuple[int, str], list[MonitorEvent]] = {}
                for event in sorted(events, key=lambda item: item.created_at):
                    events_by_rule.setdefault((event.monitor_version, event.rule_code), []).append(
                        event
                    )
                event_times = {
                    key: [event.created_at for event in values]
                    for key, values in events_by_rule.items()
                }
                runs = sorted(
                    self._monitors.list_runs(current.monitor_id, limit=None),
                    key=lambda run: (run.completed_at, run.run_id),
                )
                prior: dict[tuple[int, str], MonitorRunObservation] = {}
                represented: set[str] = set()
                for run in runs:
                    if run.completed_at > now:
                        continue
                    scoped_observations = tuple(
                        observation
                        for observation in run.observations
                        if observation.monitor_id == current.monitor_id
                    )
                    if not run.observation_history_complete or not scoped_observations:
                        effective = max(
                            (
                                version
                                for version in historical_versions
                                if version.created_at <= run.started_at
                            ),
                            key=lambda version: (version.created_at, version.version),
                            default=None,
                        )
                        if effective is None:
                            raise DataContractError("Monitor run precedes its definition history")
                        if (
                            effective.subject_id == subject_id
                            and (after is None or run.completed_at > after)
                            and (not run.observation_history_complete or effective.rules)
                        ):
                            result.partial = True
                        continue
                    for observation in run.observations:
                        if (
                            observation.monitor_id != current.monitor_id
                            or get_monitor(
                                observation.monitor_id, observation.monitor_version
                            ).subject_id
                            != subject_id
                        ):
                            continue
                        key = (observation.monitor_version, observation.rule_code)
                        previous = prior.get(key)
                        prior[key] = observation
                        candidates = events_by_rule.get(key, [])
                        event_index = bisect_left(event_times.get(key, []), run.started_at)
                        matched = (
                            candidates[event_index]
                            if event_index < len(candidates)
                            and candidates[event_index].created_at <= run.completed_at
                            else None
                        )
                        if matched:
                            represented.add(matched.event_id)
                        if after is not None and run.completed_at <= after:
                            continue
                        if previous is not None and (
                            previous.observed_value,
                            previous.state,
                            previous.error_codes,
                            previous.warning_codes,
                        ) == (
                            observation.observed_value,
                            observation.state,
                            observation.error_codes,
                            observation.warning_codes,
                        ):
                            continue
                        row = self._monitor_row(
                            get_monitor,
                            get_plan,
                            subject_id,
                            current.monitor_id,
                            observation.monitor_version,
                            observation.rule_code,
                            f"{run.run_id}:{observation.rule_code}",
                            run.completed_at,
                            observation.fact_as_of,
                            _observation_value(previous),
                            _observation_value(observation),
                            matched.event_id if matched else None,
                            list(observation.warning_codes)
                            + list(observation.error_codes)
                            + (["PREVIOUS_VALUE_UNAVAILABLE"] if previous is None else []),
                            previous_fact_at=previous.fact_as_of if previous else None,
                            previous_source_id=(
                                f"{previous.run_id}:{previous.rule_code}" if previous else None
                            ),
                        )
                        result.append(row)
                for event in events:
                    if (
                        event.event_id in represented
                        or event.created_at > now
                        or (after is not None and event.created_at <= after)
                    ):
                        continue
                    result.partial = True
                    result.append(
                        self._monitor_row(
                            get_monitor,
                            get_plan,
                            subject_id,
                            event.monitor_id,
                            event.monitor_version,
                            event.rule_code,
                            event.event_id,
                            event.created_at,
                            event.fact_as_of,
                            None,
                            str(event.observed_value)
                            if event.observed_value is not None
                            else event.event_type.value,
                            event.event_id,
                            ["MONITOR_EVENTS_ONLY", "PREVIOUS_VALUE_UNAVAILABLE"],
                        )
                    )
        return result

    def _monitor_row(
        self,
        get_monitor: Callable[[str, int], MonitorDefinition],
        get_plan: Callable[[str, int], TradePlan | None],
        subject_id: str,
        monitor_id: str,
        version: int,
        rule_code: str,
        source_id: str,
        recorded_at: datetime,
        fact_at: datetime | None,
        old: str | None,
        new: str | None,
        event_id: str | None,
        warnings: list[str],
        *,
        previous_fact_at: datetime | None = None,
        previous_source_id: str | None = None,
    ) -> ResearchChangeDTO:
        monitor = get_monitor(monitor_id, version)
        if monitor is None or monitor.subject_id != subject_id:
            raise DataContractError("Invalid Monitor event scope")
        rule = next((r for r in monitor.rules if r.rule_code == rule_code), None)
        relation: Literal["EXACT_PLAN", "SUBJECT_SCOPE", "UNLINKED"] = "UNLINKED"
        plan = None
        if monitor.trade_plan_id is not None and monitor.trade_plan_version is not None:
            candidate = get_plan(monitor.trade_plan_id, monitor.trade_plan_version)
            if (
                candidate is not None
                and candidate.subject_id == subject_id
                and rule is not None
                and any(
                    c.condition_code == rule_code
                    and c.mode is TradePlanConditionMode.MONITORABLE
                    and rule.rule_type is MonitorRuleType.FACT_COMPARISON
                    and rule.instrument_id == c.instrument_id
                    and rule.fact_type == c.fact_type
                    and rule.metric_key == c.metric_key
                    and rule.comparator == c.comparator
                    and rule.numeric_threshold == c.threshold
                    and rule.event_after == c.event_after
                    and rule.max_fact_age_seconds == c.max_fact_age_seconds
                    for c in candidate.conditions
                )
            ):
                plan = candidate
                relation = "EXACT_PLAN"
        return ResearchChangeDTO(
            change_id=f"monitor:{monitor_id}:{source_id}",
            kind="MONITOR",
            title=f"{monitor.name} · {rule_code}",
            occurred_at=fact_at or recorded_at,
            recorded_at=recorded_at,
            instrument_id=rule.instrument_id if rule else monitor.primary_instrument_id,
            source_id=source_id,
            source_version=str(version),
            previous_occurred_at=previous_fact_at,
            previous_source_id=previous_source_id,
            source_names=[],
            old_value=old,
            new_value=new,
            relation=relation,
            relation_detail=(
                "Exact Monitor version and Trade Plan condition"
                if plan
                else "No validated Trade Plan condition link"
            ),
            thesis_id=plan.thesis_id if plan else None,
            plan_id=plan.plan_id if plan else None,
            plan_version=plan.version if plan else None,
            monitor_id=monitor_id,
            event_id=event_id,
            # Durable Monitor observations currently retain fact times and diagnostics,
            # but no successful Provider/source attribution. Never infer from route config.
            warning_codes=[*warnings, "SOURCE_PROVENANCE_UNAVAILABLE"],
        )

    def _agenda_changes(
        self,
        subject_id: str,
        after: datetime | None,
        now: datetime,
        instrument_id: str | None = None,
    ) -> list[ResearchChangeDTO]:
        versions = [
            x
            for x in self._agenda.list_visible(as_of=now)
            if x.subject_id == subject_id
            or (
                x.subject_id is None
                and instrument_id is not None
                and x.instrument_id == instrument_id
            )
        ]
        versions.sort(key=lambda x: (x.recorded_at, x.version))
        previous: dict[str, CatalystAgendaVersion] = {}
        result = []
        for item in versions:
            prior = previous.get(item.agenda_item_id)
            previous[item.agenda_item_id] = item
            if after is not None and item.recorded_at <= after:
                continue
            result.append(
                ResearchChangeDTO(
                    change_id=f"agenda:{item.agenda_item_id}:{item.version}",
                    kind="AGENDA",
                    title=item.title,
                    occurred_at=item.outcome_occurred_at or item.source_visible_at,
                    recorded_at=item.recorded_at,
                    instrument_id=item.instrument_id,
                    source_id=item.agenda_item_id,
                    source_version=str(item.version),
                    previous_occurred_at=(prior.outcome_occurred_at or prior.source_visible_at)
                    if prior
                    else None,
                    previous_source_id=f"{prior.agenda_item_id}:{prior.version}" if prior else None,
                    source_names=[item.source_vendor],
                    old_value=_agenda_value(prior),
                    new_value=_agenda_value(item),
                    relation="SUBJECT_SCOPE",
                    relation_detail=(
                        "Explicit Subject scope; dates include certainty and timezone"
                        if item.subject_id == subject_id
                        else "Instrument-only catalyst; no inferred Thesis link; "
                        "dates include certainty and timezone"
                    ),
                    event_id=item.linked_event_id,
                    agenda_item_id=item.agenda_item_id,
                )
            )
        return result


def _user_excerpt(revision: ExternalNoteRevision | None) -> str | None:
    if revision is None or revision.coverage is not NoteCoverage.FULL:
        return None
    text = "\n".join(
        block.body
        for block in sorted(revision.blocks, key=lambda b: b.ordinal)
        if block.speaker_kind is NoteSpeakerKind.USER
    )
    return (text[:497] + "…" if len(text) > 500 else text) or None


def _agenda_value(item: CatalystAgendaVersion | None) -> str | None:
    if item is None:
        return None
    window = (
        f"{item.window_start.isoformat()} – {item.window_end.isoformat()}"
        if item.window_start and item.window_end
        else "Unknown date"
    )
    return (
        f"v{item.version} · {item.status.value} · {window} · "
        f"{item.date_certainty.value} · {item.timezone}"
    )


def _observation_value(value: MonitorRunObservation | None) -> str | None:
    if value is None:
        return None
    number = str(value.observed_value) if value.observed_value is not None else "Unavailable"
    return f"{number} · {value.state.value}"
