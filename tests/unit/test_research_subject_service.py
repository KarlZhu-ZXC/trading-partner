"""ResearchSubjectService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.research_subject_service import ResearchSubjectService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import ResearchSubjectStatus, ResearchSubjectType
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def svc(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "subject.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    service = ResearchSubjectService(factory, clock, ids, redactor)
    yield service, factory, clock, ids
    eng.dispose()


def test_create_get_list_archive_and_metadata(svc) -> None:  # type: ignore[no-untyped-def]
    service, factory, _clock, _ids = svc
    created = service.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA structural",
        summary="Long GPU demand",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=("ai", "GPU"),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="create-nvda-1",
    )
    assert created.ok is True
    assert created.data is not None
    assert created.data.status == ResearchSubjectStatus.DRAFT or created.data.status == "draft"
    assert created.data.topic_tags == ("ai", "gpu")
    subject_id = created.data.subject_id

    got = service.get_subject(subject_id)
    assert got.ok is True
    assert got.data is not None
    assert got.data.subject_id == subject_id

    listed = service.list_subjects(topic_tag="ai")
    assert listed.ok is True
    assert listed.data is not None
    assert listed.data.total == 1

    updated = service.update_subject_metadata(
        subject_id,
        title="NVDA updated",
        reviewed_by="user",
        idempotency_key="meta-1",
    )
    assert updated.ok is True
    assert updated.data is not None
    assert updated.data.title == "NVDA updated"

    archived = service.archive_subject(
        subject_id,
        archived_reason="done researching",
        reviewed_by="user",
        idempotency_key="arch-1",
    )
    assert archived.ok is True
    assert archived.data is not None
    assert archived.data.status in {ResearchSubjectStatus.ARCHIVED, "archived"}
    assert archived.data.archived_reason == "done researching"

    # Confirmed candidate written in same txn
    with factory() as uow:
        cands = uow.candidates.list(subject_id=subject_id, limit=20)
        assert any(c.status.value == "confirmed" for c in cands)


def test_create_rejects_codex_confirm(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    env = service.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="AI theme",
        summary="Theme case",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="codex",
        idempotency_key="bad-create",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_archive_rejects_codex(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    created = service.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="c1",
    )
    assert created.data is not None
    env = service.archive_subject(
        created.data.subject_id,
        archived_reason="nope",
        reviewed_by="codex",
        idempotency_key="a1",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_update_metadata_rejects_codex(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    created = service.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="c2",
    )
    assert created.data is not None
    env = service.update_subject_metadata(
        created.data.subject_id,
        title="Hacked",
        reviewed_by="codex",
        idempotency_key="m2",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_create_idempotent_same_payload(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    kwargs = dict(
        subject_type=ResearchSubjectType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="idem-create",
    )
    first = service.create_subject(**kwargs)  # type: ignore[arg-type]
    second = service.create_subject(**kwargs)  # type: ignore[arg-type]
    assert first.ok and second.ok
    assert first.data is not None and second.data is not None
    assert first.data.subject_id == second.data.subject_id
    assert any(w.code == "DUPLICATE_IDEMPOTENCY_KEY" for w in second.warnings)


def test_case_metadata_rejects_action_plan_language_on_create_and_update(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    rejected_create = service.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="原油回调与UCO分批加仓计划",
        summary="研究原油与相关产品",
        primary_instrument_id="etf:US:UCO",
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="action-title",
    )
    assert rejected_create.ok is False
    assert rejected_create.errors[0].code == "CASE_METADATA_POLICY_VIOLATION"
    assert rejected_create.errors[0].retryable is False

    created = service.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="原油市场与UCO产品研究",
        summary="研究原油供需、期限结构和产品风险",
        primary_instrument_id="etf:US:UCO",
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="research-title",
    )
    assert created.ok and created.data is not None
    rejected_update = service.update_subject_metadata(
        created.data.subject_id,
        summary="跌破支撑后止损",
        reviewed_by="user",
        idempotency_key="action-summary",
    )
    assert rejected_update.ok is False
    assert rejected_update.errors[0].code == "CASE_METADATA_POLICY_VIOLATION"
