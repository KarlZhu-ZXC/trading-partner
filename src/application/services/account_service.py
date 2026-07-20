"""Read-only account refresh and durable snapshot queries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from application.ports.account_provider import AccountProvider
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.clock import Clock
from domain.common.enums import DataCategory, VendorId
from domain.common.errors import (
    AccountSnapshotNotFound,
    DataContractError,
    ProviderNotConfigured,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.portfolio.models import AccountSnapshot


@dataclass(frozen=True, slots=True)
class AccountRefreshResult:
    snapshots: tuple[AccountSnapshot, ...]
    warning_codes: tuple[str, ...]

    @property
    def degraded(self) -> bool:
        return bool(self.warning_codes) or any(item.degraded for item in self.snapshots)


class AccountService:
    def __init__(
        self,
        providers: Mapping[VendorId, AccountProvider],
        repository: AccountSnapshotRepository,
        clock: Clock,
        *,
        default_order: tuple[VendorId, ...] = (
            VendorId.SCHWAB,
            VendorId.MOOMOO,
            VendorId.MANUAL_CSV,
        ),
    ) -> None:
        self._providers = dict(providers)
        self._repository = repository
        self._clock = clock
        self._default_order = default_order

    async def refresh(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        as_of: datetime | None = None,
    ) -> AccountRefreshResult:
        cutoff = as_of or self._clock.now()
        require_aware_datetime(cutoff, field_name="as_of")
        if cutoff > self._clock.now():
            raise DataContractError("as_of must not be in the future")
        selected = providers or self._default_order
        if len(selected) != len(set(selected)):
            raise DataContractError("providers must be unique")

        snapshots: list[AccountSnapshot] = []
        warnings: list[str] = []
        failures: list[TradingPartnerError] = []
        configured = 0
        for vendor in selected:
            adapter = self._providers.get(vendor)
            if adapter is None or not adapter.is_configured():
                warnings.append(f"{vendor.value.upper()}_NOT_CONFIGURED")
                continue
            configured += 1
            try:
                result = await adapter.get_account_snapshots(as_of=cutoff)
                if (
                    result.meta.vendor is not vendor
                    or result.meta.category is not DataCategory.ACCOUNT
                ):
                    raise DataContractError("account provider metadata does not match adapter")
                if not result.value:
                    warnings.append(f"{vendor.value.upper()}_NO_ACCOUNTS")
                for snapshot in result.value:
                    if snapshot.provider is not vendor:
                        raise DataContractError("account snapshot provider does not match adapter")
                    snapshots.append(self._repository.append_account(snapshot))
            except TradingPartnerError as exc:
                failures.append(exc)
                warnings.append(f"{vendor.value.upper()}_READ_FAILED")

        if not snapshots and failures:
            raise failures[0]
        if configured == 0:
            raise ProviderNotConfigured("No account provider is configured")
        return AccountRefreshResult(tuple(snapshots), tuple(dict.fromkeys(warnings)))

    def get_snapshots(self, snapshot_ids: tuple[str, ...] = ()) -> tuple[AccountSnapshot, ...]:
        if not snapshot_ids:
            latest = self._repository.latest_accounts()
            if not latest:
                raise AccountSnapshotNotFound("No durable account snapshot exists")
            return latest
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise DataContractError("account snapshot ids must be unique")
        resolved: list[AccountSnapshot] = []
        for snapshot_id in snapshot_ids:
            snapshot = self._repository.get_account(snapshot_id)
            if snapshot is None:
                raise AccountSnapshotNotFound(
                    "Account snapshot was not found", details={"snapshot_id": snapshot_id}
                )
            resolved.append(snapshot)
        return tuple(resolved)
