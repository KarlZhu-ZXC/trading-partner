from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine

from application.services.research_changes_service import ResearchChangesService
from domain.common.errors import DataContractError
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import (
    AttributedNoteBlock,
    ExternalNoteIdentity,
    ExternalNoteRevision,
)
from domain.monitoring.enums import MonitorRuleType
from domain.trade_plan.enums import TradePlanConditionMode
from infrastructure.persistence.external_note_repository import SqlAlchemyExternalNoteRepository

NOW = datetime(2026, 9, 14, tzinfo=UTC)
BEFORE = NOW - timedelta(days=2)


def decision(identity="d1", **updates):
    fields = dict(
        decision_id=identity,
        subject_id="s1",
        decided_by="user",
        recorded_at=BEFORE,
        decided_at=BEFORE,
        title="Reviewed",
        rationale="Original reviewed rationale",
        external_note_revision_id=None,
        thesis_revision_ids=(),
        trade_plan_id=None,
        trade_plan_version=None,
    )
    return NS(**(fields | updates))


def revision(version=1, **updates):
    fields = dict(
        note_revision_id=f"r{version}",
        note_id="n1",
        version=version,
        content_sha256="a" * 64,
        source_revision_key=f"key{version}",
        title="Synthetic note",
        summary="Private summary",
        full_body="Synthetic user and third-party text",
        coverage=NoteCoverage.FULL,
        source_timestamp=BEFORE - timedelta(days=30),
        observed_at=NOW - timedelta(hours=1),
        visibility="PRIVATE",
        related_provider_stock_ids=(),
        related_provider_codes=(),
        blocks=(
            AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", f"User text {version}"),
            AttributedNoteBlock(1, NoteSpeakerKind.NAMED_PERSON, "Other", "Third party"),
        ),
    )
    return ExternalNoteRevision(**(fields | updates))


def service(decisions=(), revisions=()):
    uow = NS(
        subjects=NS(get=lambda _: NS(primary_instrument_id="equity:US:TEST")),
        decisions=NS(list_by_subject=lambda _: decisions),
    )
    notes = NS(
        get=lambda _: NS(primary_instrument_id="equity:US:TEST", source="LOCAL"),
        list_revisions_for_instrument=lambda *a, **k: revisions,
        previous_revision=lambda *a: None,
    )
    monitors = NS(list_current=lambda: ())
    agenda = NS(list_visible=lambda **k: ())
    return (
        ResearchChangesService(
            notes, lambda: nullcontext(uow), monitors, agenda, NS(now=lambda: NOW)
        ),
        uow,
        notes,
        monitors,
        agenda,
    )


def test_baseline_uses_recorded_time_and_explicit_user_only():
    s, *_ = service(
        (
            decision(),
            decision("agent", decided_by="external_agent", recorded_at=NOW),
            decision(
                "backdated",
                decided_at=BEFORE - timedelta(days=50),
                recorded_at=NOW - timedelta(days=1),
            ),
        ),
        (revision(),),
    )
    result = s.get("s1")
    assert result.baseline.decision_id == "backdated"
    assert result.baseline.rationale == "Original reviewed rationale"
    assert result.items[0].occurred_at < result.baseline.decided_at + timedelta(days=60)
    assert result.items[0].recorded_at > result.baseline.recorded_at


@pytest.mark.parametrize("baseline", ["unknown", "wrong"])
def test_pinned_wrong_subject_or_unknown_baseline_rejected(baseline):
    s, *_ = service((decision("wrong", subject_id="s2"),))
    with pytest.raises(DataContractError):
        s.get("s1", baseline_decision_id=baseline)


def test_source_failure_preserves_successful_changes_and_deduplicates():
    s, _, _, monitors, _ = service((), (revision(), revision()))
    monitors.list_current = lambda: (_ for _ in ()).throw(RuntimeError("private detail"))
    result = s.get("s1")
    assert result.total == 1
    assert result.coverage["MONITOR"] == "UNAVAILABLE"
    assert "NO_REVIEWED_BASELINE" in result.warning_codes
    assert "private detail" not in result.model_dump_json()
    assert result.items[0].new_value == "User text 1"
    assert result.items[0].relation == "SUBJECT_SCOPE"


def test_summary_only_never_discloses_summary():
    s, *_ = service((), (revision(coverage=NoteCoverage.SUMMARY_ONLY, full_body=None),))
    row = s.get("s1").items[0]
    assert row.new_value is None
    assert "Private summary" not in row.model_dump_json()


def test_exact_previous_revision_excerpt_and_pagination():
    s, _, notes, _, _ = service((), (revision(2), revision(3)))
    notes.previous_revision = lambda note_id, before_version: revision(before_version - 1)
    result = s.get("s1", limit=1)
    assert result.total == 2 and result.has_more
    assert result.items[0].old_value == "User text 2"
    assert result.items[0].previous_source_id == "r2"
    assert result.items[0].previous_occurred_at == revision(2).source_timestamp
    assert result.items[0].source_names == ["LOCAL"]
    assert s.get("s1", offset=1, limit=1).items[0].note_revision_id == "r2"


@pytest.mark.parametrize("linked", [False, True])
def test_monitor_exact_plan_requires_condition_code_and_scope(linked):
    s, uow, _, monitors, _ = service()
    plan = NS(
        plan_id="p1",
        version=2,
        subject_id="s1",
        thesis_id="t1",
        conditions=[
            NS(
                condition_code="rule" if linked else "other",
                mode=TradePlanConditionMode.MONITORABLE,
                instrument_id="equity:US:TEST",
                fact_type="PRICE",
                metric_key="last",
                comparator="GT",
                threshold=10,
                event_after=None,
                max_fact_age_seconds=3600,
            )
        ],
    )
    monitor = NS(
        subject_id="s1",
        monitor_id="m1",
        name="Monitor",
        version=1,
        primary_instrument_id=None,
        rules=[
            NS(
                rule_code="rule",
                instrument_id="equity:US:TEST",
                rule_type=MonitorRuleType.FACT_COMPARISON,
                fact_type="PRICE",
                metric_key="last",
                comparator="GT",
                numeric_threshold=10,
                event_after=None,
                max_fact_age_seconds=3600,
            )
        ],
        trade_plan_id="p1",
        trade_plan_version=2,
    )
    monitors.list_current = lambda: (monitor,)
    monitors.get_version = lambda *a: monitor
    monitors.list_events = lambda *a, **k: (
        NS(
            event_id="e1",
            created_at=NOW,
            fact_as_of=NOW,
            monitor_id="m1",
            monitor_version=1,
            rule_code="rule",
            observed_value=12,
        ),
    )
    monitors.list_runs = lambda *a, **k: ()
    uow.trade_plans = NS(get_version=lambda *a: plan)
    uow.theses = NS(get=lambda _: NS(subject_id="s1"))
    result = s.get("s1")
    assert result.coverage["MONITOR"] == "PARTIAL"
    assert result.items[0].relation == ("EXACT_PLAN" if linked else "UNLINKED")
    assert result.items[0].old_value is None


def test_instrument_scoped_repository_has_no_global_cap(migrated_sqlite_url):
    engine = create_engine(migrated_sqlite_url)
    notes = SqlAlchemyExternalNoteRepository(engine)
    notes.append_identity(
        ExternalNoteIdentity("n1", "LOCAL", "e1", "Synthetic", "equity:US:TEST", BEFORE, NOW)
    )
    for version in range(1, 502):
        notes.append_revision(revision(version))
    assert len(notes.list_revisions_for_instrument("equity:US:TEST")) == 501
    assert notes.list_revisions_for_instrument("equity:US:OTHER") == ()
    assert notes.list_revisions_for_instrument("equity:US:TEST", observed_after=NOW) == ()
    engine.dispose()


def test_explicit_none_keeps_unreviewed_baseline_after_new_decision():
    s, *_ = service((decision(),))
    result = s.get("s1", baseline_decision_id="none")
    assert result.baseline is None
    assert "NO_REVIEWED_BASELINE" in result.warning_codes


def test_immutable_run_fact_change_survives_without_alert_and_skips_unchanged():
    from decimal import Decimal

    from domain.monitoring.enums import MonitorRuleStateValue

    s, _, _, monitors, _ = service((decision(),))
    monitor = NS(
        subject_id="s1",
        monitor_id="m1",
        name="Monitor",
        version=1,
        primary_instrument_id=None,
        rules=[
            NS(
                rule_code="rule",
                instrument_id="equity:US:TEST",
                rule_type=MonitorRuleType.FACT_COMPARISON,
                fact_type="PRICE",
                metric_key="last",
                comparator="GT",
                numeric_threshold=10,
                event_after=None,
                max_fact_age_seconds=3600,
            )
        ],
        trade_plan_id=None,
        trade_plan_version=None,
    )
    monitors.list_current = lambda: (monitor,)
    monitors.get_version = lambda *a: monitor
    monitors.list_events = lambda *a, **k: ()
    runs = []
    for index, value in enumerate((10, 11, 11)):
        stamp = BEFORE + timedelta(hours=index)
        observation = NS(
            run_id=f"run{index}",
            monitor_id="m1",
            monitor_version=1,
            rule_code="rule",
            observed_value=Decimal(value),
            state=MonitorRuleStateValue.QUIET,
            warning_codes=(),
            error_codes=(),
            fact_as_of=stamp,
        )
        runs.append(
            NS(
                run_id=f"run{index}",
                started_at=stamp,
                completed_at=stamp,
                observation_history_complete=True,
                observations=(observation,),
            )
        )
    monitors.list_runs = lambda *a, **k: tuple(runs)
    result = s.get("s1")
    assert result.coverage["MONITOR"] == "COMPLETE"
    assert result.total == 1
    assert result.items[0].old_value == "10 · QUIET"
    assert result.items[0].new_value == "11 · QUIET"
    assert result.items[0].event_id is None
    assert result.items[0].previous_source_id == "run0:rule"
    assert result.items[0].previous_occurred_at == BEFORE
    assert result.items[0].source_names == []
    assert "SOURCE_PROVENANCE_UNAVAILABLE" in result.items[0].warning_codes


def test_baseline_loads_exact_revision_not_current_thesis_status():
    s, uow, *_ = service((decision(thesis_revision_ids=("revision1",)),))
    uow.revisions = NS(
        get=lambda _: NS(
            subject_id="s1",
            thesis_id="t1",
            revision_no=1,
            revision_id="revision1",
            statement="Original judgment",
            confirmed_at=BEFORE,
        )
    )
    uow.assumptions = NS(
        list_by_revision=lambda *a: (
            NS(
                subject_id="s1",
                thesis_id="t1",
                revision_no=1,
                assumption_id="a1",
                statement="Original premise",
                status="RETIRED",
            ),
        )
    )
    uow.invalidations = NS(list_by_revision=lambda *a: ())
    result = s.get("s1")
    assert result.baseline.theses[0].statement == "Original judgment"
    assert result.baseline.theses[0].assumptions == [
        {"assumption_id": "a1", "statement": "Original premise"}
    ]
    assert "RETIRED" not in result.model_dump_json()


def test_agenda_preserves_each_revision_and_prior_version_dates():
    s, _, _, _, agenda = service((decision(),))
    versions = []
    for version in (1, 2, 3):
        stamp = BEFORE + timedelta(hours=version - 1)
        versions.append(
            NS(
                subject_id="s1",
                agenda_item_id="a1",
                version=version,
                recorded_at=stamp,
                title="Synthetic earnings",
                outcome_occurred_at=None,
                source_visible_at=stamp,
                instrument_id="equity:US:TEST",
                linked_event_id=None,
                window_start=stamp,
                window_end=stamp,
                status=NS(value="UPCOMING"),
                date_certainty=NS(value="ESTIMATED"),
                timezone="UTC",
                source_vendor="SYNTHETIC",
            )
        )
    agenda.list_visible = lambda **k: tuple(versions)
    result = s.get("s1")
    assert result.total == 2
    assert result.items[0].source_version == "3"
    assert result.items[0].old_value.startswith("v2 ·")
    assert result.items[1].old_value.startswith("v1 ·")
    assert "ESTIMATED" in result.items[0].new_value
    assert result.items[0].source_names == ["SYNTHETIC"]
    assert result.items[0].previous_source_id == "a1:2"
    assert result.items[0].previous_occurred_at == BEFORE + timedelta(hours=1)


def test_selected_change_relocates_page_after_new_records_arrive():
    s, _, notes, *_ = service((), (revision(1), revision(2)))
    initial = s.get("s1", offset=1, limit=1)
    selected = initial.items[0].change_id
    assert selected == "observation:r1"
    notes.list_revisions_for_instrument = lambda *a, **k: (revision(1), revision(2), revision(3))
    restored = s.get("s1", offset=1, limit=1, change_id=selected)
    assert restored.offset == 2
    assert restored.items[0].change_id == selected
    assert "SELECTED_CHANGE_UNAVAILABLE" not in restored.warning_codes


def test_missing_selected_change_preserves_offset_and_discloses_failure():
    s, *_ = service((), (revision(1), revision(2)))
    result = s.get("s1", offset=1, limit=1, change_id="observation:missing")
    assert result.offset == 1
    assert "SELECTED_CHANGE_UNAVAILABLE" in result.warning_codes


def test_instrument_only_agenda_included_but_other_subject_excluded():
    s, _, _, _, agenda = service()

    def item(identity, subject, instrument):
        return NS(
            agenda_item_id=identity,
            subject_id=subject,
            instrument_id=instrument,
            version=1,
            recorded_at=NOW,
            title="Earnings",
            outcome_occurred_at=None,
            source_visible_at=NOW,
            source_vendor="SYNTHETIC",
            window_start=None,
            window_end=None,
            status=NS(value="UPCOMING"),
            date_certainty=NS(value="UNKNOWN"),
            timezone="UTC",
            linked_event_id=None,
        )

    agenda.list_visible = lambda **k: (
        item("a1", None, "equity:US:TEST"),
        item("a2", "s2", "equity:US:TEST"),
        item("a3", None, "equity:US:OTHER"),
    )
    result = s.get("s1")
    assert [row.agenda_item_id for row in result.items] == ["a1"]
    assert "Instrument-only" in result.items[0].relation_detail


def test_sql_legacy_selected_run_without_observations_is_partial(migrated_sqlite_url):
    from domain.monitoring.enums import MonitorRunStatus
    from domain.monitoring.models import MonitorRun
    from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository

    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyMonitorRepository(engine)
    for identity, selected in (("legacy", "m1"), ("unrelated", "m10")):
        repository.record_evaluation(
            MonitorRun(
                run_id=identity,
                requested_monitor_ids=("m1",),
                selected_monitor_ids=(selected,),
                cadence=None,
                as_of=NOW,
                started_at=NOW,
                completed_at=NOW,
                status=MonitorRunStatus.SUCCEEDED,
                monitors_evaluated=1,
                rules_evaluated=1,
                events_created=0,
                warning_codes=(),
                error_codes=(),
                observation_history_complete=False,
            ),
            (),
            (),
            (),
        )
    assert [run.run_id for run in repository.list_runs("m1", None)] == ["legacy"]
    s, _, _, monitors, _ = service()
    legacy_monitor = NS(
        subject_id="s1", monitor_id="m1", version=1, created_at=BEFORE, rules=[NS(rule_code="rule")]
    )
    monitors.list_current = lambda: (legacy_monitor,)
    monitors.get_version = lambda *a: legacy_monitor
    monitors.list_events = repository.list_events
    monitors.list_runs = repository.list_runs
    result = s.get("s1")
    assert result.total == 0
    assert result.coverage["MONITOR"] == "PARTIAL"
    engine.dispose()


@pytest.mark.parametrize("mismatch", ["numeric_threshold", "instrument_id"])
def test_matching_condition_code_with_different_semantics_is_unlinked(mismatch):
    s, uow, _, monitors, _ = service()
    condition = NS(
        condition_code="rule",
        mode=TradePlanConditionMode.MONITORABLE,
        instrument_id="equity:US:TEST",
        fact_type="PRICE",
        metric_key="last",
        comparator="GT",
        threshold=10,
        event_after=None,
        max_fact_age_seconds=3600,
    )
    rule = NS(
        rule_code="rule",
        instrument_id="equity:US:TEST",
        rule_type=MonitorRuleType.FACT_COMPARISON,
        fact_type="PRICE",
        metric_key="last",
        comparator="GT",
        numeric_threshold=10,
        event_after=None,
        max_fact_age_seconds=3600,
    )
    setattr(rule, mismatch, 20 if mismatch == "numeric_threshold" else "equity:US:OTHER")
    monitor = NS(
        subject_id="s1",
        monitor_id="m1",
        name="Monitor",
        version=1,
        primary_instrument_id=None,
        rules=[rule],
        trade_plan_id="p1",
        trade_plan_version=1,
    )
    monitors.list_current = lambda: (monitor,)
    monitors.get_version = lambda *a: monitor
    monitors.list_events = lambda *a, **k: (
        NS(
            event_id="e1",
            created_at=NOW,
            fact_as_of=NOW,
            monitor_id="m1",
            monitor_version=1,
            rule_code="rule",
            observed_value=12,
        ),
    )
    monitors.list_runs = lambda *a, **k: ()
    uow.trade_plans = NS(
        get_version=lambda *a: NS(subject_id="s1", thesis_id="t1", conditions=[condition])
    )
    uow.theses = NS(get=lambda _: NS(subject_id="s1"))
    assert s.get("s1").items[0].relation == "UNLINKED"


def test_reassigned_monitor_keeps_each_subjects_immutable_event_history():
    s, _, _, monitors, _ = service()

    def monitor(subject):
        return NS(
            subject_id=subject,
            monitor_id="m1",
            name="Monitor",
            version=2 if subject == "s2" else 1,
            rules=[],
            primary_instrument_id="equity:US:TEST",
            trade_plan_id=None,
            trade_plan_version=None,
        )

    monitors.list_current = lambda: (monitor("s2"),)
    monitors.get_version = lambda identity, version: monitor("s1" if version == 1 else "s2")
    monitors.list_events = lambda *a, **k: tuple(
        NS(
            event_id=f"e{version}",
            created_at=NOW,
            fact_as_of=NOW,
            monitor_id="m1",
            monitor_version=version,
            rule_code="rule",
            observed_value=12,
        )
        for version in (1, 2)
    )
    monitors.list_runs = lambda *a, **k: ()
    assert [row.event_id for row in s.get("s1").items] == ["e1"]
    assert [row.event_id for row in s.get("s2").items] == ["e2"]


@pytest.mark.parametrize("complete", [False, True])
def test_reassigned_monitor_missing_observation_gap_uses_historical_subject(complete):
    s, _, _, monitors, _ = service()
    old = NS(
        subject_id="s1", monitor_id="m1", version=1, created_at=BEFORE, rules=[NS(rule_code="rule")]
    )
    current = NS(
        subject_id="s2",
        monitor_id="m1",
        version=2,
        created_at=NOW - timedelta(hours=1),
        rules=[NS(rule_code="rule")],
    )
    monitors.list_current = lambda: (current,)
    monitors.get_version = lambda identity, version: old if version == 1 else current
    monitors.list_events = lambda *a, **k: ()
    monitors.list_runs = lambda *a, **k: (
        NS(
            run_id="legacy",
            started_at=BEFORE,
            completed_at=BEFORE + timedelta(minutes=1),
            observation_history_complete=complete,
            observations=(),
        ),
    )
    assert s.get("s1").coverage["MONITOR"] == "PARTIAL"
    assert s.get("s2").coverage["MONITOR"] == "COMPLETE"


def test_monitor_version_queries_are_per_version_and_unrelated_runs_are_skipped():
    s, _, _, monitors, _ = service()
    relevant = NS(
        subject_id="s1",
        monitor_id="m1",
        version=1,
        name="Monitor",
        rules=[],
        primary_instrument_id=None,
        trade_plan_id=None,
        trade_plan_version=None,
    )
    unrelated = NS(subject_id="s2", monitor_id="m2", version=1)
    calls = []
    monitors.list_current = lambda: (relevant, unrelated)

    def get_version(identity, version):
        calls.append((identity, version))
        return relevant if identity == "m1" else unrelated

    monitors.get_version = get_version

    def events(identity, **kwargs):
        assert identity == "m1", "Unrelated scope must not load events"
        return tuple(
            NS(
                event_id=f"e{index}",
                created_at=NOW,
                fact_as_of=NOW,
                monitor_id="m1",
                monitor_version=1,
                rule_code="rule",
                observed_value=index,
            )
            for index in range(3000)
        )

    monitors.list_events = events

    def runs(identity, **kwargs):
        assert identity == "m1", "Unrelated scope must not load runs"
        return ()

    monitors.list_runs = runs
    assert s.get("s1").total == 3000
    assert calls == [("m1", 1), ("m2", 1)]
    s.get("s1")
    assert len(calls) == 4, "Caches must be per request"


def test_monitor_repository_batches_observation_queries(migrated_sqlite_url):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
    from infrastructure.persistence.orm.monitoring import MonitorRunRow

    engine = create_engine(migrated_sqlite_url)
    with Session(engine) as session, session.begin():
        session.add_all(
            MonitorRunRow(
                run_id=f"r{index}",
                requested_monitor_ids=("m1",),
                selected_monitor_ids=("m1",),
                cadence=None,
                as_of=NOW.isoformat(),
                started_at=NOW.isoformat(),
                completed_at=NOW.isoformat(),
                status="SUCCEEDED",
                monitors_evaluated=1,
                rules_evaluated=1,
                events_created=0,
                warning_codes=(),
                error_codes=(),
                observation_history_complete=False,
            )
            for index in range(501)
        )
    statements = []

    def collect(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", collect)
    rows = SqlAlchemyMonitorRepository(engine).list_runs("m1", None)
    event.remove(engine, "before_cursor_execute", collect)
    assert len(rows) == 501
    assert len(statements) == 3, "One run query plus two <=500 observation batches"
    engine.dispose()
