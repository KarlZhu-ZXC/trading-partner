"""Durable-only DataQualityService tests."""

from __future__ import annotations

from types import SimpleNamespace

from application.dto.provider_route_history import ProviderRouteReceipt
from application.services.data_quality_service import DataQualityService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    DataCriticality,
    HealthState,
    Market,
    ResearchSubjectStatus,
    SourceRole,
    ThesisStatus,
    VendorId,
)
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorRuleStateValue,
    MonitorRunStatus,
    MonitorStatus,
)
from domain.trade_plan.enums import TradePlanStatus
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


class _Routes:
    is_durable = True

    def __init__(self, values=()) -> None:
        self.values = values

    def list_since(self, since, *, limit):
        assert limit == 500
        return self.values


class _ResearchCases:
    def __init__(self, values=()) -> None:
        self.values = values

    def list(self, *, include_archived, limit, offset):
        assert include_archived is True
        return self.values[offset : offset + limit]


class _ResearchTheses:
    def __init__(self, values=None) -> None:
        self.values = values or {}

    def list_by_subject(self, subject_id):
        return self.values.get(subject_id, ())


class _ResearchPlans:
    def __init__(self, values=None) -> None:
        self.values = values or {}

    def get_current_by_subject(self, subject_id):
        return self.values.get(subject_id)


class _ResearchUow:
    def __init__(self, subjects=(), theses=None, plans=None) -> None:
        self.subjects = _ResearchCases(subjects)
        self.theses = _ResearchTheses(theses)
        self.trade_plans = _ResearchPlans(plans)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def _service(
    snapshots=(), activity=(), monitors=(), runs=None, routes=(), research_uow=None
) -> DataQualityService:
    return DataQualityService(
        _Snapshots(snapshots),
        _Activity(activity),
        _Monitors(monitors, runs),
        _Routes(routes),
        lambda: research_uow or _ResearchUow(),
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
    assert envelope.data.provider_routes == ()
    assert "DURABLE_ONLY_NO_UPSTREAM_PROBE" in envelope.data.limitations


def test_recent_provider_fallback_is_aggregated_without_request_payload() -> None:
    now = FixedClock().now()
    route = ProviderRouteReceipt(
        route_id="provider_route_00000000-0000-7000-8000-000000000001",
        recorded_at=now,
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        operation_name="market.quote",
        instrument_id="equity:US:NVDA",
        criticality=DataCriticality.CORE,
        requested_chain=(VendorId.YFINANCE, VendorId.ALPHA_VANTAGE),
        ok=True,
        selected_vendor=VendorId.ALPHA_VANTAGE,
        selected_role=SourceRole.FALLBACK,
        cache_disposition=CacheDisposition.MISS,
        attempts=(),
        warning_codes=("FALLBACK_VENDOR_USED",),
        final_error_code=None,
    )

    envelope = _service(routes=(route,)).check()

    assert envelope.data is not None
    assert envelope.degraded is True
    quality = envelope.data.provider_routes[0]
    assert quality.execution_count == 1
    assert quality.fallback_count == 1
    assert quality.latest_selected_vendor in {VendorId.ALPHA_VANTAGE, "alpha_vantage"}
    assert "PROVIDER_FALLBACKS_RECENT" in {
        item.code for item in envelope.data.issues
    }
    assert "request_fingerprint" not in str(envelope.model_dump(mode="json"))


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
    actions = {item.code: item.recommended_action_code for item in envelope.data.issues}
    assert actions["ACCOUNT_SNAPSHOT_DEGRADED"] == "SYNC_ACCOUNTS"
    assert actions["ACCOUNT_ACTIVITY_COVERAGE_MISSING"] == "SYNC_ACCOUNT_TRANSACTIONS"


def test_non_tracking_case_with_live_judgment_is_reported() -> None:
    subject = SimpleNamespace(
        subject_id="case_00000000-0000-7000-8000-000000000001",
        status=ResearchSubjectStatus.DRAFT,
    )
    thesis = SimpleNamespace(
        thesis_id="thesis_00000000-0000-7000-8000-000000000001",
        status=ThesisStatus.ACTIVE,
    )
    plan = SimpleNamespace(
        thesis_id=thesis.thesis_id,
        status=TradePlanStatus.ACTIVE,
    )
    uow = _ResearchUow(
        subjects=(subject,),
        theses={subject.subject_id: (thesis,)},
        plans={subject.subject_id: plan},
    )

    envelope = _service(research_uow=uow).check()

    assert envelope.data is not None
    codes = {item.code for item in envelope.data.issues}
    assert "RESEARCH_CASE_WITH_LIVE_THESIS" in codes
    assert "RESEARCH_CASE_WITH_LIVE_TRADE_PLAN" in codes


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
    issue = next(
        item
        for item in envelope.data.issues
        if item.code == "MONITOR_CURRENT_VERSION_NEVER_EVALUATED"
    )
    assert issue.recommended_action_code == "EVALUATE_MONITOR"
    assert issue.automatic_recovery_expected is False


def test_repository_failure_is_redacted_and_does_not_probe_upstream() -> None:
    class _BrokenSnapshots:
        def latest_accounts(self):
            raise RuntimeError("database token=supersecret")

    service = DataQualityService(
        _BrokenSnapshots(),
        _Activity(),
        _Monitors(),
        _Routes(),
        lambda: _ResearchUow(),
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
        _Routes(),
        lambda: _ResearchUow(),
        FixedClock(),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )

    envelope = service.check()

    assert envelope.data is not None
    codes = {item.code for item in envelope.data.issues}
    assert "DATA_QUALITY_PERSISTENCE_ERROR" in codes
    assert "MONITOR_NEVER_EVALUATED" not in codes
