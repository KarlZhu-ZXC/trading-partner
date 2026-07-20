from __future__ import annotations

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
from interfaces.mcp.server import (
    PHASE1K_CHALLENGE_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

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
    review = repository.append(_review())

    resolved = repository.resolve(
        review.review_id,
        resolution=ChallengeResolution.REJECT,
        rationale="I accept the risk and keep the original judgment.",
        confirmed_by="user",
        resolved_at=NOW,
    )

    assert resolved.status is ChallengeReviewStatus.RESOLVED
    assert resolved.resolution is ChallengeResolution.REJECT
    assert len(resolved.questions) == 10
    assert resolved.execution_effect is False
    with pytest.raises(ChallengeReviewAlreadyResolved):
        repository.resolve(
            review.review_id,
            resolution=ChallengeResolution.DEFER,
            rationale="later",
            confirmed_by="user",
            resolved_at=NOW,
        )
    engine.dispose()


def test_service_bypasses_discussion_and_persists_material_review() -> None:
    repository = MagicMock()
    repository.append.side_effect = lambda review: review
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


@pytest.mark.asyncio
async def test_challenge_mcp_delegates_three_tools_in_exact_inventory() -> None:
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

    assert {
        "challenge_review_start",
        "challenge_review_get",
        "challenge_review_resolve",
    } == PHASE1K_CHALLENGE_TOOL_NAMES
    assert {tool.name for tool in manager.list_tools()} == set(PUBLIC_TOOL_NAMES)
    assert len(PUBLIC_TOOL_NAMES) == 52
    started = await manager.call_tool(
        "challenge_review_start",
        {
            "case_id": "case_1",
            "trigger": "confidence_increase",
            "proposed_action": "Raise confidence",
        },
    )
    await manager.call_tool("challenge_review_get", {"review_id": REVIEW_ID})
    await manager.call_tool(
        "challenge_review_resolve",
        {
            "review_id": REVIEW_ID,
            "resolution": "defer",
            "rationale": "Need more primary evidence",
            "confirmed_by": "user",
        },
    )
    assert started["request_id"] == "req_start"
    assert isinstance(
        container.challenge_review_service.resolve.call_args.args[0],
        ChallengeReviewResolveInput,
    )
