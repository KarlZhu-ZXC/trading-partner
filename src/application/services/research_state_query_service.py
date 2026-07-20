"""Read-only research state snapshots and open-question listings."""

from __future__ import annotations

from application.dto.research import OpenQuestionDTO, OpenQuestionListDTO, ResearchStateDTO
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    build_research_state,
    envelope_failure,
    envelope_success,
)
from domain.common.ids import EntityIdPrefix


class ResearchStateQueryService:
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

    def get_state(
        self,
        case_id: str,
        *,
        include_archived_theses: bool = False,
        include_watchlist: bool = True,
    ) -> ToolEnvelope[ResearchStateDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                data = build_research_state(
                    uow,
                    case_id,
                    include_archived_theses=include_archived_theses,
                    include_watchlist=include_watchlist,
                )
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def list_open_questions(self, case_id: str) -> ToolEnvelope[OpenQuestionListDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                uow.cases.get(case_id)
                items = uow.questions.list_by_case(case_id)
                data = OpenQuestionListDTO(items=OpenQuestionDTO.from_domain_list(items))
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
