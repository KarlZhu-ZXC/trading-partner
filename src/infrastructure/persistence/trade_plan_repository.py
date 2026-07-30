"""Session-bound append-only persistence for Trade Plans."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.common.errors import (
    IdempotencyConflict,
    PersistenceError,
    TradePlanVersionConflict,
)
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlan, TradePlanCondition
from infrastructure.persistence.orm import (
    TradePlanConditionRow,
    TradePlanIdentityRow,
    TradePlanVersionRow,
)
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _plan_from_row(
    row: TradePlanVersionRow,
    identity: TradePlanIdentityRow,
    conditions: tuple[TradePlanConditionRow, ...],
) -> TradePlan:
    return TradePlan(
        plan_id=row.plan_id,
        version=row.version,
        case_id=identity.case_id,
        thesis_id=row.thesis_id,
        instrument_id=row.instrument_id,
        status=TradePlanStatus(row.status),
        valid_from=dt_from_db(row.valid_from, field_name="valid_from"),
        valid_until=(
            dt_from_db(row.valid_until, field_name="valid_until")
            if row.valid_until is not None
            else None
        ),
        currency=row.currency,
        reference_price=Decimal(row.reference_price),
        reference_price_at=dt_from_db(
            row.reference_price_at, field_name="reference_price_at"
        ),
        target_position_percent=Decimal(row.target_position_percent),
        max_position_percent=Decimal(row.max_position_percent),
        risk_budget_percent=Decimal(row.risk_budget_percent),
        stop_price=_dec(row.stop_price),
        conditions=tuple(
            TradePlanCondition(
                condition_code=item.condition_code,
                phase=TradePlanConditionPhase(item.phase),
                mode=TradePlanConditionMode(item.mode),
                description=item.description,
                severity=item.severity,
                fact_type=(TradePlanFactType(item.fact_type) if item.fact_type else None),
                metric_key=item.metric_key,
                comparator=(
                    TradePlanComparator(item.comparator) if item.comparator else None
                ),
                threshold=_dec(item.threshold),
                unit=item.unit,
                instrument_id=item.instrument_id,
                max_fact_age_seconds=item.max_fact_age_seconds,
                event_after=(
                    dt_from_db(item.event_after, field_name="event_after")
                    if item.event_after
                    else None
                ),
            )
            for item in conditions
        ),
        notes=row.notes,
        confirmed_by=row.confirmed_by,
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        idempotency_key=row.idempotency_key,
        schema_version=row.schema_version,
    )


class SqlAlchemyTradePlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _hydrate(self, row: TradePlanVersionRow) -> TradePlan:
        identity = self._session.get(TradePlanIdentityRow, row.plan_id)
        if identity is None:
            raise PersistenceError("Trade Plan identity is missing", retryable=False)
        conditions = tuple(
            self._session.scalars(
                select(TradePlanConditionRow)
                .where(
                    TradePlanConditionRow.plan_id == row.plan_id,
                    TradePlanConditionRow.version == row.version,
                )
                .order_by(TradePlanConditionRow.position)
            )
        )
        return _plan_from_row(row, identity, conditions)

    def get_current(self, plan_id: str) -> TradePlan | None:
        row = self._session.scalar(
            select(TradePlanVersionRow)
            .where(TradePlanVersionRow.plan_id == plan_id)
            .order_by(TradePlanVersionRow.version.desc())
            .limit(1)
        )
        return self._hydrate(row) if row is not None else None

    def get_current_by_case(self, case_id: str) -> TradePlan | None:
        identity = self._session.scalar(
            select(TradePlanIdentityRow).where(TradePlanIdentityRow.case_id == case_id)
        )
        return self.get_current(identity.plan_id) if identity is not None else None

    def get_version(self, plan_id: str, version: int) -> TradePlan | None:
        row = self._session.get(TradePlanVersionRow, (plan_id, version))
        return self._hydrate(row) if row is not None else None

    def list_versions(self, plan_id: str) -> tuple[TradePlan, ...]:
        rows = tuple(
            self._session.scalars(
                select(TradePlanVersionRow)
                .where(TradePlanVersionRow.plan_id == plan_id)
                .order_by(TradePlanVersionRow.version)
            )
        )
        return tuple(self._hydrate(row) for row in rows)

    def get_by_idempotency_key(self, key: str) -> TradePlan | None:
        row = self._session.scalar(
            select(TradePlanVersionRow).where(TradePlanVersionRow.idempotency_key == key)
        )
        return self._hydrate(row) if row is not None else None

    def append(self, plan: TradePlan) -> TradePlan:
        duplicate = self.get_by_idempotency_key(plan.idempotency_key)
        if duplicate is not None:
            if duplicate == plan:
                return duplicate
            raise IdempotencyConflict("Trade Plan idempotency key was reused")

        identity = self._session.get(TradePlanIdentityRow, plan.plan_id)
        if identity is None:
            if plan.version != 1:
                raise TradePlanVersionConflict("New Trade Plan must start at version 1")
            existing_case = self._session.scalar(
                select(TradePlanIdentityRow).where(
                    TradePlanIdentityRow.case_id == plan.case_id
                )
            )
            if existing_case is not None:
                raise TradePlanVersionConflict(
                    "Investment Case already has a Trade Plan identity",
                    details={"case_id": plan.case_id},
                )
            identity = TradePlanIdentityRow(
                plan_id=plan.plan_id,
                case_id=plan.case_id,
                created_at=dt_to_db(plan.created_at),
            )
            self._session.add(identity)
        elif identity.case_id != plan.case_id:
            raise TradePlanVersionConflict("Trade Plan case_id cannot change")
        else:
            current = self._session.scalar(
                select(func.max(TradePlanVersionRow.version)).where(
                    TradePlanVersionRow.plan_id == plan.plan_id
                )
            )
            if current is None or plan.version != current + 1:
                raise TradePlanVersionConflict(
                    "Trade Plan expected version does not match current version",
                    details={"current_version": current, "requested_version": plan.version},
                )

        version_row = TradePlanVersionRow(
                plan_id=plan.plan_id,
                version=plan.version,
                thesis_id=plan.thesis_id,
                instrument_id=plan.instrument_id,
                status=plan.status.value,
                valid_from=dt_to_db(plan.valid_from),
                valid_until=dt_to_db(plan.valid_until) if plan.valid_until else None,
                currency=plan.currency,
                reference_price=str(plan.reference_price),
                reference_price_at=dt_to_db(plan.reference_price_at),
                target_position_percent=str(plan.target_position_percent),
                max_position_percent=str(plan.max_position_percent),
                risk_budget_percent=str(plan.risk_budget_percent),
                stop_price=str(plan.stop_price) if plan.stop_price is not None else None,
                notes=plan.notes,
                confirmed_by=plan.confirmed_by,
                created_at=dt_to_db(plan.created_at),
                idempotency_key=plan.idempotency_key,
                schema_version=plan.schema_version,
            )
        self._session.add(version_row)
        # The condition table uses a composite FK. Flush the identity/version
        # before inserting children so SQLite foreign-key enforcement does not
        # depend on ORM unit-of-work ordering heuristics.
        self._session.flush()
        self._session.add_all(
            [
                TradePlanConditionRow(
                    plan_id=plan.plan_id,
                    version=plan.version,
                    condition_code=item.condition_code,
                    position=position,
                    phase=item.phase.value,
                    mode=item.mode.value,
                    description=item.description,
                    severity=item.severity,
                    fact_type=item.fact_type.value if item.fact_type else None,
                    metric_key=item.metric_key,
                    comparator=item.comparator.value if item.comparator else None,
                    threshold=str(item.threshold) if item.threshold is not None else None,
                    unit=item.unit,
                    instrument_id=item.instrument_id,
                    max_fact_age_seconds=item.max_fact_age_seconds,
                    event_after=dt_to_db(item.event_after) if item.event_after else None,
                )
                for position, item in enumerate(plan.conditions)
            ]
        )
        self._session.flush()
        return plan
