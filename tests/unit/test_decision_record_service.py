"""Phase 1C C4b2 unit tests for DecisionRecordService."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research_memory import ResearchSearchQuery
from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY
from application.services._research_memory_write_support import (
    compute_decision_idempotency_payload_sha256,
)
from application.services.decision_record_service import DecisionRecordService
from application.services.evidence_service import EvidenceService
from application.services.research_archive_service import ResearchArchiveService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfidenceBand,
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    InvestmentRating,
    ReliabilityLevel,
    ResearchReportType,
    ResearchSearchEntityType,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    ResearchSubject,
    Thesis,
    ThesisRevision,
)
from infrastructure.persistence.orm import DecisionRecordRow, SystemAuditLogRow
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
FUTURE = NOW + timedelta(hours=4)
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DECISION_SERVICE_PATH = (
    PROJECT_ROOT / "src" / "application" / "services" / "decision_record_service.py"
)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def harness(migrated_sqlite_url: str):  # type: ignore[no-untyped-def]
    eng = create_engine(migrated_sqlite_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    decision = DecisionRecordService(factory, clock, ids, redactor)
    evidence = EvidenceService(factory, clock, ids, redactor)
    archive = ResearchArchiveService(factory, clock, ids, redactor)
    yield decision, evidence, archive, factory, clock, ids, eng
    eng.dispose()


def _create_subject(factory, ids, clock) -> str:  # type: ignore[no-untyped-def]
    subject = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="Case",
        summary="Summary",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=US,
        topic_tags=("ai",),
        created_at=clock.now(),
        updated_at=clock.now(),
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_subject_ids=(),
        evidence_ids=(),
        report_ids=(),
        event_ids=(),
        decision_ids=(),
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.commit()
    return subject.subject_id


def _link_evidence(evidence: EvidenceService, *, subject_id: str, title: str = "ev") -> str:
    env = evidence.record_evidence(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title=title,
        summary=f"summary {title}",
        content_text="body",
        structured_data_json=None,
        source_name="mock_us",
        source_vendor="mock_us",
        source_record_id=None,
        source_url=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(US,),
        topic_tags=("gpu",),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.9"),
        supersedes_evidence_id=None,
        recorded_by="provider:mock_us",
        subject_ids=(subject_id,),
        observed_at=EARLIER,
    )
    assert env.ok and env.data is not None
    return env.data.evidence_id


def _add_revision(
    factory,
    ids,
    *,
    subject_id: str,
    confirmed_at: datetime = EARLIER,
    role: ThesisRole = ThesisRole.PRIMARY,
) -> str:  # type: ignore[no-untyped-def]
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    thesis = Thesis(
        thesis_id=thesis_id,
        subject_id=subject_id,
        title="Primary",
        role=role,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=confirmed_at,
        updated_at=confirmed_at,
        archived_at=None,
    )
    revision = ThesisRevision(
        revision_id=rev_id,
        thesis_id=thesis_id,
        subject_id=subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Demand structural",
        rationale="Capex",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=confirmed_at,
        confirmed_at=confirmed_at,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="Watch GM",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.commit()
    return rev_id


def _base_append(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "subject_id": "case_placeholder",
        "decision_type": DecisionType.WATCH,
        "title": "Watch NVDA",
        "rationale": "Need more evidence",
        "decided_at": EARLIER,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "primary_instrument_id": US,
        "thesis_revision_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "supersedes_decision_id": None,
        "position_context_snapshot_id": None,
        "idempotency_key": "  Decision-Key-1  ",
    }
    base.update(overrides)
    return base


def test_append_happy_path_cache_and_execution_effect_false(harness) -> None:  # type: ignore[no-untyped-def]
    decision, evidence, _archive, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _link_evidence(evidence, subject_id=subject_id)
    env = decision.append(
        **_base_append(
            subject_id=subject_id,
            evidence_ids=(eid,),
            rationale="RATIONALE_SECRET_BODY",
        )
    )
    assert env.ok and env.data is not None
    assert env.data.execution_effect is False
    assert env.data.recorded_at == NOW
    assert env.data.decided_at == EARLIER

    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert env.data.decision_id in subject.decision_ids
        assert subject.updated_at == NOW

    with Session(eng) as session:
        audits = session.scalars(select(SystemAuditLogRow)).all()
        dec_audits = [a for a in audits if a.event_type == "phase1c.decision.recorded"]
        assert len(dec_audits) == 1
        payload = json.loads(dec_audits[0].payload_json)
        assert payload["actor"] == "user"
        assert payload["confirmed_by"] == "user"
        assert payload["idempotency_key"] == "decision-key-1"
        assert "RATIONALE_SECRET_BODY" not in dec_audits[0].payload_json


def test_decided_by_gate_rejects_codex(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = decision.append(**_base_append(subject_id=subject_id, decided_by="codex"))
    assert env.ok is False
    assert any(e.code == "UNAUTHORIZED_REVIEWER" for e in env.errors)


def test_strict_review_required_for_trading_intent(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    bad = decision.append(
        **_base_append(
            subject_id=subject_id,
            decision_type=DecisionType.INITIATE_INTENT,
            confirmation_mode=ConfirmationMode.NORMAL,
            idempotency_key="strict-bad",
        )
    )
    assert bad.ok is False
    # Domain DataContractError maps to envelope failure
    assert bad.errors

    good = decision.append(
        **_base_append(
            subject_id=subject_id,
            decision_type=DecisionType.INITIATE_INTENT,
            confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            idempotency_key="strict-good",
        )
    )
    assert good.ok is True
    assert good.data is not None
    assert good.data.execution_effect is False
    assert good.data.confirmation_mode in {
        ConfirmationMode.STRICT_REVIEW,
        "strict_review",
    }


def test_normal_only_types_reject_strict(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = decision.append(
        **_base_append(
            subject_id=subject_id,
            decision_type=DecisionType.WATCH,
            confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            idempotency_key="watch-strict",
        )
    )
    assert env.ok is False


def test_same_key_same_payload_after_clock_no_reindex_or_cache(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    first = decision.append(**_base_append(subject_id=subject_id, rationale="stable"))
    assert first.ok and first.data is not None
    did = first.data.decision_id
    recorded = first.data.recorded_at

    with factory() as uow:
        case_before = uow.subjects.get(subject_id)
        assert case_before.decision_ids == (did,)
        assert case_before.updated_at == recorded
        page_before = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject_id,
                entity_types=(ResearchSearchEntityType.DECISION,),
            )
        )
        total_before = page_before.total

    clock.advance(7200)
    second = decision.append(**_base_append(subject_id=subject_id, rationale="stable"))
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_IDEMPOTENCY_KEY in second.warnings
    assert second.data is not None
    assert second.data.decision_id == did
    assert second.data.recorded_at == recorded

    with factory() as uow:
        case_after = uow.subjects.get(subject_id)
        assert case_after.decision_ids == (did,)
        assert case_after.updated_at == recorded  # not advanced clock
        page_after = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject_id,
                entity_types=(ResearchSearchEntityType.DECISION,),
            )
        )
        assert page_after.total == total_before

    with Session(eng) as session:
        assert len(session.scalars(select(DecisionRecordRow)).all()) == 1
        dec_audits = [
            a
            for a in session.scalars(select(SystemAuditLogRow)).all()
            if a.event_type == "phase1c.decision.recorded"
        ]
        assert len(dec_audits) == 1


def test_same_key_different_payload_conflicts(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    first = decision.append(**_base_append(subject_id=subject_id, rationale="one"))
    assert first.ok
    conflict = decision.append(**_base_append(subject_id=subject_id, rationale="two"))
    assert conflict.ok is False
    assert any(e.code == "DUPLICATE_IDEMPOTENCY_KEY" for e in conflict.errors)


def test_same_key_tuple_order_only_is_duplicate_not_conflict(harness) -> None:  # type: ignore[no-untyped-def]
    """Set id tuples differ only in order → same hash; domain keeps first-seen."""
    decision, evidence, archive, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid1 = _link_evidence(evidence, subject_id=subject_id, title="ev-a")
    eid2 = _link_evidence(evidence, subject_id=subject_id, title="ev-b")
    rid1 = _add_revision(factory, ids, subject_id=subject_id)
    # Second revision needs distinct thesis ids from helper (new each call).
    rid2 = _add_revision(factory, ids, subject_id=subject_id, role=ThesisRole.COMPETITOR)
    rep1 = archive.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="rep-a",
        summary="s",
        content_markdown="body a",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid1,),
        thesis_revision_ids=(rid1,),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    rep2 = archive.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="rep-b",
        summary="s",
        content_markdown="body b",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid2,),
        thesis_revision_ids=(rid2,),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep1.ok and rep1.data and rep2.ok and rep2.data
    report_a = rep1.data.report_id
    report_b = rep2.data.report_id

    first = decision.append(
        **_base_append(
            subject_id=subject_id,
            rationale="order-stable",
            evidence_ids=(eid1, eid2),
            thesis_revision_ids=(rid1, rid2),
            report_ids=(report_a, report_b),
            idempotency_key="order-d-key",
        )
    )
    assert first.ok and first.data is not None
    did = first.data.decision_id
    recorded = first.data.recorded_at
    assert first.data.evidence_ids == (eid1, eid2)
    assert first.data.thesis_revision_ids == (rid1, rid2)
    assert first.data.report_ids == (report_a, report_b)

    with factory() as uow:
        case_before = uow.subjects.get(subject_id)
        assert case_before.decision_ids == (did,)
        page_before = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject_id,
                entity_types=(ResearchSearchEntityType.DECISION,),
            )
        )
        total_before = page_before.total

    clock.advance(120)
    second = decision.append(
        **_base_append(
            subject_id=subject_id,
            rationale="order-stable",
            evidence_ids=(eid2, eid1),
            thesis_revision_ids=(rid2, rid1),
            report_ids=(report_b, report_a),
            idempotency_key="order-d-key",
        )
    )
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_IDEMPOTENCY_KEY in second.warnings
    assert second.data is not None
    assert second.data.decision_id == did
    assert second.data.recorded_at == recorded
    # First-write first-seen order preserved on the returned entity.
    assert second.data.evidence_ids == (eid1, eid2)
    assert second.data.thesis_revision_ids == (rid1, rid2)
    assert second.data.report_ids == (report_a, report_b)

    with factory() as uow:
        stored = uow.decisions.get(did)
        assert stored.evidence_ids == (eid1, eid2)
        assert stored.thesis_revision_ids == (rid1, rid2)
        assert stored.report_ids == (report_a, report_b)
        case_after = uow.subjects.get(subject_id)
        assert case_after.decision_ids == (did,)
        assert case_after.updated_at == recorded
        page_after = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject_id,
                entity_types=(ResearchSearchEntityType.DECISION,),
            )
        )
        assert page_after.total == total_before

    with Session(eng) as session:
        assert len(session.scalars(select(DecisionRecordRow)).all()) == 1
        dec_audits = [
            a
            for a in session.scalars(select(SystemAuditLogRow)).all()
            if a.event_type == "phase1c.decision.recorded"
        ]
        assert len(dec_audits) == 1


def test_hash_coherence_with_redaction_and_normalized_tuples(harness) -> None:  # type: ignore[no-untyped-def]
    decision, evidence, archive, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _link_evidence(evidence, subject_id=subject_id, title="hash-ev")
    rid = _add_revision(factory, ids, subject_id=subject_id)
    rep = archive.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="hash-rep",
        summary="s",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(rid,),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep.ok and rep.data

    env = decision.append(
        **_base_append(
            subject_id=subject_id,
            evidence_ids=(eid, eid, "  "),
            thesis_revision_ids=(rid,),
            report_ids=(rep.data.report_id,),
            rationale="password=secret_should_redact",
            idempotency_key="  Hash-Dec  ",
        )
    )
    assert env.ok and env.data is not None
    stored = env.data
    entry_type = (
        DecisionType(stored.decision_type)
        if not isinstance(stored.decision_type, DecisionType)
        else stored.decision_type
    )
    mode = (
        ConfirmationMode(stored.confirmation_mode)
        if not isinstance(stored.confirmation_mode, ConfirmationMode)
        else stored.confirmation_mode
    )
    recomputed = compute_decision_idempotency_payload_sha256(
        subject_id=stored.subject_id,
        decision_type=entry_type,
        title=stored.title,
        rationale=stored.rationale,
        decided_at=stored.decided_at,
        decided_by=stored.decided_by,
        confirmation_mode=mode,
        primary_instrument_id=stored.primary_instrument_id,
        thesis_revision_ids=stored.thesis_revision_ids,
        evidence_ids=stored.evidence_ids,
        report_ids=stored.report_ids,
        supersedes_decision_id=stored.supersedes_decision_id,
        position_context_snapshot_id=stored.position_context_snapshot_id,
    )
    with Session(eng) as session:
        row = session.get(DecisionRecordRow, stored.decision_id)
        assert row is not None
        assert row.idempotency_payload_sha256 == recomputed
        assert row.idempotency_key == "hash-dec"
    assert stored.evidence_ids == (eid,)


def test_cross_case_and_future_refs_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    decision, evidence, _ar, factory, clock, ids, _eng = harness
    case_a = _create_subject(factory, ids, clock)
    case_b = _create_subject(factory, ids, clock)
    eid_b = _link_evidence(evidence, subject_id=case_b, title="b-ev")
    cross = decision.append(
        **_base_append(
            subject_id=case_a,
            evidence_ids=(eid_b,),
            idempotency_key="cross-ev",
        )
    )
    assert cross.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in cross.errors)

    future_rev = _add_revision(factory, ids, subject_id=case_a, confirmed_at=FUTURE)
    future = decision.append(
        **_base_append(
            subject_id=case_a,
            thesis_revision_ids=(future_rev,),
            idempotency_key="future-rev",
        )
    )
    assert future.ok is False
    assert any(e.code == "HISTORICAL_VISIBILITY_VIOLATION" for e in future.errors)


def test_decided_at_after_recorded_at_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = decision.append(
        **_base_append(
            subject_id=subject_id,
            decided_at=FUTURE,
            idempotency_key="future-decided",
        )
    )
    assert env.ok is False
    assert any(e.code == "INPUT_VALIDATION_ERROR" for e in env.errors)


def test_search_and_audit_failure_rollback(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    real_factory = factory

    class BoomSearchUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> BoomSearchUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def search_index(self) -> Any:
            mock = MagicMock()
            mock.index.side_effect = RuntimeError("search boom")
            return mock

    boom = DecisionRecordService(
        lambda: BoomSearchUow(real_factory()),
        clock,
        SequentialIdGenerator(start=7000),
        DefaultSecretRedactor(),
    )
    env = boom.append(
        **_base_append(
            subject_id=subject_id,
            title="rollback-decision",
            idempotency_key="rb-d",
        )
    )
    assert env.ok is False
    with Session(eng) as session:
        assert "rollback-decision" not in session.scalars(select(DecisionRecordRow.title)).all()
    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert subject.decision_ids == ()

    class BoomAuditUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> BoomAuditUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def audit(self) -> Any:
            mock = MagicMock()
            mock.append.side_effect = RuntimeError("audit boom")
            return mock

    boom2 = DecisionRecordService(
        lambda: BoomAuditUow(real_factory()),
        clock,
        SequentialIdGenerator(start=8000),
        DefaultSecretRedactor(),
    )
    env2 = boom2.append(
        **_base_append(
            subject_id=subject_id,
            title="audit-rb-decision",
            idempotency_key="audit-rb-d",
        )
    )
    assert env2.ok is False
    with Session(eng) as session:
        assert "audit-rb-decision" not in session.scalars(select(DecisionRecordRow.title)).all()
        count = session.execute(
            text("SELECT COUNT(*) FROM research_search_documents WHERE entity_type='decision'")
        ).scalar_one()
        assert count == 0
    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert subject.decision_ids == ()


def test_no_trading_or_order_imports() -> None:
    source = DECISION_SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "broker",
        "order",
        "orders",
        "trading",
        "execution",
        "fill",
        "fills",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & forbidden), f"forbidden imports: {imported & forbidden}"
    assert "execution_effect" in source or "DecisionRecordDTO" in source


def test_a_share_primary_instrument(harness) -> None:  # type: ignore[no-untyped-def]
    decision, _ev, _ar, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = decision.append(
        **_base_append(
            subject_id=subject_id,
            primary_instrument_id=A_SHARE,
            idempotency_key="a-share-d",
        )
    )
    assert env.ok and env.data is not None
    assert env.data.primary_instrument_id == A_SHARE
    assert env.data.execution_effect is False
