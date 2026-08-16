"""SQLAlchemy persistence for immutable Trade Retro records."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.errors import (
    DataContractError,
    IdempotencyConflict,
    PersistenceError,
    TradeRetroReviewVersionConflict,
)
from domain.retro.enums import (
    TradeRetroFindingReviewStatus,
    TradeRetroReviewStatus,
    TradeRetroSeverity,
    TradeRetroStatus,
)
from domain.retro.models import (
    TradeRetroExportReceipt,
    TradeRetroFinding,
    TradeRetroFindingReview,
    TradeRetroPlanEntry,
    TradeRetroPlanSnapshot,
    TradeRetroReviewRevision,
    TradeRetroRun,
)
from infrastructure.persistence.orm.trade_retro import (
    TradeRetroExportReceiptRow,
    TradeRetroPlanSnapshotRow,
    TradeRetroReviewRevisionRow,
    TradeRetroRunRow,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _snapshot(row: TradeRetroPlanSnapshotRow) -> TradeRetroPlanSnapshot:
    entries = json.loads(row.entries_json)
    return TradeRetroPlanSnapshot(
        snapshot_id=row.snapshot_id,
        period_start=datetime.fromisoformat(row.period_start),
        period_end=datetime.fromisoformat(row.period_end),
        captured_at=datetime.fromisoformat(row.captured_at),
        entries=tuple(
            TradeRetroPlanEntry(
                **{
                    **item,
                    "condition_codes": tuple(item["condition_codes"]),
                    "decision_records": tuple(tuple(record) for record in item["decision_records"]),
                }
            )
            for item in entries
        ),
        idempotency_key=row.idempotency_key,
        schema_version=row.schema_version,
    )


def _run(row: TradeRetroRunRow) -> TradeRetroRun:
    findings = tuple(
        TradeRetroFinding(
            code=item["code"],
            severity=TradeRetroSeverity(item["severity"]),
            title=item["title"],
            detail=item["detail"],
            instrument_id=item.get("instrument_id"),
            transaction_ids=tuple(item["transaction_ids"]),
            plan_id=item.get("plan_id"),
        )
        for item in json.loads(row.findings_json)
    )
    return TradeRetroRun(
        run_id=row.run_id,
        period_start=datetime.fromisoformat(row.period_start),
        period_end=datetime.fromisoformat(row.period_end),
        generated_at=datetime.fromisoformat(row.generated_at),
        status=TradeRetroStatus(row.status),
        plan_snapshot_id=row.plan_snapshot_id,
        transaction_ids=tuple(json.loads(row.transaction_ids_json)),
        findings=findings,
        warning_codes=tuple(json.loads(row.warning_codes_json)),
        summary_markdown=row.summary_markdown,
        llm_provider=row.llm_provider,
        llm_model=row.llm_model,
        idempotency_key=row.idempotency_key,
        algorithm_version=row.algorithm_version,
        schema_version=row.schema_version,
        execution_effect=bool(row.execution_effect),
    )


def _export(row: TradeRetroExportReceiptRow) -> TradeRetroExportReceipt:
    return TradeRetroExportReceipt(
        receipt_id=row.receipt_id,
        run_id=row.run_id,
        target_path=row.target_path,
        content_sha256=row.content_sha256,
        exported_at=datetime.fromisoformat(row.exported_at),
        idempotency_key=row.idempotency_key,
        review_version=row.review_version,
    )


def _review(row: TradeRetroReviewRevisionRow) -> TradeRetroReviewRevision:
    return TradeRetroReviewRevision(
        review_id=row.review_id,
        run_id=row.run_id,
        version=row.version,
        status=TradeRetroReviewStatus(row.status),
        note_markdown=row.note_markdown,
        action_items=tuple(json.loads(row.action_items_json)),
        finding_reviews=tuple(
            TradeRetroFindingReview(
                finding_key=item["finding_key"],
                status=TradeRetroFindingReviewStatus(item["status"]),
                note=item.get("note"),
            )
            for item in json.loads(row.finding_reviews_json)
        ),
        reviewed_by=row.reviewed_by,
        authorization_note=row.authorization_note,
        created_at=datetime.fromisoformat(row.created_at),
        idempotency_key=row.idempotency_key,
        schema_version=row.schema_version,
        execution_effect=bool(row.execution_effect),
    )


class SqlAlchemyTradeRetroRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_plan_snapshot(self, value: TradeRetroPlanSnapshot) -> TradeRetroPlanSnapshot:
        existing = self.get_plan_snapshot_by_idempotency_key(value.idempotency_key)
        if existing is not None:
            if existing == value:
                return existing
            raise IdempotencyConflict("Trade Retro plan snapshot idempotency key was reused")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    TradeRetroPlanSnapshotRow(
                        snapshot_id=value.snapshot_id,
                        period_start=value.period_start.isoformat(),
                        period_end=value.period_end.isoformat(),
                        captured_at=value.captured_at.isoformat(),
                        entries_json=_dump([asdict(item) for item in value.entries]),
                        idempotency_key=value.idempotency_key,
                        schema_version=value.schema_version,
                    )
                )
        except IntegrityError as exc:
            raise PersistenceError("Trade Retro plan snapshot persistence conflict") from exc
        return value

    def get_plan_snapshot_by_idempotency_key(self, key: str) -> TradeRetroPlanSnapshot | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeRetroPlanSnapshotRow).where(
                    TradeRetroPlanSnapshotRow.idempotency_key == key
                )
            )
            return _snapshot(row) if row is not None else None

    def get_plan_snapshot(self, snapshot_id: str) -> TradeRetroPlanSnapshot | None:
        with Session(self._engine) as session:
            row = session.get(TradeRetroPlanSnapshotRow, snapshot_id)
            return _snapshot(row) if row is not None else None

    def get_plan_snapshots(
        self, snapshot_ids: tuple[str, ...]
    ) -> dict[str, TradeRetroPlanSnapshot]:
        if not snapshot_ids:
            return {}
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TradeRetroPlanSnapshotRow).where(
                    TradeRetroPlanSnapshotRow.snapshot_id.in_(set(snapshot_ids))
                )
            )
            values = (_snapshot(row) for row in rows)
            return {value.snapshot_id: value for value in values}

    def latest_plan_snapshot_for_period(
        self, *, period_start: datetime, period_end: datetime
    ) -> TradeRetroPlanSnapshot | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeRetroPlanSnapshotRow)
                .where(
                    TradeRetroPlanSnapshotRow.period_start == period_start.isoformat(),
                    TradeRetroPlanSnapshotRow.period_end == period_end.isoformat(),
                    TradeRetroPlanSnapshotRow.captured_at <= period_start.isoformat(),
                )
                .order_by(TradeRetroPlanSnapshotRow.captured_at.desc())
                .limit(1)
            )
            return _snapshot(row) if row is not None else None

    def append_run(self, value: TradeRetroRun) -> TradeRetroRun:
        existing = self.get_run_by_idempotency_key(value.idempotency_key)
        if existing is not None:
            if existing == value:
                return existing
            raise IdempotencyConflict("Trade Retro run idempotency key was reused")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    TradeRetroRunRow(
                        run_id=value.run_id,
                        period_start=value.period_start.isoformat(),
                        period_end=value.period_end.isoformat(),
                        generated_at=value.generated_at.isoformat(),
                        status=value.status.value,
                        plan_snapshot_id=value.plan_snapshot_id,
                        transaction_ids_json=_dump(value.transaction_ids),
                        findings_json=_dump(
                            [
                                {
                                    **asdict(item),
                                    "severity": item.severity.value,
                                }
                                for item in value.findings
                            ]
                        ),
                        warning_codes_json=_dump(value.warning_codes),
                        summary_markdown=value.summary_markdown,
                        llm_provider=value.llm_provider,
                        llm_model=value.llm_model,
                        idempotency_key=value.idempotency_key,
                        algorithm_version=value.algorithm_version,
                        schema_version=value.schema_version,
                        execution_effect=int(value.execution_effect),
                    )
                )
        except IntegrityError as exc:
            raise PersistenceError("Trade Retro run persistence conflict") from exc
        return value

    def get_run(self, run_id: str) -> TradeRetroRun | None:
        with Session(self._engine) as session:
            row = session.get(TradeRetroRunRow, run_id)
            return _run(row) if row is not None else None

    def get_run_by_idempotency_key(self, key: str) -> TradeRetroRun | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeRetroRunRow).where(TradeRetroRunRow.idempotency_key == key)
            )
            return _run(row) if row is not None else None

    def list_runs(self, limit: int) -> tuple[TradeRetroRun, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TradeRetroRunRow)
                .order_by(TradeRetroRunRow.period_end.desc(), TradeRetroRunRow.generated_at.desc())
                .limit(limit)
            )
            return tuple(_run(row) for row in rows)

    def append_review(self, value: TradeRetroReviewRevision) -> TradeRetroReviewRevision:
        existing = self.get_review_by_idempotency_key(value.idempotency_key)
        if existing is not None:
            if existing == value:
                return existing
            raise IdempotencyConflict("Trade Retro review idempotency key was reused")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    TradeRetroReviewRevisionRow(
                        review_id=value.review_id,
                        run_id=value.run_id,
                        version=value.version,
                        status=value.status.value,
                        note_markdown=value.note_markdown,
                        action_items_json=_dump(value.action_items),
                        finding_reviews_json=_dump(
                            [
                                {
                                    "finding_key": item.finding_key,
                                    "status": item.status.value,
                                    "note": item.note,
                                }
                                for item in value.finding_reviews
                            ]
                        ),
                        reviewed_by=value.reviewed_by,
                        authorization_note=value.authorization_note,
                        created_at=value.created_at.isoformat(),
                        idempotency_key=value.idempotency_key,
                        schema_version=value.schema_version,
                        execution_effect=int(value.execution_effect),
                    )
                )
        except IntegrityError as exc:
            raise TradeRetroReviewVersionConflict(
                "Trade Retro review version changed before this revision was saved",
                details={"run_id": value.run_id, "attempted_version": value.version},
            ) from exc
        return value

    def get_review_by_idempotency_key(self, key: str) -> TradeRetroReviewRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeRetroReviewRevisionRow).where(
                    TradeRetroReviewRevisionRow.idempotency_key == key
                )
            )
            return _review(row) if row is not None else None

    def latest_review(self, run_id: str) -> TradeRetroReviewRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeRetroReviewRevisionRow)
                .where(TradeRetroReviewRevisionRow.run_id == run_id)
                .order_by(TradeRetroReviewRevisionRow.version.desc())
                .limit(1)
            )
            return _review(row) if row is not None else None

    def list_reviews(
        self, run_id: str, *, limit: int = 100
    ) -> tuple[TradeRetroReviewRevision, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TradeRetroReviewRevisionRow)
                .where(TradeRetroReviewRevisionRow.run_id == run_id)
                .order_by(TradeRetroReviewRevisionRow.version.desc())
                .limit(limit)
            )
            return tuple(_review(row) for row in rows)

    def list_reviews_for_runs(
        self,
        run_ids: tuple[str, ...],
        *,
        limit_per_run: int = 100,
    ) -> dict[str, tuple[TradeRetroReviewRevision, ...]]:
        if type(limit_per_run) is not int or not 1 <= limit_per_run <= 500:
            raise DataContractError("Trade Retro review limit must be between 1 and 500")
        if not run_ids:
            return {}
        grouped: dict[str, list[TradeRetroReviewRevision]] = {}
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TradeRetroReviewRevisionRow)
                .where(TradeRetroReviewRevisionRow.run_id.in_(set(run_ids)))
                .order_by(
                    TradeRetroReviewRevisionRow.run_id,
                    TradeRetroReviewRevisionRow.version.desc(),
                )
            )
            for row in rows:
                values = grouped.setdefault(row.run_id, [])
                if len(values) < limit_per_run:
                    values.append(_review(row))
        return {run_id: tuple(values) for run_id, values in grouped.items()}

    def append_export(self, value: TradeRetroExportReceipt) -> TradeRetroExportReceipt:
        existing = self.get_export_by_idempotency_key(value.idempotency_key)
        if existing is not None:
            if existing == value:
                return existing
            raise IdempotencyConflict("Trade Retro export idempotency key was reused")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    TradeRetroExportReceiptRow(
                        receipt_id=value.receipt_id,
                        run_id=value.run_id,
                        target_path=value.target_path,
                        content_sha256=value.content_sha256,
                        exported_at=value.exported_at.isoformat(),
                        idempotency_key=value.idempotency_key,
                        review_version=value.review_version,
                    )
                )
        except IntegrityError as exc:
            raise PersistenceError("Trade Retro export persistence conflict") from exc
        return value

    def get_export_by_idempotency_key(self, key: str) -> TradeRetroExportReceipt | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TradeRetroExportReceiptRow).where(
                    TradeRetroExportReceiptRow.idempotency_key == key
                )
            )
            return _export(row) if row is not None else None
