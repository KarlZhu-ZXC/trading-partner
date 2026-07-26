"""HealthService unit tests."""

from __future__ import annotations

from application.services.health_service import HealthService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import AppEnvironment, HealthState
from domain.common.errors import MigrationError, PersistenceError
from infrastructure.system.redactor import DefaultSecretRedactor


class _OkDb:
    def check_connection(self) -> None:
        return None


class _ErrDb:
    def check_connection(self) -> None:
        raise PersistenceError("db down api_key=supersecret")


class _MigrationErrDb:
    def check_connection(self) -> None:
        raise MigrationError("migration failed token=supersecret")


class _Settings:
    app_name = "tp"
    app_env = AppEnvironment.TEST
    mcp_server_name = "tp"


def test_health_ok() -> None:
    service = HealthService(
        database=_OkDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        app_version="0.1.0",
    )
    env = service.check()
    assert env.ok is True
    assert env.degraded is False
    assert env.data is not None
    assert env.data.status == HealthState.OK or env.data.status == "ok"
    assert env.data.database == HealthState.OK or env.data.database == "ok"
    # Direct construction without probe must not invent a false search component.
    assert env.data.components == {}
    assert env.market is None


def test_health_db_error_still_ok_envelope() -> None:
    service = HealthService(
        database=_ErrDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        app_version="0.1.0",
    )
    env = service.check()
    assert env.ok is True
    assert env.degraded is True
    assert env.data is not None
    assert env.data.status in {HealthState.ERROR, "error"}
    assert env.warnings
    assert env.warnings[0].code == "DATABASE_HEALTH_ERROR"
    assert "supersecret" not in env.warnings[0].message


def test_health_migration_error_maps_to_error_state() -> None:
    service = HealthService(
        database=_MigrationErrDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        app_version="0.1.0",
    )
    env = service.check()
    assert env.ok is True
    assert env.degraded is True
    assert env.data is not None
    assert env.data.database in {HealthState.ERROR, "error"}
    assert "supersecret" not in env.warnings[0].message


def test_health_search_probe_ok_component() -> None:
    service = HealthService(
        database=_OkDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        app_version="0.1.0",
        search_backend_probe=lambda: True,
    )
    env = service.check()
    assert env.ok is True
    assert env.degraded is False
    assert env.data is not None
    assert env.data.components["research_search"] in {HealthState.OK, "ok"}
    assert not any(w.code == "SEARCH_BACKEND_UNAVAILABLE" for w in env.warnings)


def test_health_search_probe_false_degrades_without_leaking_details() -> None:
    def _failing_probe() -> bool:
        raise RuntimeError("SELECT * FROM research_search_fts; path=/secret/db.sqlite")

    service = HealthService(
        database=_OkDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        app_version="0.1.0",
        search_backend_probe=_failing_probe,
    )
    env = service.check()
    assert env.ok is True
    assert env.degraded is True
    assert env.data is not None
    assert env.data.database in {HealthState.OK, "ok"}
    assert env.data.components["research_search"] in {
        HealthState.DEGRADED,
        "degraded",
    }
    assert env.data.status in {HealthState.DEGRADED, "degraded"}
    codes = [w.code for w in env.warnings]
    assert "SEARCH_BACKEND_UNAVAILABLE" in codes
    payload = env.model_dump(mode="json")
    text = str(payload)
    assert "SELECT" not in text
    assert "/secret/db.sqlite" not in text
    assert "research_search_fts" not in text


def test_health_db_error_dominates_search_degraded() -> None:
    service = HealthService(
        database=_ErrDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        app_version="0.1.0",
        search_backend_probe=lambda: False,
    )
    env = service.check()
    assert env.data is not None
    assert env.data.status in {HealthState.ERROR, "error"}
    assert env.data.database in {HealthState.ERROR, "error"}
    assert env.data.components["research_search"] in {
        HealthState.DEGRADED,
        "degraded",
    }


def test_health_component_capability_probes_are_source_separated() -> None:
    service = HealthService(
        database=_OkDb(),
        settings=_Settings(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        component_probes={
            "cross_asset.cme_reference": lambda: True,
            "cross_asset.dukascopy_spot": lambda: False,
        },
    )
    env = service.check()
    assert env.data is not None
    assert env.data.components["cross_asset.cme_reference"] in {HealthState.OK, "ok"}
    assert env.data.components["cross_asset.dukascopy_spot"] in {
        HealthState.DEGRADED,
        "degraded",
    }
    assert any(
        item.code == "COMPONENT_UNAVAILABLE"
        and item.details["component"] == "cross_asset.dukascopy_spot"
        for item in env.warnings
    )
