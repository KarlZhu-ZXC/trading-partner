"""SQLAlchemy repository for append-only Catalyst Agenda identities and versions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaItemStatus,
    AgendaSourceType,
)
from domain.catalyst_agenda.models import CatalystAgendaIdentity, CatalystAgendaVersion
from domain.common.errors import (
    CatalystAgendaVersionConflict,
    IdempotencyConflict,
    PersistenceError,
)
from infrastructure.persistence.orm.catalyst_agenda import (
    CatalystAgendaIdentityRow,
    CatalystAgendaVersionRow,
)


def _version_from_row(row: CatalystAgendaVersionRow) -> CatalystAgendaVersion:
    return CatalystAgendaVersion(
        agenda_item_id=row.agenda_item_id,
        version=row.version,
        supersedes_version=row.supersedes_version,
        instrument_id=row.instrument_id,
        subject_id=row.subject_id,
        kind=AgendaItemKind(row.kind),
        title=row.title,
        fiscal_period=row.fiscal_period,
        upstream_event_key=row.upstream_event_key,
        window_start=datetime.fromisoformat(row.window_start) if row.window_start else None,
        window_end=datetime.fromisoformat(row.window_end) if row.window_end else None,
        timezone=row.timezone,
        date_certainty=AgendaDateCertainty(row.date_certainty),
        status=AgendaItemStatus(row.status),
        source_type=AgendaSourceType(row.source_type),
        source_vendor=row.source_vendor,
        source_reference=row.source_reference,
        source_visible_at=datetime.fromisoformat(row.source_visible_at),
        last_verified_at=datetime.fromisoformat(row.last_verified_at),
        expected_question=row.expected_question,
        linked_event_id=row.linked_event_id,
        linked_report_id=row.linked_report_id,
        linked_evidence_id=row.linked_evidence_id,
        outcome_occurred_at=(
            datetime.fromisoformat(row.outcome_occurred_at)
            if row.outcome_occurred_at
            else None
        ),
        outcome_note=row.outcome_note,
        revision_note=row.revision_note,
        created_by=row.created_by,
        confirmed_by=row.confirmed_by,
        authorization_note=row.authorization_note,
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        historical_vintage=bool(row.historical_vintage),
        recorded_at=datetime.fromisoformat(row.recorded_at),
        schema_version=row.schema_version,
        execution_effect=bool(row.execution_effect),
    )


def _version_row(value: CatalystAgendaVersion) -> CatalystAgendaVersionRow:
    if value.status is AgendaItemStatus.SUPERSEDED:
        raise CatalystAgendaVersionConflict("SUPERSEDED is a read projection and cannot persist")
    return CatalystAgendaVersionRow(
        agenda_item_id=value.agenda_item_id,
        version=value.version,
        supersedes_version=value.supersedes_version,
        instrument_id=value.instrument_id,
        subject_id=value.subject_id,
        kind=value.kind.value,
        title=value.title,
        fiscal_period=value.fiscal_period,
        upstream_event_key=value.upstream_event_key,
        window_start=value.window_start.isoformat() if value.window_start else None,
        window_end=value.window_end.isoformat() if value.window_end else None,
        timezone=value.timezone,
        date_certainty=value.date_certainty.value,
        status=value.status.value,
        source_type=value.source_type.value,
        source_vendor=value.source_vendor,
        source_reference=value.source_reference,
        source_visible_at=value.source_visible_at.isoformat(),
        last_verified_at=value.last_verified_at.isoformat(),
        expected_question=value.expected_question,
        linked_event_id=value.linked_event_id,
        linked_report_id=value.linked_report_id,
        linked_evidence_id=value.linked_evidence_id,
        outcome_occurred_at=(
            value.outcome_occurred_at.isoformat()
            if value.outcome_occurred_at is not None
            else None
        ),
        outcome_note=value.outcome_note,
        revision_note=value.revision_note,
        created_by=value.created_by,
        confirmed_by=value.confirmed_by,
        authorization_note=value.authorization_note,
        idempotency_key=value.idempotency_key,
        request_fingerprint=value.request_fingerprint,
        historical_vintage=int(value.historical_vintage),
        recorded_at=value.recorded_at.isoformat(),
        schema_version=value.schema_version,
        execution_effect=int(value.execution_effect),
    )


class SqlAlchemyCatalystAgendaRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_by_idempotency_key(self, key: str) -> CatalystAgendaVersion | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(CatalystAgendaVersionRow).where(
                    CatalystAgendaVersionRow.idempotency_key == key
                )
            )
            return _version_from_row(row) if row is not None else None

    def get_by_logical_key(self, key: str) -> CatalystAgendaVersion | None:
        with Session(self._engine) as session:
            identity = session.scalar(
                select(CatalystAgendaIdentityRow).where(
                    CatalystAgendaIdentityRow.logical_key == key
                )
            )
            if identity is None:
                return None
            row = session.scalar(
                select(CatalystAgendaVersionRow)
                .where(
                    CatalystAgendaVersionRow.agenda_item_id == identity.agenda_item_id
                )
                .order_by(CatalystAgendaVersionRow.version.desc())
                .limit(1)
            )
            return _version_from_row(row) if row is not None else None

    def get_current(self, agenda_item_id: str) -> CatalystAgendaVersion | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(CatalystAgendaVersionRow)
                .where(CatalystAgendaVersionRow.agenda_item_id == agenda_item_id)
                .order_by(CatalystAgendaVersionRow.version.desc())
                .limit(1)
            )
            return _version_from_row(row) if row is not None else None

    def get_current_by_logical_key(self, logical_key: str) -> CatalystAgendaVersion | None:
        with Session(self._engine) as session:
            identity = session.scalar(
                select(CatalystAgendaIdentityRow).where(
                    CatalystAgendaIdentityRow.logical_key == logical_key
                )
            )
            if identity is None:
                return None
            row = session.scalar(
                select(CatalystAgendaVersionRow)
                .where(
                    CatalystAgendaVersionRow.agenda_item_id == identity.agenda_item_id
                )
                .order_by(CatalystAgendaVersionRow.version.desc())
                .limit(1)
            )
            return _version_from_row(row) if row is not None else None

    def append_initial(
        self, identity: CatalystAgendaIdentity, version: CatalystAgendaVersion
    ) -> CatalystAgendaVersion:
        if version.agenda_item_id != identity.agenda_item_id or version.version != 1:
            raise CatalystAgendaVersionConflict("initial Agenda version must match its identity")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    CatalystAgendaIdentityRow(
                        agenda_item_id=identity.agenda_item_id,
                        logical_key=identity.logical_key,
                        created_at=identity.created_at.isoformat(),
                    )
                )
                session.flush()
                session.add(_version_row(version))
        except IntegrityError as exc:
            return self._resolve_integrity(exc, version)
        return version

    def append_version(
        self, version: CatalystAgendaVersion, *, expected_version: int
    ) -> CatalystAgendaVersion:
        try:
            with Session(self._engine) as session, session.begin():
                current_row = session.scalar(
                    select(CatalystAgendaVersionRow)
                    .where(CatalystAgendaVersionRow.agenda_item_id == version.agenda_item_id)
                    .order_by(CatalystAgendaVersionRow.version.desc())
                    .limit(1)
                    .with_for_update()
                )
                if current_row is None:
                    raise CatalystAgendaVersionConflict("Agenda item does not exist")
                if current_row.version != expected_version:
                    raise CatalystAgendaVersionConflict(
                        "Agenda item version changed before this revision was saved",
                        details={
                            "agenda_item_id": version.agenda_item_id,
                            "expected_version": expected_version,
                            "current_version": current_row.version,
                        },
                    )
                allowed_statuses = {
                    AgendaItemStatus.UPCOMING.value: {
                        AgendaItemStatus.UPCOMING.value,
                        AgendaItemStatus.OCCURRED.value,
                        AgendaItemStatus.CANCELLED.value,
                    },
                    AgendaItemStatus.OCCURRED.value: {
                        AgendaItemStatus.OCCURRED.value,
                    },
                }
                if version.status.value not in allowed_statuses.get(
                    current_row.status, set()
                ):
                    raise CatalystAgendaVersionConflict(
                        "Catalyst Agenda status transition is not append-only valid",
                        details={
                            "agenda_item_id": version.agenda_item_id,
                            "current_status": current_row.status,
                            "next_status": version.status.value,
                        },
                    )
                if version.version != expected_version + 1:
                    raise CatalystAgendaVersionConflict("next Agenda version is not contiguous")
                session.add(_version_row(version))
        except IntegrityError as exc:
            return self._resolve_integrity(exc, version)
        return version

    def list_visible(
        self,
        *,
        as_of: datetime,
    ) -> tuple[CatalystAgendaVersion, ...]:
        with Session(self._engine) as session:
            statement = select(CatalystAgendaVersionRow)
            rows = session.scalars(
                statement.order_by(
                    CatalystAgendaVersionRow.agenda_item_id,
                    CatalystAgendaVersionRow.version,
                )
            ).all()
        return tuple(
            value
            for row in rows
            if (value := _version_from_row(row)).source_visible_at <= as_of
            and value.recorded_at <= as_of
        )

    def _resolve_integrity(
        self, exc: IntegrityError, attempted: CatalystAgendaVersion
    ) -> CatalystAgendaVersion:
        existing = self.get_by_idempotency_key(attempted.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint == attempted.request_fingerprint:
                return existing
            raise IdempotencyConflict("Catalyst Agenda idempotency key was reused") from exc
        current = self.get_current(attempted.agenda_item_id)
        if current is not None and current.version >= attempted.version:
            raise CatalystAgendaVersionConflict(
                "Catalyst Agenda version changed before append",
                details={
                    "agenda_item_id": attempted.agenda_item_id,
                    "attempted_version": attempted.version,
                    "current_version": current.version,
                },
            ) from exc
        raise PersistenceError("Catalyst Agenda persistence conflict") from exc
