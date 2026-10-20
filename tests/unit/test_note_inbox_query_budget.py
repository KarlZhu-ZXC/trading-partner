"""Bound inbox SQL without changing identity limits or note/discussion visibility."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from application.services.note_desk_service import NoteDeskService
from conftest import FixedClock
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteIdentity, ExternalNoteRevision
from infrastructure.persistence.external_note_repository import (
    SqlAlchemyExternalNoteRepository,
    _identity,
    _revision,
)
from infrastructure.persistence.orm.operations import (
    ExternalNoteIdentityRow,
    ExternalNoteRevisionRow,
)
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def fixture(repository, count):
    for index in range(count):
        at = NOW - timedelta(seconds=index)
        identity = ExternalNoteIdentity(
            f"synthetic_{index}", "MOOMOO_NOTE", str(index), "Synthetic", None, at, at
        )
        repository.append_identity(identity)
        for version in (1, 3):
            repository.append_revision(
                ExternalNoteRevision(
                    f"synthetic_revision_{index}_{version}",
                    identity.note_id,
                    version,
                    str(version) * 64,
                    str(version),
                    "Synthetic",
                    "Synthetic",
                    None,
                    NoteCoverage.SUMMARY_ONLY,
                    at,
                    at,
                    "PRIVATE",
                    (),
                    (),
                    (),
                )
            )
    # An identity without revisions still consumes an identity-first limit slot.
    repository.append_identity(
        ExternalNoteIdentity(
            "synthetic_empty",
            "MOOMOO_NOTE",
            "empty",
            "Empty",
            None,
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=1),
        )
    )


def legacy_list_latest(engine, limit):
    with Session(engine) as session:
        identities = session.scalars(
            select(ExternalNoteIdentityRow)
            .order_by(ExternalNoteIdentityRow.last_seen_at.desc())
            .limit(max(1, min(limit, 500)))
        ).all()
        output = []
        for identity in identities:
            revision = session.scalar(
                select(ExternalNoteRevisionRow)
                .where(ExternalNoteRevisionRow.note_id == identity.note_id)
                .order_by(ExternalNoteRevisionRow.version.desc())
                .limit(1)
            )
            if revision is not None:
                output.append((_identity(identity), _revision(revision)))
        return tuple(output)


@pytest.mark.parametrize("limit", [1, 25, 500])
def test_latest_revision_batch_matches_full_legacy_result_with_two_selects(
    migrated_sqlite_url, limit
):
    engine = create_engine(migrated_sqlite_url)
    try:
        repository = SqlAlchemyExternalNoteRepository(engine)
        fixture(repository, 40)
        expected = legacy_list_latest(engine, limit)
        statements = []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda c, cur, stmt, p, ctx, many: statements.append(stmt.lstrip().split()[0].upper()),
        )
        assert repository.list_latest(limit) == expected
        assert statements.count("SELECT") == 2
        assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    finally:
        engine.dispose()


def test_inbox_compares_only_visible_rows_but_keeps_full_discussion_coverage():
    notes, uow = MagicMock(), MagicMock()
    uow.__enter__.return_value = uow
    uow.journal.list_related.return_value = (MagicMock(created_at=NOW + timedelta(days=1)),) * 501
    identity = ExternalNoteIdentity("synthetic", "MOOMOO_NOTE", "id", "Synthetic", None, NOW, NOW)
    revision = ExternalNoteRevision(
        "revision_a",
        "synthetic",
        2,
        "a" * 64,
        "2",
        "Synthetic",
        "Synthetic",
        None,
        NoteCoverage.SUMMARY_ONLY,
        NOW,
        NOW,
        "PRIVATE",
        (),
        (),
        (),
    )
    notes.previous_revisions.return_value = {"synthetic": None}
    # Storage order differs from aware-time inbox order; same-time IDs break ties.
    values = [
        (
            identity,
            replace(
                revision,
                note_revision_id=f"revision_{index:02}",
                observed_at=NOW - timedelta(seconds=index),
            ),
        )
        for index in range(8)
    ]
    values.append(
        (
            identity,
            replace(revision, note_revision_id="future", observed_at=NOW + timedelta(days=1)),
        )
    )
    values.append((replace(identity, source="OTHER_SOURCE"), revision))
    notes.list_latest.return_value = tuple(reversed(values))
    desk = NoteDeskService(notes, lambda: uow, FixedClock(NOW), DefaultSecretRedactor())
    result = desk.inbox(limit=2, observed_after=NOW - timedelta(seconds=6))
    assert [row["note_revision_id"] for row in result["items"]] == ["revision_00", "revision_01"]
    assert result["returned_count"] == 2 and result["has_more"]
    assert result["coverage"] == "COMPLETE" and result["discussion_coverage"] == "PARTIAL"
    notes.previous_revisions.assert_called_once_with({"synthetic": 2})
    notes.previous_revision.assert_not_called()
    assert uow.journal.list_related.call_args.args[1] == tuple(
        f"revision_{index:02}" for index in range(6)
    )
    assert all(row["discussion"] is None for row in result["items"])
    assert all(row["change"] == "CONTENT_UNAVAILABLE" for row in result["items"])
    uow.commit.assert_not_called()


def test_batch_predecessors_keep_per_note_version_bounds_and_independent_corruption(
    migrated_sqlite_url,
):
    from sqlalchemy import update

    engine = create_engine(migrated_sqlite_url)
    try:
        repository = SqlAlchemyExternalNoteRepository(engine)
        fixture(repository, 5)
        bounds = {"synthetic_0": 1, "synthetic_1": 3, "synthetic_2": 4, "absent": 9}
        expected = {key: repository.previous_revision(key, version)
                    for key, version in bounds.items()}
        assert repository.previous_revisions(bounds) == expected
        assert repository.previous_revisions({}) == {}
        # A malformed stored source is unavailable, never a new note or an older fallback.
        with engine.begin() as connection:
            connection.execute(update(ExternalNoteRevisionRow).where(
                ExternalNoteRevisionRow.note_revision_id == "synthetic_revision_1_1"
            ).values(blocks_json="not valid json"))
        result = repository.previous_revisions(bounds)
        assert "synthetic_1" not in result
        assert result == {key: value for key, value in expected.items() if key != "synthetic_1"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("limit", [1, 30, 100])
def test_inbox_preserves_full_response_with_constant_four_selects(migrated_sqlite_url, limit):
    from conftest import SequentialIdGenerator
    from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork

    engine = create_engine(migrated_sqlite_url)
    try:
        repository = SqlAlchemyExternalNoteRepository(engine)
        fixture(repository, 100)
        clock, redactor = FixedClock(NOW), DefaultSecretRedactor()
        def uow():
            return SqlAlchemyResearchUnitOfWork(engine, clock, SequentialIdGenerator(), redactor)

        legacy = MagicMock(wraps=repository)
        legacy.previous_revisions.side_effect = lambda bounds: {
            key: repository.previous_revision(key, version) for key, version in bounds.items()
        }
        expected = NoteDeskService(legacy, uow, clock, redactor).inbox(limit=limit)
        statements = []
        event.listen(engine, "before_cursor_execute", lambda c, cur, stmt, p, ctx, many:
                     statements.append(stmt.lstrip().split()[0].upper()))
        actual = NoteDeskService(repository, uow, clock, redactor).inbox(limit=limit)
        assert actual == expected
        assert statements.count("SELECT") == 4
        assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    finally:
        engine.dispose()


def test_inbox_batch_distinguishes_absent_unreadable_and_future_predecessors():
    from domain.external_note.enums import NoteSpeakerKind
    from domain.external_note.models import AttributedNoteBlock

    notes, uow = MagicMock(), MagicMock()
    uow.__enter__.return_value = uow
    uow.journal.list_related.return_value = ()
    identity = ExternalNoteIdentity("note", "MOOMOO_NOTE", "id", "Synthetic", None, NOW, NOW)
    current = ExternalNoteRevision(
        "revision", "note", 3, "a" * 64, "3", "Synthetic", "Synthetic", "Owner text",
        NoteCoverage.FULL, NOW, NOW, "PRIVATE", (), (),
        (AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Owner text"),),
    )
    notes.list_latest.return_value = ((identity, current),)
    service = NoteDeskService(notes, lambda: uow, FixedClock(NOW), DefaultSecretRedactor())
    for predecessors, change in (
        ({"note": None}, "NEW_USER_NOTE"),
        ({}, "PREVIOUS_CONTENT_UNAVAILABLE"),
        ({"note": replace(current, version=1, observed_at=NOW + timedelta(days=1))},
         "PREVIOUS_CONTENT_UNAVAILABLE"),
        ({"note": replace(current, version=1)}, "USER_TEXT_UNCHANGED"),
    ):
        notes.previous_revisions.return_value = predecessors
        assert service.inbox()["items"][0]["change"] == change
    notes.previous_revisions.side_effect = RuntimeError("synthetic private error")
    result = service.inbox()
    assert result["items"][0]["change"] == "PREVIOUS_CONTENT_UNAVAILABLE"
    assert "synthetic private error" not in str(result)
    notes.previous_revision.assert_not_called()
    uow.commit.assert_not_called()


def test_browse_reaches_older_notes_and_searches_latest_titles_without_loading_all_bodies(
    migrated_sqlite_url,
):
    from sqlalchemy import update

    engine = create_engine(migrated_sqlite_url)
    try:
        repository = SqlAlchemyExternalNoteRepository(engine)
        fixture(repository, 505)
        with engine.begin() as connection:
            connection.execute(update(ExternalNoteRevisionRow).where(
                ExternalNoteRevisionRow.note_revision_id == "synthetic_revision_504_3"
            ).values(title="Older 100% Thinking"))
            connection.execute(update(ExternalNoteIdentityRow).where(
                ExternalNoteIdentityRow.note_id == "synthetic_504"
            ).values(primary_instrument_id="equity:US:SYNM"))
            # Corrupt an off-page body: metadata search must not deserialize it.
            connection.execute(update(ExternalNoteRevisionRow).where(
                ExternalNoteRevisionRow.note_revision_id == "synthetic_revision_0_3"
            ).values(blocks_json="unreadable"))
        page, more = repository.browse_latest(
            limit=30, offset=480, query="", observed_through=NOW,
        )
        assert len(page) == 25 and not more
        assert page[-1][1].note_revision_id == "synthetic_revision_504_3"
        for query in ("100%", "THINKING", "us:synm"):
            found, more = repository.browse_latest(
                limit=30, offset=0, query=query, observed_through=NOW,
            )
            assert [revision.note_revision_id for _, revision in found] == [
                "synthetic_revision_504_3",
            ]
            assert not more
        assert repository.browse_latest(
            limit=30, offset=510, query="", observed_through=NOW,
        ) == ((), False)
    finally:
        engine.dispose()
