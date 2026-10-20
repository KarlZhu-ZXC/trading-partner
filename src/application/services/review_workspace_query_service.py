"""Read-only Console review projection; explicit materialization is a separate command.

Uses existing durable application services, never provider or model ports. The
same collected batch can be passed to ReviewItemRefreshService.apply by an
explicit command. Reading it never reconciles, expires, or acknowledges rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from application.dto.catalyst_agenda import AgendaQueryInput
from application.dto.judgment_scorecard import JudgmentScorecardHistoryInput
from application.dto.trade_retro import TradeRetroHistoryInput
from application.ports.clock import Clock
from application.ports.research_attention_reader import ResearchAttentionReader
from application.ports.review_scan_receipt_store import ReviewScanReceiptStore
from application.services.catalyst_agenda_service import CatalystAgendaService
from application.services.judgment_scorecard_service import JudgmentScorecardService
from application.services.review_item_refresh_service import (
    ReviewItemRefreshBatch,
    ReviewItemRefreshScan,
    ReviewItemRefreshService,
    _PageResult,
    _scan_typed_source,
)
from application.services.review_item_service import ReviewItemService
from application.services.trade_retro_service import TradeRetroService
from domain.review_item.enums import ReviewItemSourceType


@dataclass(frozen=True, slots=True)
class ReviewWorkspaceSources:
    agenda: dict[str, Any]
    batch: ReviewItemRefreshBatch
    coverage: dict[str, str]
    scan_receipt: dict[str, Any] | None = None


def _read_source(read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return read()
    except Exception:  # noqa: BLE001 - independent durable source failure, no private error text
        return {"ok": False, "data": None, "errors": [{"code": "REVIEW_SOURCE_UNAVAILABLE"}]}


def _coverage(envelope: dict[str, Any], limit: int) -> str:
    if envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
        return "UNAVAILABLE"
    data = envelope["data"]
    rows = data.get("items", data.get("runs", ()))
    if (
        envelope.get("degraded")
        or envelope.get("_truncated")
        or data.get("has_more")
        or len(rows) >= limit
    ):
        return "PARTIAL"
    return "COMPLETE"


class ReviewWorkspaceQueryService:
    def __init__(
        self,
        *,
        clock: Clock,
        research: ResearchAttentionReader,
        agenda: CatalystAgendaService,
        retro: TradeRetroService,
        scorecards: JudgmentScorecardService,
        refresh: ReviewItemRefreshService,
        review_items: ReviewItemService,
        receipt_store: ReviewScanReceiptStore | None = None,
    ) -> None:
        self._clock = clock
        self._research = research
        self._agenda = agenda
        self._retro = retro
        self._scorecards = scorecards
        self._refresh = refresh
        self._items = review_items
        self._receipt_store = receipt_store

    @staticmethod
    def _workflow_envelope(
        *,
        rows: tuple[Any, ...],
        row_key: str,
    ) -> dict[str, Any]:
        return {"ok": True, "data": {row_key: list(rows)}}

    def _scan_workflow_source(
        self,
        *,
        source: str,
        page_limit: int,
        row_key: str,
        fetch_page: Callable[[int, int], Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Scan one workflow projection twice without constructing DTOs."""

        def rows(offset: int, limit: int) -> _PageResult:
            envelope = fetch_page(offset, limit)
            if not isinstance(envelope, dict) or envelope.get("ok") is not True:
                raise RuntimeError("workflow source unavailable")
            data = envelope.get("data")
            values = data.get(row_key) if isinstance(data, dict) else None
            if not isinstance(values, list):
                raise RuntimeError("workflow source payload invalid")
            warnings: list[str] = []
            if envelope.get("degraded"):
                warnings.append("REVIEW_SOURCE_DEGRADED")
            if envelope.get("_truncated"):
                warnings.append("REVIEW_SOURCE_TRUNCATED")
            has_more = data.get("has_more") if isinstance(data, dict) else None
            if not isinstance(has_more, bool):
                has_more = None
            return _PageResult(
                rows=tuple(values),
                has_more=has_more,
                warning_codes=tuple(warnings),
            )

        selected, receipt = _scan_typed_source(
            source=source,
            page_limit=page_limit,
            fetch_page=rows,
            clock=self._clock,
        )
        if receipt["status"] == "UNAVAILABLE":
            return (
                {
                    "ok": False,
                    "data": None,
                    "errors": [{"code": "REVIEW_SOURCE_UNAVAILABLE"}],
                },
                receipt,
            )
        return self._workflow_envelope(rows=tuple(selected), row_key=row_key), receipt

    def collect_for_refresh(self) -> ReviewWorkspaceSources:
        """Explicitly scan all durable Review Queue sources before applying."""

        cutoff = self._clock.now()

        def agenda_page(offset: int, limit: int) -> dict[str, Any]:
            request = AgendaQueryInput(
                window_days=90,
                limit=limit,
                offset=offset,
                as_of=cutoff,
            )
            return self._agenda.query(request, offset=offset).model_dump(mode="json")

        def retro_page(offset: int, limit: int) -> dict[str, Any]:
            request = TradeRetroHistoryInput(limit=limit)
            return self._retro.history(request, offset=offset).model_dump(mode="json")

        def scorecard_page(offset: int, limit: int) -> dict[str, Any]:
            request = JudgmentScorecardHistoryInput(limit=limit, offset=offset)
            return self._scorecards.history(request).model_dump(mode="json")

        agenda, agenda_receipt = self._scan_workflow_source(
            source="CATALYST_AGENDA",
            page_limit=200,
            row_key="items",
            fetch_page=agenda_page,
        )
        retro, retro_receipt = self._scan_workflow_source(
            source="TRADE_RETRO",
            page_limit=50,
            row_key="runs",
            fetch_page=retro_page,
        )
        scorecards, scorecard_receipt = self._scan_workflow_source(
            source="SCORECARD_GAP",
            page_limit=50,
            row_key="runs",
            fetch_page=scorecard_page,
        )
        direct: ReviewItemRefreshScan = self._refresh.collect_for_refresh(
            agenda,
            retro,
            scorecards,
            cutoff=cutoff,
        )

        receipts = {
            "CATALYST_AGENDA": agenda_receipt,
            "TRADE_RETRO": retro_receipt,
            "SCORECARD_GAP": scorecard_receipt,
            **direct.receipts,
        }
        # Keep only source refs from a workflow page that passed both scans.
        # A changed/partial page may still be displayed, but it cannot close
        # an older ReviewItem during this apply.
        complete_types = {
            ReviewItemSourceType(source)
            for source, receipt in receipts.items()
            if receipt.get("status") == "COMPLETE"
            and source
            in {
                "CATALYST_AGENDA",
                "TRADE_RETRO",
                "SCORECARD_GAP",
            }
        }
        batch = replace(
            direct.batch,
            authoritative_source_refs=frozenset(
                ref
                for ref in direct.batch.authoritative_source_refs
                if ref[0] in complete_types
            ),
            fully_observed_source_types=frozenset(
                source
                for source in direct.batch.fully_observed_source_types
                if source.value not in {"CATALYST_AGENDA", "TRADE_RETRO", "SCORECARD_GAP"}
                or source in complete_types
            ),
            partial_failures=tuple(
                dict.fromkeys(
                    direct.batch.partial_failures
                    + tuple(
                        code
                        for source, receipt in receipts.items()
                        if source in {"CATALYST_AGENDA", "TRADE_RETRO", "SCORECARD_GAP"}
                        and receipt.get("status") != "COMPLETE"
                        for code in receipt.get(
                            "warning_codes", receipt.get("warnings", ())
                        )
                    )
                )
            ),
        )
        started_at = min(
            str(receipt["started_at"])
            for receipt in receipts.values()
            if receipt.get("started_at")
        )
        completed_at = max(
            str(receipt["completed_at"])
            for receipt in receipts.values()
            if receipt.get("completed_at")
        )
        all_complete = all(
            receipt.get("status") == "COMPLETE" for receipt in receipts.values()
        )
        scan_receipt: dict[str, Any] = {
            "status": "COMPLETE" if all_complete else "PARTIAL",
            "complete": all_complete,
            "cutoff_at": cutoff.isoformat(),
            "receipt_window": {
                "started_at": started_at,
                "completed_at": completed_at,
            },
            "sources": receipts,
            "warning_codes": tuple(
                dict.fromkeys(
                    code
                    for receipt in receipts.values()
                    for code in receipt.get("warning_codes", receipt.get("warnings", ()))
                )
            ),
        }
        coverage = {source: str(receipt["status"]) for source, receipt in receipts.items()}
        return ReviewWorkspaceSources(
            agenda=agenda,
            batch=batch,
            coverage=coverage,
            scan_receipt=scan_receipt,
        )

    def refresh(self) -> dict[str, Any]:
        """Scan once, apply once, then persist the bounded scan receipt."""

        sources = self.collect_for_refresh()
        items = self._refresh.apply(sources.batch)
        receipt = dict(sources.scan_receipt or {})
        persistence: dict[str, Any]
        if self._receipt_store is None:
            persistence = {
                "status": "UNAVAILABLE",
                "warning": "REVIEW_RECEIPT_STORE_UNAVAILABLE",
            }
            receipt["status"] = "PARTIAL"
            receipt["complete"] = False
            receipt["warnings"] = ("REVIEW_RECEIPT_STORE_UNAVAILABLE",)
            receipt["warning_codes"] = ("REVIEW_RECEIPT_STORE_UNAVAILABLE",)
        else:
            try:
                receipt_id = self._receipt_store.save(receipt)
                if not isinstance(receipt_id, str) or not receipt_id:
                    raise RuntimeError("receipt identity unavailable")
                persistence = {"status": "SAVED", "receipt_id": receipt_id}
            except Exception:  # noqa: BLE001 - reconcile is never retried
                persistence = {
                    "status": "UNAVAILABLE",
                    "warning": "REVIEW_RECEIPT_PERSIST_FAILED",
                }
                receipt["status"] = "PARTIAL"
                receipt["complete"] = False
                receipt["warnings"] = ("REVIEW_RECEIPT_PERSIST_FAILED",)
                receipt["warning_codes"] = ("REVIEW_RECEIPT_PERSIST_FAILED",)
        receipt["persistence"] = persistence
        return {
            "updated_count": len(items),
            "coverage": sources.coverage,
            "complete": bool(receipt.get("complete"))
            and persistence["status"] == "SAVED",
            "scan_receipt": receipt,
        }

    def collect(self) -> ReviewWorkspaceSources:
        agenda = _read_source(
            lambda: self._agenda.query(AgendaQueryInput(window_days=90, limit=200)).model_dump(
                mode="json"
            )
        )
        retro = _read_source(
            lambda: self._retro.history(TradeRetroHistoryInput(limit=50)).model_dump(mode="json")
        )
        scorecards = _read_source(
            lambda: self._scorecards.history(JudgmentScorecardHistoryInput(limit=50)).model_dump(
                mode="json"
            )
        )
        batch = self._refresh.collect(agenda, retro, scorecards)
        coverage = {
            "agenda": _coverage(agenda, 200),
            "retro": _coverage(retro, 50),
            "scorecards": _coverage(scorecards, 50),
        }
        for source in (
            "DECISION_REVIEW_DUE",
            "OPEN_QUESTION_DUE",
            "OBSERVATION_REVIEW_DUE",
            "AGENT_PENDING_ACTION",
            "BROKER_ORDER_INTENT",
        ):
            coverage[source] = (
                "COMPLETE"
                if source in batch.fully_observed_source_types
                else "PARTIAL"
                if source in batch.observed_source_types
                else "UNAVAILABLE"
            )
        return ReviewWorkspaceSources(agenda=agenda, batch=batch, coverage=coverage)

    def read_operations(self) -> dict[str, Any]:
        """Expose source-bound recovery state without requiring queue maintenance."""
        batch = self._refresh.collect_operations()
        coverage = {
            source.value: (
                "COMPLETE" if source in batch.fully_observed_source_types
                else "PARTIAL" if source in batch.observed_source_types
                else "UNAVAILABLE"
            )
            for source in (
                ReviewItemSourceType.AGENT_PENDING_ACTION,
                ReviewItemSourceType.BROKER_ORDER_INTENT,
            )
        }
        return {
            "workflow_attention_projection": [
                {
                    **asdict(row),
                    "key": row.source_key,
                    "source_type": row.source_type.value,
                    "severity": row.severity.value,
                }
                for row in batch.projections
            ],
            "review_coverage": {
                "scope": "operations",
                "generated_at": self._clock.now().isoformat(),
                "sources": coverage,
                "complete": all(value == "COMPLETE" for value in coverage.values()),
            },
        }

    def read(self) -> dict[str, Any]:
        sources = self.collect()
        coverage = dict(sources.coverage)
        try:
            research = [asdict(row) for row in self._research.pending_by_subject()]
            coverage["research"] = "COMPLETE"
        except Exception:  # noqa: BLE001 - failed research is not an empty successful result
            research = []
            coverage["research"] = "UNAVAILABLE"
        projected = [
            {
                **asdict(row),
                "key": row.source_key,
                "source_type": row.source_type.value,
                "severity": row.severity.value,
            }
            for row in sources.batch.projections
        ]
        error = None
        observed_at = None
        metrics = None
        items: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        workflow = projected
        try:
            items = [row.model_dump(mode="json") for row in self._items.list_open(limit=100)]
            history = [row.model_dump(mode="json") for row in self._items.list_recent(limit=50)]
            metrics = self._items.metrics().model_dump(mode="json")
            observed_at = self._items.latest_observed_at()
            known = self._items.known_active_source_keys(
                tuple(row.source_key for row in sources.batch.projections)
            )
            workflow = [row for row in projected if row["key"] not in known]
            coverage["review_items"] = (
                "PARTIAL"
                if metrics["open_count"] + metrics["acknowledged_count"] > len(items)
                else "COMPLETE"
            )
        except Exception:  # noqa: BLE001 - preserve independently read live projections
            coverage["review_items"] = "UNAVAILABLE"
            error = {
                "ok": False,
                "data": None,
                "errors": [{"code": "CONSOLE_REVIEW_ITEMS_READ_FAILED"}],
            }
        recent_receipt: dict[str, Any] | None = None
        recent_receipt_status = "UNAVAILABLE"
        recent_receipt_warning: str | None = "REVIEW_RECEIPT_READ_UNAVAILABLE"
        if self._receipt_store is not None:
            try:
                recent_receipt = self._receipt_store.latest()
                recent_receipt_status = "AVAILABLE" if recent_receipt is not None else "NONE"
                recent_receipt_warning = None
            except Exception:  # noqa: BLE001 - a GET must remain read-only
                recent_receipt_status = "UNAVAILABLE"
                recent_receipt_warning = "REVIEW_RECEIPT_READ_FAILED"
        return {
            "research_attention": research,
            "workflow_attention": workflow,
            "workflow_attention_projection": projected,
            "review_items": items,
            "review_item_metrics": metrics,
            "review_item_history": history,
            "review_items_error": error,
            "recent_refresh_receipt": recent_receipt,
            "recent_refresh_receipt_status": recent_receipt_status,
            "recent_refresh_receipt_warning": recent_receipt_warning,
            "agenda_summary": sources.agenda,
            "review_coverage": {
                "generated_at": self._clock.now().isoformat(),
                "materialized_at": observed_at.isoformat() if observed_at else None,
                "sources": coverage,
                "complete": all(value == "COMPLETE" for value in coverage.values()),
                "materialization_required": bool(workflow),
            },
        }
