"""Read-only quality ledger over durable account and Monitor facts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from application.dto.data_quality import (
    AccountActivityQualityDTO,
    AccountSnapshotQualityDTO,
    DataQualityCenterDTO,
    DataQualityIssueDTO,
    MonitorQualityDTO,
    ProviderRouteQualityDTO,
)
from application.dto.provider_route_history import ProviderRouteReceipt
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.monitor_repository import MonitorRepository
from application.ports.provider_route_history_store import ProviderRouteHistoryStore
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import DataCategory, Freshness, HealthState, Market, VendorId
from domain.common.ids import EntityIdPrefix
from domain.monitoring.enums import (
    MonitorRuleStateValue,
    MonitorRunStatus,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition
from domain.portfolio.enums import AccountActivityCoverageStatus
from domain.portfolio.models import AccountActivityCoverageReceipt, AccountSnapshot


class DataQualityService:
    """Summarize persisted evidence without contacting any upstream Provider."""

    _BASE_LIMITATIONS = (
        "DURABLE_ONLY_NO_UPSTREAM_PROBE",
        "ACCOUNT_AGE_REPORTED_WITHOUT_GLOBAL_STALENESS_THRESHOLD",
    )
    _ROUTE_WINDOW = timedelta(hours=24)
    _ROUTE_LIMIT = 500

    def __init__(
        self,
        account_snapshots: AccountSnapshotRepository,
        account_transactions: AccountTransactionRepository,
        monitors: MonitorRepository,
        provider_routes: ProviderRouteHistoryStore,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._account_snapshots = account_snapshots
        self._account_transactions = account_transactions
        self._monitors = monitors
        self._provider_routes = provider_routes
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor

    def check(self) -> ToolEnvelope[DataQualityCenterDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        issues: list[DataQualityIssueDTO] = []
        repository_errors = False

        try:
            snapshots = self._account_snapshots.latest_accounts()
        except Exception as exc:  # noqa: BLE001 — health view must not raise or leak
            snapshots = ()
            repository_errors = True
            issues.append(self._persistence_issue("account_snapshots", exc, now))

        try:
            coverage_receipts = self._account_transactions.list_coverage(
                providers=(), account_refs=(), limit=500
            )
        except Exception as exc:  # noqa: BLE001 — health view must not raise or leak
            coverage_receipts = ()
            repository_errors = True
            issues.append(self._persistence_issue("account_activity", exc, now))

        try:
            monitor_definitions = self._monitors.list_current(MonitorStatus.ACTIVE)
        except Exception as exc:  # noqa: BLE001 — health view must not raise or leak
            monitor_definitions = ()
            repository_errors = True
            issues.append(self._persistence_issue("monitors", exc, now))

        route_window_start = now - self._ROUTE_WINDOW
        try:
            route_receipts = self._provider_routes.list_since(
                route_window_start, limit=self._ROUTE_LIMIT
            )
        except Exception as exc:  # noqa: BLE001 — health view must not raise or leak
            route_receipts = ()
            repository_errors = True
            issues.append(self._persistence_issue("provider_routes", exc, now))

        account_items = tuple(
            self._account_quality(snapshot, now, issues) for snapshot in snapshots
        )
        latest_coverage: dict[
            tuple[VendorId, str], AccountActivityCoverageReceipt
        ] = {}
        for receipt in coverage_receipts:
            key = (receipt.provider, receipt.account_ref)
            current = latest_coverage.get(key)
            if current is None or receipt.fetched_at > current.fetched_at:
                latest_coverage[key] = receipt
        activity_items = tuple(
            self._activity_quality(receipt, issues) for receipt in latest_coverage.values()
        )
        for snapshot in snapshots:
            key = (snapshot.provider, snapshot.account_ref)
            if key not in latest_coverage:
                issues.append(
                    DataQualityIssueDTO(
                        code="ACCOUNT_ACTIVITY_COVERAGE_MISSING",
                        severity=HealthState.DEGRADED,
                        scope="account_activity",
                        subject_ref=snapshot.account_ref,
                        observed_at=snapshot.fetched_at,
                        detail="No durable activity coverage receipt exists for this account.",
                    )
                )

        monitor_items = tuple(
            self._monitor_quality(monitor, now, issues) for monitor in monitor_definitions
        )
        route_items = self._provider_route_quality(
            route_receipts, route_window_start, issues
        )
        route_window_truncated = len(route_receipts) == self._ROUTE_LIMIT
        limitations = list(self._BASE_LIMITATIONS)
        if not self._provider_routes.is_durable:
            limitations.append("PROVIDER_ROUTE_HISTORY_NOT_DURABLE")
        if route_window_truncated:
            limitations.append("PROVIDER_ROUTE_HISTORY_WINDOW_TRUNCATED")

        if repository_errors or any(item.severity == HealthState.ERROR for item in issues):
            status = HealthState.ERROR
        elif issues:
            status = HealthState.DEGRADED
        else:
            status = HealthState.OK
        warning_codes = tuple(sorted({item.code for item in issues}))
        warnings = (
            (
                WarningInfo(
                    code="DATA_QUALITY_ISSUES",
                    message="Durable data-quality checks found incomplete or unavailable evidence.",
                    details={"issue_count": len(issues), "issue_codes": warning_codes},
                ),
            )
            if issues
            else ()
        )
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=now,
            fetched_at=self._clock.now(),
            freshness=Freshness.FRESH,
            sources=(),
            data=DataQualityCenterDTO(
                status=status,
                generated_at=now,
                account_snapshots=account_items,
                account_activity=activity_items,
                monitors=monitor_items,
                provider_routes=route_items,
                provider_route_window_truncated=route_window_truncated,
                issues=tuple(issues),
                limitations=tuple(limitations),
            ),
            degraded=status is not HealthState.OK,
            warnings=warnings,
        )

    @staticmethod
    def _provider_route_quality(
        receipts: tuple[ProviderRouteReceipt, ...],
        window_start: datetime,
        issues: list[DataQualityIssueDTO],
    ) -> tuple[ProviderRouteQualityDTO, ...]:
        groups: dict[tuple[Market, DataCategory], list[ProviderRouteReceipt]] = {}
        for receipt in receipts:
            groups.setdefault((receipt.market, receipt.category), []).append(receipt)
        output: list[ProviderRouteQualityDTO] = []
        for (market, category), items in sorted(
            groups.items(), key=lambda item: (item[0][0].value, item[0][1].value)
        ):
            items.sort(key=lambda item: (item.recorded_at, item.route_id), reverse=True)
            latest = items[0]
            failures = sum(not item.ok for item in items)
            fallbacks = sum(item.used_fallback for item in items)
            warning_codes = tuple(
                sorted({code for item in items for code in item.warning_codes})
            )
            subject = f"{market.value}:{category.value}"
            if failures:
                issues.append(
                    DataQualityIssueDTO(
                        code="PROVIDER_ROUTE_FAILURES_RECENT",
                        severity=HealthState.DEGRADED,
                        scope="provider_route",
                        subject_ref=subject,
                        observed_at=latest.recorded_at,
                        detail="One or more Provider routes failed in the last 24 hours.",
                    )
                )
            if fallbacks:
                issues.append(
                    DataQualityIssueDTO(
                        code="PROVIDER_FALLBACKS_RECENT",
                        severity=HealthState.DEGRADED,
                        scope="provider_route",
                        subject_ref=subject,
                        observed_at=latest.recorded_at,
                        detail="One or more Provider routes used a fallback in the last 24 hours.",
                    )
                )
            output.append(
                ProviderRouteQualityDTO(
                    market=market,
                    category=category,
                    window_start=window_start,
                    latest_at=latest.recorded_at,
                    execution_count=len(items),
                    success_count=len(items) - failures,
                    failure_count=failures,
                    fallback_count=fallbacks,
                    cache_count=sum(item.used_cache for item in items),
                    latest_selected_vendor=latest.selected_vendor,
                    latest_error_code=latest.final_error_code,
                    warning_codes=warning_codes,
                )
            )
        return tuple(output)

    def _account_quality(
        self,
        snapshot: AccountSnapshot,
        now: datetime,
        issues: list[DataQualityIssueDTO],
    ) -> AccountSnapshotQualityDTO:
        positions = snapshot.positions
        valued = sum(item.market_value is not None for item in positions)
        timestamped = sum(item.market_price_at is not None for item in positions)
        total = len(positions)
        ratio = None if total == 0 else Decimal(valued) / Decimal(total)
        time_ratio = None if total == 0 else Decimal(timestamped) / Decimal(total)
        age_seconds = max(0, int((now - snapshot.account_as_of).total_seconds()))
        if snapshot.degraded:
            issues.append(
                DataQualityIssueDTO(
                    code="ACCOUNT_SNAPSHOT_DEGRADED",
                    severity=HealthState.DEGRADED,
                    scope="account_snapshot",
                    subject_ref=snapshot.account_ref,
                    observed_at=snapshot.fetched_at,
                    detail="The latest durable account snapshot carries degraded=true.",
                )
            )
        if valued < total:
            issues.append(
                DataQualityIssueDTO(
                    code="ACCOUNT_VALUATION_INCOMPLETE",
                    severity=HealthState.DEGRADED,
                    scope="account_snapshot",
                    subject_ref=snapshot.account_ref,
                    observed_at=snapshot.fetched_at,
                    detail="One or more positions lack a persisted market value.",
                )
            )
        if timestamped < total:
            issues.append(
                DataQualityIssueDTO(
                    code="ACCOUNT_PRICE_TIME_INCOMPLETE",
                    severity=HealthState.DEGRADED,
                    scope="account_snapshot",
                    subject_ref=snapshot.account_ref,
                    observed_at=snapshot.fetched_at,
                    detail="One or more positions lack an auditable market-price timestamp.",
                )
            )
        if snapshot.net_assets is None:
            issues.append(
                DataQualityIssueDTO(
                    code="ACCOUNT_NAV_UNAVAILABLE",
                    severity=HealthState.DEGRADED,
                    scope="account_snapshot",
                    subject_ref=snapshot.account_ref,
                    observed_at=snapshot.fetched_at,
                    detail="The latest account snapshot has no reported net asset value.",
                )
            )
        return AccountSnapshotQualityDTO(
            snapshot_id=snapshot.snapshot_id,
            account_ref=snapshot.account_ref,
            provider=snapshot.provider,
            account_as_of=snapshot.account_as_of,
            fetched_at=snapshot.fetched_at,
            age_seconds=age_seconds,
            position_count=total,
            valued_position_count=valued,
            timestamped_price_count=timestamped,
            valuation_coverage_ratio=ratio,
            price_time_coverage_ratio=time_ratio,
            net_assets_available=snapshot.net_assets is not None,
            degraded=snapshot.degraded,
            warning_codes=snapshot.warning_codes,
        )

    @staticmethod
    def _activity_quality(
        receipt: AccountActivityCoverageReceipt,
        issues: list[DataQualityIssueDTO],
    ) -> AccountActivityQualityDTO:
        if receipt.status is AccountActivityCoverageStatus.INCOMPLETE:
            issues.append(
                DataQualityIssueDTO(
                    code="ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE",
                    severity=HealthState.DEGRADED,
                    scope="account_activity",
                    subject_ref=receipt.account_ref,
                    observed_at=receipt.fetched_at,
                    detail="The latest activity receipt reports incomplete source coverage.",
                )
            )
        return AccountActivityQualityDTO(
            receipt_id=receipt.receipt_id,
            account_ref=receipt.account_ref,
            provider=receipt.provider,
            requested_start=receipt.requested_start,
            requested_end=receipt.requested_end,
            effective_start=receipt.effective_start,
            effective_end=receipt.effective_end,
            fetched_at=receipt.fetched_at,
            event_count=receipt.event_count,
            snapshot_count=receipt.snapshot_count,
            mapping_version=receipt.mapping_version,
            status=receipt.status,
            unavailable_kinds=tuple(item.value for item in receipt.unavailable_kinds),
            gap_codes=receipt.gap_codes,
        )

    def _monitor_quality(
        self,
        monitor: MonitorDefinition,
        now: datetime,
        issues: list[DataQualityIssueDTO],
    ) -> MonitorQualityDTO:
        run_lookup_failed = False
        try:
            run = self._monitors.latest_run_for_monitor(monitor.monitor_id)
        except Exception as exc:  # noqa: BLE001 — health view must not raise or leak
            issues.append(self._persistence_issue(f"monitor:{monitor.monitor_id}", exc, now))
            run_lookup_failed = True
            run = None
        all_observations = (
            tuple(
                item
                for item in run.observations
                if item.monitor_id == monitor.monitor_id
            )
            if run is not None
            else ()
        )
        observations = tuple(
            item for item in all_observations if item.monitor_version == monitor.version
        )
        evaluated_versions = tuple(
            sorted({item.monitor_version for item in all_observations})
        )
        latest_evaluated_version = evaluated_versions[-1] if evaluated_versions else None
        not_evaluated = sum(
            item.state is MonitorRuleStateValue.NOT_EVALUATED for item in observations
        )
        if monitor.valid_until is not None and monitor.valid_until < now:
            issues.append(
                DataQualityIssueDTO(
                    code="MONITOR_EXPIRED_ACTIVE",
                    severity=HealthState.DEGRADED,
                    scope="monitor",
                    subject_ref=monitor.monitor_id,
                    observed_at=monitor.valid_until,
                    detail="The Monitor remains ACTIVE after its inclusive validity window.",
                )
            )
        if run is None and not run_lookup_failed:
            issues.append(
                DataQualityIssueDTO(
                    code="MONITOR_NEVER_EVALUATED",
                    severity=HealthState.DEGRADED,
                    scope="monitor",
                    subject_ref=monitor.monitor_id,
                    observed_at=monitor.created_at,
                    detail="No durable evaluation run exists for this active Monitor.",
                )
            )
        elif run is not None:
            if all_observations and not observations:
                issues.append(
                    DataQualityIssueDTO(
                        code="MONITOR_CURRENT_VERSION_NEVER_EVALUATED",
                        severity=HealthState.DEGRADED,
                        scope="monitor",
                        subject_ref=monitor.monitor_id,
                        observed_at=run.completed_at,
                        detail="The latest durable run evaluates an older Monitor version.",
                    )
                )
            elif len(observations) != len(monitor.rules):
                issues.append(
                    DataQualityIssueDTO(
                        code="MONITOR_CURRENT_RULE_COVERAGE_INCOMPLETE",
                        severity=HealthState.DEGRADED,
                        scope="monitor",
                        subject_ref=monitor.monitor_id,
                        observed_at=run.completed_at,
                        detail="The latest run lacks one observation per current Monitor rule.",
                    )
                )
            if run.status is not MonitorRunStatus.SUCCEEDED:
                issues.append(
                    DataQualityIssueDTO(
                        code="MONITOR_LATEST_RUN_INCOMPLETE",
                        severity=HealthState.DEGRADED,
                        scope="monitor",
                        subject_ref=monitor.monitor_id,
                        observed_at=run.completed_at,
                        detail="The latest Monitor run did not complete with SUCCEEDED status.",
                    )
                )
            if not_evaluated:
                issues.append(
                    DataQualityIssueDTO(
                        code="MONITOR_LATEST_NOT_EVALUATED",
                        severity=HealthState.DEGRADED,
                        scope="monitor",
                        subject_ref=monitor.monitor_id,
                        observed_at=run.completed_at,
                        detail="One or more rules were NOT_EVALUATED in the latest run.",
                    )
                )
            if not run.observation_history_complete:
                issues.append(
                    DataQualityIssueDTO(
                        code="MONITOR_OBSERVATION_HISTORY_INCOMPLETE",
                        severity=HealthState.DEGRADED,
                        scope="monitor",
                        subject_ref=monitor.monitor_id,
                        observed_at=run.completed_at,
                        detail=(
                            "The latest run does not contain a complete immutable "
                            "observation set."
                        ),
                    )
                )
        warning_codes = tuple(
            sorted(
                set(() if run is None else run.warning_codes)
                | {code for item in all_observations for code in item.warning_codes}
            )
        )
        error_codes = tuple(
            sorted(
                set(() if run is None else run.error_codes)
                | {code for item in all_observations for code in item.error_codes}
            )
        )
        return MonitorQualityDTO(
            monitor_id=monitor.monitor_id,
            monitor_version=monitor.version,
            name=monitor.name,
            cadence=monitor.cadence,
            primary_instrument_id=monitor.primary_instrument_id,
            latest_run_id=None if run is None else run.run_id,
            latest_run_at=None if run is None else run.completed_at,
            latest_run_status=None if run is None else run.status,
            latest_evaluated_version=latest_evaluated_version,
            current_version_evaluated=bool(observations),
            rule_count=len(monitor.rules),
            latest_observation_count=len(observations),
            not_evaluated_count=not_evaluated,
            warning_codes=warning_codes,
            error_codes=error_codes,
        )

    def _persistence_issue(
        self,
        subject: str,
        exc: Exception,
        observed_at: datetime,
    ) -> DataQualityIssueDTO:
        # Redaction is intentionally performed even though the exception text is not returned.
        self._secret_redactor.redact_text(str(exc) or type(exc).__name__)
        return DataQualityIssueDTO(
            code="DATA_QUALITY_PERSISTENCE_ERROR",
            severity=HealthState.ERROR,
            scope="persistence",
            subject_ref=subject,
            observed_at=observed_at,
            detail="A durable quality source could not be read; details were redacted.",
        )
