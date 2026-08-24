"""SQLAlchemy persistence for append-only Behavior Review Runs."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.behavior_review.enums import (
    BehaviorActionStatus,
    BehaviorReviewPeriodKind,
    BehaviorReviewRunStatus,
)
from domain.behavior_review.models import (
    BehaviorActionObservation,
    BehaviorReviewCohort,
    BehaviorReviewRun,
)
from domain.common.errors import IdempotencyConflict, PersistenceError
from infrastructure.persistence.orm.operations import (
    BehaviorActionObservationRow,
    BehaviorReviewRunRow,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _cohort_payload(value: BehaviorReviewCohort) -> dict[str, object]:
    return {
        "period_kind": value.period_kind.value,
        "period_start": value.period_start.isoformat(),
        "period_end": value.period_end.isoformat(),
        "strategy_code": value.strategy_code,
        "strategy_version": value.strategy_version,
        "horizon": value.horizon,
        "instrument_ids": value.instrument_ids,
        "currency": value.currency,
        "cycle_ids": value.cycle_ids,
        "decision_ids": value.decision_ids,
        "retro_run_ids": value.retro_run_ids,
        "retro_review_ids": value.retro_review_ids,
        "review_item_source_keys": value.review_item_source_keys,
        "subject_ids": value.subject_ids,
    }


def _cohort(row: BehaviorReviewRunRow) -> BehaviorReviewCohort:
    value = json.loads(row.cohort_json)
    return BehaviorReviewCohort(
        period_kind=BehaviorReviewPeriodKind(value["period_kind"]),
        period_start=datetime.fromisoformat(value["period_start"]),
        period_end=datetime.fromisoformat(value["period_end"]),
        strategy_code=value.get("strategy_code"),
        strategy_version=value.get("strategy_version"),
        horizon=value.get("horizon"),
        instrument_ids=tuple(value.get("instrument_ids", ())),
        currency=value.get("currency"),
        cycle_ids=tuple(value.get("cycle_ids", ())),
        decision_ids=tuple(value.get("decision_ids", ())),
        retro_run_ids=tuple(value.get("retro_run_ids", ())),
        retro_review_ids=tuple(value.get("retro_review_ids", ())),
        review_item_source_keys=tuple(value.get("review_item_source_keys", ())),
        subject_ids=tuple(value.get("subject_ids", ())),
    )


def _action(row: BehaviorActionObservationRow) -> BehaviorActionObservation:
    return BehaviorActionObservation(
        observation_id=row.observation_id,
        run_id=row.run_id,
        stable_key=row.stable_key,
        action_text=row.action_text,
        action_code=row.action_code,
        status=BehaviorActionStatus(row.status),
        occurrence_count=row.occurrence_count,
        period_key=row.period_key,
        cohort_key=row.cohort_key,
        review_item_source_keys=tuple(json.loads(row.review_item_source_keys_json)),
        retro_review_ids=tuple(json.loads(row.retro_review_ids_json)),
        cycle_ids=tuple(json.loads(row.cycle_ids_json)),
        decision_ids=tuple(json.loads(row.decision_ids_json)),
        observed_at=datetime.fromisoformat(row.observed_at),
        previous_observation_id=row.previous_observation_id,
        resolved_at=datetime.fromisoformat(row.resolved_at) if row.resolved_at else None,
        resolution_note=row.resolution_note,
    )


def _run(
    row: BehaviorReviewRunRow,
    actions: tuple[BehaviorActionObservation, ...],
) -> BehaviorReviewRun:
    return BehaviorReviewRun(
        run_id=row.run_id,
        cohort=_cohort(row),
        generated_at=datetime.fromisoformat(row.generated_at),
        status=BehaviorReviewRunStatus(row.status),
        source_read_complete=bool(row.source_read_complete),
        action_observations=actions,
        warning_codes=tuple(json.loads(row.warning_codes_json)),
        idempotency_key=row.idempotency_key,
        source_error_code=row.source_error_code,
        algorithm_version=row.algorithm_version,
        schema_version=row.schema_version,
        execution_effect=bool(row.execution_effect),
    )


class SqlAlchemyBehaviorReviewRepository:
    """Persist immutable Run and action rows; replay uses exact idempotency."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_run(self, value: BehaviorReviewRun) -> BehaviorReviewRun:
        existing = self.get_run_by_idempotency_key(value.idempotency_key)
        if existing is not None:
            if existing == value:
                return existing
            raise IdempotencyConflict("Behavior Review idempotency key was reused")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    BehaviorReviewRunRow(
                        run_id=value.run_id,
                        period_kind=value.cohort.period_kind.value,
                        period_start=value.cohort.period_start.isoformat(),
                        period_end=value.cohort.period_end.isoformat(),
                        cohort_key=value.cohort.cohort_key,
                        cohort_json=_dump(_cohort_payload(value.cohort)),
                        generated_at=value.generated_at.isoformat(),
                        status=value.status.value,
                        source_read_complete=int(value.source_read_complete),
                        source_error_code=value.source_error_code,
                        warning_codes_json=_dump(value.warning_codes),
                        idempotency_key=value.idempotency_key,
                        algorithm_version=value.algorithm_version,
                        schema_version=value.schema_version,
                        execution_effect=int(value.execution_effect),
                    )
                )
                session.flush()
                session.add_all(
                    BehaviorActionObservationRow(
                        observation_id=item.observation_id,
                        run_id=item.run_id,
                        stable_key=item.stable_key,
                        action_text=item.action_text,
                        action_code=item.action_code,
                        status=item.status.value,
                        occurrence_count=item.occurrence_count,
                        period_key=item.period_key,
                        cohort_key=item.cohort_key,
                        review_item_source_keys_json=_dump(item.review_item_source_keys),
                        retro_review_ids_json=_dump(item.retro_review_ids),
                        cycle_ids_json=_dump(item.cycle_ids),
                        decision_ids_json=_dump(item.decision_ids),
                        observed_at=item.observed_at.isoformat(),
                        previous_observation_id=item.previous_observation_id,
                        resolved_at=item.resolved_at.isoformat() if item.resolved_at else None,
                        resolution_note=item.resolution_note,
                    )
                    for item in value.action_observations
                )
        except IntegrityError as exc:
            raise PersistenceError("Behavior Review persistence conflict") from exc
        return value

    def get_run(self, run_id: str) -> BehaviorReviewRun | None:
        with Session(self._engine) as session:
            row = session.get(BehaviorReviewRunRow, run_id)
            if row is None:
                return None
            return _run(row, self._actions_for_run(session, run_id))

    def get_run_by_idempotency_key(self, key: str) -> BehaviorReviewRun | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(BehaviorReviewRunRow).where(
                    BehaviorReviewRunRow.idempotency_key == key
                )
            )
            if row is None:
                return None
            return _run(row, self._actions_for_run(session, row.run_id))

    def list_runs(self, *, limit: int = 50) -> tuple[BehaviorReviewRun, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with Session(self._engine) as session:
            rows = tuple(
                session.scalars(
                    select(BehaviorReviewRunRow)
                    .order_by(
                        BehaviorReviewRunRow.period_end.desc(),
                        BehaviorReviewRunRow.generated_at.desc(),
                    )
                    .limit(limit)
                )
            )
            actions = self._actions_by_run(session, tuple(row.run_id for row in rows))
            return tuple(_run(row, actions.get(row.run_id, ())) for row in rows)

    def list_action_observations(
        self,
        *,
        limit: int = 2_000,
    ) -> tuple[BehaviorActionObservation, ...]:
        if not 1 <= limit <= 20_000:
            raise ValueError("limit must be between 1 and 20000")
        with Session(self._engine) as session:
            rows = tuple(
                session.scalars(
                    select(BehaviorActionObservationRow)
                    .order_by(
                        BehaviorActionObservationRow.observed_at.desc(),
                        BehaviorActionObservationRow.observation_id.desc(),
                    )
                    .limit(limit)
                )
            )
            return tuple(_action(row) for row in rows)

    @staticmethod
    def _actions_for_run(
        session: Session,
        run_id: str,
    ) -> tuple[BehaviorActionObservation, ...]:
        rows = tuple(
            session.scalars(
                select(BehaviorActionObservationRow)
                .where(BehaviorActionObservationRow.run_id == run_id)
                .order_by(BehaviorActionObservationRow.stable_key)
            )
        )
        return tuple(_action(row) for row in rows)

    @staticmethod
    def _actions_by_run(
        session: Session,
        run_ids: tuple[str, ...],
    ) -> dict[str, tuple[BehaviorActionObservation, ...]]:
        if not run_ids:
            return {}
        rows = tuple(
            session.scalars(
                select(BehaviorActionObservationRow)
                .where(BehaviorActionObservationRow.run_id.in_(set(run_ids)))
                .order_by(
                    BehaviorActionObservationRow.run_id,
                    BehaviorActionObservationRow.stable_key,
                )
            )
        )
        grouped: dict[str, list[BehaviorActionObservation]] = {}
        for row in rows:
            grouped.setdefault(row.run_id, []).append(_action(row))
        return {key: tuple(value) for key, value in grouped.items()}


__all__ = ["SqlAlchemyBehaviorReviewRepository"]
