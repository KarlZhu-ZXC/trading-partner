"""Focused tests for the bounded research composition graph."""

from __future__ import annotations

from unittest.mock import MagicMock

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.challenge_review_repository import ChallengeReviewRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import UowFactory
from application.services.challenge_review_service import ChallengeReviewService
from application.services.research_context_builder import ResearchContextBuilder
from application.services.research_subject_service import ResearchSubjectService
from composition_root.research import ResearchServices, build_research_services


def test_build_research_services_shares_runtime_dependencies() -> None:
    uow_factory = MagicMock(spec=UowFactory)
    account_snapshots = MagicMock(spec=AccountSnapshotRepository)
    challenge_reviews = MagicMock(spec=ChallengeReviewRepository)
    clock = MagicMock(spec=Clock)
    id_generator = MagicMock(spec=IdGenerator)
    secret_redactor = MagicMock(spec=SecretRedactor)

    services = build_research_services(
        uow_factory=uow_factory,
        account_snapshot_repository=account_snapshots,
        challenge_review_repository=challenge_reviews,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
    )

    assert isinstance(services, ResearchServices)
    assert isinstance(services.research_subjects, ResearchSubjectService)
    assert isinstance(services.research_context, ResearchContextBuilder)
    assert isinstance(services.challenge, ChallengeReviewService)
    assert services.research_subjects._uow_factory is uow_factory  # type: ignore[attr-defined]
    assert services.research_subjects._clock is clock  # type: ignore[attr-defined]
    assert services.research_context._uow_factory is uow_factory  # type: ignore[attr-defined]
    assert services.research_context._accounts is account_snapshots  # type: ignore[attr-defined]
    assert services.challenge._repository is challenge_reviews  # type: ignore[attr-defined]
    assert services.challenge._context is services.research_context  # type: ignore[attr-defined]
