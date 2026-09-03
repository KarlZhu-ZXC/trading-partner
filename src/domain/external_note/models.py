"""Immutable external-note identities, revisions, and sync receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind, NoteSyncStatus


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be bounded non-blank text")
    return value.strip()


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _codes(values: tuple[str, ...], field: str) -> None:
    if len(values) > 100 or any(not isinstance(item, str) or not item for item in values):
        raise DataContractError(f"{field} is invalid")


@dataclass(frozen=True, slots=True)
class AttributedNoteBlock:
    ordinal: int
    speaker_kind: NoteSpeakerKind
    speaker_label: str
    body: str
    section_date: str | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise DataContractError("note block ordinal must be nonnegative")
        if not isinstance(self.speaker_kind, NoteSpeakerKind):
            raise DataContractError("note block speaker kind is invalid")
        object.__setattr__(self, "speaker_label", _text(self.speaker_label, "speaker", 80))
        object.__setattr__(self, "body", _text(self.body, "note block body", 20_000))
        object.__setattr__(
            self,
            "section_date",
            _optional_text(self.section_date, "note section date", 32),
        )


@dataclass(frozen=True, slots=True)
class ExternalNoteSourceSnapshot:
    source: str
    external_id: str
    title: str
    summary: str
    full_body: str | None
    coverage: NoteCoverage
    source_timestamp: datetime | None
    observed_at: datetime
    primary_instrument_id: str | None
    related_provider_stock_ids: tuple[str, ...]
    related_provider_codes: tuple[str, ...]
    visibility: str
    blocks: tuple[AttributedNoteBlock, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _text(self.source, "note source", 40))
        object.__setattr__(self, "external_id", _text(self.external_id, "external id", 200))
        object.__setattr__(self, "title", _text(self.title, "note title", 500))
        if not isinstance(self.summary, str) or len(self.summary) > 50_000:
            raise DataContractError("note summary is invalid")
        if self.full_body is not None and (
            not isinstance(self.full_body, str) or len(self.full_body) > 100_000
        ):
            raise DataContractError("note full body is invalid")
        if not isinstance(self.coverage, NoteCoverage):
            raise DataContractError("note coverage is invalid")
        if self.coverage is NoteCoverage.FULL and not (self.full_body or "").strip():
            raise DataContractError("FULL note coverage requires a body")
        if self.source_timestamp is not None:
            require_aware_datetime(self.source_timestamp, field_name="source_timestamp")
        require_aware_datetime(self.observed_at, field_name="observed_at")
        object.__setattr__(
            self,
            "primary_instrument_id",
            _optional_text(self.primary_instrument_id, "primary instrument id", 200),
        )
        _codes(self.related_provider_stock_ids, "related stock ids")
        _codes(self.related_provider_codes, "related provider codes")
        object.__setattr__(self, "visibility", _text(self.visibility, "visibility", 40))


@dataclass(frozen=True, slots=True)
class ExternalNoteIdentity:
    note_id: str
    source: str
    external_id: str
    title: str
    primary_instrument_id: str | None
    created_at: datetime
    last_seen_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "note_id", _text(self.note_id, "note id", 128))
        object.__setattr__(self, "source", _text(self.source, "note source", 40))
        object.__setattr__(self, "external_id", _text(self.external_id, "external id", 200))
        object.__setattr__(self, "title", _text(self.title, "note title", 500))
        object.__setattr__(
            self,
            "primary_instrument_id",
            _optional_text(self.primary_instrument_id, "primary instrument id", 200),
        )
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.last_seen_at, field_name="last_seen_at")
        if self.last_seen_at < self.created_at:
            raise DataContractError("note last_seen_at precedes created_at")


@dataclass(frozen=True, slots=True)
class ExternalNoteRevision:
    note_revision_id: str
    note_id: str
    version: int
    content_sha256: str
    source_revision_key: str
    title: str
    summary: str
    full_body: str | None
    coverage: NoteCoverage
    source_timestamp: datetime | None
    observed_at: datetime
    visibility: str
    related_provider_stock_ids: tuple[str, ...]
    related_provider_codes: tuple[str, ...]
    blocks: tuple[AttributedNoteBlock, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "note_revision_id", _text(self.note_revision_id, "revision id", 128)
        )
        object.__setattr__(self, "note_id", _text(self.note_id, "note id", 128))
        if self.version < 1:
            raise DataContractError("note revision version must be positive")
        if len(self.content_sha256) != 64:
            raise DataContractError("note content hash is invalid")
        object.__setattr__(
            self,
            "source_revision_key",
            _text(self.source_revision_key, "source revision key", 128),
        )
        ExternalNoteSourceSnapshot(
            source="MOOMOO_NOTE",
            external_id="revision-validation",
            title=self.title,
            summary=self.summary,
            full_body=self.full_body,
            coverage=self.coverage,
            source_timestamp=self.source_timestamp,
            observed_at=self.observed_at,
            primary_instrument_id=None,
            related_provider_stock_ids=self.related_provider_stock_ids,
            related_provider_codes=self.related_provider_codes,
            visibility=self.visibility,
            blocks=self.blocks,
        )


@dataclass(frozen=True, slots=True)
class ExternalNoteInterpretation:
    interpretation_id: str
    note_revision_id: str
    status: str
    provider: str
    model: str
    reasoning_effort: str
    schema_version: str
    payload_json: str
    error_code: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalNoteSyncReceipt:
    receipt_id: str
    status: NoteSyncStatus
    cache_files_scanned: int
    notes_seen: int
    identities_created: int
    revisions_created: int
    unchanged_count: int
    full_count: int
    summary_only_count: int
    interpretations_created: int
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    started_at: datetime
    completed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _text(self.receipt_id, "receipt id", 128))
        if not isinstance(self.status, NoteSyncStatus):
            raise DataContractError("note sync status is invalid")
        counts = (
            self.cache_files_scanned,
            self.notes_seen,
            self.identities_created,
            self.revisions_created,
            self.unchanged_count,
            self.full_count,
            self.summary_only_count,
            self.interpretations_created,
        )
        if any(value < 0 for value in counts):
            raise DataContractError("note sync counts must be nonnegative")
        _codes(self.warning_codes, "note sync warning codes")
        _codes(self.error_codes, "note sync error codes")
        require_aware_datetime(self.started_at, field_name="started_at")
        require_aware_datetime(self.completed_at, field_name="completed_at")
        if self.completed_at < self.started_at:
            raise DataContractError("note sync completed_at precedes started_at")
