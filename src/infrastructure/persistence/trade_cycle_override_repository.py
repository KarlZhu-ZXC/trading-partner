"""SQLAlchemy append-only repository for Trade Cycle manual overrides."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.trade_cycle_override_repository import TradeCycleOverrideRepository
from domain.common.errors import (
    IdempotencyConflict,
    PersistenceError,
    TradeCycleOverrideVersionConflict,
)
from domain.portfolio.trade_cycle_overrides import (
    TradeCycleOverrideOperation,
    TradeCycleOverrideRevision,
)
from infrastructure.persistence.orm import TradeCycleOverrideRevisionRow
from infrastructure.persistence.repositories.append_only import register_append_only_listeners

register_append_only_listeners()


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _domain(row: TradeCycleOverrideRevisionRow) -> TradeCycleOverrideRevision:
    return TradeCycleOverrideRevision(
        override_id=row.override_id,
        root_cycle_id=row.root_cycle_id,
        version=row.version,
        operation=TradeCycleOverrideOperation(row.operation),
        cycle_ids=tuple(json.loads(row.cycle_ids_json)),
        activity_ids=tuple(json.loads(row.activity_ids_json)),
        split_groups=tuple(tuple(item) for item in json.loads(row.split_groups_json)),
        target_cycle_id=row.target_cycle_id,
        algorithm_version=row.algorithm_version,
        note=row.note,
        actor=row.actor,
        authorization_note=row.authorization_note,
        idempotency_key=row.idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
        expected_version=row.expected_version,
    )


def _same_payload(left: TradeCycleOverrideRevision, right: TradeCycleOverrideRevision) -> bool:
    return (
        left.root_cycle_id == right.root_cycle_id
        and left.operation is right.operation
        and left.cycle_ids == right.cycle_ids
        and left.activity_ids == right.activity_ids
        and left.split_groups == right.split_groups
        and left.target_cycle_id == right.target_cycle_id
        and left.algorithm_version == right.algorithm_version
        and left.note == right.note
        and left.actor == right.actor
        and left.authorization_note == right.authorization_note
        and left.expected_version == right.expected_version
    )


class SqlAlchemyTradeCycleOverrideRepository(TradeCycleOverrideRepository):
    """Persist revisions without ever updating/deleting an earlier revision."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(
        self,
        value: TradeCycleOverrideRevision,
        *,
        expected_version: int | None = None,
    ) -> TradeCycleOverrideRevision:
        with Session(self._engine) as session, session.begin():
            duplicate = session.scalar(
                select(TradeCycleOverrideRevisionRow).where(
                    TradeCycleOverrideRevisionRow.idempotency_key == value.idempotency_key
                )
            )
            if duplicate is not None:
                existing = _domain(duplicate)
                if not _same_payload(existing, value):
                    raise IdempotencyConflict(
                        "Trade Cycle override idempotency key was reused"
                    )
                return existing

            current = session.scalar(
                select(func.max(TradeCycleOverrideRevisionRow.version)).where(
                    TradeCycleOverrideRevisionRow.root_cycle_id == value.root_cycle_id
                )
            ) or 0
            if expected_version is not None and expected_version != current:
                raise TradeCycleOverrideVersionConflict(
                    "Trade Cycle override expected version does not match current version",
                    details={"current_version": current, "expected_version": expected_version},
                )
            if value.version != current + 1:
                raise TradeCycleOverrideVersionConflict(
                    "Trade Cycle override version must append the current revision",
                    details={"current_version": current, "requested_version": value.version},
                )
            session.add(
                TradeCycleOverrideRevisionRow(
                    override_id=value.override_id,
                    root_cycle_id=value.root_cycle_id,
                    version=value.version,
                    operation=value.operation.value,
                    cycle_ids_json=_dump(value.cycle_ids),
                    activity_ids_json=_dump(value.activity_ids),
                    split_groups_json=_dump(value.split_groups),
                    target_cycle_id=value.target_cycle_id,
                    algorithm_version=value.algorithm_version,
                    note=value.note,
                    actor=value.actor,
                    authorization_note=value.authorization_note,
                    idempotency_key=value.idempotency_key,
                    created_at=value.created_at.isoformat(),
                    expected_version=value.expected_version,
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise PersistenceError("Trade Cycle override append conflict") from exc
            return value

    append_revision = append

    def get_by_idempotency_key(self, key: str) -> TradeCycleOverrideRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeCycleOverrideRevisionRow).where(
                    TradeCycleOverrideRevisionRow.idempotency_key == key
                )
            )
            return _domain(row) if row is not None else None

    def get_latest(self, root_cycle_id: str) -> TradeCycleOverrideRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeCycleOverrideRevisionRow)
                .where(TradeCycleOverrideRevisionRow.root_cycle_id == root_cycle_id)
                .order_by(TradeCycleOverrideRevisionRow.version.desc())
                .limit(1)
            )
            return _domain(row) if row is not None else None

    def list(
        self,
        *,
        root_cycle_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[TradeCycleOverrideRevision, ...]:
        with Session(self._engine) as session:
            statement = select(TradeCycleOverrideRevisionRow).order_by(
                TradeCycleOverrideRevisionRow.created_at,
                TradeCycleOverrideRevisionRow.version,
                TradeCycleOverrideRevisionRow.override_id,
            )
            if root_cycle_id is not None:
                statement = statement.where(
                    TradeCycleOverrideRevisionRow.root_cycle_id == root_cycle_id
                )
            if limit is not None:
                statement = statement.limit(limit)
            return tuple(_domain(row) for row in session.scalars(statement))


SqlAlchemyTradeCycleOverrideRevisionRepository = SqlAlchemyTradeCycleOverrideRepository
