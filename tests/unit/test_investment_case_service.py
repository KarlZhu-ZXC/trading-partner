"""InvestmentCaseService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.investment_case_service import InvestmentCaseService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import InvestmentCaseStatus, InvestmentCaseType
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
    path = tmp_path / "case.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    service = InvestmentCaseService(factory, clock, ids, redactor)
    yield service, factory, clock, ids
    eng.dispose()


def test_create_get_list_archive_and_metadata(svc) -> None:  # type: ignore[no-untyped-def]
    service, factory, _clock, _ids = svc
    created = service.create_case(
        case_type=InvestmentCaseType.COMPANY,
        title="NVDA structural",
        summary="Long GPU demand",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=("ai", "GPU"),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="create-nvda-1",
    )
    assert created.ok is True
    assert created.data is not None
    assert created.data.status == InvestmentCaseStatus.DRAFT or created.data.status == "draft"
    assert created.data.topic_tags == ("ai", "gpu")
    case_id = created.data.case_id

    got = service.get_case(case_id)
    assert got.ok is True
    assert got.data is not None
    assert got.data.case_id == case_id

    listed = service.list_cases(topic_tag="ai")
    assert listed.ok is True
    assert listed.data is not None
    assert listed.data.total == 1

    updated = service.update_case_metadata(
        case_id,
        title="NVDA updated",
        reviewed_by="user",
        idempotency_key="meta-1",
    )
    assert updated.ok is True
    assert updated.data is not None
    assert updated.data.title == "NVDA updated"

    archived = service.archive_case(
        case_id,
        archived_reason="done researching",
        reviewed_by="user",
        idempotency_key="arch-1",
    )
    assert archived.ok is True
    assert archived.data is not None
    assert archived.data.status in {InvestmentCaseStatus.ARCHIVED, "archived"}
    assert archived.data.archived_reason == "done researching"

    # Confirmed candidate written in same txn
    with factory() as uow:
        cands = uow.candidates.list(case_id=case_id, limit=20)
        assert any(c.status.value == "confirmed" for c in cands)


def test_create_rejects_codex_confirm(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    env = service.create_case(
        case_type=InvestmentCaseType.THEME,
        title="AI theme",
        summary="Theme case",
        primary_instrument_id=None,
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="codex",
        idempotency_key="bad-create",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_archive_rejects_codex(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    created = service.create_case(
        case_type=InvestmentCaseType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="c1",
    )
    assert created.data is not None
    env = service.archive_case(
        created.data.case_id,
        archived_reason="nope",
        reviewed_by="codex",
        idempotency_key="a1",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_update_metadata_rejects_codex(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    created = service.create_case(
        case_type=InvestmentCaseType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="c2",
    )
    assert created.data is not None
    env = service.update_case_metadata(
        created.data.case_id,
        title="Hacked",
        reviewed_by="codex",
        idempotency_key="m2",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_create_idempotent_same_payload(svc) -> None:  # type: ignore[no-untyped-def]
    service, *_ = svc
    kwargs = dict(
        case_type=InvestmentCaseType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="idem-create",
    )
    first = service.create_case(**kwargs)  # type: ignore[arg-type]
    second = service.create_case(**kwargs)  # type: ignore[arg-type]
    assert first.ok and second.ok
    assert first.data is not None and second.data is not None
    assert first.data.case_id == second.data.case_id
    assert any(w.code == "DUPLICATE_IDEMPOTENCY_KEY" for w in second.warnings)
