from datetime import UTC, date, datetime
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine

from application.dto.valuation import ValuationAssumptionsInput, ValuationSaveInput
from application.services.journal_service import JournalService
from application.services.valuation_service import ValuationService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import ResearchSubjectStatus, ResearchSubjectType
from domain.common.errors import IdempotencyConflict
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor


@pytest.mark.asyncio
async def test_immutable_versions_and_retry_survive_service_restart(migrated_sqlite_url):
    engine = create_engine(migrated_sqlite_url)
    now = datetime(2026, 9, 14, tzinfo=UTC)
    clock, ids, redactor = FixedClock(now), SequentialIdGenerator(), DefaultSecretRedactor()

    def factory():
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    subject = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="Synthetic valuation",
        summary="Test",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        created_at=now,
        updated_at=now,
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

    async def get(request):
        period = NS(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            filed_at=datetime(2026, 2, 1, tzinfo=UTC),
            accession="0000000000-26-000001",
            filing_form="10-K",
            currency="USD",
            line_items=(("eps_diluted", "2.5"),),
        )
        return NS(
            ok=True,
            data=NS(instrument_id="equity:US:NVDA", income=[period], balance_sheet=[]),
            sources=[NS(name="sec_edgar")],
            request_id="req_synthetic",
            degraded=False,
            warnings=[],
        )

    journal = JournalService(factory, clock, ids, redactor)
    service = ValuationService(factory, NS(get_fundamental_statements=get), journal, clock)
    source = await service.prepare(subject.subject_id)
    assumptions = ValuationAssumptionsInput(
        normalization_factor="1",
        normalization_step="0.2",
        pe_multiple="20",
        pe_step="5",
        business_model="operating_company",
        rationale="Synthetic user assumptions",
    )
    request = ValuationSaveInput(
        source_token=source["source_token"],
        assumptions=assumptions,
        idempotency_key="save-one",
        authorization_note="Save these assumptions",
        confirmed=True,
    )
    first = service.save(subject.subject_id, request)
    restarted = ValuationService(factory, NS(get_fundamental_statements=get), journal, clock)
    assert restarted.save(subject.subject_id, request)["version_id"] == first["version_id"]
    with pytest.raises(IdempotencyConflict):
        restarted.save(
            subject.subject_id, request.model_copy(update={"authorization_note": "Different"})
        )
    second_request = request.model_copy(
        update={
            "idempotency_key": "save-two",
            "supersedes_version_id": first["version_id"],
            "assumptions": assumptions.model_copy(update={"rationale": "Second version"}),
        }
    )
    second = service.save(subject.subject_id, second_request)
    history = restarted.history(subject.subject_id)["items"]
    assert len(history) == 2
    assert history[0]["version_id"] == second["version_id"]
    assert history[0]["supersedes_version_id"] == first["version_id"]
    assert history[1]["snapshot"]["assumptions"]["rationale"] == "Synthetic user assumptions"
    engine.dispose()
