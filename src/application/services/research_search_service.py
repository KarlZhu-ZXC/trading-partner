"""ResearchSearchService — read-only search and report hydration (Phase 1C C4b1).

Delegates text/structured search to ResearchSearchIndex. get_report hydrates from
ResearchReportRepository only. No commit, no audit writes.
"""

from __future__ import annotations

from application.dto.research_memory import (
    ResearchReportDTO,
    ResearchSearchPageDTO,
    ResearchSearchQuery,
)
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
)
from domain.common.ids import EntityIdPrefix


class ResearchSearchService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._redactor = secret_redactor

    def search(
        self, query: ResearchSearchQuery
    ) -> ToolEnvelope[ResearchSearchPageDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                page = uow.search_index.search(query)
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=page,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def get_report(
        self, report_id: str
    ) -> ToolEnvelope[ResearchReportDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                report = uow.reports.get(report_id)
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchReportDTO.from_domain(report),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
