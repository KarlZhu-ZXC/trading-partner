"""SQLite contracts for the bounded research-attention readers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from bootstrap import build_application
from domain.common.enums import CandidateStatus, ConfirmationMode, ResearchSubjectStatus
from domain.review_item.enums import (
    ReviewItemSeverity,
    ReviewItemSourceType,
    ReviewItemStatus,
)
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.orm.research import (
    CandidateThesisRevisionRow,
    ResearchSubjectRow,
)
from infrastructure.persistence.orm.review_item import ReviewItemRow
from infrastructure.persistence.research_attention_reader import (
    SqlAlchemyResearchAttentionReader,
)
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository
from interfaces.capabilities import CompactCapabilityRegistry
from interfaces.console.capabilities import create_capability_registry

_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
_NOW_TEXT = _NOW.isoformat()


def test_composed_review_workspace_reads_real_durable_graph_without_writes(
    test_settings: AppSettings, orm_sqlite_url: str, tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(update={
        "database_url": orm_sqlite_url, "runtime_root": tmp_path,
    })
    container = build_application(settings)
    registry = create_capability_registry(container)
    assert type(registry) is CompactCapabilityRegistry
    assert registry.find_operation("research_get", "query").policy.read_only is True
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(str(args[2]).lstrip().split()[0].upper())

    from sqlalchemy.engine import Engine

    event.listen(Engine, "before_cursor_execute", record)
    try:
        result = container.services.review_workspace_query.read()
        assert result["research_attention"] == []
        assert result["review_items_error"] is None
        assert result["review_coverage"]["sources"]["research"] == "COMPLETE"
        assert result["agenda_summary"]["ok"] is True
        assert "SELECT" in statements
        assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}
    finally:
        event.remove(Engine, "before_cursor_execute", record)
        container.close()


def _subject(subject_id: str, *, status: ResearchSubjectStatus) -> ResearchSubjectRow:
    archived = status is ResearchSubjectStatus.ARCHIVED
    return ResearchSubjectRow(
        subject_id=subject_id,
        subject_type="theme",
        title=f"Subject {subject_id}",
        summary="Reader contract fixture",
        status=status.value,
        primary_instrument_id=None,
        topic_tags_json=(),
        created_at=_NOW_TEXT,
        updated_at=_NOW_TEXT,
        created_by="user",
        archived_at=_NOW_TEXT if archived else None,
        archived_reason="fixture" if archived else None,
        linked_subject_ids_json=(),
        evidence_ids_json=(),
        report_ids_json=(),
        event_ids_json=(),
        decision_ids_json=(),
        schema_version=1,
    )


def _candidate(
    candidate_id: str,
    subject_id: str,
    *,
    status: CandidateStatus = CandidateStatus.PROPOSED,
) -> CandidateThesisRevisionRow:
    reviewed = status is not CandidateStatus.PROPOSED and status is not CandidateStatus.EXPIRED
    return CandidateThesisRevisionRow(
        candidate_id=candidate_id,
        subject_id=subject_id,
        thesis_id=None,
        target_revision_no=None,
        payload_json='{"kind":"case_status_change","action":"update"}',
        kind="case_status_change",
        confirmation_mode=ConfirmationMode.NORMAL.value,
        status=status.value,
        proposed_at=_NOW_TEXT,
        expires_at="2027-09-22T12:00:00+00:00",
        proposed_by="user",
        proposed_by_rationale="Reader contract fixture",
        reviewed_at=_NOW_TEXT if reviewed else None,
        reviewed_by="user" if reviewed else None,
        review_note=None,
        rejection_reason=None,
        idempotency_key=candidate_id,
    )


def test_research_attention_reader_uses_one_select_and_exact_counts(
    orm_sqlite_url: str,
) -> None:
    engine = create_engine(orm_sqlite_url)
    try:
        with Session(engine) as session, session.begin():
            # 251 active subjects exercise the aggregate beyond the old 200-row
            # overview cap, followed by one archived subject that must vanish.
            for index in range(252):
                subject_id = f"case_attention_{index:04d}"
                status = (
                    ResearchSubjectStatus.ARCHIVED
                    if index == 251
                    else ResearchSubjectStatus.ACTIVE
                )
                session.add(_subject(subject_id, status=status))
                session.add(_candidate(f"candidate_{index:04d}_0", subject_id))

            # The aggregate must count every proposed candidate, even above the
            # per-source page size used by ReviewItem materialization.
            for index in range(1, 501):
                session.add(
                    _candidate(
                        f"candidate_0000_{index:04d}",
                        "case_attention_0000",
                    )
                )
            # Confirmed candidates are durable history, not pending attention.
            session.add(
                _candidate(
                    "candidate_0001_confirmed",
                    "case_attention_0001",
                    status=CandidateStatus.CONFIRMED,
                )
            )

        reader = SqlAlchemyResearchAttentionReader(engine)
        selects = 0
        writes = 0

        def count_sql(*args: object) -> None:
            nonlocal selects, writes
            statement = str(args[2]).lstrip().upper()
            if statement.startswith("SELECT"):
                selects += 1
            elif statement.startswith(("INSERT", "UPDATE", "DELETE")):
                writes += 1

        event.listen(engine, "before_cursor_execute", count_sql)
        try:
            first = reader.pending_by_subject()
            second = reader.pending_by_subject()
        finally:
            event.remove(engine, "before_cursor_execute", count_sql)

        assert first == second
        assert selects == 2
        assert writes == 0
        assert len(first) == 251
        assert tuple(row.subject_id for row in first) == tuple(
            sorted(row.subject_id for row in first)
        )
        assert first[0].subject_id == "case_attention_0000"
        assert first[0].pending_count == 501
        assert first[1].subject_id == "case_attention_0001"
        assert first[1].pending_count == 1
        assert "case_attention_0251" not in {row.subject_id for row in first}
    finally:
        engine.dispose()


def _review_item(
    index: int,
    *,
    status: ReviewItemStatus,
    active_at_source: bool,
) -> ReviewItemRow:
    resolved = status in {ReviewItemStatus.RESOLVED, ReviewItemStatus.AUTO_RESOLVED}
    return ReviewItemRow(
        review_item_id=f"review_item_{index:032d}",
        source_key=f"attention-key-{index:04d}",
        source_type=ReviewItemSourceType.DECISION_REVIEW_DUE.value,
        source_ref=f"decision-{index:04d}",
        subject_id=f"case_attention_{index:04d}",
        title="Attention fixture",
        detail="Attention fixture detail",
        severity=ReviewItemSeverity.ATTENTION.value,
        recommended_action="review",
        href="/review",
        status=status.value,
        active_at_source=int(active_at_source),
        first_seen_at=_NOW_TEXT,
        last_seen_at=_NOW_TEXT,
        due_at=None,
        resolved_at=_NOW_TEXT if resolved else None,
        resolved_by="user" if resolved else None,
        resolution_note="closed by fixture" if status is ReviewItemStatus.RESOLVED else None,
        resolution_ref=None,
        occurrence_count=1,
        version=2 if resolved else 1,
    )


def test_review_item_repository_known_active_keys_chunks_and_keeps_manual_resolution(
    orm_sqlite_url: str,
) -> None:
    engine = create_engine(orm_sqlite_url)
    try:
        with Session(engine) as session, session.begin():
            # More than two 200-key chunks makes the batching contract
            # observable while keeping the fixture disposable.
            for index in range(405):
                if index == 1:
                    status = ReviewItemStatus.RESOLVED
                    active = True
                elif index == 2:
                    status = ReviewItemStatus.AUTO_RESOLVED
                    active = False
                else:
                    status = ReviewItemStatus.OPEN
                    active = True
                session.add(_review_item(index, status=status, active_at_source=active))
            session.add(_review_item(999, status=ReviewItemStatus.OPEN, active_at_source=True))

        keys = tuple(f"attention-key-{index:04d}" for index in range(405))
        result = SqlAlchemyReviewItemRepository(engine).known_active_source_keys(
            keys + ("not-present",)
        )

        expected = set(keys)
        expected.remove("attention-key-0002")
        assert result == frozenset(expected)
        assert "attention-key-0001" in result
        assert "attention-key-0002" not in result
        assert "not-present" not in result
    finally:
        engine.dispose()
