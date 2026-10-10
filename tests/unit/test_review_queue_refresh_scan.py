"""Focused contracts for the explicit two-pass Review Queue refresh."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from application.services.review_item_refresh_service import (
    ReviewItemRefreshService,
    _PageResult,
    _scan_typed_source,
)
from application.services.review_workspace_query_service import ReviewWorkspaceQueryService
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Envelope:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


def _page(values: list[Any], key: str, *, offset: int, limit: int) -> _Envelope:
    rows = values[offset : offset + limit]
    return _Envelope(
        {
            "ok": True,
            "data": {
                key: rows,
                "has_more": offset + len(rows) < len(values),
            },
        }
    )


class _Decisions:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.calls: list[dict[str, Any]] = []

    def list_review_due(self, **kwargs: Any) -> tuple[Any, ...]:
        self.calls.append(kwargs)
        offset = int(kwargs.get("offset", 0))
        return tuple(self.values[offset : offset + int(kwargs["limit"])])


class _Questions:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def list_due(self, **kwargs: Any) -> tuple[Any, ...]:
        offset = int(kwargs.get("offset", 0))
        return tuple(self.values[offset : offset + int(kwargs["limit"])])


class _Uow:
    def __init__(self, values: list[Any]) -> None:
        self.questions = _Questions(values)

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Observations:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def pending_projections(self, **kwargs: Any) -> tuple[Any, ...]:
        offset = int(kwargs.get("offset", 0))
        return tuple(self.values[offset : offset + int(kwargs["limit"])])


class _Actions:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def list_unresolved(self, **kwargs: Any) -> tuple[Any, ...]:
        offset = int(kwargs.get("offset", 0))
        return tuple(self.values[offset : offset + int(kwargs["limit"])])


class _Brokers:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def list_unresolved(self, **kwargs: Any) -> tuple[Any, ...]:
        offset = int(kwargs.get("offset", 0))
        return tuple(self.values[offset : offset + int(kwargs["limit"])])


class _Items:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def reconcile(self, projections: Any, **kwargs: Any) -> tuple[Any, ...]:
        self.calls.append((projections, kwargs))
        return ()


class _Workflow:
    def __init__(self, agenda: list[Any], retro: list[Any], scorecards: list[Any]) -> None:
        self.agenda = agenda
        self.retro = retro
        self.scorecards = scorecards

    def query(self, request: Any, *, offset: int = 0) -> _Envelope:
        return _page(self.agenda, "items", offset=offset, limit=request.limit)


class _Retro:
    def __init__(self, values: list[Any], *, fail_second_scan: bool = False) -> None:
        self.values = values
        self.calls = 0
        self.fail_second_scan = fail_second_scan

    def history(self, request: Any, *, offset: int = 0) -> _Envelope:
        self.calls += 1
        if self.fail_second_scan and self.calls == 2:
            raise RuntimeError("source changed")
        return _page(self.values, "runs", offset=offset, limit=request.limit)


class _Scorecards:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def history(self, request: Any) -> _Envelope:
        return _page(self.values, "runs", offset=request.offset, limit=request.limit)


class _Store:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    def save(self, receipt: dict[str, Any]) -> str:
        self.saved.append(receipt)
        return "audit_review_refresh"

    def latest(self) -> dict[str, Any] | None:
        return self.saved[-1] if self.saved else None


def _workspace(*, retro: _Retro | None = None) -> tuple[ReviewWorkspaceQueryService, _Items]:
    decisions = _Decisions(
        [
            SimpleNamespace(
                decision_id=f"decision_{index}",
                subject_id="case_1",
                title="Decision",
                review_due_at=NOW,
                schema_version=1,
            )
            for index in range(101)
        ]
    )
    questions = [
        SimpleNamespace(
            question_id=f"question_{index}",
            subject_id="case_1",
            text="Question",
            due_at=NOW,
        )
        for index in range(101)
    ]
    observation = ReviewItemProjection(
        source_key="obs_1",
        source_type=ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
        source_ref="revision_1",
        subject_id="case_1",
        title="Observation",
        detail="Pending",
        severity=ReviewItemSeverity.ATTENTION,
        recommended_action="REVIEW_OBSERVATION",
        href="/decision-workbench#notes",
    )
    refresh = ReviewItemRefreshService(
        _Clock(),
        decisions,
        _Observations([observation]),
        _Actions([SimpleNamespace(action_id="action_1", status="UNKNOWN")]),
        _Brokers(
            [
                SimpleNamespace(
                    order_intent_id="order_1",
                    status="UNKNOWN",
                    symbol="SGOV",
                    instrument_id="etf:US:SGOV",
                )
            ]
        ),
        (items := _Items()),
        research_uow_factory=lambda: _Uow(questions),
    )
    agenda = _Workflow(
        [
            {
                "agenda_item_id": f"agenda_{index}",
                "title": "Agenda",
                "subject_id": "case_1",
                "limitation_codes": ["AGENDA_EVENT_OUTCOME_UNVERIFIED"],
            }
            for index in range(201)
        ],
        [],
        [],
    )
    retro_service = retro or _Retro(
        [
            {
                "run_id": f"retro_{index}",
                "findings": [],
                "subject_ids": ["case_1"],
                "latest_review": {},
            }
            for index in range(51)
        ]
    )
    scorecards = _Scorecards(
        [
            {
                "scorecard_id": f"scorecard_{index}",
                "thesis_id": f"thesis_{index}",
                "dimensions": [],
            }
            for index in range(51)
        ]
    )
    workspace = ReviewWorkspaceQueryService(
        clock=_Clock(),
        research=SimpleNamespace(),
        agenda=agenda,
        retro=retro_service,
        scorecards=scorecards,
        refresh=refresh,
        review_items=SimpleNamespace(),
        receipt_store=_Store(),
    )
    return workspace, items


def test_explicit_refresh_scans_bounded_pages_twice_and_applies_once() -> None:
    workspace, items = _workspace()

    sources = workspace.collect_for_refresh()

    assert sources.scan_receipt is not None
    assert sources.scan_receipt["complete"] is True
    assert sources.scan_receipt["sources"]["DECISION_REVIEW_DUE"]["row_count"] == 101
    assert sources.scan_receipt["sources"]["DECISION_REVIEW_DUE"]["pages"] == 2
    assert sources.scan_receipt["sources"]["CATALYST_AGENDA"]["row_count"] == 201
    assert sources.scan_receipt["sources"]["TRADE_RETRO"]["pages"] == 2
    assert sources.scan_receipt["sources"]["SCORECARD_GAP"]["pages"] == 2
    assert items.calls == []

    result = workspace.refresh()

    assert result["complete"] is True
    assert len(items.calls) == 1


def test_changed_second_scan_is_partial_and_protects_source_closure() -> None:
    state = {"calls": 0}

    def changing(offset: int, _limit: int) -> tuple[dict[str, str], ...]:
        state["calls"] += 1
        value = "old" if state["calls"] == 1 else "new"
        return (({"decision_id": "decision_1", "version": value}),) if offset == 0 else ()

    _rows, receipt = _scan_typed_source(
        source="DECISION_REVIEW_DUE",
        page_limit=100,
        fetch_page=changing,
        clock=_Clock(),
    )

    assert receipt["status"] == "PARTIAL"
    assert "REVIEW_SOURCE_CHANGED_BETWEEN_SCANS" in receipt["warnings"]


def test_mutable_source_cannot_rewrite_first_pass_fingerprint() -> None:
    row = {"decision_id": "decision_1", "version": 0}

    def read_page(offset: int, limit: int):
        row["version"] += 1
        return (row,)

    _, receipt = _scan_typed_source(
        source="DECISION_REVIEW_DUE", page_limit=100, fetch_page=read_page, clock=_Clock()
    )
    assert receipt["status"] == "PARTIAL"


def test_incomplete_overflow_probe_cannot_certify_source_complete(monkeypatch) -> None:
    import application.services.review_item_refresh_service as module

    monkeypatch.setattr(module, "_REFRESH_MAX_ROWS", 2)

    def read_page(offset: int, limit: int):
        if offset < 2:
            return module._PageResult(rows=({"decision_id": "d1"}, {"decision_id": "d2"}))
        return module._PageResult(rows=(), has_more=True)

    _, receipt = _scan_typed_source(
        source="DECISION_REVIEW_DUE", page_limit=2, fetch_page=read_page, clock=_Clock()
    )
    assert receipt["status"] == "PARTIAL"
    assert "REVIEW_SOURCE_PAGE_GAP" in receipt["warning_codes"]


def test_failed_scan_can_recover_on_next_explicit_refresh() -> None:
    state = {"calls": 0}

    def recovering(_offset: int, _limit: int) -> tuple[dict[str, str], ...]:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("unavailable")
        return (({"decision_id": "decision_1", "version": "1"}),)

    _rows, failed = _scan_typed_source(
        source="DECISION_REVIEW_DUE",
        page_limit=100,
        fetch_page=recovering,
        clock=_Clock(),
    )
    _rows, recovered = _scan_typed_source(
        source="DECISION_REVIEW_DUE",
        page_limit=100,
        fetch_page=lambda _offset, _limit: ({"decision_id": "decision_1", "version": "1"},),
        clock=_Clock(),
    )

    assert failed["status"] == "UNAVAILABLE"
    assert recovered["status"] == "COMPLETE"


def test_failed_page_after_partial_rows_does_not_return_unobserved_rows() -> None:
    def failed_after_one_page(offset: int, _limit: int) -> tuple[dict[str, str], ...]:
        if offset == 0:
            return (({"decision_id": "decision_1", "version": "1"}),)
        raise RuntimeError("source failed")

    rows, receipt = _scan_typed_source(
        source="DECISION_REVIEW_DUE",
        page_limit=1,
        fetch_page=failed_after_one_page,
        clock=_Clock(),
    )

    assert rows == ()
    assert receipt["status"] == "UNAVAILABLE"


def test_degraded_short_workflow_page_is_partial() -> None:
    workspace, _items = _workspace()

    _payload, receipt = workspace._scan_workflow_source(  # type: ignore[attr-defined]
        source="TRADE_RETRO",
        page_limit=50,
        row_key="runs",
        fetch_page=lambda _offset, _limit: {
            "ok": True,
            "degraded": True,
            "data": {"runs": [{"run_id": "retro_1"}], "has_more": False},
        },
    )

    assert receipt["status"] == "PARTIAL"
    assert "REVIEW_SOURCE_DEGRADED" in receipt["warnings"]


def test_has_more_empty_page_is_a_gap() -> None:
    values = iter(
        (
            _PageResult(rows=({"run_id": "retro_1"},), has_more=True),
            _PageResult(rows=(), has_more=True),
        )
    )

    _rows, receipt = _scan_typed_source(
        source="TRADE_RETRO",
        page_limit=50,
        fetch_page=lambda _offset, _limit: next(values),
        clock=_Clock(),
    )

    assert receipt["status"] == "PARTIAL"
    assert "REVIEW_SOURCE_PAGE_GAP" in receipt["warnings"]


def test_workflow_source_second_scan_failure_never_marks_full_observation() -> None:
    workspace, _items = _workspace(retro=_Retro([], fail_second_scan=True))

    sources = workspace.collect_for_refresh()

    assert sources.scan_receipt is not None
    assert sources.scan_receipt["sources"]["TRADE_RETRO"]["status"] == "PARTIAL"
    assert "TRADE_RETRO" not in sources.coverage or sources.coverage["TRADE_RETRO"] == "PARTIAL"
    assert ReviewItemSourceType.TRADE_RETRO not in sources.batch.fully_observed_source_types


def test_missing_receipt_store_keeps_apply_once_but_reports_partial() -> None:
    workspace, items = _workspace()
    workspace._receipt_store = None  # type: ignore[attr-defined]

    result = workspace.refresh()

    assert len(items.calls) == 1
    assert result["complete"] is False
    assert result["scan_receipt"]["status"] == "PARTIAL"
    assert result["scan_receipt"]["persistence"]["status"] == "UNAVAILABLE"
