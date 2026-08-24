"""Persistence port for append-only transaction activity annotations."""

from __future__ import annotations

from typing import Protocol

from domain.common.enums import VendorId
from domain.portfolio.models import ActivityAnnotation


class ActivityAnnotationRepository(Protocol):
    def append(
        self,
        annotation: ActivityAnnotation,
        *,
        expected_version: int | None = None,
    ) -> ActivityAnnotation: ...

    def get_latest(
        self,
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> ActivityAnnotation | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> ActivityAnnotation | None: ...

    def list_latest(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        account_refs: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[ActivityAnnotation, ...]: ...

    def list(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        account_refs: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[ActivityAnnotation, ...]: ...

    def list_revisions(
        self,
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> tuple[ActivityAnnotation, ...]: ...
