"""SQLAlchemy append-only risk-policy repository."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from domain.common.errors import PersistenceError
from domain.risk.enums import RiskConfirmer
from domain.risk.models import RiskPolicy
from infrastructure.persistence.models import RiskPolicyRow
from infrastructure.persistence.repositories._mapping import (
    bool_from_db,
    bool_to_db,
    decimal_from_db,
    decimal_to_db,
    dt_from_db,
    dt_to_db,
)


def _required_decimal(value: str, *, field_name: str) -> Decimal:
    converted = decimal_from_db(value)
    if converted is None:
        raise PersistenceError(
            "Stored risk policy contains a null decimal",
            details={"field": field_name},
            retryable=False,
        )
    return converted


def _to_domain(row: RiskPolicyRow) -> RiskPolicy:
    return RiskPolicy(
        policy_id=row.policy_id,
        version=row.version,
        single_position_max_percent=_required_decimal(
            row.single_position_max_percent, field_name="single_position_max_percent"
        ),
        gross_exposure_max_percent=_required_decimal(
            row.gross_exposure_max_percent, field_name="gross_exposure_max_percent"
        ),
        minimum_cash_percent=_required_decimal(
            row.minimum_cash_percent, field_name="minimum_cash_percent"
        ),
        margin_usage_max_percent=_required_decimal(
            row.margin_usage_max_percent, field_name="margin_usage_max_percent"
        ),
        max_account_age_seconds=row.max_account_age_seconds,
        max_price_age_seconds=row.max_price_age_seconds,
        is_system_default=bool_from_db(row.is_system_default),
        confirmed_by=RiskConfirmer(row.confirmed_by),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        idempotency_key=row.idempotency_key,
        schema_version=row.schema_version,
    )


def _to_row(policy: RiskPolicy) -> RiskPolicyRow:
    return RiskPolicyRow(
        policy_id=policy.policy_id,
        version=policy.version,
        single_position_max_percent=decimal_to_db(policy.single_position_max_percent),
        gross_exposure_max_percent=decimal_to_db(policy.gross_exposure_max_percent),
        minimum_cash_percent=decimal_to_db(policy.minimum_cash_percent),
        margin_usage_max_percent=decimal_to_db(policy.margin_usage_max_percent),
        max_account_age_seconds=policy.max_account_age_seconds,
        max_price_age_seconds=policy.max_price_age_seconds,
        is_system_default=bool_to_db(policy.is_system_default),
        confirmed_by=policy.confirmed_by.value,
        created_at=dt_to_db(policy.created_at),
        idempotency_key=policy.idempotency_key,
        schema_version=policy.schema_version,
    )


def _persistence_conflict(exc: IntegrityError) -> PersistenceError:
    return PersistenceError(
        "Risk policy persistence failed",
        details={"cause": str(exc.orig)},
        retryable=True,
    )


def _same_update(left: RiskPolicy, right: RiskPolicy) -> bool:
    return (
        left.version == right.version
        and left.single_position_max_percent == right.single_position_max_percent
        and left.gross_exposure_max_percent == right.gross_exposure_max_percent
        and left.minimum_cash_percent == right.minimum_cash_percent
        and left.margin_usage_max_percent == right.margin_usage_max_percent
        and left.max_account_age_seconds == right.max_account_age_seconds
        and left.max_price_age_seconds == right.max_price_age_seconds
        and left.is_system_default == right.is_system_default
        and left.confirmed_by is right.confirmed_by
        and left.idempotency_key == right.idempotency_key
    )


class SqlAlchemyRiskPolicyRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_current(self) -> RiskPolicy | None:
        from sqlalchemy.orm import Session

        with Session(self._engine) as session:
            row = session.scalar(
                select(RiskPolicyRow)
                .order_by(RiskPolicyRow.version.desc())
                .order_by(RiskPolicyRow.created_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            return _to_domain(row)

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> RiskPolicy | None:
        from sqlalchemy.orm import Session

        with Session(self._engine) as session:
            row = session.scalar(
                select(RiskPolicyRow).where(
                    RiskPolicyRow.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            return _to_domain(row)

    def append(self, policy: RiskPolicy) -> RiskPolicy:
        from sqlalchemy.orm import Session

        row = _to_row(policy)
        try:
            with Session(self._engine) as session, session.begin():
                session.add(row)
            return policy
        except IntegrityError as exc:  # noqa: BLE001
            with Session(self._engine) as session:
                existing = session.scalar(
                    select(RiskPolicyRow).where(
                        RiskPolicyRow.idempotency_key == policy.idempotency_key,
                    )
                )
                if existing is not None:
                    persisted = _to_domain(existing)
                    if _same_update(persisted, policy):
                        return persisted
            raise _persistence_conflict(exc) from exc
