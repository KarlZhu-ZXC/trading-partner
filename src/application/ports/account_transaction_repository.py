"""Durable normalized account transaction repository port."""

from datetime import datetime
from typing import Protocol

from domain.common.enums import VendorId
from domain.portfolio.models import AccountActivityCoverageReceipt, AccountTransaction


class AccountTransactionRepository(Protocol):
    def append_many(
        self, transactions: tuple[AccountTransaction, ...]
    ) -> tuple[AccountTransaction, ...]: ...

    def list(
        self,
        *,
        providers: tuple[VendorId, ...],
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> tuple[AccountTransaction, ...]: ...

    def append_coverage(
        self, receipts: tuple[AccountActivityCoverageReceipt, ...]
    ) -> tuple[AccountActivityCoverageReceipt, ...]: ...

    def list_coverage(
        self,
        *,
        providers: tuple[VendorId, ...],
        account_refs: tuple[str, ...],
        limit: int,
    ) -> tuple[AccountActivityCoverageReceipt, ...]: ...
