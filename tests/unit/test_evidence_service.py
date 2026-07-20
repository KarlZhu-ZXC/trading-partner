"""Phase 1C C4a unit tests for EvidenceService."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.tool_envelope import DUPLICATE_CONTENT
from application.services.evidence_service import EvidenceService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    InvestmentCaseStatus,
    InvestmentCaseType,
    ReliabilityLevel,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, InvestmentCase
from infrastructure.persistence.models import (
    ResearchEvidenceRow,
    SystemAuditLogRow,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "evidence-service-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "evidence-service-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _make_case(ids: SequentialIdGenerator, clock: FixedClock, **overrides: Any) -> InvestmentCase:
    base: dict[str, Any] = {
        "case_id": ids.new(EntityIdPrefix.CASE),
        "case_type": InvestmentCaseType.COMPANY,
        "title": "Case",
        "summary": "Summary",
        "status": InvestmentCaseStatus.ACTIVE,
        "primary_instrument_id": US,
        "topic_tags": ("ai",),
        "created_at": clock.now(),
        "updated_at": clock.now(),
        "created_by": "user",
        "archived_at": None,
        "archived_reason": None,
        "linked_case_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "event_ids": (),
        "decision_ids": (),
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return InvestmentCase(**base)


@pytest.fixture
def harness(tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "evidence_svc.db"
    database_url = f"sqlite:///{path}"
    _set_test_env(monkeypatch, database_url)
    command.upgrade(_alembic_config(database_url, project_root), "head")
    eng = create_engine(database_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    # 0003 migration already seeds A_SHARE/US instruments.
    service = EvidenceService(factory, clock, ids, redactor)
    yield service, factory, clock, ids, eng
    eng.dispose()


def _create_case(factory, ids, clock) -> str:  # type: ignore[no-untyped-def]
    case = _make_case(ids, clock)
    with factory() as uow:
        uow.cases.add(case)
        uow.commit()
    return case.case_id


def _base_record_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "evidence_type": EvidenceType.A_SHARE_ANNOUNCEMENT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "Moutai announcement",
        "summary": "Dividend plan",
        "content_text": "Full text body",
        "structured_data_json": None,
        "source_name": "eastmoney",
        "source_vendor": "eastmoney",
        "source_record_id": "ann-1",
        "source_url": "https://example.com/a?token=secret123&q=ok",
        "published_at": EARLIER,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (A_SHARE,),
        "topic_tags": ("A-Share", " liquor ", "a-share"),
        "quality": EvidenceQuality.PRIMARY,
        "reliability": ReliabilityLevel.HIGH,
        "confidence": Decimal("0.8"),
        "supersedes_evidence_id": None,
        "recorded_by": "provider:eastmoney",
        "case_ids": (),
        "observed_at": None,
    }
    base.update(overrides)
    return base


def test_record_evidence_happy_path_and_topic_dedupe(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    case_id = _create_case(factory, ids, clock)
    env = service.record_evidence(**_base_record_kwargs(case_ids=(case_id,)))
    assert env.ok is True
    assert env.data is not None
    assert env.degraded is False
    assert env.data.topic_tags == ("a-share", "liquor")
    assert "secret123" not in (env.data.source_url or "")
    assert "***REDACTED***" in (env.data.source_url or "")
    assert env.data.instrument_ids == (A_SHARE,)

    with factory() as uow:
        case = uow.cases.get(case_id)
        assert env.data.evidence_id in case.evidence_ids
        assert uow.case_evidence_links.exists(case_id, env.data.evidence_id)
        from application.dto.research_memory import ResearchSearchQuery

        hits = uow.search_index.search(ResearchSearchQuery(text="Dividend", case_id=case_id))
        assert hits.total >= 1


def test_duplicate_content_adds_new_case_links(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, eng = harness
    case_a = _create_case(factory, ids, clock)
    case_b = _create_case(factory, ids, clock)
    first = service.record_evidence(**_base_record_kwargs(case_ids=(case_a,)))
    assert first.ok and first.data is not None
    eid = first.data.evidence_id

    second = service.record_evidence(**_base_record_kwargs(case_ids=(case_a, case_b)))
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_CONTENT in second.warnings
    assert second.data is not None
    assert second.data.evidence_id == eid

    with factory() as uow:
        assert uow.case_evidence_links.exists(case_a, eid)
        assert uow.case_evidence_links.exists(case_b, eid)
        case_b_row = uow.cases.get(case_b)
        assert eid in case_b_row.evidence_ids

    with Session(eng) as session:
        rows = session.scalars(select(ResearchEvidenceRow)).all()
        assert len(rows) == 1


def test_link_duplicate_returns_warning(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    case_id = _create_case(factory, ids, clock)
    rec = service.record_evidence(**_base_record_kwargs(case_ids=()))
    assert rec.ok and rec.data
    eid = rec.data.evidence_id
    link1 = service.link_evidence_to_case(evidence_id=eid, case_id=case_id, linked_by="user")
    assert link1.ok and link1.data and link1.degraded is False
    link2 = service.link_evidence_to_case(evidence_id=eid, case_id=case_id, linked_by="user")
    assert link2.ok is True
    assert link2.degraded is True
    assert DUPLICATE_CONTENT in link2.warnings
    assert link2.data is not None
    assert link2.data.link_id == link1.data.link_id


def test_assess_requires_user_or_external_confirmation(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    case_id = _create_case(factory, ids, clock)
    rec = service.record_evidence(**_base_record_kwargs(case_ids=(case_id,)))
    assert rec.ok and rec.data
    bad = service.assess_evidence(
        evidence_id=rec.data.evidence_id,
        case_id=case_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.5"),
        rationale="Strong support",
        assessed_by="codex",
        confirmed_by="codex",
    )
    assert bad.ok is False
    assert any(e.code == "UNAUTHORIZED_REVIEWER" for e in bad.errors)

    good = service.assess_evidence(
        evidence_id=rec.data.evidence_id,
        case_id=case_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.CONTRADICTS,
        materiality=Decimal("0.7"),
        rationale="Counter evidence",
        assessed_by="codex",
        confirmed_by="user",
    )
    assert good.ok is True
    assert good.data is not None
    assert good.data.confirmed_by == "user"
    assert good.data.stance in {EvidenceStance.CONTRADICTS, "contradicts"}


def test_url_scheme_userinfo_fragment_query_secret(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    _create_case(factory, ids, clock)

    ftp = service.record_evidence(**_base_record_kwargs(source_url="ftp://example.com/a"))
    assert ftp.ok is False

    userinfo = service.record_evidence(
        **_base_record_kwargs(source_url="https://user:pass@example.com/a")
    )
    assert userinfo.ok is False
    assert any(e.code == "INPUT_VALIDATION_ERROR" for e in userinfo.errors)

    ok = service.record_evidence(
        **_base_record_kwargs(
            title="url-ok",
            source_url="https://example.com/path?api_key=sk-abc&q=1#frag",
        )
    )
    assert ok.ok is True
    assert ok.data is not None
    url = ok.data.source_url or ""
    assert "#frag" not in url
    assert "sk-abc" not in url
    assert "api_key=" in url
    assert "***REDACTED***" in url
    assert "q=1" in url


def test_structured_json_deep_redaction_and_canonicalization(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _clock, _ids, _eng = harness
    raw = json.dumps(
        {
            "b": 2,
            "a": {"password": "hunter2", "note": "ok"},
            "token": "xyz",
        },
        indent=2,
    )
    env = service.record_evidence(
        **_base_record_kwargs(
            title="struct",
            content_text=None,
            structured_data_json=raw,
            source_url=None,
        )
    )
    assert env.ok is True
    assert env.data is not None
    stored = env.data.structured_data_json
    assert stored is not None
    assert stored == json.dumps(
        json.loads(stored), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert "hunter2" not in stored
    assert "xyz" not in stored
    assert "***REDACTED***" in stored
    assert stored.index('"a"') < stored.index('"b"')


def test_us_and_a_share_evidence_types_preserved(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _clock, _ids, _eng = harness
    a = service.record_evidence(
        **_base_record_kwargs(
            evidence_type=EvidenceType.A_SHARE_DRAGON_TIGER,
            title="a-share-dt",
            instrument_ids=(A_SHARE,),
        )
    )
    u = service.record_evidence(
        **_base_record_kwargs(
            evidence_type=EvidenceType.SEC_FILING,
            title="us-sec",
            instrument_ids=(US,),
            source_vendor="sec_edgar",
            recorded_by="provider:sec_edgar",
        )
    )
    assert a.ok and a.data
    assert u.ok and u.data
    assert a.data.evidence_type in {
        EvidenceType.A_SHARE_DRAGON_TIGER,
        "a_share_dragon_tiger",
    }
    assert u.data.evidence_type in {EvidenceType.SEC_FILING, "sec_filing"}


def test_audit_summary_excludes_body_url_rationale(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, eng = harness
    case_id = _create_case(factory, ids, clock)
    env = service.record_evidence(
        **_base_record_kwargs(
            case_ids=(case_id,),
            content_text="SECRET_BODY_SHOULD_NOT_AUDIT",
            source_url="https://example.com/?token=abc",
        )
    )
    assert env.ok and env.data is not None
    assess = service.assess_evidence(
        evidence_id=env.data.evidence_id,
        case_id=case_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.NEUTRAL,
        materiality=Decimal("0.1"),
        rationale="RATIONALE_SHOULD_NOT_APPEAR_IN_AUDIT",
        assessed_by="codex",
        confirmed_by="external_agent",
    )
    assert assess.ok

    with Session(eng) as session:
        rows = session.scalars(select(SystemAuditLogRow)).all()
        assert rows
        payloads = [r.payload_json for r in rows]
        joined = "\n".join(payloads)
        assert "SECRET_BODY_SHOULD_NOT_AUDIT" not in joined
        assert "RATIONALE_SHOULD_NOT_APPEAR_IN_AUDIT" not in joined
        assert "https://example.com" not in joined
        for p in payloads:
            data = json.loads(p)
            for key in (
                "action",
                "entity_type",
                "entity_id",
                "case_id",
                "actor",
                "confirmed_by",
                "idempotency_key",
                "content_sha256",
                "linked_entity_ids",
            ):
                assert key in data
            assert data["idempotency_key"] is None


def test_projection_failure_full_rollback(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, eng = harness
    case_id = _create_case(factory, ids, clock)

    real_factory = factory

    class BoomUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> BoomUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def search_index(self) -> Any:
            mock = MagicMock()
            mock.index.side_effect = RuntimeError("search backend boom")
            mock.refresh_evidence_membership.side_effect = RuntimeError("search boom")
            return mock

    def boom_factory() -> BoomUow:
        return BoomUow(real_factory())

    boom_service = EvidenceService(
        boom_factory, clock, SequentialIdGenerator(start=9000), DefaultSecretRedactor()
    )
    env = boom_service.record_evidence(
        **_base_record_kwargs(title="rollback-test", case_ids=(case_id,))
    )
    assert env.ok is False

    with Session(eng) as session:
        titles = session.scalars(select(ResearchEvidenceRow.title)).all()
        assert "rollback-test" not in titles
        count = session.execute(text("SELECT COUNT(*) FROM research_search_documents")).scalar_one()
        assert count == 0
        audit_count = session.scalars(select(SystemAuditLogRow)).all()
        assert audit_count == []

    with factory() as uow:
        case = uow.cases.get(case_id)
        assert case.evidence_ids == ()


def test_case_cache_updated_at_matches_link_write_timestamp(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    old = NOW - timedelta(days=3)
    case = _make_case(ids, clock, created_at=old, updated_at=old)
    with factory() as uow:
        uow.cases.add(case)
        uow.commit()
    case_id = case.case_id

    clock.set(NOW)
    rec = service.record_evidence(**_base_record_kwargs(case_ids=(), title="cache-ts"))
    assert rec.ok and rec.data
    eid = rec.data.evidence_id

    link = service.link_evidence_to_case(evidence_id=eid, case_id=case_id, linked_by="user")
    assert link.ok and link.data is not None
    assert link.data.linked_at == NOW

    with factory() as uow:
        loaded = uow.cases.get(case_id)
        assert loaded.updated_at == NOW
        assert loaded.updated_at != old
        assert eid in loaded.evidence_ids
        stored_link = uow.case_evidence_links.get(case_id, eid)
        assert stored_link.linked_at == loaded.updated_at == NOW


def _delete_search_projection(eng: Engine, entity_id: str) -> None:
    """Remove projection + mapping rows so link/index can prove self-heal."""
    with Session(eng) as session:
        row = session.execute(
            text("SELECT rowid FROM research_search_documents WHERE entity_id = :eid"),
            {"eid": entity_id},
        ).first()
        assert row is not None
        rowid = int(row[0])
        for table in (
            "research_search_document_cases",
            "research_search_document_instruments",
            "research_search_document_tags",
        ):
            session.execute(
                text(f"DELETE FROM {table} WHERE document_rowid = :rowid"),
                {"rowid": rowid},
            )
        session.execute(
            text("DELETE FROM research_search_documents WHERE entity_id = :eid"),
            {"eid": entity_id},
        )
        session.commit()
        remaining = session.execute(
            text("SELECT COUNT(*) FROM research_search_documents WHERE entity_id = :eid"),
            {"eid": entity_id},
        ).scalar_one()
        assert remaining == 0


def test_link_rebuilds_deleted_search_projection(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, eng = harness
    case_id = _create_case(factory, ids, clock)
    rec = service.record_evidence(**_base_record_kwargs(case_ids=(), title="heal-projection"))
    assert rec.ok and rec.data
    eid = rec.data.evidence_id

    with factory() as uow:
        from application.dto.research_memory import ResearchSearchQuery

        page = uow.search_index.search(ResearchSearchQuery(text="heal-projection", case_id=None))
        assert page.total >= 1

    _delete_search_projection(eng, eid)

    link = service.link_evidence_to_case(evidence_id=eid, case_id=case_id, linked_by="user")
    assert link.ok is True

    with factory() as uow:
        from application.dto.research_memory import ResearchSearchQuery

        page = uow.search_index.search(ResearchSearchQuery(text="heal-projection", case_id=case_id))
        assert page.total >= 1
        entity_ids = {hit.entity_id for hit in page.items}
        assert eid in entity_ids


def test_duplicate_evidence_new_link_rebuilds_projection(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, eng = harness
    case_a = _create_case(factory, ids, clock)
    case_b = _create_case(factory, ids, clock)
    first = service.record_evidence(**_base_record_kwargs(case_ids=(case_a,), title="dup-heal"))
    assert first.ok and first.data
    eid = first.data.evidence_id

    _delete_search_projection(eng, eid)

    second = service.record_evidence(**_base_record_kwargs(case_ids=(case_b,), title="dup-heal"))
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_CONTENT in second.warnings

    with factory() as uow:
        from application.dto.research_memory import ResearchSearchQuery

        page = uow.search_index.search(ResearchSearchQuery(text="dup-heal", case_id=case_b))
        assert page.total >= 1
        assert any(hit.entity_id == eid for hit in page.items)


def test_url_query_redacts_api_key_and_mixed_case_token(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _clock, _ids, _eng = harness
    env = service.record_evidence(
        **_base_record_kwargs(
            title="url-keys",
            source_url=(
                "https://example.com/x?api-key=AKIA_SECRET&Token=mixedCaseTok&safe=1&API_KEY=upper"
            ),
        )
    )
    assert env.ok is True
    assert env.data is not None
    url = env.data.source_url or ""
    assert "AKIA_SECRET" not in url
    assert "mixedCaseTok" not in url
    assert "upper" not in url or "***REDACTED***" in url
    assert "safe=1" in url
    assert "***REDACTED***" in url
    # Keys preserved; values redacted via SecretRedactor.redact_mapping.
    assert "api-key=" in url
    assert "Token=" in url or "token=" in url.lower()


def test_topic_tags_redact_then_stable_dedupe_sentinels(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _clock, _ids, _eng = harness
    # Free-text redaction turns both secret-bearing tags into the same sentinel.
    env = service.record_evidence(
        **_base_record_kwargs(
            title="tag-dedupe",
            topic_tags=(
                "api_key=secretA",
                "normal-tag",
                "token=secretB",
                "Normal-Tag",
            ),
            source_url=None,
        )
    )
    assert env.ok is True
    assert env.data is not None
    tags = env.data.topic_tags
    assert "normal-tag" in tags
    # Only one redaction sentinel after post-redact stable dedupe.
    assert tags.count("***REDACTED***") == 1
    assert len(tags) == len(set(tags))


def test_audit_writer_failure_rolls_back_evidence_path(harness) -> None:  # type: ignore[no-untyped-def]
    """Link audit succeeds; evidence.recorded fails → full tx rollback.

    Proves an already-flushed Evidence row, Case cache, Search projection, and
    any earlier audit row all roll back when a later audit append fails.
    """
    service, factory, clock, ids, eng = harness
    case_id = _create_case(factory, ids, clock)
    real_factory = factory

    class _SelectiveBoomAudit:
        """First append (link) succeeds; subsequent (evidence.recorded) fails."""

        def __init__(self, real_audit: Any) -> None:
            self._real = real_audit
            self._calls = 0

        def append(self, *args: Any, **kwargs: Any) -> Any:
            self._calls += 1
            if self._calls == 1:
                return self._real.append(*args, **kwargs)
            raise RuntimeError("audit writer boom on evidence.recorded")

    class BoomAuditUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner
            self._audit_proxy: _SelectiveBoomAudit | None = None

        def __enter__(self) -> BoomAuditUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def audit(self) -> Any:
            if self._audit_proxy is None:
                self._audit_proxy = _SelectiveBoomAudit(self._inner.audit)
            return self._audit_proxy

    def boom_factory() -> BoomAuditUow:
        return BoomAuditUow(real_factory())

    boom = EvidenceService(
        boom_factory, clock, SequentialIdGenerator(start=9100), DefaultSecretRedactor()
    )
    env = boom.record_evidence(**_base_record_kwargs(title="audit-fail-ev", case_ids=(case_id,)))
    assert env.ok is False

    with Session(eng) as session:
        titles = session.scalars(select(ResearchEvidenceRow.title)).all()
        assert "audit-fail-ev" not in titles
        proj = session.execute(text("SELECT COUNT(*) FROM research_search_documents")).scalar_one()
        assert proj == 0
        audits = session.scalars(select(SystemAuditLogRow)).all()
        assert audits == []
        link_count = session.execute(text("SELECT COUNT(*) FROM case_evidence_links")).scalar_one()
        assert link_count == 0

    with factory() as uow:
        case = uow.cases.get(case_id)
        assert case.evidence_ids == ()
        assert case.updated_at == NOW  # unchanged from create


def test_duplicate_evidence_link_audit_failure_rolls_back_new_link(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    """Duplicate path: index succeeds, link audit fails → new mutations roll back.

    Pre-existing Evidence and its prior Case link remain; only the new link,
    Case cache mutation, Search projection mutation, and audit are rolled back.
    """
    service, factory, clock, ids, eng = harness
    case_a = _create_case(factory, ids, clock)
    case_b = _create_case(factory, ids, clock)
    first = service.record_evidence(
        **_base_record_kwargs(case_ids=(case_a,), title="dup-audit-fail")
    )
    assert first.ok and first.data is not None
    eid = first.data.evidence_id

    with factory() as uow:
        from application.dto.research_memory import ResearchSearchQuery

        page_before = uow.search_index.search(
            ResearchSearchQuery(text="dup-audit-fail", case_id=case_a)
        )
        assert page_before.total >= 1
        case_a_before = uow.cases.get(case_a)
        case_a_evidence_ids = case_a_before.evidence_ids
        case_a_updated_at = case_a_before.updated_at

    with Session(eng) as session:
        audits_before = len(session.scalars(select(SystemAuditLogRow)).all())
        membership_before = set(
            session.execute(
                text(
                    "SELECT c.case_id FROM research_search_document_cases c "
                    "JOIN research_search_documents d "
                    "ON d.rowid = c.document_rowid "
                    "WHERE d.entity_id = :eid"
                ),
                {"eid": eid},
            ).scalars()
        )
        assert case_a in membership_before
        assert case_b not in membership_before

    real_factory = factory

    class _AlwaysBoomAudit:
        def append(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit writer boom on link after index")

    class BoomLinkAuditUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner
            self._audit_proxy = _AlwaysBoomAudit()

        def __enter__(self) -> BoomLinkAuditUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def audit(self) -> Any:
            return self._audit_proxy

    def boom_factory() -> BoomLinkAuditUow:
        return BoomLinkAuditUow(real_factory())

    boom = EvidenceService(
        boom_factory, clock, SequentialIdGenerator(start=9200), DefaultSecretRedactor()
    )
    second = boom.record_evidence(**_base_record_kwargs(case_ids=(case_b,), title="dup-audit-fail"))
    assert second.ok is False

    with Session(eng) as session:
        # Pre-existing Evidence remains.
        titles = session.scalars(select(ResearchEvidenceRow.title)).all()
        assert "dup-audit-fail" in titles
        rows = session.scalars(select(ResearchEvidenceRow)).all()
        assert len(rows) == 1
        assert rows[0].evidence_id == eid

        # New link for case_b rolled back.
        link_b = session.execute(
            text(
                "SELECT COUNT(*) FROM case_evidence_links "
                "WHERE case_id = :cid AND evidence_id = :eid"
            ),
            {"cid": case_b, "eid": eid},
        ).scalar_one()
        assert link_b == 0

        # Prior link for case_a remains.
        link_a = session.execute(
            text(
                "SELECT COUNT(*) FROM case_evidence_links "
                "WHERE case_id = :cid AND evidence_id = :eid"
            ),
            {"cid": case_a, "eid": eid},
        ).scalar_one()
        assert link_a == 1

        # No new audits from the failed duplicate path.
        audits_after = session.scalars(select(SystemAuditLogRow)).all()
        assert len(audits_after) == audits_before

        # Search projection case membership not mutated toward case_b.
        membership_after = set(
            session.execute(
                text(
                    "SELECT c.case_id FROM research_search_document_cases c "
                    "JOIN research_search_documents d "
                    "ON d.rowid = c.document_rowid "
                    "WHERE d.entity_id = :eid"
                ),
                {"eid": eid},
            ).scalars()
        )
        assert membership_after == membership_before
        assert case_b not in membership_after

    with factory() as uow:
        case_a_after = uow.cases.get(case_a)
        assert case_a_after.evidence_ids == case_a_evidence_ids
        assert case_a_after.updated_at == case_a_updated_at
        case_b_after = uow.cases.get(case_b)
        assert eid not in case_b_after.evidence_ids
        assert case_b_after.evidence_ids == ()
        assert uow.case_evidence_links.exists(case_a, eid)
        assert not uow.case_evidence_links.exists(case_b, eid)
