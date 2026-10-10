"""Focused application-rule tests for evidence impact candidates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from application.dto.research_links import EvidenceImpactCandidatePayload, ResearchSourcePayload
from application.services.research_impact_rules import apply_impact, validate_impact
from domain.common.errors import DataContractError

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000001"
OTHER_SUBJECT_ID = "case_00000000-0000-7000-8000-000000000002"
THESIS_ID = "thesis_00000000-0000-7000-8000-000000000001"
REVISION_ID = "rev_00000000-0000-7000-8000-000000000001"
REVISION_ID_2 = "rev_00000000-0000-7000-8000-000000000002"
ASSUMPTION_ID = "rev_00000000-0000-7000-8000-000000000011"
SOURCE_ID = "evidence_00000000-0000-7000-8000-000000000001"
IMPACT_ID = "impact_00000000-0000-7000-8000-000000000001"
RUN_ID = "run_00000000-0000-7000-8000-000000000001"


def _source(source_id: str = SOURCE_ID) -> ResearchSourcePayload:
    return ResearchSourcePayload(kind="EVIDENCE", source_id=source_id)


class _Repo:
    def __init__(self, value: object | None = None) -> None:
        self.value = value
        self.calls: list[tuple[object, ...]] = []

    def get(self, *args: object, **kwargs: object) -> object | None:
        self.calls.append((*args, *kwargs.values()))
        return self.value


class _ImpactRepo(_Repo):
    def append(self, value: object, expected_version: int | None) -> None:
        self.calls.append((value, expected_version))


class _Ids:
    def new(self, _prefix: object) -> str:
        return IMPACT_ID


def _uow(
    *,
    subject_id: str = SUBJECT_ID,
    revision_id: str = REVISION_ID,
    current_revision_id: str = REVISION_ID,
    source: object | None = object(),
    impact: object | None = None,
) -> SimpleNamespace:
    revision = SimpleNamespace(
        revision_id=revision_id,
        thesis_id=THESIS_ID,
        subject_id=subject_id,
        revision_no=1,
        confirmed_at=NOW - timedelta(days=1),
    )
    thesis = SimpleNamespace(
        thesis_id=THESIS_ID,
        subject_id=subject_id,
        current_revision_no=1,
        latest_revision_id=current_revision_id,
    )
    assumption = SimpleNamespace(
        assumption_id=ASSUMPTION_ID,
        thesis_id=THESIS_ID,
        subject_id=subject_id,
        revision_no=1,
        status="accepted",
    )
    return SimpleNamespace(
        subjects=_Repo(SimpleNamespace(subject_id=subject_id)),
        revisions=_Repo(revision),
        theses=_Repo(thesis),
        assumptions=_Repo(assumption),
        research_sources=_Repo(source),
        evidence_impacts=_ImpactRepo(impact),
    )


def _payload(**overrides: object) -> EvidenceImpactCandidatePayload:
    values: dict[str, object] = {
        "action": "create",
        "thesis_revision_id": REVISION_ID,
        "assumption_id": ASSUMPTION_ID,
        "source": _source(),
        "stance": "supports",
        "rationale": "The source supports the active assumption.",
    }
    values.update(overrides)
    return EvidenceImpactCandidatePayload(**values)


def test_create_validates_current_revision_and_appends_active_version() -> None:
    uow = _uow()
    candidate = SimpleNamespace(subject_id=SUBJECT_ID, candidate_id=RUN_ID)

    kind, impact_id = apply_impact(uow, candidate, _payload(), "user", NOW, _Ids())

    assert (kind, impact_id) == ("evidence_impact", IMPACT_ID)
    value, expected_version = uow.evidence_impacts.calls[-1]
    assert expected_version is None
    assert value.version == 1
    assert value.state == "ACTIVE"
    assert value.subject_id == SUBJECT_ID
    assert value.thesis_revision_id == REVISION_ID
    assert value.source.source_id == SOURCE_ID


def test_stale_revision_is_rejected_without_writing_an_impact() -> None:
    uow = _uow(current_revision_id=REVISION_ID_2)

    with pytest.raises(DataContractError, match="current Thesis revision"):
        validate_impact(uow, SUBJECT_ID, _payload(), NOW)

    assert uow.evidence_impacts.calls == []


def test_cross_subject_revision_is_rejected() -> None:
    uow = _uow(subject_id=OTHER_SUBJECT_ID)
    with pytest.raises(DataContractError):
        validate_impact(uow, SUBJECT_ID, _payload(), NOW)


def test_summary_only_source_is_rejected() -> None:
    uow = _uow(source=None)
    with pytest.raises(DataContractError, match="source reference"):
        validate_impact(uow, SUBJECT_ID, _payload(), NOW)


def test_revise_requires_exact_current_version() -> None:
    impact = SimpleNamespace(
        impact_id=IMPACT_ID,
        version=3,
        subject_id=SUBJECT_ID,
        thesis_id=THESIS_ID,
        thesis_revision_id=REVISION_ID,
        assumption_id=ASSUMPTION_ID,
        source=_source().to_domain(),
        stance="supports",
        rationale="prior rationale",
        state="ACTIVE",
    )
    uow = _uow(impact=impact)

    with pytest.raises(DataContractError, match="expected_version"):
        validate_impact(
            uow,
            SUBJECT_ID,
            _payload(action="revise", impact_id=IMPACT_ID, expected_version=2),
            NOW,
        )


def test_retract_preserves_target_source_and_records_reason_when_source_is_unavailable() -> None:
    source = _source().to_domain()
    impact = SimpleNamespace(
        impact_id=IMPACT_ID,
        version=3,
        subject_id=SUBJECT_ID,
        thesis_id=THESIS_ID,
        thesis_revision_id=REVISION_ID,
        assumption_id=ASSUMPTION_ID,
        source=source,
        stance="contradicts",
        rationale="prior rationale",
        state="ACTIVE",
    )
    uow = _uow(impact=impact, source=None)
    payload = EvidenceImpactCandidatePayload(
        action="retract",
        impact_id=IMPACT_ID,
        expected_version=3,
        rationale="The user withdrew this interpretation",
    )

    apply_impact(
        uow,
        SimpleNamespace(subject_id=SUBJECT_ID, candidate_id=RUN_ID),
        payload,
        "external_agent",
        NOW,
        _Ids(),
    )

    value, expected_version = uow.evidence_impacts.calls[-1]
    assert expected_version == 3
    assert value.version == 4
    assert value.state == "RETRACTED"
    assert value.source == source
    assert value.rationale == "The user withdrew this interpretation"
    assert value.thesis_revision_id == REVISION_ID
