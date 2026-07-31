"""Read-only historical account transaction refresh and query boundary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from application.dto.account_transactions import (
    AccountActivityCoverageDTO,
    AccountActivityCoverageReceiptDTO,
    AccountGetActivityCoverageInput,
    AccountGetTransactionsInput,
    AccountTransactionDTO,
    AccountTransactionsDTO,
)
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.performance_attribution import (
    PerformanceAttributionDTO,
    PerformanceAttributionInput,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_provider import AccountTransactionProvider
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.performance_attribution_calculator import (
    PerformanceAttributionCalculator,
)
from domain.attribution.enums import AttributionStatus
from domain.attribution.models import PerformanceAttribution
from domain.common.enums import Freshness, SourceRole, VendorId
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.portfolio.enums import AccountActivityCoverageStatus
from domain.portfolio.models import AccountActivityCoverageReceipt

_PROVIDER_WARNING_MESSAGES = {
    "SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN": (
        "Schwab omitted explicit trade instruction; side was derived from the signed "
        "security quantity."
    ),
    "SCHWAB_TRANSACTION_ITEM_OMITTED": (
        "One or more Schwab transaction items could not be normalized."
    ),
    "SCHWAB_TRANSACTION_WINDOW_DEFAULTED": (
        "Schwab transaction history used its supported default lookback window."
    ),
    "SCHWAB_TRANSACTION_WINDOW_PAGED": (
        "Schwab transaction history was accumulated through bounded 60-day windows."
    ),
    "TRANSACTION_FEES_UNAVAILABLE": "Broker transaction fees are unavailable.",
    "MOOMOO_ACTIVITY_TYPES_UNAVAILABLE": (
        "Moomoo history deals include trades but not the full cash-activity ledger."
    ),
    "PROVIDER_RESULT_TRUNCATED": "The provider result reached the requested item limit.",
    "TRANSACTION_WINDOW_DEFAULTED": (
        "Transaction history used the provider's bounded default lookback window."
    ),
    "ACCOUNT_SNAPSHOTS_UNAVAILABLE": (
        "No durable account snapshot exists inside the activity coverage window."
    ),
    "COVERAGE_WINDOW_CLAMPED": (
        "The effective provider window does not cover the full requested interval."
    ),
    "SCHWAB_CORPORATE_ACTION_DETAILS_PARTIAL": (
        "A corporate action is preserved, but its detailed transformation is incomplete."
    ),
    "SCHWAB_REVERSAL_LINK_UNAVAILABLE": (
        "A reversal-like activity could not be linked to its original broker event."
    ),
}

_ATTRIBUTION_WARNING_MESSAGES = {
    "ATTRIBUTION_INPUTS_UNAVAILABLE": (
        "No durable account activities or account snapshots matched the request."
    ),
    "ATTRIBUTION_COVERAGE_UNAVAILABLE": (
        "No durable activity receipt proves coverage of the attribution window."
    ),
    "ATTRIBUTION_COVERAGE_INCOMPLETE": (
        "The durable activity receipt reports one or more coverage gaps."
    ),
    "FIFO_OPENING_HISTORY_UNVERIFIED": (
        "The activity ledger does not prove full account-inception history for FIFO lots."
    ),
    "CORPORATE_ACTION_LOT_EFFECT_UNSUPPORTED": (
        "A corporate action exists but its lot transformation is not yet modeled."
    ),
    "TRANSACTION_FEES_UNAVAILABLE": (
        "Net realized performance is unavailable because at least one fee is missing."
    ),
    "VALUATION_SNAPSHOT_UNAVAILABLE": (
        "No durable account snapshot at or before the requested end time is available."
    ),
    "TIMESTAMPED_VALUATION_UNAVAILABLE": (
        "A timestamped end valuation is unavailable for an open lot."
    ),
    "ENDING_POSITION_MISMATCH": (
        "Reconstructed activity lots do not reconcile to the durable ending position."
    ),
    "BROKER_REPORTED_REALIZED_PERIOD_UNVERIFIED": (
        "Broker-reported position P&L is not presented as realized P&L for this period."
    ),
}


class AccountTransactionCoordinator:
    def __init__(
        self,
        providers: Mapping[VendorId, AccountTransactionProvider],
        repository: AccountTransactionRepository,
        snapshots: AccountSnapshotRepository,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._providers = dict(providers)
        self._repository = repository
        self._snapshots = snapshots
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor
        self._attribution = PerformanceAttributionCalculator()

    async def get_transactions(
        self, request: AccountGetTransactionsInput
    ) -> ToolEnvelope[AccountTransactionsDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.end or self._clock.now()
        try:
            selected = request.providers or tuple(self._providers)
            unavailable: list[VendorId] = []
            sources: list[SourceReference] = []
            provider_warnings: dict[str, VendorId] = {}
            coverage_receipts: list[AccountActivityCoverageReceipt] = []
            for vendor in selected:
                provider = self._providers.get(vendor)
                if provider is None or not provider.is_configured():
                    unavailable.append(vendor)
                    continue
                result = await provider.get_account_transactions(
                    start=request.start, end=request.end, limit=request.limit
                )
                inserted = self._repository.append_many(result.value.transactions)
                inserted_keys = {
                    (item.account_ref, item.provider_transaction_id) for item in inserted
                }
                for coverage in result.value.coverage:
                    values = tuple(
                        item
                        for item in result.value.transactions
                        if item.account_ref == coverage.account_ref
                    )
                    inserted_count = sum(
                        (item.account_ref, item.provider_transaction_id) in inserted_keys
                        for item in values
                    )
                    snapshots = self._snapshots.list_account_history(
                        account_ref=coverage.account_ref,
                        start=coverage.effective_start,
                        end=coverage.effective_end,
                    )
                    gap_codes = set(coverage.gap_codes)
                    if coverage.effective_start > coverage.requested_start:
                        gap_codes.add("COVERAGE_WINDOW_CLAMPED")
                    if not snapshots:
                        gap_codes.add("ACCOUNT_SNAPSHOTS_UNAVAILABLE")
                    for code in gap_codes:
                        provider_warnings.setdefault(code, vendor)
                    status = (
                        AccountActivityCoverageStatus.INCOMPLETE
                        if gap_codes or coverage.unavailable_kinds or coverage.truncated
                        else AccountActivityCoverageStatus.COMPLETE
                    )
                    ordered_values = sorted(item.occurred_at for item in values)
                    ordered_snapshots = sorted(item.account_as_of for item in snapshots)
                    coverage_receipts.append(
                        AccountActivityCoverageReceipt(
                            receipt_id=self._ids.new(EntityIdPrefix.ACTIVITY_COVERAGE),
                            provider=vendor,
                            account_ref=coverage.account_ref,
                            requested_start=coverage.requested_start,
                            requested_end=coverage.requested_end,
                            effective_start=coverage.effective_start,
                            effective_end=coverage.effective_end,
                            earliest_event_at=ordered_values[0] if ordered_values else None,
                            latest_event_at=ordered_values[-1] if ordered_values else None,
                            event_count=len(values),
                            inserted_count=inserted_count,
                            duplicate_count=len(values) - inserted_count,
                            snapshot_count=len(snapshots),
                            earliest_snapshot_at=(
                                ordered_snapshots[0] if ordered_snapshots else None
                            ),
                            latest_snapshot_at=(
                                ordered_snapshots[-1] if ordered_snapshots else None
                            ),
                            mapping_version=coverage.mapping_version,
                            supported_kinds=coverage.supported_kinds,
                            unavailable_kinds=coverage.unavailable_kinds,
                            status=status,
                            gap_codes=tuple(sorted(gap_codes)),
                            fetched_at=result.meta.fetched_at,
                        )
                    )
                for code in result.meta.warnings:
                    provider_warnings.setdefault(code, vendor)
                sources.append(
                    SourceReference(
                        name=vendor.value,
                        role=SourceRole.PRIMARY,
                        retrieved_at=result.meta.fetched_at,
                    )
                )
            self._repository.append_coverage(tuple(coverage_receipts))
            values = self._repository.list(
                providers=request.providers,
                start=request.start,
                end=request.end,
                limit=request.limit,
            )
            unavailable_warnings = tuple(
                WarningInfo(
                    code=f"{vendor.value.upper()}_TRANSACTIONS_UNAVAILABLE",
                    message="Historical transactions are unavailable from this provider.",
                    details={},
                )
                for vendor in unavailable
            )
            upstream_warnings = tuple(
                WarningInfo(
                    code=code,
                    message=_PROVIDER_WARNING_MESSAGES.get(
                        code, "Account transaction provider warning."
                    ),
                    details={"provider": vendor.value},
                )
                for code, vendor in provider_warnings.items()
            )
            warnings = unavailable_warnings + upstream_warnings
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                sources=tuple(sources),
                data=AccountTransactionsDTO(
                    transactions=tuple(AccountTransactionDTO.from_domain(item) for item in values),
                    unavailable_providers=tuple(unavailable),
                    coverage_receipts=tuple(
                        AccountActivityCoverageReceiptDTO.from_domain(item)
                        for item in coverage_receipts
                    ),
                ),
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                errors=(error,),
            )

    def list_durable_transactions(
        self, request: AccountGetTransactionsInput
    ) -> ToolEnvelope[AccountTransactionsDTO]:
        """Read normalized durable transactions without contacting a broker."""
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.end or self._clock.now()
        try:
            values = self._repository.list(
                providers=request.providers,
                start=request.start,
                end=request.end,
                limit=request.limit,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=AccountTransactionsDTO(
                    transactions=tuple(AccountTransactionDTO.from_domain(item) for item in values),
                    unavailable_providers=(),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                errors=(error,),
            )

    def get_coverage(
        self, request: AccountGetActivityCoverageInput
    ) -> ToolEnvelope[AccountActivityCoverageDTO]:
        """Read durable activity/snapshot coverage without contacting a broker."""
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            receipts = self._repository.list_coverage(
                providers=request.providers,
                account_refs=request.account_refs,
                limit=request.limit,
            )
            overall_status = (
                AccountActivityCoverageStatus.COMPLETE
                if receipts
                and all(
                    item.status is AccountActivityCoverageStatus.COMPLETE for item in receipts
                )
                else AccountActivityCoverageStatus.INCOMPLETE
            )
            warning = (
                WarningInfo(
                    code="ATTRIBUTION_COVERAGE_UNAVAILABLE",
                    message="No durable account-activity coverage receipt is available.",
                    details={},
                )
                if not receipts
                else WarningInfo(
                    code="ATTRIBUTION_COVERAGE_INCOMPLETE",
                    message="One or more account-activity coverage receipts are incomplete.",
                    details={},
                )
                if overall_status is AccountActivityCoverageStatus.INCOMPLETE
                else None
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=AccountActivityCoverageDTO(
                    receipts=tuple(
                        AccountActivityCoverageReceiptDTO.from_domain(item)
                        for item in receipts
                    ),
                    overall_status=overall_status,
                ),
                degraded=warning is not None,
                warnings=(warning,) if warning else (),
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=now,
                fetched_at=now,
                errors=(error,),
            )

    def get_performance_attribution(
        self, request: PerformanceAttributionInput
    ) -> ToolEnvelope[PerformanceAttributionDTO]:
        """Calculate native-currency attribution from durable activities/snapshots."""
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            transactions = self._repository.list(
                providers=request.providers,
                start=None,
                end=request.end,
                limit=None,
            )
            coverage = self._repository.list_coverage(
                providers=request.providers,
                account_refs=request.account_refs,
                limit=500,
            )
            latest_snapshots = tuple(
                item
                for item in self._snapshots.latest_accounts()
                if (not request.providers or item.provider in request.providers)
                and (not request.account_refs or item.account_ref in request.account_refs)
            )
            account_keys = {
                (item.provider, item.account_ref) for item in transactions
            } | {(item.provider, item.account_ref) for item in latest_snapshots}
            if request.account_refs:
                account_keys = {
                    item for item in account_keys if item[1] in request.account_refs
                }
            accounts = []
            all_warnings: set[str] = set()
            epoch = datetime.min.replace(tzinfo=UTC)
            for provider, account_ref in sorted(
                account_keys, key=lambda item: (item[0].value, item[1])
            ):
                history = self._snapshots.list_account_history(
                    account_ref=account_ref,
                    start=epoch,
                    end=request.end,
                )
                snapshot = next(
                    (
                        item
                        for item in reversed(history)
                        if item.provider is provider
                    ),
                    None,
                )
                account_transactions = tuple(
                    item
                    for item in transactions
                    if item.provider is provider and item.account_ref == account_ref
                )
                receipts = tuple(
                    item
                    for item in coverage
                    if item.provider is provider and item.account_ref == account_ref
                )
                covering = next(
                    (
                        item
                        for item in receipts
                        if item.effective_start <= request.start
                        and item.effective_end >= request.end
                    ),
                    None,
                )
                coverage_warnings: set[str] = set()
                if covering is None:
                    coverage_warnings.add("ATTRIBUTION_COVERAGE_UNAVAILABLE")
                elif covering.status is AccountActivityCoverageStatus.INCOMPLETE:
                    coverage_warnings.add("ATTRIBUTION_COVERAGE_INCOMPLETE")
                    coverage_warnings.update(covering.gap_codes)
                if snapshot is None:
                    coverage_warnings.add("VALUATION_SNAPSHOT_UNAVAILABLE")
                currencies = {item.currency for item in account_transactions}
                if snapshot is not None:
                    currencies.add(snapshot.base_currency)
                    currencies.update(item.currency for item in snapshot.positions)
                for currency in sorted(currencies):
                    account_result = self._attribution.calculate_account(
                        account_ref=account_ref,
                        provider=provider,
                        currency=currency,
                        transactions=account_transactions,
                        snapshot=snapshot,
                        start=request.start,
                        end=request.end,
                        method=request.cost_basis_method,
                        opening_history_verified=False,
                        coverage_warning_codes=tuple(sorted(coverage_warnings)),
                    )
                    accounts.append(account_result)
                    all_warnings.update(account_result.warning_codes)
            if not accounts:
                all_warnings.add("ATTRIBUTION_INPUTS_UNAVAILABLE")
            status = (
                AttributionStatus.INCOMPLETE
                if all_warnings
                else AttributionStatus.COMPLETE
            )
            attribution = PerformanceAttribution(
                start=request.start,
                end=request.end,
                cost_basis_method=request.cost_basis_method,
                accounts=tuple(accounts),
                status=status,
                warning_codes=tuple(sorted(all_warnings)),
            )
            warnings = tuple(
                WarningInfo(
                    code=code,
                    message=_ATTRIBUTION_WARNING_MESSAGES.get(
                        code, "Performance attribution is incomplete."
                    ),
                    details={},
                )
                for code in attribution.warning_codes
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=request.end,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=PerformanceAttributionDTO.from_domain(attribution),
                degraded=status is AttributionStatus.INCOMPLETE,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=request.end,
                fetched_at=now,
                errors=(error,),
            )
