"""ResearchArchiveService — internal Report / Event archive writes (Phase 1C C4a).

Not registered as public MCP tools. Phase 1L workflows and Provider adapters call
this surface. Events are not content-hashed; each call is a new external record.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.research_memory import ResearchEventDTO, ResearchReportDTO
from application.dto.tool_envelope import DUPLICATE_CONTENT, ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_memory_write_support import (
    audit_summary,
    redact_optional_text,
    redact_required_text,
    require_aware_optional,
    schema_version,
    stable_dedupe_strs,
    update_case_event_cache,
    update_case_report_cache,
    validate_event_references,
    validate_event_related_entity,
    validate_report_references,
)
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
)
from domain.common.enums import (
    ResearchEventType,
    ResearchReportType,
    ResearchSearchEntityType,
)
from domain.common.errors import InputValidationError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.models import (
    ResearchEvent,
    ResearchReport,
    compute_report_content_sha256,
)


class ResearchArchiveService:
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

    def get_report(self, report_id: str) -> ToolEnvelope[ResearchReportDTO]:
        """Read-only hydrate from ResearchReportRepository (Phase 1C C5 MCP surface).

        Does not commit or write audit. Keeps Report body as business-table source
        of truth rather than Search projection/snippets.
        """
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                report = uow.reports.get(report_id)
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchReportDTO.from_domain(report),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def archive_report(
        self,
        *,
        case_id: str,
        report_type: ResearchReportType,
        title: str,
        summary: str,
        content_markdown: str,
        as_of: datetime,
        created_by: str,
        research_run_id: str | None,
        evidence_ids: tuple[str, ...],
        thesis_revision_ids: tuple[str, ...],
        supersedes_report_id: str | None,
        model_name: str | None,
        prompt_version: str | None,
    ) -> ToolEnvelope[ResearchReportDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            case_id_n = case_id.strip()
            if not case_id_n:
                raise InputValidationError(
                    "case_id must be non-blank",
                    details={"field": "case_id"},
                )
            title_r = redact_required_text(title, self._redactor, field="title")
            summary_r = redact_required_text(summary, self._redactor, field="summary")
            # Markdown: secret redaction only; no reformatting.
            content_r = redact_required_text(
                content_markdown, self._redactor, field="content_markdown"
            )
            created_by_r = redact_required_text(
                created_by, self._redactor, field="created_by"
            )
            model_r = redact_optional_text(
                model_name, self._redactor, field="model_name"
            )
            prompt_r = redact_optional_text(
                prompt_version, self._redactor, field="prompt_version"
            )
            if model_r is not None and not model_r.strip():
                raise InputValidationError(
                    "model_name must be non-blank when provided",
                    details={"field": "model_name"},
                )
            if prompt_r is not None and not prompt_r.strip():
                raise InputValidationError(
                    "prompt_version must be non-blank when provided",
                    details={"field": "prompt_version"},
                )
            as_of_n = require_aware_datetime(as_of, field_name="as_of")
            evidence = stable_dedupe_strs(evidence_ids)
            revisions = stable_dedupe_strs(thesis_revision_ids)
            supersedes = (
                supersedes_report_id.strip()
                if supersedes_report_id is not None
                else None
            )
            run_id = (
                research_run_id.strip() if research_run_id is not None else None
            )
            if run_id is not None and not run_id:
                raise InputValidationError(
                    "research_run_id must be non-blank when provided",
                    details={"field": "research_run_id"},
                )

            content_hash = compute_report_content_sha256(
                case_id=case_id_n,
                report_type=report_type,
                title=title_r,
                summary=summary_r,
                content_markdown=content_r,
                as_of=as_of_n,
                evidence_ids=evidence,
                thesis_revision_ids=revisions,
            )

            with self._uow_factory() as uow:
                existing = uow.reports.get_by_content_sha256(content_hash)
                if existing is not None:
                    # Immutable duplicate: no Case cache / Search rewrite.
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=ResearchReportDTO.from_domain(existing),
                        warnings=(DUPLICATE_CONTENT,),
                        degraded=True,
                    )

                created_at = self._clock.now()
                validate_report_references(
                    uow,
                    case_id=case_id_n,
                    as_of=as_of_n,
                    created_at=created_at,
                    evidence_ids=evidence,
                    thesis_revision_ids=revisions,
                    supersedes_report_id=supersedes,
                )
                report_id = self._id_generator.new(EntityIdPrefix.REPORT)
                report = ResearchReport(
                    report_id=report_id,
                    case_id=case_id_n,
                    report_type=report_type,
                    title=title_r,
                    summary=summary_r,
                    content_markdown=content_r,
                    as_of=as_of_n,
                    created_at=created_at,
                    created_by=created_by_r,
                    research_run_id=run_id,
                    evidence_ids=evidence,
                    thesis_revision_ids=revisions,
                    supersedes_report_id=supersedes,
                    content_sha256=content_hash,
                    model_name=model_r,
                    prompt_version=prompt_r,
                    schema_version=schema_version(),
                )
                uow.reports.add(report)
                update_case_report_cache(
                    uow,
                    case_id=case_id_n,
                    report_id=report_id,
                    updated_at=created_at,
                )
                uow.search_index.index(ResearchSearchEntityType.REPORT, report_id)
                uow.audit.append(
                    "phase1c.report.archived",
                    audit_summary(
                        action="archive",
                        entity_type="report",
                        entity_id=report_id,
                        case_id=case_id_n,
                        actor=created_by_r,
                        content_sha256=content_hash,
                        linked_entity_ids=evidence + revisions,
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchReportDTO.from_domain(report),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def record_event(
        self,
        *,
        case_id: str,
        event_type: ResearchEventType,
        title: str,
        summary: str,
        occurred_at: datetime,
        published_at: datetime | None,
        instrument_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        report_ids: tuple[str, ...],
        related_entity_type: str | None,
        related_entity_id: str | None,
        source_name: str,
        recorded_by: str,
    ) -> ToolEnvelope[ResearchEventDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            case_id_n = case_id.strip()
            if not case_id_n:
                raise InputValidationError(
                    "case_id must be non-blank",
                    details={"field": "case_id"},
                )
            # recorded_by is audit actor only — never stored on ResearchEvent.
            actor = redact_required_text(
                recorded_by, self._redactor, field="recorded_by"
            )
            title_r = redact_required_text(title, self._redactor, field="title")
            summary_r = redact_required_text(summary, self._redactor, field="summary")
            source_name_r = redact_required_text(
                source_name, self._redactor, field="source_name"
            )
            occurred = require_aware_datetime(occurred_at, field_name="occurred_at")
            published = require_aware_optional(published_at, field="published_at")
            instruments = stable_dedupe_strs(instrument_ids)
            evidence = stable_dedupe_strs(evidence_ids)
            reports = stable_dedupe_strs(report_ids)
            rel_type = (
                related_entity_type.strip()
                if related_entity_type is not None
                else None
            )
            rel_id = (
                related_entity_id.strip() if related_entity_id is not None else None
            )
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

            with self._uow_factory() as uow:
                recorded_at = self._clock.now()
                validate_event_references(
                    uow,
                    case_id=case_id_n,
                    recorded_at=recorded_at,
                    evidence_ids=evidence,
                    report_ids=reports,
                )
                validate_event_related_entity(
                    uow,
                    case_id=case_id_n,
                    recorded_at=recorded_at,
                    related_entity_type=rel_type,
                    related_entity_id=rel_id,
                )
                event_id = self._id_generator.new(EntityIdPrefix.EVENT)
                event = ResearchEvent(
                    event_id=event_id,
                    case_id=case_id_n,
                    event_type=event_type,
                    title=title_r,
                    summary=summary_r,
                    occurred_at=occurred,
                    recorded_at=recorded_at,
                    published_at=published,
                    instrument_ids=instruments,
                    evidence_ids=evidence,
                    report_ids=reports,
                    related_entity_type=rel_type,
                    related_entity_id=rel_id,
                    source_name=source_name_r,
                    schema_version=schema_version(),
                )
                uow.events.add(event)
                update_case_event_cache(
                    uow,
                    case_id=case_id_n,
                    event_id=event_id,
                    updated_at=recorded_at,
                )
                uow.search_index.index(ResearchSearchEntityType.EVENT, event_id)
                uow.audit.append(
                    "phase1c.event.recorded",
                    audit_summary(
                        action="record",
                        entity_type="event",
                        entity_id=event_id,
                        case_id=case_id_n,
                        actor=actor,
                        content_sha256=None,
                        linked_entity_ids=evidence + reports,
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchEventDTO.from_domain(event),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
