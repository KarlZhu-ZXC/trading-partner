from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from application.services.weekly_review_service import WeeklyReviewService

NOW = datetime(2026, 9, 14, 1, tzinfo=UTC)
START = datetime(2026, 9, 13, 16, tzinfo=UTC)


def revision(number=1, **updates):
    return NS(
        **(
            dict(
                revision_id=f"r{number}",
                thesis_id="t1",
                subject_id="s1",
                revision_no=number,
                supersedes_revision_no=None if number == 1 else number - 1,
                confirmed_at=NOW,
                statement=f"Synthetic statement {number}",
                rationale="Synthetic rationale",
                rating="NEUTRAL",
                confidence_band="MEDIUM",
            )
            | updates
        )
    )


def service(revisions=(), decisions=(), questions=()):
    uow = NS(
        subjects=NS(list=lambda **kw: (NS(subject_id="s1", title="Synthetic Research"),)),
        theses=NS(list_by_subject=lambda sid: (NS(thesis_id="t1", subject_id="s1"),)),
        revisions=NS(list_by_thesis=lambda tid: revisions),
        assumptions=NS(list_by_revision=lambda *args: ()),
        invalidations=NS(list_by_revision=lambda *args: ()),
        decisions=NS(list_by_subject=lambda sid, **kw: decisions),
        questions=NS(list_by_subject=lambda sid: questions),
    )
    today = NS(
        get=lambda: NS(groups=(), unscoped=(), coverage={"SOURCES": "COMPLETE"}, warnings=())
    )
    return (
        WeeklyReviewService(lambda: nullcontext(uow), today, NS(now=lambda: NOW), "Asia/Shanghai"),
        uow,
        today,
    )


def test_local_monday_boundary_includes_sunday_utc_but_excludes_future():
    s, *_ = service(
        (
            revision(1, confirmed_at=START),
            revision(2, confirmed_at=START - timedelta(seconds=1)),
            revision(3, confirmed_at=NOW + timedelta(seconds=1)),
        )
    )
    result = s.get()
    assert result.week_start == START
    assert result.week_start.isoformat() == "2026-09-14T00:00:00+08:00"
    assert result.week_end.isoformat() == "2026-09-21T00:00:00+08:00"
    assert [v.revision_id for v in result.confirmed_views] == ["r1"]


def test_exact_supersedes_not_latest_and_reconfirmation_detected():
    one = revision(1, confirmed_at=START - timedelta(days=1))
    two = revision(2, confirmed_at=START - timedelta(hours=1))
    three = revision(3, supersedes_revision_no=1, statement=one.statement)
    s, *_ = service((one, two, three))
    view = s.get().confirmed_views[0]
    assert view.kind == "RECONFIRMED"
    assert view.before_statement == one.statement
    assert view.changed_fields == ()
    assert "revision_id=r3" in view.href and "subject_id=s1" in view.href


def test_missing_predecessor_explicit_and_rating_change_detected():
    s, *_ = service((revision(2),))
    result = s.get()
    assert "PREVIOUS_REVISION_UNAVAILABLE:r2" in result.warnings
    assert result.confirmed_views[0].before_statement is None
    assert result.confirmed_views[0].kind == "COMPARISON_UNAVAILABLE"
    one = revision(1, confirmed_at=START - timedelta(days=1))
    s, *_ = service((one, revision(2, statement=one.statement, rating="POSITIVE")))
    assert s.get().confirmed_views[0].changed_fields == ("rating",)


def test_user_no_action_included_and_future_agent_excluded():
    def decision(identity, **updates):
        return NS(
            **(
                dict(
                    subject_id="s1",
                    decision_id=identity,
                    decided_by="user",
                    recorded_at=NOW,
                    decision_type=NS(value="no_action"),
                    title="Synthetic wait",
                    rationale="Need evidence",
                    review_due_at=None,
                )
                | updates
            )
        )

    s, *_ = service(
        decisions=(
            decision("good"),
            decision("future", recorded_at=NOW + timedelta(days=1)),
            decision("agent", decided_by="external_agent"),
        )
    )
    result = s.get()
    assert [d.decision_id for d in result.decisions] == ["good"]
    assert result.decisions[0].decision_type == "no_action"


def test_current_open_and_stale_questions_only_no_future():
    def question(identity, status="OPEN", at=NOW):
        return NS(
            subject_id="s1",
            question_id=identity,
            status=NS(value=status),
            asked_at=at,
            text="Synthetic uncertainty",
        )

    s, *_ = service(
        questions=(
            question("old", at=START - timedelta(days=30)),
            question("stale", "STALE"),
            question("closed", "ANSWERED"),
            question("future", at=NOW + timedelta(days=1)),
        )
    )
    assert {q.question_id for q in s.get().open_questions} == {"old", "stale"}


def test_independent_failure_never_claims_all_clear():
    s, uow, today = service()

    def fail(*args, **kwargs):
        raise RuntimeError("Private source failure")

    uow.revisions.list_by_thesis = fail
    today.get = fail
    result = s.get()
    assert result.coverage["CONFIRMED_VIEWS"] == "PARTIAL"
    assert result.coverage["TODAY"] == "UNAVAILABLE"
    assert result.coverage["QUESTIONS"] == "COMPLETE"
    assert "Private source" not in result.model_dump_json()


def test_definition_change_detected_without_comparing_mutable_status():
    one = revision(1, confirmed_at=START - timedelta(days=1))
    two = revision(2, statement=one.statement)
    s, uow, _ = service((one, two))

    def definitions(tid, number):
        return (
            NS(
                subject_id="s1",
                thesis_id="t1",
                revision_no=number,
                confirmed_at=one.confirmed_at if number == 1 else two.confirmed_at,
                statement="Synthetic assumption",
                basis=f"Basis {number}",
                falsifiability="Observable criterion",
                status="ACTIVE",
            ),
        )

    uow.assumptions.list_by_revision = definitions
    view = s.get().confirmed_views[0]
    assert view.kind == "CHANGED"
    assert view.changed_fields == ("assumptions",)

    def same_definitions(tid, number):
        row = definitions(tid, number)[0]
        row.basis = "Same basis"
        row.status = "RETIRED" if number == 1 else "ACTIVE"
        return (row,)

    uow.assumptions.list_by_revision = same_definitions
    assert s.get().confirmed_views[0].kind == "RECONFIRMED"


def test_definition_lookup_failure_does_not_claim_reconfirmed():
    one = revision(1, confirmed_at=START - timedelta(days=1))
    s, uow, _ = service((one, revision(2, statement=one.statement)))

    def fail(*args):
        raise RuntimeError("Synthetic unavailable")

    uow.invalidations.list_by_revision = fail
    result = s.get()
    assert result.confirmed_views[0].kind == "COMPARISON_UNAVAILABLE"
    assert "REVISION_DEFINITIONS_UNAVAILABLE:r2" in result.warnings


def test_current_week_keeps_records_of_subsequently_archived_subjects():
    s, uow, _ = service((revision(),))
    calls = []

    def subjects(**kwargs):
        calls.append(kwargs)
        return (
            (NS(subject_id="s1", title="Archived research"),)
            if kwargs.get("include_archived")
            else ()
        )

    uow.subjects.list = subjects
    result = s.get()
    assert len(result.confirmed_views) == 1
    assert calls[0]["include_archived"] is True
