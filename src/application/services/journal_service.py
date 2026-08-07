"""JournalService — confirmed Journal append + structured search (Phase 1C C4b2).

Not registered as a public MCP tool in this slice (C5). Host must only call
append after the user explicitly requests a durable journal entry.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.research_memory import (
    JournalEntryDTO,
    JournalSearchPageDTO,
    ResearchSearchQuery,
)
from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY, ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_memory_write_support import (
    audit_summary,
    compute_journal_idempotency_payload_sha256,
    prepare_topic_tags,
    redact_required_text,
    schema_version,
    stable_dedupe_strs,
    validate_journal_related_entity,
    validate_journal_supersedes,
)
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
    normalize_idempotency_key,
    require_confirm_reviewer,
)
from domain.common.enums import JournalEntryType, ResearchSearchEntityType
from domain.common.errors import DuplicateIdempotencyKey, InputValidationError
from domain.common.ids import EntityIdPrefix
from domain.research.models import JournalEntry


class JournalService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._redactor = secret_redactor

    def append(
        self,
        *,
        subject_id: str | None,
        entry_type: JournalEntryType,
        title: str,
        body_markdown: str,
        authored_by: str,
        confirmed_by: str,
        instrument_ids: tuple[str, ...],
        topic_tags: tuple[str, ...],
        related_entity_type: str | None,
        related_entity_id: str | None,
        supersedes_journal_id: str | None,
        idempotency_key: str,
    ) -> ToolEnvelope[JournalEntryDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(confirmed_by, action="journal_append")
            confirmer = confirmed_by.strip()
            author = authored_by.strip()
            if not author:
                raise InputValidationError(
                    "authored_by must be non-blank",
                    details={"field": "authored_by"},
                )

            subject_id_n: str | None
            if subject_id is None:
                subject_id_n = None
            else:
                subject_id_n = subject_id.strip()
                if not subject_id_n:
                    raise InputValidationError(
                        "subject_id must be non-blank when provided",
                        details={"field": "subject_id"},
                    )

            title_r = redact_required_text(title, self._redactor, field="title")
            body_r = redact_required_text(body_markdown, self._redactor, field="body_markdown")
            instruments = stable_dedupe_strs(instrument_ids)
            tags = prepare_topic_tags(topic_tags, self._redactor)

            rel_type = related_entity_type.strip() if related_entity_type is not None else None
            rel_id = related_entity_id.strip() if related_entity_id is not None else None
            if rel_type is not None and not rel_type:
                raise InputValidationError(
                    "related_entity_type must be non-blank when provided",
                    details={"field": "related_entity_type"},
                )
            if rel_id is not None and not rel_id:
                raise InputValidationError(
                    "related_entity_id must be non-blank when provided",
                    details={"field": "related_entity_id"},
                )

            supersedes = (
                supersedes_journal_id.strip() if supersedes_journal_id is not None else None
            )
            if supersedes is not None and not supersedes:
                raise InputValidationError(
                    "supersedes_journal_id must be non-blank when provided",
                    details={"field": "supersedes_journal_id"},
                )

            key = normalize_idempotency_key(idempotency_key)
            payload_sha = compute_journal_idempotency_payload_sha256(
                subject_id=subject_id_n,
                entry_type=entry_type,
                title=title_r,
                body_markdown=body_r,
                authored_by=author,
                confirmed_by=confirmer,
                instrument_ids=instruments,
                topic_tags=tags,
                related_entity_type=rel_type,
                related_entity_id=rel_id,
                supersedes_journal_id=supersedes,
            )

            with self._uow_factory() as uow:
                existing = uow.journal.get_by_idempotency_key(key)
                if existing is not None:
                    existing_sha = compute_journal_idempotency_payload_sha256(
                        subject_id=existing.subject_id,
                        entry_type=existing.entry_type,
                        title=existing.title,
                        body_markdown=existing.body_markdown,
                        authored_by=existing.authored_by,
                        confirmed_by=existing.confirmed_by,
                        instrument_ids=existing.instrument_ids,
                        topic_tags=existing.topic_tags,
                        related_entity_type=existing.related_entity_type,
                        related_entity_id=existing.related_entity_id,
                        supersedes_journal_id=existing.supersedes_journal_id,
                    )
                    if existing_sha != payload_sha:
                        raise DuplicateIdempotencyKey(
                            "idempotency_key already used with a different payload",
                            details={
                                "idempotency_key": key,
                                "existing_journal_id": existing.journal_id,
                            },
                        )
                    # Same key + same payload: return existing; no re-index/audit.
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=JournalEntryDTO.from_domain(existing),
                        warnings=(DUPLICATE_IDEMPOTENCY_KEY,),
                        degraded=True,
                    )

                if subject_id_n is not None:
                    uow.subjects.get(subject_id_n)

                created_at = self._clock.now()
                validate_journal_related_entity(
                    uow,
                    subject_id=subject_id_n,
                    created_at=created_at,
                    related_entity_type=rel_type,
                    related_entity_id=rel_id,
                )
                validate_journal_supersedes(
                    uow,
                    subject_id=subject_id_n,
                    created_at=created_at,
                    supersedes_journal_id=supersedes,
                )

                journal_id = self._id_generator.new(EntityIdPrefix.JOURNAL)
                entry = JournalEntry(
                    journal_id=journal_id,
                    subject_id=subject_id_n,
                    entry_type=entry_type,
                    title=title_r,
                    body_markdown=body_r,
                    created_at=created_at,
                    authored_by=author,
                    confirmed_by=confirmer,
                    instrument_ids=instruments,
                    topic_tags=tags,
                    related_entity_type=rel_type,
                    related_entity_id=rel_id,
                    supersedes_journal_id=supersedes,
                    schema_version=schema_version(),
                )
                # Frozen order: business → Search → Audit → commit.
                uow.journal.add(
                    entry,
                    idempotency_key=key,
                    idempotency_payload_sha256=payload_sha,
                )
                uow.search_index.index(ResearchSearchEntityType.JOURNAL, journal_id)
                linked: list[str] = []
                if rel_id is not None:
                    linked.append(rel_id)
                if supersedes is not None:
                    linked.append(supersedes)
                uow.audit.append(
                    "phase1c.journal.appended",
                    audit_summary(
                        action="append",
                        entity_type="journal",
                        entity_id=journal_id,
                        subject_id=subject_id_n,
                        actor=author,
                        confirmed_by=confirmer,
                        content_sha256=payload_sha,
                        linked_entity_ids=tuple(linked),
                        idempotency_key=key,
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=JournalEntryDTO.from_domain(entry),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def search(
        self,
        *,
        text: str | None,
        subject_id: str | None,
        instrument_id: str | None,
        entry_types: tuple[JournalEntryType, ...],
        as_of: datetime | None,
        limit: int,
        offset: int,
    ) -> ToolEnvelope[JournalSearchPageDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            # Caller must supply a real filter; forced JOURNAL entity type is not one.
            text_filter = text is not None and bool(text.strip())
            case_filter = subject_id is not None and bool(subject_id.strip())
            instrument_filter = instrument_id is not None and bool(instrument_id.strip())
            types_filter = len(entry_types) > 0
            as_of_filter = as_of is not None
            if not any(
                (
                    text_filter,
                    case_filter,
                    instrument_filter,
                    types_filter,
                    as_of_filter,
                )
            ):
                raise InputValidationError(
                    "journal search requires at least one effective filter "
                    "(non-blank text, subject_id, instrument_id, entry_types, or as_of)",
                    details={
                        "field": "filters",
                        "rule": "at_least_one_effective_filter",
                    },
                )

            # Structured filter on journal_entry_types then hydrate in hit order.
            query = ResearchSearchQuery(
                text=text,
                subject_id=subject_id,
                instrument_id=instrument_id,
                entity_types=(ResearchSearchEntityType.JOURNAL,),
                journal_entry_types=entry_types,
                as_of=as_of,
                include_superseded=False,
                limit=limit,
                offset=offset,
            )
            with self._uow_factory() as uow:
                page = uow.search_index.search(query)
                items = tuple(
                    JournalEntryDTO.from_domain(uow.journal.get(hit.entity_id))
                    for hit in page.items
                )
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=JournalSearchPageDTO(
                        items=items,
                        total=page.total,
                        limit=page.limit,
                        offset=page.offset,
                        has_more=page.has_more,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
