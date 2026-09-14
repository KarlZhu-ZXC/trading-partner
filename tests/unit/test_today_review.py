from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from application.services.today_review_service import TodayReviewService

NOW = datetime(2026, 9, 15, tzinfo=UTC)
OLD = NOW - timedelta(days=2)


def code(value):
    return NS(value=value)


def subject(identity="s1", instrument="equity:US:SYNTHETIC"):
    return NS(subject_id=identity, primary_instrument_id=instrument, title=f"Subject {identity}")


def decision(identity="d1", scope="s1", at=OLD, due=None):
    return NS(
        decision_id=identity,
        subject_id=scope,
        decided_by="user",
        recorded_at=at,
        review_due_at=due,
        title="Synthetic reviewed view",
    )


def item(identity="n1", scope="s1", kind="OBSERVATION_REVIEW_DUE", at=OLD, **updates):
    return NS(
        **(
            dict(
                source_type=code(kind),
                source_ref=identity,
                subject_id=scope,
                title="Synthetic reminder",
                detail="Synthetic source",
                first_seen_at=at,
                last_seen_at=NOW,
                due_at=None,
                href="/reviews",
                severity=code("ATTENTION"),
                active_at_source=True,
                status=code("OPEN"),
            )
            | updates
        )
    )


def service(subjects=None, decisions=(), items=(), observations=(), events=(), agenda=()):
    uow = NS(
        subjects=NS(list=lambda **kw: (subject(),) if subjects is None else subjects),
        decisions=NS(
            list_by_subject=lambda sid, **kw: tuple(d for d in decisions if d.subject_id == sid)
        ),
    )
    reviews = NS(list_latest=lambda **kw: observations)
    notes = NS(
        list_latest=lambda **kw: (),
        revision_by_id=lambda identity: NS(
            note_id="note1", note_revision_id=identity, observed_at=OLD, title="Synthetic Note"
        ),
        get=lambda identity: NS(primary_instrument_id="equity:US:SYNTHETIC"),
    )
    monitors = NS(
        get_rule_states=lambda _: (),
        latest_judgment=lambda _: None,
        list_current=lambda: (
            NS(
                monitor_id="m1",
                version=1,
                name="Synthetic Monitor",
                valid_until=None,
                subject_id="s1",
                status=code("ACTIVE"),
                created_at=OLD,
            ),
        ),
        list_events_for_monitor_version=lambda *a, **kw: events,
        latest_resolution=lambda identity: None,
    )
    items_repo = NS(list=lambda **kw: items)
    agenda_repo = NS(list_visible=lambda **kw: agenda)
    return TodayReviewService(
        lambda: nullcontext(uow),
        items_repo,
        reviews,
        notes,
        monitors,
        agenda_repo,
        NS(now=lambda: NOW),
        "Asia/Shanghai",
    ), items_repo


def test_same_instrument_groups_subjects_without_merging_decision_verdicts():
    s, _ = service(
        subjects=(subject(), subject("s2")),
        decisions=(decision(at=NOW),),
        items=(item(), item("other", "s2")),
    )
    result = s.get()
    assert len(result.groups) == 1
    assert result.groups[0].status == "ACTIVE"
    assert {v.subject_id for v in result.groups[0].subjects} == {"s1", "s2"}
    assert {v.subject_id for v in result.groups[0].reasons} == {"s1", "s2"}
    assert all(f"subject-{v.subject_id}" in v.href for v in result.groups[0].subjects)


def test_latest_review_collapses_old_but_new_source_reopens():
    s, _ = service(decisions=(decision(at=NOW),), items=(item(),))
    assert s.get().groups[0].status == "REVIEWED"
    s, _ = service(decisions=(decision(),), items=(item(at=NOW),))
    assert s.get().groups[0].status == "ACTIVE"


def test_due_decision_without_review_item_and_future_followup():
    s, _ = service(decisions=(decision(due=OLD),))
    group = s.get().groups[0]
    assert group.status == "ACTIVE" and group.priority == "DUE"
    assert group.reasons[0].source_type == "DECISION_REVIEW_DUE"
    s, _ = service(decisions=(decision(due=NOW + timedelta(days=1)),))
    assert s.get().groups[0].status == "DEFERRED"


def test_broker_agent_and_errors_never_collapse_under_research_decision():
    rows = (
        item("broker", kind="BROKER_ORDER_INTENT"),
        item("agent", kind="AGENT_PENDING_ACTION"),
        item("error", kind="SCORECARD_GAP", severity=code("ERROR")),
    )
    s, _ = service(decisions=(decision(at=NOW),), items=rows)
    result = s.get()
    assert len(result.unscoped) == 2
    assert result.groups[0].status == "ACTIVE"
    assert result.groups[0].priority == "ERROR"


def observation(status="PENDING", due=None):
    return NS(
        review_id="review1",
        note_id="note1",
        note_revision_id="n1",
        subject_id="s1",
        created_at=NOW,
        status=code(status),
        due_at=due,
    )


def test_observation_duplicate_dedup_and_source_resolution_tombstone():
    s, _ = service(items=(item(),), observations=(observation(),))
    assert len(s.get().groups[0].reasons) == 1
    s, _ = service(items=(item(),), observations=(observation("ADOPTED"),))
    assert s.get().groups == ()
    s, _ = service(
        items=(item(),), observations=(observation("DEFERRED", NOW + timedelta(days=1)),)
    )
    assert s.get().groups[0].status == "DEFERRED"


def event(identity, at, kind="TRIGGERED"):
    return NS(
        event_id=identity,
        created_at=at,
        monitor_version=1,
        rule_code="RULE",
        event_type=code(kind),
        message="Synthetic transition",
    )


def test_repeated_monitor_polling_does_not_reopen_review_but_new_transition_does():
    # Use shared value enum stand-ins whose equality behaves like StrEnum.
    events = (event("old", OLD), event("poll", NOW))
    s, _ = service(decisions=(decision(at=OLD + timedelta(days=1)),), events=events)
    group = s.get().groups[0]
    assert group.status == "REVIEWED"
    assert group.reasons[0].source_id == "old"
    s, _ = service(
        decisions=(decision(at=OLD + timedelta(days=1)),),
        events=(
            event("old", OLD),
            event("recovered", OLD + timedelta(hours=1), "RECOVERED"),
            event("again", NOW),
        ),
    )
    assert s.get().groups[0].status == "ACTIVE"
    s, _ = service(events=(event("old", OLD), event("recovered", NOW, "RECOVERED")))
    assert s.get().groups == ()


def test_ambiguous_unscoped_is_not_linked_by_title_and_null_instruments_stay_separate():
    s, _ = service(
        subjects=(subject(instrument=None), subject("s2", None)),
        items=(item(), item("second", "s2"), item("unscoped", None)),
    )
    result = s.get()
    assert len(result.groups) == 2
    assert result.unscoped[0].subject_id is None


def test_resolved_inactive_reminders_excluded_and_source_failure_exposed():
    s, repo = service(
        items=(item(status=code("RESOLVED")), item("inactive", active_at_source=False))
    )
    assert s.get().groups == ()

    def fail(**kwargs):
        raise RuntimeError("Private source must never appear in diagnostic")

    repo.list = fail
    result = s.get()
    assert result.coverage["REVIEW_ITEMS"] == "UNAVAILABLE"
    assert result.coverage["OBSERVATIONS"] == "COMPLETE"
    assert "Private source" not in result.model_dump_json()


def test_history_limit_disclosed():
    s, _ = service(items=tuple(item(f"r{i}") for i in range(1000)))
    result = s.get()
    assert result.coverage["REVIEW_ITEMS"] == "PARTIAL"
    assert "REVIEW_ITEMS_HISTORY_BOUNDED" in result.warnings


def test_canceled_agenda_removes_materialized_duplicate():
    agenda = NS(
        status=code("CANCELLED"),
        recorded_at=OLD,
        agenda_item_id="a1",
        subject_id="s1",
        title="Cancelled fixture",
    )
    s, _ = service(items=(item("a1", kind="CATALYST_AGENDA"),), agenda=(agenda,))
    assert s.get().groups == ()


def test_research_failure_preserves_materialized_due_decision():
    s, _ = service(items=(item("d1", kind="DECISION_REVIEW_DUE", due_at=OLD),))

    def failed_uow():
        raise RuntimeError("Synthetic research unavailable")

    s._uow = failed_uow
    result = s.get()
    assert result.coverage["DECISIONS"] == "UNAVAILABLE"
    assert result.unscoped[0].source_id == "d1"


def test_untrusted_persisted_href_uses_safe_local_fallback():
    for href in ["javascript:alert(1)", "//external.invalid", "/\\external.invalid", "/\n/evil"]:
        s, _ = service(items=(item(href=href),))
        assert s.get().groups[0].reasons[0].href == "/decision-workbench#reviews"
    s, _ = service(items=(item(href="/research?section=quick-review#subject-s1"),))
    assert s.get().groups[0].reasons[0].href == "/research?section=quick-review#subject-s1"


def test_unscoped_observation_maps_only_one_exact_instrument_subject():
    obs = observation()
    obs.subject_id = None
    s, _ = service(observations=(obs,))
    assert s.get().groups[0].reasons[0].subject_id == "s1"
    s, _ = service(subjects=(subject(), subject("s2")), observations=(obs,))
    assert s.get().groups == ()
    assert s.get().unscoped[0].subject_id is None
    s, _ = service(subjects=(subject(instrument="equity:US:DIFFERENT"),), observations=(obs,))
    assert s.get().unscoped[0].subject_id is None


def test_failed_note_processing_remains_visible_even_after_general_review():
    svc, *_ = service(decisions=(decision(),))
    svc._notes.list_latest = lambda **k: (
        (
            NS(primary_instrument_id="equity:US:SYNTHETIC"),
            NS(observed_at=OLD, coverage="FULL", note_revision_id="revision_failed"),
        ),
    )
    svc._notes.interpretation_for_revision = lambda _: NS(status="FAILED", created_at=OLD)
    svc._reviews.latest_for_revision = lambda _: NS(subject_id="s1")
    digest = svc.get()
    group = next(
        g for g in digest.groups if any(r.source_type == "NOTE_PROCESSING" for r in g.reasons)
    )
    assert group.status == "ACTIVE" and group.priority == "ERROR"
    assert digest.coverage["NOTE_PROCESSING"] == "COMPLETE"


def test_recovered_current_rule_does_not_retain_stale_unavailable_event():
    svc, _ = service(events=(event("failed", OLD, "NOT_EVALUATED"),))
    svc._monitors.get_rule_states = lambda _: (
        NS(rule_code="RULE", monitor_version=1, updated_at=NOW, state=code("QUIET")),
    )
    assert svc.get().groups == ()


def test_successful_current_judgment_clears_legacy_unavailable_reminder():
    svc, _ = service(events=(event("failed", OLD, "JUDGMENT_UNAVAILABLE"),))
    svc._monitors.latest_judgment = lambda _: NS(
        monitor_version=1, status="SUCCEEDED", created_at=NOW
    )
    assert svc.get().groups == ()
