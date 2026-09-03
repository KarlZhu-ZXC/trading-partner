"""Build provider-neutral observation ingestion and draft analysis."""

from __future__ import annotations

from dataclasses import dataclass

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.agent_model_provider import AgentModelProvider
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.id_generator import IdGenerator
from application.ports.monitor_repository import MonitorRepository
from application.services._research_support import UowFactory
from application.services.external_note_interpretation_service import (
    ExternalNoteInterpretationService,
)
from application.services.external_note_review_draft_service import (
    ExternalNoteReviewDraftService,
)
from application.services.external_note_review_service import ExternalNoteReviewService
from application.services.external_note_sync_service import ExternalNoteSyncService
from application.services.view_review_service import ViewReviewService
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
    reviews: ExternalNoteReviewService
    review_drafts: ExternalNoteReviewDraftService
    view_reviews: ViewReviewService
    analysis_provider: AgentModelProvider | None
    review_provider: AgentModelProvider | None


def build_external_note_services(
    *,
    settings: AppSettings,
    repository: ExternalNoteRepository,
    review_repository: ExternalNoteReviewRepository,
    research_uow_factory: UowFactory,
    account_snapshots: AccountSnapshotRepository,
    monitors: MonitorRepository,
    clock: Clock,
    id_generator: IdGenerator,
    resilience: LLMResilienceController,
) -> ExternalNotesBundle:
    config = settings.resolved_external_note_analysis_config
    review_config = settings.resolved_external_note_review_config
    provider: AgentModelProvider | None = None
    review_provider: AgentModelProvider | None = None
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
    if review_config is not None:
        review_provider = RoutedAgentModelProvider(
            OpenCodeGoModelProvider(
                review_config,
                proxy_url=settings.provider_proxy_url,
            ),
            vendor=VendorId.OPENCODE_GO,
            resilience=resilience,
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
    review_service = ExternalNoteReviewService(
        review_repository,
        repository,
        research_uow_factory,
        clock,
        id_generator,
    )
    view_review_service = ViewReviewService(
        repository,
        review_repository,
        research_uow_factory,
        account_snapshots,
        monitors,
    )
    review_draft_service = ExternalNoteReviewDraftService(
        review_provider,
        provider_name="opencode_go",
        model=(review_config.model if review_config is not None else "qwen3.8-max"),
        reviews=review_repository,
        notes=repository,
        view_reviews=view_review_service,
        clock=clock,
        id_generator=id_generator,
        timeout_seconds=(review_config.timeout_seconds if review_config is not None else 120),
        max_output_tokens=(review_config.max_output_tokens if review_config is not None else 5000),
        reasoning_effort=(
            review_config.reasoning_effort
            if review_config is not None and review_config.reasoning_effort is not None
            else "max"
        ),
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
            review_materializer=(
                review_service if settings.observation_review_workflow_enabled else None
            ),
            deep_reviewer=(
                review_draft_service
                if settings.observation_review_workflow_enabled
                else None
            ),
        ),
        reviews=review_service,
        review_drafts=review_draft_service,
        view_reviews=view_review_service,
        analysis_provider=provider,
        review_provider=review_provider,
    )


__all__ = ["ExternalNotesBundle", "build_external_note_services"]
