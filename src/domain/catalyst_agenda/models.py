"""Append-only Catalyst Agenda identity and version models."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaItemStatus,
    AgendaSourceType,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

CATALYST_AGENDA_SCHEMA_VERSION = 2
_SUPPORTED_CATALYST_AGENDA_SCHEMA_VERSIONS = {1, CATALYST_AGENDA_SCHEMA_VERSION}
CATALYST_AGENDA_SOURCE = "USER_CONFIRMED"


def _text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized


def _optional_text(value: str | None, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


@dataclass(frozen=True, slots=True)
class CatalystAgendaIdentity:
    agenda_item_id: str
    logical_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.agenda_item_id.startswith("agenda_"):
            raise DataContractError("agenda_item_id must use agenda_ prefix")
        _text(self.logical_key, "logical_key", 500)
        require_aware_datetime(self.created_at, field_name="created_at")


@dataclass(frozen=True, slots=True)
class CatalystAgendaVersion:
    agenda_item_id: str
    version: int
    supersedes_version: int | None
    instrument_id: str | None
    subject_id: str | None
    kind: AgendaItemKind
    title: str
    fiscal_period: str | None
    upstream_event_key: str | None
    window_start: datetime | None
    window_end: datetime | None
    timezone: str
    date_certainty: AgendaDateCertainty
    status: AgendaItemStatus
    source_type: AgendaSourceType
    source_vendor: str
    source_reference: str | None
    source_visible_at: datetime
    last_verified_at: datetime
    expected_question: str | None
    linked_event_id: str | None
    linked_report_id: str | None
    revision_note: str | None
    created_by: str
    confirmed_by: str
    authorization_note: str
    idempotency_key: str
    request_fingerprint: str
    historical_vintage: bool
    recorded_at: datetime
    schema_version: int = CATALYST_AGENDA_SCHEMA_VERSION
    execution_effect: bool = False
    linked_evidence_id: str | None = None
    outcome_occurred_at: datetime | None = None
    outcome_note: str | None = None

    def __post_init__(self) -> None:
        if not self.agenda_item_id.startswith("agenda_"):
            raise DataContractError("agenda_item_id must use agenda_ prefix")
        if type(self.version) is not int or self.version < 1:
            raise DataContractError("version must be a positive integer")
        if self.version == 1 and self.supersedes_version is not None:
            raise DataContractError("first Agenda version cannot supersede another version")
        if self.version > 1 and self.supersedes_version != self.version - 1:
            raise DataContractError("Agenda versions must supersede the immediately prior version")
        if (
            self.instrument_id is None
            and self.subject_id is None
            and self.kind not in {AgendaItemKind.MACRO_RELEASE, AgendaItemKind.POLICY}
        ):
            raise DataContractError(
                "only MACRO_RELEASE or POLICY may omit instrument_id and subject_id"
            )
        _optional_text(self.instrument_id, "instrument_id", 256)
        _optional_text(self.subject_id, "subject_id", 128)
        if not isinstance(self.kind, AgendaItemKind):
            raise DataContractError("kind is invalid")
        _text(self.title, "title", 300)
        _optional_text(self.fiscal_period, "fiscal_period", 100)
        _optional_text(self.upstream_event_key, "upstream_event_key", 300)
        _text(self.timezone, "timezone", 100)
        if not isinstance(self.date_certainty, AgendaDateCertainty):
            raise DataContractError("date_certainty is invalid")
        if (self.window_start is None) != (self.window_end is None):
            raise DataContractError("window_start and window_end must be supplied together")
        if self.window_start is None:
            if self.date_certainty is not AgendaDateCertainty.UNKNOWN:
                raise DataContractError("only UNKNOWN date certainty may omit the event window")
        else:
            require_aware_datetime(self.window_start, field_name="window_start")
            assert self.window_end is not None
            require_aware_datetime(self.window_end, field_name="window_end")
            if self.window_end < self.window_start:
                raise DataContractError("window_end must be >= window_start")
        if self.status not in {
            AgendaItemStatus.UPCOMING,
            AgendaItemStatus.OCCURRED,
            AgendaItemStatus.CANCELLED,
            AgendaItemStatus.SUPERSEDED,
        }:
            raise DataContractError("Agenda status is invalid")
        if not isinstance(self.source_type, AgendaSourceType):
            raise DataContractError("source_type is invalid")
        _text(self.source_vendor, "source_vendor", 100)
        _optional_text(self.source_reference, "source_reference", 1_000)
        require_aware_datetime(self.source_visible_at, field_name="source_visible_at")
        require_aware_datetime(self.last_verified_at, field_name="last_verified_at")
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        if self.source_visible_at > self.last_verified_at:
            raise DataContractError("source_visible_at must be <= last_verified_at")
        if self.last_verified_at > self.recorded_at:
            raise DataContractError("last_verified_at must be <= recorded_at")
        _optional_text(self.expected_question, "expected_question", 2_000)
        _optional_text(self.linked_event_id, "linked_event_id", 128)
        _optional_text(self.linked_report_id, "linked_report_id", 128)
        _optional_text(self.linked_evidence_id, "linked_evidence_id", 128)
        _optional_text(self.revision_note, "revision_note", 1_000)
        if self.outcome_occurred_at is not None:
            require_aware_datetime(
                self.outcome_occurred_at,
                field_name="outcome_occurred_at",
            )
        _optional_text(self.outcome_note, "outcome_note", 2_000)
        if self.status is AgendaItemStatus.OCCURRED:
            if not (
                self.linked_event_id
                or self.linked_report_id
                or self.linked_evidence_id
            ):
                raise DataContractError("OCCURRED Agenda item requires a linked durable fact")
            if self.schema_version >= 2 and (
                self.outcome_occurred_at is None or self.outcome_note is None
            ):
                raise DataContractError(
                    "schema v2 OCCURRED Agenda item requires outcome_occurred_at and outcome_note"
                )
        _text(self.created_by, "created_by", 100)
        if self.source_type is AgendaSourceType.PROVIDER:
            if self.confirmed_by != "system":
                raise DataContractError("Provider Agenda versions require confirmed_by=system")
            sync_ref = self.authorization_note.removeprefix("provider_sync:").strip()
            if not self.authorization_note.startswith("provider_sync:") or not sync_ref:
                raise DataContractError(
                    "Provider Agenda versions require authorization_note=provider_sync:<run_id>"
                )
        elif self.confirmed_by not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by must be user or external_agent")
        _text(self.authorization_note, "authorization_note", 1_000)
        _text(self.idempotency_key, "idempotency_key", 128)
        if re.fullmatch(r"[0-9a-f]{64}", self.request_fingerprint) is None:
            raise DataContractError("request_fingerprint must be a SHA-256 digest")
        if type(self.historical_vintage) is not bool:
            raise DataContractError("historical_vintage must be bool")
        if self.schema_version not in _SUPPORTED_CATALYST_AGENDA_SCHEMA_VERSIONS:
            raise DataContractError("unsupported Catalyst Agenda schema version")
        if self.execution_effect:
            raise DataContractError("Catalyst Agenda cannot have execution effect")


def agenda_request_fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
