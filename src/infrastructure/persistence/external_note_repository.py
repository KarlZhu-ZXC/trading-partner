"""SQLAlchemy persistence for append-only external living notes."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import (
    AttributedNoteBlock,
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteRevision,
    ExternalNoteSyncReceipt,
)
from infrastructure.persistence.orm.operations import (
    ExternalNoteIdentityRow,
    ExternalNoteInterpretationRow,
    ExternalNoteRevisionRow,
    ExternalNoteSyncReceiptRow,
)


class SqlAlchemyExternalNoteRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_by_source_id(self, source: str, external_id: str) -> ExternalNoteIdentity | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteIdentityRow).where(
                    ExternalNoteIdentityRow.source == source,
                    ExternalNoteIdentityRow.external_id == external_id,
                )
            )
            return _identity(row) if row is not None else None

    def latest_revision(self, note_id: str) -> ExternalNoteRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteRevisionRow)
                .where(ExternalNoteRevisionRow.note_id == note_id)
                .order_by(ExternalNoteRevisionRow.version.desc())
                .limit(1)
            )
            return _revision(row) if row is not None else None

    def revision_by_id(self, note_revision_id: str) -> ExternalNoteRevision | None:
        with Session(self._engine) as session:
            row = session.get(ExternalNoteRevisionRow, note_revision_id)
            return _revision(row) if row is not None else None

    def previous_revision(
        self, note_id: str, before_version: int
    ) -> ExternalNoteRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteRevisionRow)
                .where(
                    ExternalNoteRevisionRow.note_id == note_id,
                    ExternalNoteRevisionRow.version < before_version,
                )
                .order_by(ExternalNoteRevisionRow.version.desc())
                .limit(1)
            )
            return _revision(row) if row is not None else None

    def revision_by_source_key(
        self, note_id: str, source_revision_key: str
    ) -> ExternalNoteRevision | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteRevisionRow).where(
                    ExternalNoteRevisionRow.note_id == note_id,
                    ExternalNoteRevisionRow.source_revision_key == source_revision_key,
                )
            )
            return _revision(row) if row is not None else None

    def append_identity(self, value: ExternalNoteIdentity) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                ExternalNoteIdentityRow(
                    note_id=value.note_id,
                    source=value.source,
                    external_id=value.external_id,
                    title=value.title,
                    primary_instrument_id=value.primary_instrument_id,
                    created_at=value.created_at.isoformat(),
                    last_seen_at=value.last_seen_at.isoformat(),
                )
            )

    def update_identity(self, value: ExternalNoteIdentity) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(ExternalNoteIdentityRow, value.note_id)
            if row is None:
                raise ValueError("external note identity not found")
            row.title = value.title
            row.primary_instrument_id = value.primary_instrument_id
            row.last_seen_at = value.last_seen_at.isoformat()

    def append_revision(self, value: ExternalNoteRevision) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                ExternalNoteRevisionRow(
                    note_revision_id=value.note_revision_id,
                    note_id=value.note_id,
                    version=value.version,
                    content_sha256=value.content_sha256,
                    source_revision_key=value.source_revision_key,
                    title=value.title,
                    summary=value.summary,
                    full_body=value.full_body,
                    coverage=value.coverage.value,
                    source_timestamp=(
                        value.source_timestamp.isoformat()
                        if value.source_timestamp is not None
                        else None
                    ),
                    observed_at=value.observed_at.isoformat(),
                    visibility=value.visibility,
                    related_stock_ids=value.related_provider_stock_ids,
                    related_codes=value.related_provider_codes,
                    blocks_json=json.dumps(
                        [
                            {
                                "ordinal": item.ordinal,
                                "speaker_kind": item.speaker_kind.value,
                                "speaker_label": item.speaker_label,
                                "body": item.body,
                                "section_date": item.section_date,
                            }
                            for item in value.blocks
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )

    def append_interpretation(self, value: ExternalNoteInterpretation) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(ExternalNoteInterpretationRow).where(
                    ExternalNoteInterpretationRow.note_revision_id == value.note_revision_id
                )
            )
            if row is None:
                session.add(
                    ExternalNoteInterpretationRow(
                        interpretation_id=value.interpretation_id,
                        note_revision_id=value.note_revision_id,
                        status=value.status,
                        provider=value.provider,
                        model=value.model,
                        reasoning_effort=value.reasoning_effort,
                        schema_version=value.schema_version,
                        payload_json=value.payload_json,
                        error_code=value.error_code,
                        created_at=value.created_at.isoformat(),
                    )
                )
            else:
                row.status = value.status
                row.provider = value.provider
                row.model = value.model
                row.reasoning_effort = value.reasoning_effort
                row.schema_version = value.schema_version
                row.payload_json = value.payload_json
                row.error_code = value.error_code
                row.created_at = value.created_at.isoformat()

    def interpretation_for_revision(
        self, note_revision_id: str
    ) -> ExternalNoteInterpretation | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteInterpretationRow).where(
                    ExternalNoteInterpretationRow.note_revision_id == note_revision_id
                )
            )
            return _interpretation(row) if row is not None else None

    def append_sync_receipt(self, value: ExternalNoteSyncReceipt) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                ExternalNoteSyncReceiptRow(
                    receipt_id=value.receipt_id,
                    status=value.status.value,
                    cache_files_scanned=value.cache_files_scanned,
                    notes_seen=value.notes_seen,
                    identities_created=value.identities_created,
                    revisions_created=value.revisions_created,
                    unchanged_count=value.unchanged_count,
                    full_count=value.full_count,
                    summary_only_count=value.summary_only_count,
                    interpretations_created=value.interpretations_created,
                    warning_codes=value.warning_codes,
                    error_codes=value.error_codes,
                    started_at=value.started_at.isoformat(),
                    completed_at=value.completed_at.isoformat(),
                )
            )

    def list_latest(
        self, limit: int = 100
    ) -> tuple[tuple[ExternalNoteIdentity, ExternalNoteRevision], ...]:
        bounded = max(1, min(limit, 500))
        with Session(self._engine) as session:
            identities = session.scalars(
                select(ExternalNoteIdentityRow)
                .order_by(ExternalNoteIdentityRow.last_seen_at.desc())
                .limit(bounded)
            ).all()
            result: list[tuple[ExternalNoteIdentity, ExternalNoteRevision]] = []
            for identity_row in identities:
                revision_row = session.scalar(
                    select(ExternalNoteRevisionRow)
                    .where(ExternalNoteRevisionRow.note_id == identity_row.note_id)
                    .order_by(ExternalNoteRevisionRow.version.desc())
                    .limit(1)
                )
                if revision_row is not None:
                    result.append((_identity(identity_row), _revision(revision_row)))
            return tuple(result)

    def list_revisions(
        self, note_id: str, limit: int = 50
    ) -> tuple[ExternalNoteRevision, ...]:
        bounded = max(1, min(limit, 200))
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ExternalNoteRevisionRow)
                .where(ExternalNoteRevisionRow.note_id == note_id)
                .order_by(ExternalNoteRevisionRow.version.desc())
                .limit(bounded)
            ).all()
            return tuple(_revision(row) for row in rows)


def _identity(row: ExternalNoteIdentityRow) -> ExternalNoteIdentity:
    return ExternalNoteIdentity(
        note_id=row.note_id,
        source=row.source,
        external_id=row.external_id,
        title=row.title,
        primary_instrument_id=row.primary_instrument_id,
        created_at=datetime.fromisoformat(row.created_at),
        last_seen_at=datetime.fromisoformat(row.last_seen_at),
    )


def _revision(row: ExternalNoteRevisionRow) -> ExternalNoteRevision:
    blocks = json.loads(row.blocks_json)
    return ExternalNoteRevision(
        note_revision_id=row.note_revision_id,
        note_id=row.note_id,
        version=row.version,
        content_sha256=row.content_sha256,
        source_revision_key=row.source_revision_key,
        title=row.title,
        summary=row.summary,
        full_body=row.full_body,
        coverage=NoteCoverage(row.coverage),
        source_timestamp=(
            datetime.fromisoformat(row.source_timestamp)
            if row.source_timestamp is not None
            else None
        ),
        observed_at=datetime.fromisoformat(row.observed_at),
        visibility=row.visibility,
        related_provider_stock_ids=row.related_stock_ids,
        related_provider_codes=row.related_codes,
        blocks=tuple(
            AttributedNoteBlock(
                ordinal=int(item["ordinal"]),
                speaker_kind=NoteSpeakerKind(item["speaker_kind"]),
                speaker_label=str(item["speaker_label"]),
                body=str(item["body"]),
                section_date=(
                    str(item["section_date"])
                    if item.get("section_date") is not None
                    else None
                ),
            )
            for item in blocks
        ),
    )


def _interpretation(row: ExternalNoteInterpretationRow) -> ExternalNoteInterpretation:
    return ExternalNoteInterpretation(
        interpretation_id=row.interpretation_id,
        note_revision_id=row.note_revision_id,
        status=row.status,
        provider=row.provider,
        model=row.model,
        reasoning_effort=row.reasoning_effort,
        schema_version=row.schema_version,
        payload_json=row.payload_json,
        error_code=row.error_code,
        created_at=datetime.fromisoformat(row.created_at),
    )


__all__ = ["SqlAlchemyExternalNoteRepository"]
