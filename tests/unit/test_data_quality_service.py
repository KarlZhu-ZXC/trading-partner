"""Durable-only DataQualityService tests."""

from __future__ import annotations

from types import SimpleNamespace

from application.services.data_quality_service import DataQualityService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import HealthState, VendorId
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorRuleStateValue,
    MonitorRunStatus,
    MonitorStatus,
)
from infrastructure.system.redactor import DefaultSecretRedactor


class _Snapshots:
    def __init__(self, values=()) -> None:
        self.values = values

    def latest_accounts(self):
        return self.values


class _Activity:
    def __init__(self, values=()) -> None:
        self.values = values

    def list_coverage(self, *, providers, account_refs, limit):
        assert providers == ()
        assert account_refs == ()
        assert limit == 500
        return self.values


class _Monitors:
    def __init__(self, values=(), runs=None) -> None:
        self.values = values
        self.runs = runs or {}

    def list_current(self, status):
        assert status is MonitorStatus.ACTIVE
        return self.values

    def latest_run_for_monitor(self, monitor_id):
        return self.runs.get(monitor_id)


def _service(snapshots=(), activity=(), monitors=(), runs=None) -> DataQualityService:
    return DataQualityService(
        _Snapshots(snapshots),
        _Activity(activity),
        _Monitors(monitors, runs),
        FixedClock(),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )


def test_empty_durable_ledgers_are_healthy_not_fabricated_gaps() -> None:
    envelope = _service().check()

    assert envelope.degraded is False
    assert envelope.data is not None
    assert envelope.data.status in {HealthState.OK, "ok"}
    assert envelope.data.account_snapshots == ()
    assert envelope.data.account_activity == ()
    assert envelope.data.monitors == ()
    assert "DURABLE_ONLY_NO_UPSTREAM_PROBE" in envelope.data.limitations


def test_account_and_monitor_gaps_are_machine_readable() -> None:
    now = FixedClock().now()
    snapshot = SimpleNamespace(
        snapshot_id="snapshot_1",
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        account_as_of=now,
        fetched_at=now,
        positions=(SimpleNamespace(market_value=None, market_price_at=None),),
        net_assets=None,
        degraded=True,
        warning_codes=("PRICE_TIME_UNAVAILABLE",),
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_1",
        version=1,
        name="Never evaluated",
        cadence=MonitorCadence.US_POST_MARKET,
        status=MonitorStatus.ACTIVE,
        primary_instrument_id="equity:US:TTWO",
        rules=(object(),),
        valid_until=None,
        created_at=now,
    )

    envelope = _service(snapshots=(snapshot,), monitors=(monitor,)).check()

    assert envelope.degraded is True
    assert envelope.data is not None
    assert envelope.data.status in {HealthState.DEGRADED, "degraded"}
    codes = {item.code for item in envelope.data.issues}
    assert {
        "ACCOUNT_SNAPSHOT_DEGRADED",
        "ACCOUNT_VALUATION_INCOMPLETE",
        "ACCOUNT_PRICE_TIME_INCOMPLETE",
        "ACCOUNT_NAV_UNAVAILABLE",
        "ACCOUNT_ACTIVITY_COVERAGE_MISSING",
        "MONITOR_NEVER_EVALUATED",
    } <= codes
    assert envelope.data.account_snapshots[0].valuation_coverage_ratio == 0


def test_old_definition_run_does_not_cover_the_current_monitor_version() -> None:
    now = FixedClock().now()
    monitor = SimpleNamespace(
        monitor_id="monitor_1",
        version=2,
        name="Revised monitor",
        cadence=MonitorCadence.US_POST_MARKET,
        status=MonitorStatus.ACTIVE,
        primary_instrument_id="equity:US:TTWO",
        rules=(object(),),
        valid_until=None,
        created_at=now,
    )
    old_observation = SimpleNamespace(
        monitor_id="monitor_1",
        monitor_version=1,
        state=MonitorRuleStateValue.QUIET,
        warning_codes=(),
        error_codes=(),
    )
    run = SimpleNamespace(
        run_id="run_1",
        completed_at=now,
        status=MonitorRunStatus.SUCCEEDED,
        observations=(old_observation,),
        warning_codes=(),
        error_codes=(),
        observation_history_complete=True,
    )

    envelope = _service(monitors=(monitor,), runs={"monitor_1": run}).check()

    assert envelope.data is not None
    quality = envelope.data.monitors[0]
    assert quality.latest_evaluated_version == 1
    assert quality.current_version_evaluated is False
    assert "MONITOR_CURRENT_VERSION_NEVER_EVALUATED" in {
        item.code for item in envelope.data.issues
    }


def test_repository_failure_is_redacted_and_does_not_probe_upstream() -> None:
    class _BrokenSnapshots:
        def latest_accounts(self):
            raise RuntimeError("database token=supersecret")

    service = DataQualityService(
        _BrokenSnapshots(),
        _Activity(),
        _Monitors(),
        FixedClock(),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )

    envelope = service.check()

    assert envelope.degraded is True
    assert envelope.data is not None
    assert envelope.data.status in {HealthState.ERROR, "error"}
    payload = str(envelope.model_dump(mode="json"))
    assert "supersecret" not in payload
    assert "DATA_QUALITY_PERSISTENCE_ERROR" in payload


def test_monitor_run_read_failure_is_not_mislabeled_as_never_evaluated() -> None:
    now = FixedClock().now()
    monitor = SimpleNamespace(
        monitor_id="monitor_1",
        name="Read failure",
        version=1,
        cadence=MonitorCadence.US_POST_MARKET,
        status=MonitorStatus.ACTIVE,
        primary_instrument_id="equity:US:TTWO",
        rules=(object(),),
        valid_until=None,
        created_at=now,
    )

    class _BrokenMonitorRuns(_Monitors):
        def latest_run_for_monitor(self, monitor_id):
            raise RuntimeError("token=supersecret")

    service = DataQualityService(
        _Snapshots(),
        _Activity(),
        _BrokenMonitorRuns((monitor,)),
        FixedClock(),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )

    envelope = service.check()

    assert envelope.data is not None
    codes = {item.code for item in envelope.data.issues}
    assert "DATA_QUALITY_PERSISTENCE_ERROR" in codes
    assert "MONITOR_NEVER_EVALUATED" not in codes
