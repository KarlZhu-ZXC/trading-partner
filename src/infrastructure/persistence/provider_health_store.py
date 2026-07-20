"""SQLAlchemy short-session ProviderHealthStore (Phase 1D D5a)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.provider_state import (
    ProviderHealthSnapshot,
    require_provider_error_code,
)
from domain.common.enums import (
    CircuitState,
    DataCategory,
    HealthState,
    VendorId,
)
from domain.common.errors import DataContractError, PersistenceError
from domain.common.time import require_aware_datetime
from infrastructure.persistence.models import ProviderHealthRow
from infrastructure.persistence.repositories._mapping import (
    dt_opt_from_db,
    dt_to_db,
)


def _virtual_snapshot(
    vendor: VendorId, category: DataCategory
) -> ProviderHealthSnapshot:
    return ProviderHealthSnapshot(
        vendor=vendor,
        category=category,
        state=HealthState.OK,
        success_count=0,
        failure_count=0,
        last_success_at=None,
        last_failure_at=None,
        last_error_code=None,
        circuit_state=CircuitState.CLOSED,
    )


def _row_to_snapshot(row: ProviderHealthRow) -> ProviderHealthSnapshot:
    return ProviderHealthSnapshot(
        vendor=VendorId(row.vendor),
        category=DataCategory(row.category),
        state=HealthState(row.state),
        success_count=row.success_count,
        failure_count=row.failure_count,
        last_success_at=dt_opt_from_db(row.last_success_at, field_name="last_success_at"),
        last_failure_at=dt_opt_from_db(row.last_failure_at, field_name="last_failure_at"),
        last_error_code=row.last_error_code,
        circuit_state=CircuitState(row.circuit_state),
    )


def _persistence_error(operation: str, error_type: str) -> PersistenceError:
    """Build a PersistenceError with only a safe error_type (no raw chain)."""
    return PersistenceError(
        f"Failed to {operation} provider health",
        details={"error_type": error_type},
    )


class SqlAlchemyProviderHealthStore:
    """Engine-bound health store: short-lived Session per public method."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, vendor: VendorId, category: DataCategory) -> ProviderHealthSnapshot:
        error_type: str | None = None
        result: ProviderHealthSnapshot | None = None
        try:
            with Session(self._engine) as session:
                row = session.get(
                    ProviderHealthRow, (vendor.value, category.value)
                )
                if row is None:
                    result = _virtual_snapshot(vendor, category)
                else:
                    result = _row_to_snapshot(row)
        except DataContractError:
            raise
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
        if error_type is not None:
            raise _persistence_error("get", error_type)
        assert result is not None
        return result

    def record_success(
        self, vendor: VendorId, category: DataCategory, at: datetime
    ) -> None:
        require_aware_datetime(at, field_name="at")
        at_iso = dt_to_db(at)
        error_type: str | None = None
        try:
            with Session(self._engine) as session:
                try:
                    # Atomic upsert/increment — no read-modify-write.
                    insert_stmt = sqlite_insert(ProviderHealthRow).values(
                        vendor=vendor.value,
                        category=category.value,
                        state=HealthState.OK.value,
                        success_count=1,
                        failure_count=0,
                        last_success_at=at_iso,
                        last_failure_at=None,
                        last_error_code=None,
                        circuit_state=CircuitState.CLOSED.value,
                        updated_at=at_iso,
                    )
                    upsert_stmt = insert_stmt.on_conflict_do_update(
                        index_elements=["vendor", "category"],
                        set_={
                            "success_count": ProviderHealthRow.success_count + 1,
                            "state": HealthState.OK.value,
                            "last_success_at": insert_stmt.excluded.last_success_at,
                            "last_error_code": None,
                            "updated_at": insert_stmt.excluded.updated_at,
                            # Preserve failure_count, last_failure_at, circuit_state.
                        },
                    )
                    session.execute(upsert_stmt)
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
        if error_type is not None:
            raise _persistence_error("record_success", error_type)

    def record_failure(
        self,
        vendor: VendorId,
        category: DataCategory,
        at: datetime,
        error_code: str,
    ) -> None:
        require_aware_datetime(at, field_name="at")
        code = require_provider_error_code(error_code)
        at_iso = dt_to_db(at)
        error_type: str | None = None
        try:
            with Session(self._engine) as session:
                try:
                    # Atomic upsert/increment — no read-modify-write.
                    insert_stmt = sqlite_insert(ProviderHealthRow).values(
                        vendor=vendor.value,
                        category=category.value,
                        state=HealthState.ERROR.value,
                        success_count=0,
                        failure_count=1,
                        last_success_at=None,
                        last_failure_at=at_iso,
                        last_error_code=code,
                        circuit_state=CircuitState.CLOSED.value,
                        updated_at=at_iso,
                    )
                    upsert_stmt = insert_stmt.on_conflict_do_update(
                        index_elements=["vendor", "category"],
                        set_={
                            "failure_count": ProviderHealthRow.failure_count + 1,
                            "state": HealthState.ERROR.value,
                            "last_failure_at": insert_stmt.excluded.last_failure_at,
                            "last_error_code": insert_stmt.excluded.last_error_code,
                            "updated_at": insert_stmt.excluded.updated_at,
                            # Preserve success_count, last_success_at, circuit_state.
                        },
                    )
                    session.execute(upsert_stmt)
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
        if error_type is not None:
            raise _persistence_error("record_failure", error_type)

    def set_circuit_state(
        self,
        vendor: VendorId,
        category: DataCategory,
        state: CircuitState,
        at: datetime,
    ) -> None:
        require_aware_datetime(at, field_name="at")
        if not isinstance(state, CircuitState):
            raise DataContractError(
                "state must be a CircuitState",
                details={"field": "state", "type": type(state).__name__},
            )
        error_type: str | None = None
        try:
            with Session(self._engine) as session:
                try:
                    row = session.get(
                        ProviderHealthRow, (vendor.value, category.value)
                    )
                    at_iso = dt_to_db(at)
                    if row is None:
                        health_state = _circuit_health_for_new_row(state)
                        session.add(
                            ProviderHealthRow(
                                vendor=vendor.value,
                                category=category.value,
                                state=health_state.value,
                                success_count=0,
                                failure_count=0,
                                last_success_at=None,
                                last_failure_at=None,
                                last_error_code=None,
                                circuit_state=state.value,
                                updated_at=at_iso,
                            )
                        )
                    else:
                        row.circuit_state = state.value
                        row.state = _project_health_state(
                            current=HealthState(row.state),
                            circuit=state,
                        ).value
                        # CLOSED does not auto-clear last call results.
                        row.updated_at = at_iso
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
        if error_type is not None:
            raise _persistence_error("set_circuit_state", error_type)


def _circuit_health_for_new_row(circuit: CircuitState) -> HealthState:
    """Health state when creating a zero-count row via set_circuit_state."""
    if circuit is CircuitState.OPEN:
        return HealthState.ERROR
    if circuit is CircuitState.HALF_OPEN:
        return HealthState.DEGRADED
    return HealthState.OK


def _project_health_state(
    *, current: HealthState, circuit: CircuitState
) -> HealthState:
    """Apply circuit observation projection rules without wiping call history.

    - OPEN → health state at least ERROR
    - HALF_OPEN → if not ERROR then DEGRADED
    - CLOSED → leave call-result health state unchanged
    """
    if circuit is CircuitState.OPEN:
        return HealthState.ERROR
    if circuit is CircuitState.HALF_OPEN:
        if current is HealthState.ERROR:
            return HealthState.ERROR
        return HealthState.DEGRADED
    # CLOSED: do not automatically erase recent success/failure projection.
    return current
