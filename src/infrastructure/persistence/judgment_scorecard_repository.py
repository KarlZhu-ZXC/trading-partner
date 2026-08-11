"""SQLAlchemy persistence for append-only Judgment Scorecard runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.errors import IdempotencyConflict, PersistenceError
from domain.scorecard.enums import ScorecardDimensionStatus, ScorecardStatus
from domain.scorecard.models import (
    JudgmentScorecardRun,
    ScorecardDimension,
    ScorecardSourceRef,
)
from infrastructure.persistence.orm.judgment_scorecard import JudgmentScorecardRunRow


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _dimension(row: dict[str, object]) -> ScorecardDimension:
    facts = cast(list[list[object]], row.get("facts", []))
    source_refs = cast(list[dict[str, object]], row.get("source_refs", []))
    limitation_codes = cast(list[object], row.get("limitation_codes", []))
    return ScorecardDimension(
        code=str(row["code"]),
        status=ScorecardDimensionStatus(str(row["status"])),
        result_code=str(row["result_code"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        facts=tuple((str(item[0]), str(item[1])) for item in facts),
        source_refs=tuple(
            ScorecardSourceRef(
                kind=str(item["kind"]),
                entity_id=str(item["entity_id"]),
                version=(
                    cast(int, item["version"])
                    if item.get("version") is not None
                    else None
                ),
            )
            for item in source_refs
        ),
        limitation_codes=tuple(str(item) for item in limitation_codes),
    )


def _from_row(row: JudgmentScorecardRunRow) -> JudgmentScorecardRun:
    return JudgmentScorecardRun(
        scorecard_id=row.scorecard_id,
        subject_id=row.subject_id,
        subject_title=row.subject_title,
        thesis_id=row.thesis_id,
        thesis_title=row.thesis_title,
        thesis_revision_id=row.thesis_revision_id,
        thesis_revision_no=row.thesis_revision_no,
        generated_at=datetime.fromisoformat(row.generated_at),
        status=ScorecardStatus(row.status),
        dimensions=tuple(_dimension(item) for item in json.loads(row.dimensions_json)),
        warning_codes=tuple(json.loads(row.warning_codes_json)),
        input_fingerprint=row.input_fingerprint,
        idempotency_key=row.idempotency_key,
        algorithm_version=row.algorithm_version,
        schema_version=row.schema_version,
        execution_effect=bool(row.execution_effect),
    )


class SqlAlchemyJudgmentScorecardRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, value: JudgmentScorecardRun) -> JudgmentScorecardRun:
        existing = self.get_by_idempotency_key(value.idempotency_key)
        if existing is not None:
            if existing == value:
                return existing
            raise IdempotencyConflict("Judgment Scorecard idempotency key was reused")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    JudgmentScorecardRunRow(
                        scorecard_id=value.scorecard_id,
                        subject_id=value.subject_id,
                        subject_title=value.subject_title,
                        thesis_id=value.thesis_id,
                        thesis_title=value.thesis_title,
                        thesis_revision_id=value.thesis_revision_id,
                        thesis_revision_no=value.thesis_revision_no,
                        generated_at=value.generated_at.isoformat(),
                        status=value.status.value,
                        dimensions_json=_dump(
                            [
                                {
                                    "code": item.code,
                                    "status": item.status.value,
                                    "result_code": item.result_code,
                                    "title": item.title,
                                    "summary": item.summary,
                                    "facts": item.facts,
                                    "source_refs": [asdict(ref) for ref in item.source_refs],
                                    "limitation_codes": item.limitation_codes,
                                }
                                for item in value.dimensions
                            ]
                        ),
                        warning_codes_json=_dump(value.warning_codes),
                        input_fingerprint=value.input_fingerprint,
                        idempotency_key=value.idempotency_key,
                        algorithm_version=value.algorithm_version,
                        schema_version=value.schema_version,
                        execution_effect=int(value.execution_effect),
                    )
                )
        except IntegrityError as exc:
            raise PersistenceError("Judgment Scorecard persistence conflict") from exc
        return value

    def get_by_idempotency_key(self, key: str) -> JudgmentScorecardRun | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(JudgmentScorecardRunRow).where(
                    JudgmentScorecardRunRow.idempotency_key == key
                )
            )
            return _from_row(row) if row is not None else None

    def list(
        self,
        *,
        subject_id: str | None = None,
        thesis_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[tuple[JudgmentScorecardRun, ...], int]:
        with Session(self._engine) as session:
            statement = select(JudgmentScorecardRunRow)
            count_statement = select(func.count()).select_from(JudgmentScorecardRunRow)
            if subject_id is not None:
                statement = statement.where(JudgmentScorecardRunRow.subject_id == subject_id)
                count_statement = count_statement.where(
                    JudgmentScorecardRunRow.subject_id == subject_id
                )
            if thesis_id is not None:
                statement = statement.where(JudgmentScorecardRunRow.thesis_id == thesis_id)
                count_statement = count_statement.where(
                    JudgmentScorecardRunRow.thesis_id == thesis_id
                )
            total = int(session.scalar(count_statement) or 0)
            rows = session.scalars(
                statement.order_by(
                    JudgmentScorecardRunRow.generated_at.desc(),
                    JudgmentScorecardRunRow.scorecard_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return tuple(_from_row(row) for row in rows), total
