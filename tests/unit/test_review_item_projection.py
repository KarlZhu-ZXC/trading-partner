from copy import deepcopy

from application.services.review_item_projection import observe_workflow_reviews
from domain.review_item.enums import ReviewItemSourceType


def test_failed_source_payload_never_becomes_authoritative() -> None:
    failed = {
        "ok": False,
        "data": {"runs": [{"run_id": "retro_stale", "findings": [{}]}]},
    }
    before = deepcopy(failed)
    result = observe_workflow_reviews(agenda={}, retro=failed, scorecards={})
    assert result.projections == ()
    assert result.observed_source_types == frozenset()
    assert result.authoritative_source_refs == frozenset()
    assert failed == before


def test_successful_page_only_observes_present_source_objects() -> None:
    retro = {
        "ok": True,
        "data": {
            "runs": [
                {"run_id": "retro_present", "subject_ids": ["case_a", "case_b"]}
            ],
            "has_more": True,
        },
    }
    before = deepcopy(retro)
    result = observe_workflow_reviews(
        agenda={"ok": True, "data": {"items": []}}, retro=retro, scorecards={}
    )
    assert result.observed_source_types == frozenset(
        {ReviewItemSourceType.CATALYST_AGENDA, ReviewItemSourceType.TRADE_RETRO}
    )
    assert result.authoritative_source_refs == frozenset(
        {(ReviewItemSourceType.TRADE_RETRO, "retro_present")}
    )
    assert {item.subject_id for item in result.projections} == {"case_a", "case_b"}
    assert len({item.source_key for item in result.projections}) == 2
    assert all(item.href == "/retro#retro-retro_present" for item in result.projections)
    assert retro == before
