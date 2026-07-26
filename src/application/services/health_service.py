"""System health check service."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from application import __version__
from application.dto.health import HealthStatusDTO
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.database import Database
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.ports.settings import AppSettingsView
from domain.common.enums import Freshness, HealthState
from domain.common.errors import MigrationError, PersistenceError
from domain.common.ids import EntityIdPrefix

SearchBackendProbe = Callable[[], bool]
ComponentProbe = Callable[[], bool]


class HealthService:
    def __init__(
        self,
        database: Database,
        settings: AppSettingsView,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        app_version: str = __version__,
        search_backend_probe: SearchBackendProbe | None = None,
        component_probes: Mapping[str, ComponentProbe] | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._app_version = app_version
        self._search_backend_probe = search_backend_probe
        self._component_probes = dict(component_probes or {})

    def check(self) -> ToolEnvelope[HealthStatusDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        warnings: list[WarningInfo] = []

        database_state = HealthState.OK
        try:
            self._database.check_connection()
        except (PersistenceError, MigrationError) as exc:
            database_state = HealthState.ERROR
            warnings.append(
                WarningInfo(
                    code="DATABASE_HEALTH_ERROR",
                    message=self._secret_redactor.redact_text(exc.message),
                    details=self._secret_redactor.redact_mapping(exc.details),
                )
            )
        except Exception as exc:  # noqa: BLE001 — health must never raise
            database_state = HealthState.DEGRADED
            warnings.append(
                WarningInfo(
                    code="DATABASE_HEALTH_ERROR",
                    message=self._secret_redactor.redact_text(
                        str(exc) or type(exc).__name__
                    ),
                    details={},
                )
            )

        components: dict[str, HealthState] = {}
        if self._search_backend_probe is not None:
            search_state = HealthState.OK
            try:
                if not self._search_backend_probe():
                    search_state = HealthState.DEGRADED
            except Exception:  # noqa: BLE001 — never leak SQL/path/query
                search_state = HealthState.DEGRADED
            components["research_search"] = search_state
            if search_state is not HealthState.OK:
                warnings.append(
                    WarningInfo(
                        code="SEARCH_BACKEND_UNAVAILABLE",
                        message="Research search backend is unavailable",
                        details={"component": "research_search"},
                    )
                )

        for component, probe in sorted(self._component_probes.items()):
            state = HealthState.OK
            try:
                if not probe():
                    state = HealthState.DEGRADED
            except Exception:  # noqa: BLE001 — health probes never expose internals
                state = HealthState.DEGRADED
            components[component] = state
            if state is not HealthState.OK:
                warnings.append(
                    WarningInfo(
                        code="COMPONENT_UNAVAILABLE",
                        message="Configured component is unavailable",
                        details={"component": component},
                    )
                )

        if database_state is HealthState.ERROR:
            status = HealthState.ERROR
        elif database_state is HealthState.DEGRADED or any(
            state is not HealthState.OK for state in components.values()
        ):
            status = HealthState.DEGRADED
        else:
            status = HealthState.OK

        data = HealthStatusDTO(
            status=status,
            app_name=self._settings.app_name,
            version=self._app_version,
            environment=self._settings.app_env.value,
            database=database_state,
            components=components,
        )
        fetched_at = self._clock.now()
        degraded = status is not HealthState.OK

        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            sources=(),
            data=data,
            degraded=degraded,
            warnings=tuple(warnings),
        )
