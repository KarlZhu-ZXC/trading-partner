from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from application.dto.challenge import (
    ChallengeReviewDTO,
    ChallengeReviewResolveInput,
    ChallengeReviewStartDTO,
    ChallengeReviewStartInput,
)
from application.dto.tool_envelope import ToolEnvelope
from application.services.challenge_review_service import ChallengeReviewService
from application.services.idempotency import canonical_payload_sha256
from domain.challenge.enums import (
    ChallengeDimension,
    ChallengeFindingSeverity,
    ChallengeResolution,
    ChallengeReviewStatus,
    ChallengeTrigger,
)
from domain.challenge.models import ChallengeFinding, ChallengeQuestion, ChallengeReview
from domain.common.enums import ConfirmationMode, EvidenceStance, Freshness
from domain.common.errors import ChallengeReviewAlreadyResolved, DataContractError
from infrastructure.persistence.challenge_review_repository import (
    SqlAlchemyChallengeReviewRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.mcp.server import PUBLIC_TOOL_NAMES, create_mcp_server

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
REVIEW_ID = "run_00000000-0000-7000-8000-000000000001"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def __init__(self) -> None:
        self.index = 0

    def new(self, prefix: object) -> str:
        self.index += 1
        value = getattr(prefix, "value", str(prefix))
        return f"{value}_{self.index}"


def _questions() -> tuple[ChallengeQuestion, ...]:
    return tuple(
        ChallengeQuestion(
            question_id=f"rev_question-{index}",
            review_id=REVIEW_ID,
            dimension=dimension,
            prompt=f"Challenge {dimension.value}?",
            ordinal=index,
        )
        for index, dimension in enumerate(ChallengeDimension, 1)
    )


def _review() -> ChallengeReview:
    finding = ChallengeFinding(
        finding_id="rev_finding-1",
        review_id=REVIEW_ID,
        dimension=ChallengeDimension.CONTRARY_EVIDENCE,
        severity=ChallengeFindingSeverity.WARNING,
        summary="No contrary evidence is linked.",
    )
    return ChallengeReview(
        review_id=REVIEW_ID,
        case_id="case_00000000-0000-7000-8000-000000000001",
        mode=ConfirmationMode.STRICT_REVIEW,
        trigger=ChallengeTrigger.CONFIDENCE_INCREASE,
        proposed_action="Raise confidence",
        related_candidate_id=None,
        related_evidence_ids=(),
        position_context_snapshot_id=None,
        context_as_of=NOW,
        status=ChallengeReviewStatus.OPEN,
        questions=_questions(),
        findings=(finding,),
        created_at=NOW,
    )


def test_persistent_review_requires_material_trigger_and_ten_questions() -> None:
    review = _review()
    assert len(review.questions) == 10
    with pytest.raises(DataContractError, match="material trigger"):
        ChallengeReview(
            review_id=review.review_id,
            case_id=review.case_id,
            mode=review.mode,
            trigger=ChallengeTrigger.DISCUSSION,
            proposed_action=review.proposed_action,
            related_candidate_id=None,
            related_evidence_ids=(),
            position_context_snapshot_id=None,
            context_as_of=NOW,
            status=ChallengeReviewStatus.OPEN,
            questions=review.questions,
            findings=review.findings,
            created_at=NOW,
        )


def test_repository_resolves_once_and_preserves_children() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyChallengeReviewRepository(engine)
    review = repository.append(
        _review(),
        idempotency_key="challenge-start-1",
        payload_sha256="a" * 64,
    )

    resolved = repository.resolve(
        review.review_id,
        resolution=ChallengeResolution.REJECT,
        rationale="I accept the risk and keep the original judgment.",
        confirmed_by="user",
        resolved_at=NOW,
        resolution_id="rev_resolution-1",
        idempotency_key="challenge-resolution-1",
        payload_sha256="b" * 64,
    )

    assert resolved.status is ChallengeReviewStatus.RESOLVED
    assert resolved.resolution is ChallengeResolution.REJECT
    assert len(resolved.questions) == 10
    assert resolved.execution_effect is False
    replayed = repository.resolve(
        review.review_id,
        resolution=ChallengeResolution.REJECT,
        rationale="I accept the risk and keep the original judgment.",
        confirmed_by="user",
        resolved_at=NOW,
        resolution_id="rev_resolution-replay",
        idempotency_key="challenge-resolution-1",
        payload_sha256="b" * 64,
    )
    assert replayed == resolved
    persisted_start = repository.get_by_start_idempotency_key("challenge-start-1")
    persisted_resolution = repository.get_by_resolution_idempotency_key("challenge-resolution-1")
    assert persisted_start is not None and persisted_start[1] == "a" * 64
    assert persisted_resolution is not None and persisted_resolution[1] == "b" * 64
    with pytest.raises(ChallengeReviewAlreadyResolved):
        repository.resolve(
            review.review_id,
            resolution=ChallengeResolution.DEFER,
            rationale="later",
            confirmed_by="user",
            resolved_at=NOW,
            resolution_id="rev_resolution-2",
            idempotency_key="challenge-resolution-2",
            payload_sha256="c" * 64,
        )
    engine.dispose()


def test_service_bypasses_discussion_and_persists_material_review() -> None:
    repository = MagicMock()
    repository.append.side_effect = lambda review, **_: review
    repository.get_by_start_idempotency_key.return_value = None
    context_builder = MagicMock()
    context_builder.build.return_value = SimpleNamespace(
        ok=True,
        data=SimpleNamespace(
            research_state=SimpleNamespace(invalidations=()),
            evidence=(
                SimpleNamespace(
                    evidence_id="evidence_contrary",
                    stances=(EvidenceStance.CONTRADICTS,),
                ),
            ),
            positions=(),
        ),
        as_of=NOW,
    )
    service = ChallengeReviewService(
        repository,
        context_builder,
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )

    normal = service.start(
        ChallengeReviewStartInput(
            case_id="case_1",
            trigger=ChallengeTrigger.DISCUSSION,
            proposed_action="Explore the thesis",
        )
    )
    strict = service.start(
        ChallengeReviewStartInput(
            case_id="case_1",
            trigger=ChallengeTrigger.CONFIDENCE_WITHOUT_EVIDENCE,
            proposed_action="Raise confidence",
            idempotency_key="challenge-start-1",
        )
    )

    assert normal.data is not None and normal.data.persisted is False
    assert strict.data is not None and strict.data.review is not None
    assert len(strict.data.review.questions) == 10
    assert {item.dimension for item in strict.data.review.findings} == {
        ChallengeDimension.FALSIFIABILITY,
        ChallengeDimension.EVIDENCE_QUALITY,
        ChallengeDimension.PORTFOLIO_BIAS,
    }
    repository.append.assert_called_once()


def test_service_replays_material_start_and_rejects_changed_payload() -> None:
    repository = MagicMock()
    repository.get_by_start_idempotency_key.return_value = None
    repository.append.side_effect = lambda review, **_: review
    context_builder = MagicMock()
    context_builder.build.return_value = SimpleNamespace(
        ok=True,
        data=SimpleNamespace(
            research_state=SimpleNamespace(invalidations=()),
            evidence=(),
            positions=(),
        ),
        as_of=NOW,
    )
    service = ChallengeReviewService(
        repository,
        context_builder,
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )
    request = ChallengeReviewStartInput(
        case_id="case_1",
        trigger=ChallengeTrigger.CONFIDENCE_INCREASE,
        proposed_action="Raise confidence",
        idempotency_key="challenge-start-replay",
    )
    first = service.start(request)
    assert first.ok and first.data is not None and first.data.review is not None
    persisted = repository.append.call_args.args[0]
    payload_sha256 = canonical_payload_sha256(
        request.model_dump(mode="json", exclude={"idempotency_key"})
    )
    repository.get_by_start_idempotency_key.return_value = (
        persisted,
        payload_sha256,
    )

    replay = service.start(request)
    conflict = service.start(
        request.model_copy(update={"proposed_action": "Raise confidence further"})
    )

    assert replay.ok and replay.data is not None
    assert replay.data.review == first.data.review
    assert not conflict.ok
    assert conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"
    context_builder.build.assert_called_once()
    repository.append.assert_called_once()


def test_service_replays_resolution_without_second_write() -> None:
    repository = MagicMock()
    request = ChallengeReviewResolveInput(
        review_id=REVIEW_ID,
        resolution=ChallengeResolution.DEFER,
        rationale="Need more evidence",
        confirmed_by="user",
        idempotency_key="challenge-resolution-replay",
    )
    resolved = replace(
        _review(),
        status=ChallengeReviewStatus.RESOLVED,
        resolution=ChallengeResolution.DEFER,
        resolution_rationale=request.rationale,
        resolved_at=NOW,
        confirmed_by="user",
    )
    payload_sha256 = canonical_payload_sha256(
        request.model_dump(mode="json", exclude={"idempotency_key"})
    )
    repository.get_by_resolution_idempotency_key.return_value = (
        resolved,
        payload_sha256,
    )
    service = ChallengeReviewService(
        repository,
        MagicMock(),
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )

    replay = service.resolve(request)

    assert replay.ok and replay.data is not None
    assert replay.data.status is ChallengeReviewStatus.RESOLVED
    repository.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_challenge_mcp_delegates_compact_read_and_manage_tools() -> None:
    container = MagicMock()
    container.settings.mcp_server_name = "phase1k-test"
    review = _review()
    start_envelope = ToolEnvelope.success(
        request_id="req_start",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=ChallengeReviewStartDTO(
            mode=ConfirmationMode.STRICT_REVIEW,
            persisted=True,
            review=ChallengeReviewDTO.from_domain(review),
        ),
    )
    container.challenge_review_service.start.return_value = start_envelope
    container.challenge_review_service.get.return_value = start_envelope.model_copy(
        update={"request_id": "req_get", "data": review}
    )
    container.challenge_review_service.resolve.return_value = start_envelope.model_copy(
        update={"request_id": "req_resolve", "data": review}
    )
    manager = create_mcp_server(container)._tool_manager

    assert {tool.name for tool in manager.list_tools()} == set(PUBLIC_TOOL_NAMES)
    started = await manager.call_tool(
        "challenge_review_manage",
        {
            "request": {
                "operation": "start",
                "case_id": "case_1",
                "trigger": "confidence_increase",
                "proposed_action": "Raise confidence",
                "idempotency_key": "challenge-start-1",
            }
        },
    )
    await manager.call_tool("challenge_review_get", {"review_id": REVIEW_ID})
    await manager.call_tool(
        "challenge_review_manage",
        {
            "request": {
                "operation": "resolve",
                "review_id": REVIEW_ID,
                "resolution": "defer",
                "rationale": "Need more primary evidence",
                "confirmed_by": "user",
                "idempotency_key": "challenge-resolution-1",
            }
        },
    )
    assert started["request_id"] == "req_start"
    assert isinstance(
        container.challenge_review_service.resolve.call_args.args[0],
        ChallengeReviewResolveInput,
    )
