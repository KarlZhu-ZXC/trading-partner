"""WatchlistService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.investment_case_service import InvestmentCaseService
from application.services.thesis_revision_service import ThesisRevisionService
from application.services.watchlist_service import WatchlistService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import InvestmentCaseType, Market, WatchlistItemStatus
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
def harness(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "wl.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    yield (
        WatchlistService(factory, clock, ids, redactor),
        ThesisRevisionService(factory, clock, ids, redactor),
        InvestmentCaseService(factory, clock, ids, redactor),
        factory,
    )
    eng.dispose()


def test_add_list_and_confirm_then_update_status(harness) -> None:  # type: ignore[no-untyped-def]
    wl, thesis, cases, _factory = harness
    case = cases.create_case(
        case_type=InvestmentCaseType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="c-wl",
    )
    assert case.data is not None

    proposed = wl.add_item(
        market=Market.US,
        symbol="NVDA",
        display_name="NVIDIA",
        thesis_hint="Watch earnings",
        triggers=("EPS beat",),
        case_id=case.data.case_id,
        expires_at=None,
        created_by="codex",
        idempotency_key="wl-add-1",
    )
    assert proposed.ok is True
    assert proposed.data is not None
    status = proposed.data.status
    assert status in {"proposed", "PROPOSED"} or str(status) == "proposed"

    # Not formal yet
    listed = wl.list_items(case_id=case.data.case_id)
    assert listed.ok and listed.data is not None
    assert listed.data.items == ()

    confirmed = thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="user")
    assert confirmed.ok is True

    listed2 = wl.list_items(case_id=case.data.case_id)
    assert listed2.data is not None
    assert len(listed2.data.items) == 1
    item_id = listed2.data.items[0].item_id

    # PROMOTED requires promoted_to_case_id
    bad = wl.update_status(
        item_id,
        new_status=WatchlistItemStatus.PROMOTED_TO_CASE,
        triggered_reason=None,
        promoted_to_case_id=None,
        reviewed_by="user",
        idempotency_key="wl-bad",
    )
    assert bad.ok is False
    assert bad.errors[0].code == "INPUT_VALIDATION_ERROR"

    upd = wl.update_status(
        item_id,
        new_status=WatchlistItemStatus.PROMOTED_TO_CASE,
        triggered_reason=None,
        promoted_to_case_id=case.data.case_id,
        reviewed_by="user",
        idempotency_key="wl-prom",
    )
    assert upd.ok is True
    assert upd.data is not None
    conf2 = thesis.confirm_candidate(upd.data.candidate_id, reviewed_by="user")
    assert conf2.ok is True

    got = wl.get_item(item_id)
    assert got.ok and got.data is not None
    assert got.data.status in {WatchlistItemStatus.PROMOTED_TO_CASE, "promoted_to_case"}
