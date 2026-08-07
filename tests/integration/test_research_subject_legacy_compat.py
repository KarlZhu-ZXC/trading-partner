"""Legacy storage and payload compatibility for ResearchSubject terminology."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import create_engine, text

from application.dto.research import (
    SubjectUpdateCandidatePayload,
    candidate_payload_to_json,
    parse_candidate_payload,
)
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import CandidateKind, ResearchReportType, ResearchSubjectType
from domain.research.models import compute_report_content_sha256
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor


def test_repository_reads_historical_investment_cases_row(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    created_at = "2026-07-16T12:00:00.000000Z"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO investment_cases (
                    case_id, case_type, title, summary, status,
                    primary_instrument_id, topic_tags_json, created_at, updated_at,
                    created_by, archived_at, archived_reason, linked_case_ids_json,
                    evidence_ids_json, report_ids_json, event_ids_json,
                    decision_ids_json, schema_version
                ) VALUES (
                    :case_id, :case_type, :title, :summary, 'draft',
                    :instrument_id, '["legacy"]', :created_at, :created_at,
                    'user', NULL, NULL, '[]', '[]', '[]', '[]', '[]', 1
                )
                """
            ),
            {
                "case_id": "case_00000000-0000-7000-8000-000000000001",
                "case_type": "company",
                "title": "Legacy subject",
                "summary": "Historical row using physical case columns",
                "instrument_id": "equity:US:NVDA",
                "created_at": created_at,
            },
        )

    clock = FixedClock(datetime(2026, 7, 16, 12, tzinfo=UTC))
    with SqlAlchemyResearchUnitOfWork(
        engine, clock, SequentialIdGenerator(), DefaultSecretRedactor()
    ) as uow:
        subject = uow.subjects.get("case_00000000-0000-7000-8000-000000000001")

    assert subject.subject_type is ResearchSubjectType.COMPANY
    assert subject.subject_id.startswith("case_")
    assert subject.topic_tags == ("legacy",)
    engine.dispose()


def test_legacy_candidate_payload_fields_round_trip_without_hash_drift() -> None:
    legacy_json = (
        '{"kind":"case_status_change","action":"update","case_type":null,'
        '"new_status":null,"title":"Renamed","summary":null,'
        '"primary_instrument_id":null,"topic_tags":null,'
        '"linked_case_ids":["case_legacy"],"archived_reason":null}'
    )

    payload = parse_candidate_payload(legacy_json)

    assert isinstance(payload, SubjectUpdateCandidatePayload)
    assert payload.linked_subject_ids == ("case_legacy",)
    assert json.loads(candidate_payload_to_json(payload)) == json.loads(legacy_json)
    assert CandidateKind.SUBJECT_STATUS_CHANGE.value == "case_status_change"


def test_report_hash_retains_historical_case_id_key() -> None:
    as_of = datetime(2026, 7, 16, 12, tzinfo=UTC)
    actual = compute_report_content_sha256(
        subject_id="case_legacy",
        report_type=ResearchReportType.DEEP_DIVE,
        title="Title",
        summary="Summary",
        content_markdown="Body",
        as_of=as_of,
        evidence_ids=("evidence_2", "evidence_1"),
        thesis_revision_ids=("rev_1",),
    )
    legacy_payload = {
        "as_of": "2026-07-16T12:00:00Z",
        "case_id": "case_legacy",
        "content_markdown": "Body",
        "evidence_ids_sorted": ["evidence_1", "evidence_2"],
        "report_type": ResearchReportType.DEEP_DIVE.value,
        "summary": "Summary",
        "thesis_revision_ids_sorted": ["rev_1"],
        "title": "Title",
    }
    canonical = json.dumps(
        legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    assert actual == hashlib.sha256(canonical.encode()).hexdigest()
