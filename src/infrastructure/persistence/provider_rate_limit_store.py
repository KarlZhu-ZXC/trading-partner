"""SQLAlchemy short-session ProviderRateLimitStore (Phase 1D D5a).

Fixed-window counters with SQLite-safe atomic reservations in a single
transaction.  The existing table also stores near-future anonymous slots.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.provider_state import ProviderRateLimitSnapshot
from domain.common.enums import DataCategory, VendorId
from domain.common.errors import DataContractError, PersistenceError
from domain.common.time import require_aware_datetime
from infrastructure.persistence.orm import ProviderRateLimitRow
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db


def _row_to_snapshot(row: ProviderRateLimitRow) -> ProviderRateLimitSnapshot:
    return ProviderRateLimitSnapshot(
        vendor=VendorId(row.vendor),
        category=DataCategory(row.category),
        window_start=dt_from_db(row.window_start, field_name="window_start"),
        window_seconds=row.window_seconds,
        request_count=row.request_count,
        limit_count=row.limit_count,
        updated_at=dt_from_db(row.updated_at, field_name="updated_at"),
    )


def _persistence_error(operation: str, error_type: str) -> PersistenceError:
    """Build a PersistenceError with only a safe error_type (no raw chain)."""
    return PersistenceError(
        f"Failed to {operation} provider rate limit",
        details={"error_type": error_type},
    )


def _require_positive_int(value: int, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataContractError(
            f"{field_name} must be an int",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value <= 0:
        raise DataContractError(
            f"{field_name} must be positive",
            details={"field": field_name},
        )
    return value


class SqlAlchemyProviderRateLimitStore:
    """Engine-bound fixed-window rate-limit store."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def try_reserve(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        """Atomically reserve one slot, or return ``None`` for a full window.

        SQLite's single-statement ``ON CONFLICT ... DO UPDATE ... WHERE``
        acquires the writer lock for the whole decision.  The ``WHERE`` clause
        means a denied reservation does not update either the count or the
        timestamp, even when many processes probe the same full window.
        """
        require_aware_datetime(window_start, field_name="window_start")
        require_aware_datetime(at, field_name="at")
        window_seconds = _require_positive_int(
            window_seconds, field_name="window_seconds"
        )
        limit_count = _require_positive_int(limit_count, field_name="limit_count")

        window_start_iso = dt_to_db(window_start)
        at_iso = dt_to_db(at)
        pk = (vendor.value, category.value, window_start_iso)

        error_type: str | None = None
        missing_after_upsert = False
        snapshot: ProviderRateLimitSnapshot | None = None
        try:
            with Session(self._engine) as session:
                try:
                    insert_stmt = sqlite_insert(ProviderRateLimitRow).values(
                        vendor=vendor.value,
                        category=category.value,
                        window_start=window_start_iso,
                        window_seconds=window_seconds,
                        request_count=1,
                        limit_count=limit_count,
                        updated_at=at_iso,
                    )
                    reserve_stmt = insert_stmt.on_conflict_do_update(
                        index_elements=["vendor", "category", "window_start"],
                        set_={
                            "request_count": ProviderRateLimitRow.request_count + 1,
                            "window_seconds": insert_stmt.excluded.window_seconds,
                            "limit_count": insert_stmt.excluded.limit_count,
                            "updated_at": insert_stmt.excluded.updated_at,
                        },
                        where=(
                            ProviderRateLimitRow.request_count
                            < insert_stmt.excluded.limit_count
                        ),
                    ).returning(ProviderRateLimitRow.request_count)
                    returned = session.execute(reserve_stmt).first()
                    if returned is None:
                        # A conflict row existed but was full.  Commit the
                        # read-only statement and leave its count untouched.
                        session.commit()
                        return None
                    session.flush()
                    row = session.get(ProviderRateLimitRow, pk)
                    if row is None:
                        missing_after_upsert = True
                        session.rollback()
                    else:
                        snapshot = _row_to_snapshot(row)
                        session.commit()
                except Exception:
                    session.rollback()
                    raise
        except DataContractError:
            raise
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
        if missing_after_upsert:
            raise PersistenceError(
                "Failed to reserve provider rate limit",
                details={"error_type": "MissingRowAfterUpsert"},
            )
        if error_type is not None:
            raise _persistence_error("reserve", error_type)
        assert snapshot is not None
        return snapshot

    def get(
        self,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        require_aware_datetime(window_start, field_name="window_start")
        window_start_iso = dt_to_db(window_start)
        error_type: str | None = None
        result: ProviderRateLimitSnapshot | None = None
        try:
            with Session(self._engine) as session:
                row = session.get(
                    ProviderRateLimitRow,
                    (vendor.value, category.value, window_start_iso),
                )
                if row is not None:
                    result = _row_to_snapshot(row)
        except DataContractError:
            raise
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
        if error_type is not None:
            raise _persistence_error("get", error_type)
        return result
