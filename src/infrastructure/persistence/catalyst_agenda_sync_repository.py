"""SQLAlchemy Catalyst Agenda sync receipt repository."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.catalyst_agenda.calendar import (
    CatalystAgendaProviderSyncResult,
    CatalystAgendaSyncReceipt,
)
from domain.catalyst_agenda.enums import AgendaSyncProviderStatus, AgendaSyncStatus
from domain.common.enums import VendorId
from domain.common.errors import IdempotencyConflict, PersistenceError
from infrastructure.persistence.orm import CatalystAgendaSyncReceiptRow
from infrastructure.persistence.repositories._mapping import (
    bool_from_db,
    bool_to_db,
    dt_from_db,
    dt_to_db,
)


def _json_list(value: str) -> list[object]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        raise PersistenceError("Catalyst Agenda sync receipt JSON is invalid") from None
    if not isinstance(decoded, list):
        raise PersistenceError("Catalyst Agenda sync receipt JSON must be an array")
    return decoded


def _to_domain(row: CatalystAgendaSyncReceiptRow) -> CatalystAgendaSyncReceipt:
    results: list[CatalystAgendaProviderSyncResult] = []
    for item in _json_list(row.provider_results_json):
        if not isinstance(item, dict) or set(item) != {
            "vendor",
            "scope_ref",
            "status",
            "candidate_count",
            "error_code",
            "warning_codes",
        }:
            raise PersistenceError("Catalyst Agenda sync provider result is invalid")
        warnings = item["warning_codes"]
        if not isinstance(warnings, list):
            raise PersistenceError("Catalyst Agenda sync warning codes are invalid")
        results.append(
            CatalystAgendaProviderSyncResult(
                vendor=VendorId(item["vendor"]),
                scope_ref=item["scope_ref"],
                status=AgendaSyncProviderStatus(item["status"]),
                candidate_count=item["candidate_count"],
                error_code=item["error_code"],
                warning_codes=tuple(warnings),
            )
        )
    raw_limitations = _json_list(row.limitation_codes_json)
    if any(not isinstance(item, str) for item in raw_limitations):
        raise PersistenceError("Catalyst Agenda sync limitation codes are invalid")
    limitations = tuple(item for item in raw_limitations if isinstance(item, str))
    return CatalystAgendaSyncReceipt(
        receipt_id=row.receipt_id,
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        status=AgendaSyncStatus(row.status),
        as_of=dt_from_db(row.as_of, field_name="as_of"),
        window_start=dt_from_db(row.window_start, field_name="window_start"),
        window_end=dt_from_db(row.window_end, field_name="window_end"),
        scope_count=row.scope_count,
        eligible_instrument_count=row.eligible_instrument_count,
        succeeded_scope_count=row.succeeded_scope_count,
        failed_scope_count=row.failed_scope_count,
        candidate_count=row.candidate_count,
        appended_count=row.appended_count,
        revised_count=row.revised_count,
        date_drift_count=row.date_drift_count,
        unchanged_count=row.unchanged_count,
        provider_results=tuple(results),
        limitation_codes=limitations,
        started_at=dt_from_db(row.started_at, field_name="started_at"),
        completed_at=dt_from_db(row.completed_at, field_name="completed_at"),
        schema_version=row.schema_version,
        execution_effect=bool_from_db(row.execution_effect),
    )


class SqlAlchemyCatalystAgendaSyncRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_by_idempotency_key(self, key: str) -> CatalystAgendaSyncReceipt | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(CatalystAgendaSyncReceiptRow).where(
                    CatalystAgendaSyncReceiptRow.idempotency_key == key
                )
            )
            return _to_domain(row) if row else None

    def append(self, receipt: CatalystAgendaSyncReceipt) -> CatalystAgendaSyncReceipt:
        row = CatalystAgendaSyncReceiptRow(
            receipt_id=receipt.receipt_id,
            idempotency_key=receipt.idempotency_key,
            request_fingerprint=receipt.request_fingerprint,
            status=receipt.status.value,
            as_of=dt_to_db(receipt.as_of),
            window_start=dt_to_db(receipt.window_start),
            window_end=dt_to_db(receipt.window_end),
            scope_count=receipt.scope_count,
            eligible_instrument_count=receipt.eligible_instrument_count,
            succeeded_scope_count=receipt.succeeded_scope_count,
            failed_scope_count=receipt.failed_scope_count,
            candidate_count=receipt.candidate_count,
            appended_count=receipt.appended_count,
            revised_count=receipt.revised_count,
            date_drift_count=receipt.date_drift_count,
            unchanged_count=receipt.unchanged_count,
            provider_results_json=json.dumps(
                [
                    {
                        "vendor": item.vendor.value,
                        "scope_ref": item.scope_ref,
                        "status": item.status.value,
                        "candidate_count": item.candidate_count,
                        "error_code": item.error_code,
                        "warning_codes": list(item.warning_codes),
                    }
                    for item in receipt.provider_results
                ],
                sort_keys=True,
                separators=(",", ":"),
            ),
            limitation_codes_json=json.dumps(list(receipt.limitation_codes), separators=(",", ":")),
            started_at=dt_to_db(receipt.started_at),
            completed_at=dt_to_db(receipt.completed_at),
            schema_version=receipt.schema_version,
            execution_effect=bool_to_db(receipt.execution_effect),
        )
        try:
            with Session(self._engine) as session, session.begin():
                session.add(row)
        except IntegrityError:
            existing = self.get_by_idempotency_key(receipt.idempotency_key)
            if existing and existing.request_fingerprint == receipt.request_fingerprint:
                return existing
            raise IdempotencyConflict("Catalyst Agenda sync idempotency key was reused") from None
        return receipt

    def list_since(
        self, since: datetime, *, limit: int = 20
    ) -> tuple[CatalystAgendaSyncReceipt, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(CatalystAgendaSyncReceiptRow)
                .where(CatalystAgendaSyncReceiptRow.completed_at >= dt_to_db(since))
                .order_by(CatalystAgendaSyncReceiptRow.completed_at.desc())
                .limit(limit)
            ).all()
            return tuple(_to_domain(row) for row in rows)

    def latest(self) -> CatalystAgendaSyncReceipt | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(CatalystAgendaSyncReceiptRow).order_by(
                    CatalystAgendaSyncReceiptRow.completed_at.desc(),
                    CatalystAgendaSyncReceiptRow.receipt_id.desc(),
                )
            )
            return _to_domain(row) if row else None
