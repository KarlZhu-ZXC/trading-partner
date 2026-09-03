"""Build provider-neutral observation ingestion and draft analysis."""

from __future__ import annotations

from dataclasses import dataclass

from application.ports.agent_model_provider import AgentModelProvider
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.id_generator import IdGenerator
from application.services.external_note_interpretation_service import (
    ExternalNoteInterpretationService,
)
from application.services.external_note_sync_service import ExternalNoteSyncService
from domain.common.enums import VendorId
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.observation_capture_store import (
    OwnerOnlyObservationCaptureStore,
)
from infrastructure.providers.llm import OpenCodeGoModelProvider
from infrastructure.providers.llm.routed import (
    LLMResilienceController,
    RoutedAgentModelProvider,
)
from infrastructure.providers.local_observations import LocalObservationInboxProvider
from infrastructure.providers.moomoo_notes import (
    MoomooNotesCacheProvider,
    MoomooNotesRemoteClient,
    OwnerOnlyMoomooNoteCredentialStore,
)
from infrastructure.system.process_file_lock import ProcessFileLock


@dataclass(frozen=True, slots=True)
class ExternalNotesBundle:
    service: ExternalNoteSyncService
    analysis_provider: AgentModelProvider | None


def build_external_note_services(
    *,
    settings: AppSettings,
    repository: ExternalNoteRepository,
    clock: Clock,
    id_generator: IdGenerator,
    resilience: LLMResilienceController,
) -> ExternalNotesBundle:
    config = settings.resolved_external_note_analysis_config
    provider: AgentModelProvider | None = None
    interpretation: ExternalNoteInterpretationService | None = None
    if config is not None:
        provider = RoutedAgentModelProvider(
            OpenCodeGoModelProvider(config, proxy_url=settings.provider_proxy_url),
            vendor=VendorId.OPENCODE_GO,
            resilience=resilience,
        )
        interpretation = ExternalNoteInterpretationService(
            provider,
            provider_name="opencode_go",
            model=config.model,
            clock=clock,
            id_generator=id_generator,
            timeout_seconds=config.timeout_seconds,
        )
    inbox_dir = settings.observation_inbox_dir
    remote_client = (
        MoomooNotesRemoteClient(
            cookie_file=settings.moomoo_notes_cookie_path,
            delay_min_seconds=settings.moomoo_notes_request_delay_min_seconds,
            delay_max_seconds=settings.moomoo_notes_request_delay_max_seconds,
            timeout_seconds=settings.moomoo_notes_request_timeout_seconds,
            max_stock_ids=settings.moomoo_notes_remote_max_stock_ids,
            max_notes=settings.moomoo_notes_remote_max_notes,
            proxy_url=settings.provider_proxy_url,
        )
        if settings.moomoo_notes_remote_enabled
        else None
    )
    credential_store = (
        OwnerOnlyMoomooNoteCredentialStore(settings.moomoo_notes_cookie_path)
        if settings.moomoo_notes_remote_enabled
        else None
    )
    return ExternalNotesBundle(
        service=ExternalNoteSyncService(
            (
                MoomooNotesCacheProvider.default(clock, remote_client=remote_client),
                LocalObservationInboxProvider(inbox_dir, clock),
            ),
            repository,
            clock,
            id_generator,
            interpretation,
            capture_store=OwnerOnlyObservationCaptureStore(inbox_dir),
            process_lock=ProcessFileLock(
                settings.post_market_sync_lock_path.parent / "observations.lock"
            ),
            credential_stores=(credential_store,) if credential_store is not None else (),
        ),
        analysis_provider=provider,
    )


__all__ = ["ExternalNotesBundle", "build_external_note_services"]
