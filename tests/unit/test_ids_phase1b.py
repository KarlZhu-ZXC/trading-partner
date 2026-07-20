"""Phase 1B entity ID conventions (case/thesis/rev/run/audit)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from domain.common.enums import CandidateKind, CandidateStatus, ConfirmationMode
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix, format_entity_id
from domain.research.models import CandidateThesisRevision
from infrastructure.system.id_generator import Uuid7IdGenerator

_UUID7_TOKEN = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def test_format_entity_id_phase1b_prefixes() -> None:
    gen = Uuid7IdGenerator()
    for prefix in (
        EntityIdPrefix.CASE,
        EntityIdPrefix.THESIS,
        EntityIdPrefix.REV,
        EntityIdPrefix.RUN,
        EntityIdPrefix.AUDIT,
        EntityIdPrefix.SNAPSHOT,
    ):
        eid = gen.new(prefix)
        assert eid.startswith(f"{prefix.value}_")
        token = eid.split("_", 1)[1]
        assert re.fullmatch(_UUID7_TOKEN, token), eid


def test_candidate_id_must_match_run_prefix_regex() -> None:
    gen = Uuid7IdGenerator()
    candidate_id = gen.new(EntityIdPrefix.RUN)
    pattern = rf"^{EntityIdPrefix.RUN.value}_{_UUID7_TOKEN}$"
    assert re.fullmatch(pattern, candidate_id)
    # format_entity_id also produces RUN-prefixed ids
    manual = format_entity_id(EntityIdPrefix.RUN, "00000000-0000-7000-8000-000000000042")
    assert manual == "run_00000000-0000-7000-8000-000000000042"
    assert re.fullmatch(pattern, manual)

    now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(DataContractError, match="run_<uuid7>"):
        CandidateThesisRevision(
            candidate_id="run_x",
            case_id="case_00000000-0000-7000-8000-000000000001",
            thesis_id=None,
            target_revision_no=None,
            payload_json="{}",
            kind=CandidateKind.THESIS_REVISION,
            confirmation_mode=ConfirmationMode.NORMAL,
            status=CandidateStatus.PROPOSED,
            proposed_at=now,
            expires_at=now + timedelta(days=1),
            proposed_by="codex",
            proposed_by_rationale="x",
            reviewed_at=None,
            reviewed_by=None,
            review_note=None,
            rejection_reason=None,
            idempotency_key="idem-run-x",
        )
