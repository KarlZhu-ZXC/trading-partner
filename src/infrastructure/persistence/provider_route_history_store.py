"""Bounded in-memory and SQL stores for secret-safe Provider route receipts."""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.provider_route_history import ProviderRouteReceipt
from application.dto.provider_routing import ProviderAttemptRecord
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Market,
    ProviderAttemptOutcome,
    SourceRole,
    VendorId,
)
from domain.common.errors import DataContractError, PersistenceError
from domain.common.time import require_aware_datetime
from infrastructure.persistence.orm import ProviderRouteReceiptRow
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db

_MAX_RECEIPTS = 5_000
_RETENTION_DAYS = 30


def _json_string_list(raw: str, *, field: str) -> tuple[str, ...]:
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataContractError(
            "provider route JSON is invalid",
            details={"field": field, "error_type": type(exc).__name__},
        ) from None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DataContractError(
            "provider route JSON must be a string list",
            details={"field": field},
        )
    return tuple(value)


def _hydrate(row: ProviderRouteReceiptRow) -> ProviderRouteReceipt:
    try:
        raw_attempts: Any = json.loads(row.attempts_json)
    except json.JSONDecodeError as exc:
        raise DataContractError(
            "provider route attempts JSON is invalid",
            details={"field": "attempts_json", "error_type": type(exc).__name__},
        ) from None
    if not isinstance(raw_attempts, list):
        raise DataContractError(
            "provider route attempts must be a list",
            details={"field": "attempts_json"},
        )
    attempts: list[ProviderAttemptRecord] = []
    for item in raw_attempts:
        if not isinstance(item, dict):
            raise DataContractError(
                "provider route attempt must be an object",
                details={"field": "attempts_json"},
            )
        vendor = item.get("vendor")
        outcome = item.get("outcome")
        error_code = item.get("error_code")
        duration_ms = item.get("duration_ms")
        if (
            not isinstance(vendor, str)
            or not isinstance(outcome, str)
            or (error_code is not None and not isinstance(error_code, str))
            or not isinstance(duration_ms, int)
            or isinstance(duration_ms, bool)
        ):
            raise DataContractError(
                "provider route attempt fields are invalid",
                details={"field": "attempts_json"},
            )
        attempts.append(
            ProviderAttemptRecord(
                vendor=VendorId(vendor),
                outcome=ProviderAttemptOutcome(outcome),
                error_code=error_code,
                duration_ms=duration_ms,
                message=None,
            )
        )
    chain = tuple(
        VendorId(item)
        for item in _json_string_list(row.requested_chain_json, field="requested_chain_json")
    )
    warnings = _json_string_list(row.warning_codes_json, field="warning_codes_json")
    return ProviderRouteReceipt(
        route_id=row.route_id,
        recorded_at=dt_from_db(row.recorded_at, field_name="recorded_at"),
        market=Market(row.market),
        category=DataCategory(row.category),
        operation_name=row.operation_name,
        instrument_id=row.instrument_id,
        criticality=DataCriticality(row.criticality),
        requested_chain=chain,
        ok=bool(row.ok),
        selected_vendor=(VendorId(row.selected_vendor) if row.selected_vendor else None),
        selected_role=(SourceRole(row.selected_role) if row.selected_role else None),
        cache_disposition=(
            CacheDisposition(row.cache_disposition) if row.cache_disposition else None
        ),
        attempts=tuple(attempts),
        warning_codes=warnings,
        final_error_code=row.final_error_code,
    )


class InMemoryProviderRouteHistoryStore:
    """Process-local bounded fallback used before the new migration is applied."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: deque[ProviderRouteReceipt] = deque(maxlen=_MAX_RECEIPTS)

    @property
    def is_durable(self) -> bool:
        return False

    def append(self, receipt: ProviderRouteReceipt) -> None:
        if not isinstance(receipt, ProviderRouteReceipt):
            raise DataContractError(
                "receipt must be ProviderRouteReceipt",
                details={"field": "receipt"},
            )
        cutoff = receipt.recorded_at - timedelta(days=_RETENTION_DAYS)
        with self._lock:
            retained = [item for item in self._items if item.recorded_at >= cutoff]
            self._items = deque(retained, maxlen=_MAX_RECEIPTS)
            self._items.append(receipt)

    def list_since(
        self, since: datetime, *, limit: int
    ) -> tuple[ProviderRouteReceipt, ...]:
        since_dt = require_aware_datetime(since, field_name="since")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5_000:
            raise DataContractError(
                "limit must be between 1 and 5000",
                details={"field": "limit"},
            )
        with self._lock:
            values = [item for item in self._items if item.recorded_at >= since_dt]
        values.sort(key=lambda item: (item.recorded_at, item.route_id), reverse=True)
        return tuple(values[:limit])


class SqlAlchemyProviderRouteHistoryStore:
    """SQLite append/query store with fixed 30-day/5,000-row retention."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def is_durable(self) -> bool:
        return True

    def append(self, receipt: ProviderRouteReceipt) -> None:
        if not isinstance(receipt, ProviderRouteReceipt):
            raise DataContractError(
                "receipt must be ProviderRouteReceipt",
                details={"field": "receipt"},
            )
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    ProviderRouteReceiptRow(
                        route_id=receipt.route_id,
                        recorded_at=dt_to_db(receipt.recorded_at.astimezone(UTC)),
                        market=receipt.market.value,
                        category=receipt.category.value,
                        operation_name=receipt.operation_name,
                        instrument_id=receipt.instrument_id,
                        criticality=receipt.criticality.value,
                        requested_chain_json=json.dumps(
                            [item.value for item in receipt.requested_chain],
                            separators=(",", ":"),
                        ),
                        ok=1 if receipt.ok else 0,
                        selected_vendor=(
                            receipt.selected_vendor.value if receipt.selected_vendor else None
                        ),
                        selected_role=(
                            receipt.selected_role.value if receipt.selected_role else None
                        ),
                        cache_disposition=(
                            receipt.cache_disposition.value
                            if receipt.cache_disposition
                            else None
                        ),
                        attempts_json=json.dumps(
                            [
                                {
                                    "vendor": item.vendor.value,
                                    "outcome": item.outcome.value,
                                    "error_code": item.error_code,
                                    "duration_ms": item.duration_ms,
                                }
                                for item in receipt.attempts
                            ],
                            separators=(",", ":"),
                        ),
                        warning_codes_json=json.dumps(
                            receipt.warning_codes, separators=(",", ":")
                        ),
                        final_error_code=receipt.final_error_code,
                    )
                )
                cutoff = dt_to_db(
                    (receipt.recorded_at - timedelta(days=_RETENTION_DAYS)).astimezone(UTC)
                )
                session.execute(
                    delete(ProviderRouteReceiptRow).where(
                        ProviderRouteReceiptRow.recorded_at < cutoff
                    )
                )
                overflow = tuple(
                    session.scalars(
                        select(ProviderRouteReceiptRow.route_id)
                        .order_by(
                            ProviderRouteReceiptRow.recorded_at.desc(),
                            ProviderRouteReceiptRow.route_id.desc(),
                        )
                        .offset(_MAX_RECEIPTS)
                    )
                )
                if overflow:
                    session.execute(
                        delete(ProviderRouteReceiptRow).where(
                            ProviderRouteReceiptRow.route_id.in_(overflow)
                        )
                    )
        except (DataContractError, PersistenceError):
            raise
        except Exception as exc:
            raise PersistenceError(
                "Failed to append provider route receipt",
                details={"error_type": type(exc).__name__},
            ) from None

    def list_since(
        self, since: datetime, *, limit: int
    ) -> tuple[ProviderRouteReceipt, ...]:
        since_dt = require_aware_datetime(since, field_name="since")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5_000:
            raise DataContractError(
                "limit must be between 1 and 5000",
                details={"field": "limit"},
            )
        try:
            with Session(self._engine) as session:
                rows = tuple(
                    session.scalars(
                        select(ProviderRouteReceiptRow)
                        .where(
                            ProviderRouteReceiptRow.recorded_at
                            >= dt_to_db(since_dt.astimezone(UTC))
                        )
                        .order_by(
                            ProviderRouteReceiptRow.recorded_at.desc(),
                            ProviderRouteReceiptRow.route_id.desc(),
                        )
                        .limit(limit)
                    )
                )
            return tuple(_hydrate(row) for row in rows)
        except (DataContractError, PersistenceError):
            raise
        except Exception as exc:
            raise PersistenceError(
                "Failed to list provider route receipts",
                details={"error_type": type(exc).__name__},
            ) from None
