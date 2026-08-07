"""EvidenceService — internal Evidence / Link / Assessment writes (Phase 1C C4a).

Not registered as public MCP tools. Provider / workflow adapters call this port.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from application.dto.research_memory import (
    EvidenceAssessmentDTO,
    EvidenceDTO,
    SubjectEvidenceLinkDTO,
)
from application.dto.tool_envelope import DUPLICATE_CONTENT, ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_memory_write_support import (
    audit_summary,
    normalize_source_url,
    prepare_structured_data_json,
    prepare_topic_tags,
    redact_optional_text,
    redact_required_text,
    require_aware_optional,
    schema_version,
    stable_dedupe_strs,
    update_subject_evidence_cache,
)
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
    require_confirm_reviewer,
)
from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    ReliabilityLevel,
    ResearchSearchEntityType,
)
from domain.common.errors import InputValidationError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.models import (
    Evidence,
    EvidenceAssessment,
    SubjectEvidenceLink,
    compute_evidence_content_sha256,
)


class EvidenceService:
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

    def record_evidence(
        self,
        *,
        evidence_type: EvidenceType,
        origin: EvidenceOrigin,
        title: str,
        summary: str,
        content_text: str | None,
        structured_data_json: str | None,
        source_name: str,
        source_vendor: str | None,
        source_record_id: str | None,
        source_url: str | None,
        published_at: datetime | None,
        effective_from: datetime | None,
        effective_to: datetime | None,
        instrument_ids: tuple[str, ...],
        topic_tags: tuple[str, ...],
        quality: EvidenceQuality,
        reliability: ReliabilityLevel,
        confidence: Decimal | None,
        supersedes_evidence_id: str | None,
        recorded_by: str,
        subject_ids: tuple[str, ...] = (),
        observed_at: datetime | None = None,
    ) -> ToolEnvelope[EvidenceDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            title_r = redact_required_text(title, self._redactor, field="title")
            summary_r = redact_required_text(summary, self._redactor, field="summary")
            content_r = redact_optional_text(content_text, self._redactor, field="content_text")
            source_name_r = redact_required_text(source_name, self._redactor, field="source_name")
            # source_vendor is a frozen VendorId wire value — no free-text transform.
            vendor = source_vendor.strip() if isinstance(source_vendor, str) else source_vendor
            if vendor is not None and vendor == "":
                raise InputValidationError(
                    "source_vendor must be non-blank when provided",
                    details={"field": "source_vendor"},
                )
            source_record_r = redact_optional_text(
                source_record_id, self._redactor, field="source_record_id"
            )
            if source_record_r is not None and not source_record_r.strip():
                raise InputValidationError(
                    "source_record_id must be non-blank when provided",
                    details={"field": "source_record_id"},
                )
            source_url_n = normalize_source_url(source_url, self._redactor)
            structured_n = prepare_structured_data_json(structured_data_json, self._redactor)
            instruments = stable_dedupe_strs(instrument_ids)
            tags = prepare_topic_tags(topic_tags, self._redactor)
            subjects = stable_dedupe_strs(subject_ids)
            recorded_by_r = redact_required_text(recorded_by, self._redactor, field="recorded_by")
            published = require_aware_optional(published_at, field="published_at")
            eff_from = require_aware_optional(effective_from, field="effective_from")
            eff_to = require_aware_optional(effective_to, field="effective_to")
            if observed_at is None:
                observed = self._clock.now()
            else:
                observed = require_aware_datetime(observed_at, field_name="observed_at")

            content_hash = compute_evidence_content_sha256(
                evidence_type=evidence_type,
                origin=origin,
                title=title_r,
                summary=summary_r,
                content_text=content_r,
                structured_data_json=structured_n,
                source_name=source_name_r,
                source_vendor=vendor,
                source_record_id=source_record_r,
                published_at=published,
                effective_from=eff_from,
                effective_to=eff_to,
                instrument_ids=instruments,
            )

            with self._uow_factory() as uow:
                existing = uow.evidence.get_by_content_sha256(content_hash)
                if existing is not None:
                    # Duplicate immutable content: fill missing Research Subject links only.
                    # Frozen order: business rows + Research Subject caches
                    # → Search → audits → commit.
                    dup_created_links: list[SubjectEvidenceLink] = []
                    for subject_id in subjects:
                        uow.subjects.get(subject_id)
                        if uow.subject_evidence_links.exists(subject_id, existing.evidence_id):
                            continue
                        write_at = self._clock.now()
                        link = SubjectEvidenceLink(
                            link_id=self._id_generator.new(EntityIdPrefix.REV),
                            subject_id=subject_id,
                            evidence_id=existing.evidence_id,
                            linked_at=write_at,
                            linked_by=recorded_by_r,
                            schema_version=schema_version(),
                        )
                        uow.subject_evidence_links.add(link)
                        update_subject_evidence_cache(
                            uow,
                            subject_id=subject_id,
                            evidence_id=existing.evidence_id,
                            updated_at=write_at,
                        )
                        dup_created_links.append(link)
                    if dup_created_links:
                        # Full re-index so a missing projection self-heals.
                        uow.search_index.index(
                            ResearchSearchEntityType.EVIDENCE, existing.evidence_id
                        )
                        for link in dup_created_links:
                            uow.audit.append(
                                "phase1c.evidence.linked",
                                audit_summary(
                                    action="link",
                                    entity_type="subject_evidence_link",
                                    entity_id=link.link_id,
                                    subject_id=link.subject_id,
                                    actor=recorded_by_r,
                                    content_sha256=existing.content_sha256,
                                    linked_entity_ids=(existing.evidence_id,),
                                ),
                                request_id=request_id,
                            )
                        uow.commit()
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=EvidenceDTO.from_domain(existing),
                        warnings=(DUPLICATE_CONTENT,),
                        degraded=True,
                    )

                evidence_id = self._id_generator.new(EntityIdPrefix.EVIDENCE)
                evidence = Evidence(
                    evidence_id=evidence_id,
                    evidence_type=evidence_type,
                    origin=origin,
                    title=title_r,
                    summary=summary_r,
                    content_text=content_r,
                    structured_data_json=structured_n,
                    source_name=source_name_r,
                    source_vendor=vendor,
                    source_record_id=source_record_r,
                    source_url=source_url_n,
                    published_at=published,
                    observed_at=observed,
                    effective_from=eff_from,
                    effective_to=eff_to,
                    instrument_ids=instruments,
                    topic_tags=tags,
                    quality=quality,
                    reliability=reliability,
                    confidence=confidence,
                    content_sha256=content_hash,
                    supersedes_evidence_id=supersedes_evidence_id,
                    recorded_by=recorded_by_r,
                    schema_version=schema_version(),
                )
                uow.evidence.add(evidence)

                # Frozen order: business rows + Research Subject caches → Search → audits → commit.
                created_links: list[SubjectEvidenceLink] = []
                for subject_id in subjects:
                    uow.subjects.get(subject_id)
                    write_at = self._clock.now()
                    link = SubjectEvidenceLink(
                        link_id=self._id_generator.new(EntityIdPrefix.REV),
                        subject_id=subject_id,
                        evidence_id=evidence_id,
                        linked_at=write_at,
                        linked_by=recorded_by_r,
                        schema_version=schema_version(),
                    )
                    uow.subject_evidence_links.add(link)
                    update_subject_evidence_cache(
                        uow,
                        subject_id=subject_id,
                        evidence_id=evidence_id,
                        updated_at=write_at,
                    )
                    created_links.append(link)

                uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence_id)
                for link in created_links:
                    uow.audit.append(
                        "phase1c.evidence.linked",
                        audit_summary(
                            action="link",
                            entity_type="subject_evidence_link",
                            entity_id=link.link_id,
                            subject_id=link.subject_id,
                            actor=recorded_by_r,
                            content_sha256=content_hash,
                            linked_entity_ids=(evidence_id,),
                        ),
                        request_id=request_id,
                    )
                linked_subject_ids = [link.subject_id for link in created_links]
                uow.audit.append(
                    "phase1c.evidence.recorded",
                    audit_summary(
                        action="record",
                        entity_type="evidence",
                        entity_id=evidence_id,
                        subject_id=linked_subject_ids[0] if linked_subject_ids else None,
                        actor=recorded_by_r,
                        content_sha256=content_hash,
                        linked_entity_ids=tuple(linked_subject_ids),
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=EvidenceDTO.from_domain(evidence),
                )
        except Exception as exc:  # noqa: BLE001 — map to ToolEnvelope
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def link_evidence_to_subject(
        self,
        *,
        evidence_id: str,
        subject_id: str,
        linked_by: str,
    ) -> ToolEnvelope[SubjectEvidenceLinkDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            actor = redact_required_text(linked_by, self._redactor, field="linked_by")
            subject_id_n = subject_id.strip()
            evidence_id_n = evidence_id.strip()
            if not subject_id_n or not evidence_id_n:
                raise InputValidationError(
                    "subject_id and evidence_id must be non-blank",
                    details={"field": "ids"},
                )

            with self._uow_factory() as uow:
                uow.subjects.get(subject_id_n)
                evidence = uow.evidence.get(evidence_id_n)
                if uow.subject_evidence_links.exists(subject_id_n, evidence_id_n):
                    existing = uow.subject_evidence_links.get(subject_id_n, evidence_id_n)
                    return envelope_success(
                        request_id=request_id,
                        clock=self._clock,
                        data=SubjectEvidenceLinkDTO.from_domain(existing),
                        warnings=(DUPLICATE_CONTENT,),
                        degraded=True,
                    )

                write_at = self._clock.now()
                link = SubjectEvidenceLink(
                    link_id=self._id_generator.new(EntityIdPrefix.REV),
                    subject_id=subject_id_n,
                    evidence_id=evidence_id_n,
                    linked_at=write_at,
                    linked_by=actor,
                    schema_version=schema_version(),
                )
                uow.subject_evidence_links.add(link)
                update_subject_evidence_cache(
                    uow,
                    subject_id=subject_id_n,
                    evidence_id=evidence_id_n,
                    updated_at=write_at,
                )
                # Full re-index self-heals a missing Evidence projection.
                uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence_id_n)
                uow.audit.append(
                    "phase1c.evidence.linked",
                    audit_summary(
                        action="link",
                        entity_type="subject_evidence_link",
                        entity_id=link.link_id,
                        subject_id=subject_id_n,
                        actor=actor,
                        content_sha256=evidence.content_sha256,
                        linked_entity_ids=(evidence_id_n,),
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=SubjectEvidenceLinkDTO.from_domain(link),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def assess_evidence(
        self,
        *,
        evidence_id: str,
        subject_id: str,
        thesis_id: str | None,
        thesis_revision_id: str | None,
        stance: EvidenceStance,
        materiality: Decimal,
        rationale: str,
        assessed_by: str,
        confirmed_by: str,
    ) -> ToolEnvelope[EvidenceAssessmentDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            require_confirm_reviewer(confirmed_by, action="assess_evidence")
            rationale_r = redact_required_text(rationale, self._redactor, field="rationale")
            assessed_by_r = redact_required_text(assessed_by, self._redactor, field="assessed_by")
            confirmed_by_r = confirmed_by.strip()
            subject_id_n = subject_id.strip()
            evidence_id_n = evidence_id.strip()
            thesis_n = thesis_id.strip() if thesis_id is not None else None
            rev_n = thesis_revision_id.strip() if thesis_revision_id is not None else None
            if not subject_id_n or not evidence_id_n:
                raise InputValidationError(
                    "subject_id and evidence_id must be non-blank",
                    details={"field": "ids"},
                )

            with self._uow_factory() as uow:
                uow.subjects.get(subject_id_n)
                evidence = uow.evidence.get(evidence_id_n)
                # Repository also enforces link + thesis visibility.
                assessed_at = self._clock.now()
                assessment = EvidenceAssessment(
                    assessment_id=self._id_generator.new(EntityIdPrefix.REV),
                    evidence_id=evidence_id_n,
                    subject_id=subject_id_n,
                    thesis_id=thesis_n or None,
                    thesis_revision_id=rev_n or None,
                    stance=stance,
                    materiality=materiality,
                    rationale=rationale_r,
                    assessed_at=assessed_at,
                    assessed_by=assessed_by_r,
                    confirmed_by=confirmed_by_r,
                    schema_version=schema_version(),
                )
                uow.evidence_assessments.add(assessment)
                # Assessment has no Search projection.
                uow.audit.append(
                    "phase1c.evidence.assessed",
                    audit_summary(
                        action="assess",
                        entity_type="evidence_assessment",
                        entity_id=assessment.assessment_id,
                        subject_id=subject_id_n,
                        actor=assessed_by_r,
                        confirmed_by=confirmed_by_r,
                        content_sha256=evidence.content_sha256,
                        linked_entity_ids=(evidence_id_n,),
                    ),
                    request_id=request_id,
                )
                uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=EvidenceAssessmentDTO.from_domain(assessment),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
