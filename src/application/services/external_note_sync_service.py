"""Incrementally persist external observations and their bounded model drafts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime

from application.ports.clock import Clock
from application.ports.external_note_credential_store import (
    ExternalNoteCredentialStatus,
    ExternalNoteCredentialStore,
)
from application.ports.external_note_deep_reviewer import ExternalNoteDeepReviewer
from application.ports.external_note_provider import (
    ExternalNoteProvider,
    ExternalNoteScanResult,
    ObservationSourceCapability,
)
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_materializer import (
    ExternalNoteReviewMaterializer,
)
from application.ports.external_observation_capture_store import (
    ExternalObservationCaptureStore,
)
from application.ports.id_generator import IdGenerator
from application.ports.process_lock import ProcessLock
from application.services.external_note_interpretation_service import (
    ExternalNoteInterpretationService,
)
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.external_note.attribution import attributed_blocks, prefer_proven_complete_text
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind, NoteSyncStatus
from domain.external_note.models import (
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteRevision,
    ExternalNoteSourceSnapshot,
    ExternalNoteSyncReceipt,
)


@dataclass(frozen=True, slots=True)
class ExternalNoteInboxItem:
    identity: ExternalNoteIdentity
    revision: ExternalNoteRevision
    interpretation: ExternalNoteInterpretation | None


@dataclass(frozen=True, slots=True)
class ExternalNoteHistoryItem:
    revision: ExternalNoteRevision
    interpretation: ExternalNoteInterpretation | None


@dataclass(frozen=True, slots=True)
class ExternalObservationCaptureRequest:
    source_code: str
    external_id: str
    title: str
    full_body: str
    observed_at: datetime
    summary: str | None = None
    source_timestamp: datetime | None = None
    primary_instrument_id: str | None = None
    related_provider_stock_ids: tuple[str, ...] = ()
    related_provider_codes: tuple[str, ...] = ()
    visibility: str = "SELF"


class ExternalNoteSyncService:
    def __init__(
        self,
        provider: ExternalNoteProvider | tuple[ExternalNoteProvider, ...],
        repository: ExternalNoteRepository,
        clock: Clock,
        id_generator: IdGenerator,
        interpretation: ExternalNoteInterpretationService | None = None,
        *,
        analysis_concurrency: int = 3,
        capture_store: ExternalObservationCaptureStore | None = None,
        process_lock: ProcessLock | None = None,
        process_lock_wait_seconds: float = 5.0,
        credential_stores: tuple[ExternalNoteCredentialStore, ...] = (),
        review_materializer: ExternalNoteReviewMaterializer | None = None,
        deep_reviewer: ExternalNoteDeepReviewer | None = None,
    ) -> None:
        providers = provider if isinstance(provider, tuple) else (provider,)
        self._providers = {item.capability.source_code: item for item in providers}
        if not self._providers or len(self._providers) != len(providers):
            raise DataContractError("observation source codes must be unique")
        self._repository = repository
        self._clock = clock
        self._ids = id_generator
        self._interpretation = interpretation
        self._analysis_concurrency = max(1, min(analysis_concurrency, 5))
        self._capture_store = capture_store
        self._sync_lock = asyncio.Lock()
        self._process_lock = process_lock
        self._process_lock_wait_seconds = max(0.0, min(process_lock_wait_seconds, 30.0))
        self._credential_stores = {item.source_code: item for item in credential_stores}
        self._review_materializer = review_materializer
        self._deep_reviewer = deep_reviewer
        if len(self._credential_stores) != len(credential_stores):
            raise DataContractError("observation credential source codes must be unique")

    def source_capabilities(self) -> tuple[ObservationSourceCapability, ...]:
        return tuple(item.capability for item in self._providers.values())

    def credential_status(self, source_code: str) -> ExternalNoteCredentialStatus:
        store = self._credential_stores.get(source_code)
        return ExternalNoteCredentialStatus(
            source_code=source_code,
            supported=store is not None,
            configured=store.configured() if store is not None else False,
        )

    def configure_credential(self, source_code: str, value: str) -> None:
        store = self._credential_stores.get(source_code)
        if store is None:
            raise DataContractError("observation source credential is unavailable")
        try:
            store.set_secret(value)
        except (OSError, ValueError):
            raise DataContractError(
                "observation source credential is invalid",
                code="OBSERVATION_SOURCE_CREDENTIAL_INVALID",
            ) from None

    async def capture(
        self,
        request: ExternalObservationCaptureRequest,
        *,
        analyze: bool = False,
    ) -> ExternalNoteSyncReceipt:
        if self._capture_store is None:
            raise DataContractError("observation capture is unavailable")
        snapshot = ExternalNoteSourceSnapshot(
            source=request.source_code,
            external_id=request.external_id,
            title=request.title,
            summary=request.summary or request.full_body,
            full_body=request.full_body,
            coverage=NoteCoverage.FULL,
            source_timestamp=request.source_timestamp,
            observed_at=request.observed_at,
            primary_instrument_id=request.primary_instrument_id,
            related_provider_stock_ids=request.related_provider_stock_ids,
            related_provider_codes=request.related_provider_codes,
            visibility=request.visibility,
            blocks=attributed_blocks(request.full_body),
        )
        await asyncio.to_thread(self._capture_store.append, snapshot)
        return await self.sync(
            analyze=analyze,
            source_code="LOCAL_OBSERVATION_BRIDGE",
        )

    async def sync(
        self,
        *,
        analyze: bool = False,
        source_code: str | None = None,
    ) -> ExternalNoteSyncReceipt:
        async with self._sync_lock:
            if self._process_lock is None:
                return await self._sync_locked(analyze=analyze, source_code=source_code)
            deadline = asyncio.get_running_loop().time() + self._process_lock_wait_seconds
            while not await asyncio.to_thread(self._process_lock.acquire):
                if asyncio.get_running_loop().time() >= deadline:
                    raise DataContractError(
                        "observation sync is already running",
                        code="OBSERVATION_SYNC_BUSY",
                        retryable=True,
                    )
                await asyncio.sleep(0.05)
            try:
                return await self._sync_locked(analyze=analyze, source_code=source_code)
            finally:
                await asyncio.to_thread(self._process_lock.release)

    async def _sync_locked(
        self,
        *,
        analyze: bool,
        source_code: str | None,
    ) -> ExternalNoteSyncReceipt:
        started = self._clock.now()
        warning_codes: list[str] = []
        error_codes: list[str] = []
        selected = self._selected_providers(source_code)
        scans = await asyncio.gather(
            *(asyncio.to_thread(item.scan) for item in selected),
            return_exceptions=True,
        )
        successful_scans: list[ExternalNoteScanResult] = []
        for provider, scan_value in zip(selected, scans, strict=True):
            if isinstance(scan_value, BaseException):
                error_codes.append(
                    f"OBSERVATION_SOURCE_UNAVAILABLE_{provider.capability.source_code}"
                )
            else:
                successful_scans.append(scan_value)
        if not successful_scans:
            receipt = ExternalNoteSyncReceipt(
                receipt_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_SYNC),
                status=NoteSyncStatus.FAILED,
                cache_files_scanned=0,
                notes_seen=0,
                identities_created=0,
                revisions_created=0,
                unchanged_count=0,
                full_count=0,
                summary_only_count=0,
                interpretations_created=0,
                warning_codes=(),
                error_codes=tuple(error_codes or ("OBSERVATION_SOURCES_UNAVAILABLE",)),
                started_at=started,
                completed_at=self._clock.now(),
            )
            self._repository.append_sync_receipt(receipt)
            return receipt
        scan = ExternalNoteScanResult(
            snapshots=tuple(
                snapshot for result in successful_scans for snapshot in result.snapshots
            ),
            cache_files_scanned=sum(item.cache_files_scanned for item in successful_scans),
            warning_codes=tuple(
                code for result in successful_scans for code in result.warning_codes
            ),
        )
        warning_codes.extend(scan.warning_codes)
        identities_created = 0
        revisions_created = 0
        unchanged_count = 0
        pending: list[tuple[ExternalNoteRevision, str | None]] = []
        for snapshot in scan.snapshots:
            identity = self._repository.get_by_source_id(snapshot.source, snapshot.external_id)
            if identity is None:
                identity = ExternalNoteIdentity(
                    note_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE),
                    source=snapshot.source,
                    external_id=snapshot.external_id,
                    title=snapshot.title,
                    primary_instrument_id=snapshot.primary_instrument_id,
                    created_at=snapshot.observed_at,
                    last_seen_at=snapshot.observed_at,
                )
                self._repository.append_identity(identity)
                identities_created += 1
            latest = self._repository.latest_revision(identity.note_id)
            content_hash = _content_hash(snapshot)
            source_revision_key = _source_revision_key(snapshot, content_hash)
            updated_identity = replace(
                identity,
                title=snapshot.title,
                primary_instrument_id=(
                    snapshot.primary_instrument_id or identity.primary_instrument_id
                ),
                last_seen_at=max(identity.last_seen_at, snapshot.observed_at),
            )
            self._repository.update_identity(updated_identity)
            if (
                self._repository.revision_by_source_key(
                    identity.note_id, source_revision_key
                )
                is not None
            ):
                unchanged_count += 1
                continue
            if latest is not None and _same_visible_content(latest, snapshot, content_hash):
                unchanged_count += 1
                effective_latest = self._effective_revision(latest)
                existing_interpretation = self._repository.interpretation_for_revision(
                    effective_latest.note_revision_id
                )
                if (
                    analyze
                    and self._interpretation is not None
                    and effective_latest.coverage is NoteCoverage.FULL
                    and (
                        existing_interpretation is None
                        or existing_interpretation.status == "FAILED"
                    )
                ):
                    pending.append((effective_latest, None))
                continue
            if latest is not None and _snapshot_time(snapshot) < _revision_time(latest):
                unchanged_count += 1
                warning_codes.append("OBSERVATION_OUT_OF_ORDER_IGNORED")
                continue
            revision = ExternalNoteRevision(
                note_revision_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_REVISION),
                note_id=identity.note_id,
                version=1 if latest is None else latest.version + 1,
                content_sha256=content_hash,
                source_revision_key=source_revision_key,
                title=snapshot.title,
                summary=snapshot.summary,
                full_body=snapshot.full_body,
                coverage=snapshot.coverage,
                source_timestamp=snapshot.source_timestamp,
                observed_at=snapshot.observed_at,
                visibility=snapshot.visibility,
                related_provider_stock_ids=snapshot.related_provider_stock_ids,
                related_provider_codes=snapshot.related_provider_codes,
                blocks=snapshot.blocks,
            )
            previous = (
                self._repository.interpretation_for_revision(latest.note_revision_id)
                if latest is not None
                else None
            )
            self._repository.append_revision(revision)
            revisions_created += 1
            if revision.coverage is NoteCoverage.FULL:
                pending.append(
                    (
                        revision,
                        previous.payload_json
                        if previous is not None and previous.status == "SUCCEEDED"
                        else None,
                    )
                )
        interpretations_created = 0
        if analyze and self._interpretation is not None and pending:
            interpretation_service = self._interpretation
            semaphore = asyncio.Semaphore(self._analysis_concurrency)

            async def analyze_one(
                value: tuple[ExternalNoteRevision, str | None],
            ) -> ExternalNoteInterpretation:
                async with semaphore:
                    return await interpretation_service.analyze(*value)

            interpretations = await asyncio.gather(*(analyze_one(item) for item in pending))
            for interpretation_value in interpretations:
                self._repository.append_interpretation(interpretation_value)
                interpretations_created += 1
                if interpretation_value.status == "FAILED":
                    warning_codes.append("MOOMOO_NOTE_INTERPRETATION_UNAVAILABLE")
                else:
                    revision_value = next(
                        item[0]
                        for item in pending
                        if item[0].note_revision_id
                        == interpretation_value.note_revision_id
                    )
                    materialized = self._materialize_review_if_eligible(
                        revision_value,
                        interpretation_value,
                    )
                    if materialized:
                        await self._run_deep_review(revision_value.note_revision_id)
        elif pending:
            warning_codes.append("MOOMOO_NOTE_INTERPRETATION_PENDING")
        full_count = sum(item.coverage is NoteCoverage.FULL for item in scan.snapshots)
        summary_only_count = len(scan.snapshots) - full_count
        if summary_only_count:
            warning_codes.append("MOOMOO_NOTES_SUMMARY_ONLY")
            warning_codes.append("MOOMOO_NOTES_FULL_TEXT_UNAVAILABLE")
        status = (
            NoteSyncStatus.PARTIAL if warning_codes or error_codes else NoteSyncStatus.SUCCEEDED
        )
        receipt = ExternalNoteSyncReceipt(
            receipt_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_SYNC),
            status=status,
            cache_files_scanned=scan.cache_files_scanned,
            notes_seen=len(scan.snapshots),
            identities_created=identities_created,
            revisions_created=revisions_created,
            unchanged_count=unchanged_count,
            full_count=full_count,
            summary_only_count=summary_only_count,
            interpretations_created=interpretations_created,
            warning_codes=tuple(dict.fromkeys(warning_codes)),
            error_codes=tuple(dict.fromkeys(error_codes)),
            started_at=started,
            completed_at=self._clock.now(),
        )
        self._repository.append_sync_receipt(receipt)
        return receipt

    def _selected_providers(
        self, source_code: str | None
    ) -> tuple[ExternalNoteProvider, ...]:
        if source_code is None:
            return tuple(self._providers.values())
        provider = self._providers.get(source_code)
        if provider is None:
            raise DataContractError("observation source is unavailable")
        return (provider,)

    def inbox(self, limit: int = 100) -> tuple[ExternalNoteInboxItem, ...]:
        result: list[ExternalNoteInboxItem] = []
        for identity, revision in self._repository.list_latest(limit):
            effective = self._effective_revision(revision)
            result.append(
                ExternalNoteInboxItem(
                    identity=identity,
                    revision=effective,
                    interpretation=(
                        self._repository.interpretation_for_revision(
                            effective.note_revision_id
                        )
                        if effective.coverage is NoteCoverage.FULL
                        else None
                    ),
                )
            )
        return tuple(result)

    def _effective_revision(self, revision: ExternalNoteRevision) -> ExternalNoteRevision:
        if revision.coverage is NoteCoverage.FULL:
            return revision
        previous = self._repository.previous_revision(revision.note_id, revision.version)
        if (
            previous is not None
            and previous.coverage is NoteCoverage.FULL
            and previous.title == revision.title
            and previous.summary == revision.summary
        ):
            previous_body = previous.full_body or ""
            complete = prefer_proven_complete_text(previous_body, revision.summary)
            if complete != previous_body:
                return replace(
                    revision,
                    full_body=complete,
                    coverage=NoteCoverage.FULL,
                    blocks=attributed_blocks(complete),
                )
            return previous
        return revision

    async def analyze_pending(
        self,
        *,
        limit: int = 20,
        retry_failed: bool = False,
        reanalyze_succeeded: bool = False,
    ) -> tuple[ExternalNoteInterpretation, ...]:
        if self._interpretation is None:
            return ()
        interpretation_service = self._interpretation
        candidates: list[ExternalNoteRevision] = []
        preserve_success_for: set[str] = set()
        for _identity, latest_revision in self._repository.list_latest(500):
            revision = self._effective_revision(latest_revision)
            if revision.coverage is not NoteCoverage.FULL:
                continue
            existing = self._repository.interpretation_for_revision(revision.note_revision_id)
            if (
                existing is None
                or (retry_failed and existing.status == "FAILED")
                or (reanalyze_succeeded and existing.status == "SUCCEEDED")
            ):
                candidates.append(revision)
                if existing is not None and existing.status == "SUCCEEDED":
                    preserve_success_for.add(revision.note_revision_id)
            if len(candidates) >= max(1, min(limit, 100)):
                break
        if not candidates:
            return ()
        semaphore = asyncio.Semaphore(self._analysis_concurrency)

        async def analyze_one(revision: ExternalNoteRevision) -> ExternalNoteInterpretation:
            previous_revision = self._repository.previous_revision(
                revision.note_id, revision.version
            )
            previous_interpretation = (
                self._repository.interpretation_for_revision(
                    previous_revision.note_revision_id
                )
                if previous_revision is not None
                else None
            )
            async with semaphore:
                return await interpretation_service.analyze(
                    revision,
                    (
                        previous_interpretation.payload_json
                        if previous_interpretation is not None
                        and previous_interpretation.status == "SUCCEEDED"
                        else None
                    ),
                )

        results = tuple(await asyncio.gather(*(analyze_one(item) for item in candidates)))
        for value in results:
            if value.status == "FAILED" and value.note_revision_id in preserve_success_for:
                continue
            self._repository.append_interpretation(value)
            revision = next(
                item for item in candidates if item.note_revision_id == value.note_revision_id
            )
            materialized = self._materialize_review_if_eligible(revision, value)
            if materialized:
                await self._run_deep_review(revision.note_revision_id)
        return results

    async def analyze_revision(
        self,
        note_revision_id: str,
        *,
        retry_failed: bool = True,
    ) -> ExternalNoteInterpretation:
        if self._interpretation is None:
            raise DataContractError("observation interpretation is unavailable")
        revision = self._repository.revision_by_id(note_revision_id)
        if revision is None:
            raise DataContractError("observation revision was not found")
        effective = self._effective_revision(revision)
        if effective.coverage is not NoteCoverage.FULL:
            raise DataContractError("full observation text is required for interpretation")
        existing = self._repository.interpretation_for_revision(effective.note_revision_id)
        if existing is not None and (existing.status == "SUCCEEDED" or not retry_failed):
            materialized = self._materialize_review_if_eligible(effective, existing)
            if materialized:
                await self._run_deep_review(effective.note_revision_id)
            return existing
        previous_revision = self._repository.previous_revision(
            effective.note_id, effective.version
        )
        previous_interpretation = (
            self._repository.interpretation_for_revision(
                previous_revision.note_revision_id
            )
            if previous_revision is not None
            else None
        )
        result = await self._interpretation.analyze(
            effective,
            (
                previous_interpretation.payload_json
                if previous_interpretation is not None
                and previous_interpretation.status == "SUCCEEDED"
                else None
            ),
        )
        self._repository.append_interpretation(result)
        materialized = self._materialize_review_if_eligible(effective, result)
        if materialized:
            await self._run_deep_review(effective.note_revision_id)
        return result

    async def _run_deep_review(self, note_revision_id: str) -> None:
        if self._deep_reviewer is None:
            return
        try:
            await self._deep_reviewer.review(note_revision_id)
        except Exception:  # noqa: BLE001 - first-pass Observation remains usable
            return

    def _materialize_review_if_eligible(
        self,
        revision: ExternalNoteRevision,
        interpretation: ExternalNoteInterpretation,
    ) -> bool:
        if self._review_materializer is None or interpretation.status != "SUCCEEDED":
            return False
        previous = self._repository.previous_revision(revision.note_id, revision.version)
        previous_effective = self._effective_revision(previous) if previous is not None else None

        def user_blocks(value: ExternalNoteRevision | None) -> tuple[tuple[str | None, str], ...]:
            if value is None:
                return ()
            return tuple(
                (item.section_date, " ".join(item.body.split()))
                for item in value.blocks
                if item.speaker_kind is NoteSpeakerKind.USER
            )

        current_user = user_blocks(revision)
        previous_user = user_blocks(previous_effective)
        if previous is not None and current_user == previous_user:
            return False
        if not current_user and not previous_user:
            return False
        self._review_materializer.ensure_pending(
            note_revision_id=revision.note_revision_id
        )
        return True

    def interpretation_for_revision(
        self, note_revision_id: str
    ) -> ExternalNoteInterpretation | None:
        return self._repository.interpretation_for_revision(note_revision_id)

    def history(
        self, note_id: str, limit: int = 50
    ) -> tuple[ExternalNoteHistoryItem, ...]:
        result: list[ExternalNoteHistoryItem] = []
        for revision in self._repository.list_revisions(note_id, limit):
            effective = self._effective_revision(revision)
            result.append(
                ExternalNoteHistoryItem(
                    revision=effective,
                    interpretation=(
                        self._repository.interpretation_for_revision(
                            effective.note_revision_id
                        )
                        if effective.coverage is NoteCoverage.FULL
                        else None
                    ),
                )
            )
        return tuple(result)


def _content_hash(value: ExternalNoteSourceSnapshot) -> str:
    payload = json.dumps(
        {
            "title": value.title,
            "summary": value.summary,
            "full_body": value.full_body,
            "coverage": value.coverage.value,
            "visibility": value.visibility,
            "related_stock_ids": value.related_provider_stock_ids,
            "related_codes": value.related_provider_codes,
            "blocks": [
                {
                    "ordinal": item.ordinal,
                    "speaker_kind": item.speaker_kind.value,
                    "speaker_label": item.speaker_label,
                    "body": item.body,
                    "section_date": item.section_date,
                }
                for item in value.blocks
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _source_revision_key(value: ExternalNoteSourceSnapshot, content_hash: str) -> str:
    source_time = _snapshot_time(value).isoformat()
    payload = f"{value.source}\0{value.external_id}\0{source_time}\0{content_hash}"
    return "source:" + hashlib.sha256(payload.encode()).hexdigest()


def _snapshot_time(value: ExternalNoteSourceSnapshot) -> datetime:
    return value.source_timestamp or value.observed_at


def _revision_time(value: ExternalNoteRevision) -> datetime:
    return value.source_timestamp or value.observed_at


def _same_visible_content(
    latest: ExternalNoteRevision,
    snapshot: ExternalNoteSourceSnapshot,
    content_hash: str,
) -> bool:
    if latest.content_sha256 == content_hash:
        return True
    return (
        snapshot.coverage is NoteCoverage.SUMMARY_ONLY
        and latest.coverage is NoteCoverage.FULL
        and latest.title == snapshot.title
        and latest.summary == snapshot.summary
        and latest.related_provider_stock_ids == snapshot.related_provider_stock_ids
        and latest.related_provider_codes == snapshot.related_provider_codes
    )


__all__ = [
    "ExternalNoteInboxItem",
    "ExternalNoteHistoryItem",
    "ExternalNoteSyncService",
    "ExternalObservationCaptureRequest",
]
