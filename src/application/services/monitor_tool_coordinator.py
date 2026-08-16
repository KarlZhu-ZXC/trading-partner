"""MCP envelope boundary for Phase 2C Monitoring."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from application.dto.monitoring import (
    MonitorArchiveInput,
    MonitorCreateInput,
    MonitorDashboardDTO,
    MonitorDashboardInput,
    MonitorDetailDTO,
    MonitorEvaluateInput,
    MonitorEventListDTO,
    MonitorEventListInput,
    MonitorEventResolutionDTO,
    MonitorEventResolveInput,
    MonitorGetInput,
    MonitorListDTO,
    MonitorListInput,
    MonitorRunDTO,
    MonitorRunListDTO,
    MonitorRunListInput,
    MonitorUpdateInput,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._router_envelope_support import exception_envelope
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_service import MonitorService
from domain.common.enums import Freshness, SourceRole
from domain.common.ids import EntityIdPrefix


class MonitorToolCoordinator:
    def __init__(
        self,
        service: MonitorService,
        evaluator: MonitorEvaluationService,
        clock: Clock,
        id_generator: IdGenerator,
        redactor: SecretRedactor,
    ) -> None:
        self._service = service
        self._evaluator = evaluator
        self._clock = clock
        self._ids = id_generator
        self._redactor = redactor

    def create(self, request: MonitorCreateInput) -> ToolEnvelope[MonitorDetailDTO]:
        return self._database_call(lambda: self._service.create(request))

    def update(self, request: MonitorUpdateInput) -> ToolEnvelope[MonitorDetailDTO]:
        return self._database_call(lambda: self._service.update(request))

    def archive(self, request: MonitorArchiveInput) -> ToolEnvelope[MonitorDetailDTO]:
        return self._database_call(lambda: self._service.archive(request))

    def get(self, request: MonitorGetInput) -> ToolEnvelope[MonitorDetailDTO]:
        return self._database_call(lambda: self._service.get(request))

    def list(self, request: MonitorListInput) -> ToolEnvelope[MonitorListDTO]:
        return self._database_call(lambda: self._service.list(request))

    def dashboard(self, request: MonitorDashboardInput) -> ToolEnvelope[MonitorDashboardDTO]:
        return self._database_call(lambda: self._service.dashboard(request))

    def list_runs(self, request: MonitorRunListInput) -> ToolEnvelope[MonitorRunListDTO]:
        return self._database_call(lambda: self._service.list_runs(request))

    def list_events(self, request: MonitorEventListInput) -> ToolEnvelope[MonitorEventListDTO]:
        return self._database_call(lambda: self._service.list_events(request))

    def resolve_event(
        self, request: MonitorEventResolveInput
    ) -> ToolEnvelope[MonitorEventResolutionDTO]:
        return self._database_call(
            lambda: MonitorEventResolutionDTO.from_domain(self._service.resolve_event(request))
        )

    async def evaluate(self, request: MonitorEvaluateInput) -> ToolEnvelope[MonitorRunDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.as_of or self._clock.now()
        try:
            value = await self._evaluator.evaluate(request)
            warnings = tuple(
                WarningInfo(
                    code=code,
                    message="A Monitoring source or evaluation warning applies.",
                    details={},
                )
                for code in value.warning_codes
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=value.as_of,
                fetched_at=value.completed_at,
                freshness=Freshness.UNKNOWN,
                sources=(
                    SourceReference(
                        name="monitor_evaluator",
                        role=SourceRole.SUPPLEMENTAL,
                        retrieved_at=value.completed_at,
                    ),
                ),
                data=MonitorRunDTO.from_domain(value),
                degraded=bool(warnings or value.error_codes),
                warnings=warnings
                + tuple(
                    WarningInfo(
                        code=code,
                        message="A monitor rule could not be evaluated.",
                        details={},
                    )
                    for code in value.error_codes
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def _database_call[T](self, call: Callable[[], T]) -> ToolEnvelope[T]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH,
                sources=(
                    SourceReference(
                        name="monitor_database",
                        role=SourceRole.PRIMARY,
                        retrieved_at=self._clock.now(),
                    ),
                ),
                data=call(),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, now, exc)

    def _failure[T](
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[T]:
        return exception_envelope(
            request_id=request_id,
            as_of=as_of,
            exc=exc,
            clock=self._clock,
            redactor=self._redactor,
        )
