"""Read and project durable ReviewItem sources for Console materialization.

The service owns the cross-service read orchestration and completeness rules used
by Console entry points.  ``collect`` is deliberately read-only; callers must
explicitly pass its immutable batch to ``apply`` before the durable ReviewItem
projection is reconciled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from application.ports.agent_pending_action_repository import AgentPendingActionRepository
from application.ports.clock import Clock
from application.services._research_support import UowFactory
from application.services.broker_order_service import BrokerOrderService
from application.services.decision_record_service import DecisionRecordService
from application.services.external_note_review_service import ExternalNoteReviewService
from application.services.review_item_projection import (
    decision_review_attention_items,
    observe_workflow_reviews,
    operational_attention_items,
    review_item_projection,
)
from application.services.review_item_service import ReviewItemService
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection


@dataclass(frozen=True, slots=True)
class ReviewItemRefreshBatch:
    """Immutable result of one bounded, read-only ReviewItem source scan."""

    projections: tuple[ReviewItemProjection, ...]
    observed_source_types: frozenset[ReviewItemSourceType]
    authoritative_source_refs: frozenset[tuple[ReviewItemSourceType, str]]
    fully_observed_source_types: frozenset[ReviewItemSourceType]
    subject_scope: str | None = None
    partial_failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewItemRefreshScan:
    """Direct-source scan result used by the explicit Console refresh.

    ``receipts`` contains only bounded metadata.  The source rows remain in
    ``batch`` so the caller can apply the exact deterministic projection, while
    no source body is copied into the refresh receipt or its audit event.
    """

    batch: ReviewItemRefreshBatch
    receipts: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _ScanAttempt:
    rows: tuple[Any, ...]
    row_payloads: tuple[Any, ...]
    pages: int
    complete: bool
    warning_codes: tuple[str, ...]
    failed: bool = False


@dataclass(frozen=True, slots=True)
class _PageResult:
    rows: tuple[Any, ...]
    has_more: bool | None = None
    warning_codes: tuple[str, ...] = ()


_REFRESH_MAX_ROWS = 5_000


def _jsonable(value: Any) -> Any:
    """Convert an application DTO/domain value to deterministic JSON data."""

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return _jsonable(asdict(value))
    return value


def _payload_digest(rows: tuple[Any, ...]) -> str:
    payload = json.dumps(
        [_jsonable(row) for row in rows],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _row_identity(source: str, value: Any) -> tuple[str, str]:
    payload = _jsonable(value)
    if isinstance(payload, dict):
        identity = next(
            (
                payload[key]
                for key in (
                    "decision_id",
                    "question_id",
                    "review_id",
                    "action_id",
                    "order_intent_id",
                    "source_key",
                    "scorecard_id",
                    "run_id",
                    "agenda_item_id",
                )
                if payload.get(key) is not None
            ),
            None,
        )
        version = next(
            (
                payload[key]
                for key in ("version", "schema_version", "updated_at", "created_at")
                if payload.get(key) is not None
            ),
            "",
        )
        if identity is not None:
            return str(identity), str(version)
    return source, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _same_scan(source: str, first: _ScanAttempt, second: _ScanAttempt) -> bool:
    return tuple(_row_identity(source, item) for item in first.row_payloads) == tuple(
        _row_identity(source, item) for item in second.row_payloads
    ) and _payload_digest(first.row_payloads) == _payload_digest(second.row_payloads)


def _collect_typed_pages(
    *,
    source: str,
    page_limit: int,
    fetch_page: Callable[[int, int], tuple[Any, ...] | _PageResult],
) -> _ScanAttempt:
    """Read one bounded source pass with an explicit overflow probe."""

    rows: list[Any] = []
    pages = 0
    offset = 0
    try:
        while len(rows) < _REFRESH_MAX_ROWS:
            fetched = fetch_page(offset, page_limit)
            if isinstance(fetched, _PageResult):
                page = fetched.rows
                page_has_more = fetched.has_more
                page_warnings = fetched.warning_codes
            else:
                page = tuple(fetched)
                page_has_more = None
                page_warnings = ()
            if page_warnings:
                return _ScanAttempt(
                    rows=tuple(rows) + tuple(page[: _REFRESH_MAX_ROWS - len(rows)]),
                    row_payloads=tuple(
                        _jsonable(item)
                        for item in tuple(rows) + tuple(page[: _REFRESH_MAX_ROWS - len(rows)])
                    ),
                    pages=pages + (1 if page else 0),
                    complete=False,
                    warning_codes=page_warnings,
                )
            if not page:
                if page_has_more is True:
                    return _ScanAttempt(
                        rows=tuple(rows),
                        row_payloads=tuple(_jsonable(item) for item in rows),
                        pages=pages,
                        complete=False,
                        warning_codes=("REVIEW_SOURCE_PAGE_GAP",),
                    )
                return _ScanAttempt(
                    rows=tuple(rows),
                    row_payloads=tuple(_jsonable(item) for item in rows),
                    pages=pages,
                    complete=True,
                    warning_codes=(),
                )
            pages += 1
            remaining = _REFRESH_MAX_ROWS - len(rows)
            rows.extend(page[:remaining])
            if page_has_more is False:
                return _ScanAttempt(
                    rows=tuple(rows),
                    row_payloads=tuple(_jsonable(item) for item in rows),
                    pages=pages,
                    complete=True,
                    warning_codes=(),
                )
            if page_has_more is not True and len(page) < page_limit:
                return _ScanAttempt(
                    rows=tuple(rows),
                    row_payloads=tuple(_jsonable(item) for item in rows),
                    pages=pages,
                    complete=True,
                    warning_codes=(),
                )
            offset += len(page)

        # Exactly 5,000 rows is complete only after a one-row probe confirms
        # that no additional source row exists.  The probe is not included in
        # the projected payload or row_count.
        fetched_probe = fetch_page(_REFRESH_MAX_ROWS, 1)
        if isinstance(fetched_probe, _PageResult) and (
            fetched_probe.warning_codes or fetched_probe.has_more is True
        ):
            return _ScanAttempt(
                rows=tuple(rows),
                row_payloads=tuple(_jsonable(item) for item in rows),
                pages=pages,
                complete=False,
                warning_codes=fetched_probe.warning_codes or ("REVIEW_SOURCE_PAGE_GAP",),
            )
        probe = (
            fetched_probe.rows
            if isinstance(fetched_probe, _PageResult)
            else tuple(fetched_probe)
        )
        if probe:
            return _ScanAttempt(
                rows=tuple(rows),
                row_payloads=tuple(_jsonable(item) for item in rows),
                pages=pages,
                complete=False,
                warning_codes=("REVIEW_SOURCE_MAX_ROWS",),
            )
        return _ScanAttempt(
            rows=tuple(rows),
            row_payloads=tuple(_jsonable(item) for item in rows),
            pages=pages,
            complete=True,
            warning_codes=(),
        )
    except Exception:  # noqa: BLE001 - receipt exposes only a safe code
        return _ScanAttempt(
            rows=tuple(rows),
            row_payloads=tuple(_jsonable(item) for item in rows),
            pages=pages,
            complete=False,
            warning_codes=("REVIEW_SOURCE_READ_FAILED",),
            failed=True,
        )


def _scan_typed_source(
    *,
    source: str,
    page_limit: int,
    fetch_page: Callable[[int, int], tuple[Any, ...] | _PageResult],
    clock: Clock,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Run two passes and return the latest payload only when both agree."""

    started_at = clock.now()
    first = _collect_typed_pages(
        source=source,
        page_limit=page_limit,
        fetch_page=fetch_page,
    )
    if first.failed:
        completed_at = clock.now()
        warning_codes = first.warning_codes
        return (), {
            "source": source,
            "status": "UNAVAILABLE",
            "row_count": 0,
            "pages": first.pages,
            "limit": page_limit,
            "max_rows": _REFRESH_MAX_ROWS,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "warnings": warning_codes,
            "warning_codes": warning_codes,
            "payload_digest": _payload_digest(()),
            "scan_passes": 1,
        }

    second = _collect_typed_pages(
        source=source,
        page_limit=page_limit,
        fetch_page=fetch_page,
    )
    consistent = _same_scan(source, first, second)
    selected = second.rows if consistent else first.rows
    warnings = list(first.warning_codes)
    warnings.extend(second.warning_codes)
    if second.failed:
        warnings.append("REVIEW_SOURCE_SECOND_SCAN_FAILED")
    elif not consistent:
        warnings.append("REVIEW_SOURCE_CHANGED_BETWEEN_SCANS")
    status = "COMPLETE" if first.complete and second.complete and consistent else "PARTIAL"
    completed_at = clock.now()
    return selected, {
        "source": source,
        "status": status,
        "row_count": len(selected),
        "pages": max(first.pages, second.pages),
        "limit": page_limit,
        "max_rows": _REFRESH_MAX_ROWS,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "warnings": tuple(dict.fromkeys(warnings)),
        "warning_codes": tuple(dict.fromkeys(warnings)),
        "payload_digest": _payload_digest(selected),
        "scan_passes": 2,
    }


class ReviewItemRefreshService:
    """Collect and explicitly apply the Console ReviewItem materialization batch."""

    _SOURCE_LIMIT = 100

    def __init__(
        self,
        clock: Clock,
        decisions: DecisionRecordService,
        external_note_reviews: ExternalNoteReviewService,
        agent_pending_actions: AgentPendingActionRepository,
        broker_orders: BrokerOrderService,
        review_items: ReviewItemService,
        *,
        research_uow_factory: UowFactory | None = None,
    ) -> None:
        self._research_uow = research_uow_factory
        self._clock = clock
        self._decisions = decisions
        self._external_note_reviews = external_note_reviews
        self._agent_pending_actions = agent_pending_actions
        self._broker_orders = broker_orders
        self._review_items = review_items

    def collect_for_refresh(
        self,
        agenda: dict[str, Any],
        retro: dict[str, Any],
        scorecards: dict[str, Any],
        *,
        subject_id: str | None = None,
        cutoff: datetime | None = None,
    ) -> ReviewItemRefreshScan:
        """Run the explicit two-pass scan for direct durable sources.

        Workflow sources are already scanned by ``ReviewWorkspaceQueryService``;
        this method receives their final envelopes and adds the direct Decision,
        question, Observation, Agent and Broker scans.  A source is considered
        fully observed only when both passes have identical ordered identities
        and payload digests.
        """

        scan_cutoff = cutoff or self._clock.now()
        workflow = observe_workflow_reviews(
            agenda=agenda,
            retro=retro,
            scorecards=scorecards,
        )
        observed = set(workflow.observed_source_types)
        authoritative_refs = set(workflow.authoritative_source_refs)
        fully_observed: set[ReviewItemSourceType] = set()
        projections = list(workflow.projections)
        partial_failures: list[str] = []
        receipts: dict[str, dict[str, Any]] = {}

        def scan(
            source: ReviewItemSourceType,
            *,
            page_limit: int,
            fetch_page: Callable[[int, int], tuple[Any, ...] | _PageResult],
        ) -> tuple[Any, ...]:
            values, receipt = _scan_typed_source(
                source=source.value,
                page_limit=page_limit,
                fetch_page=fetch_page,
                clock=self._clock,
            )
            receipts[source.value] = receipt
            if receipt["status"] != "UNAVAILABLE":
                observed.add(source)
            if receipt["status"] == "COMPLETE":
                fully_observed.add(source)
            else:
                partial_failures.append(
                    str(receipt["warnings"][0])
                    if receipt.get("warnings")
                    else f"{source.value}:PARTIAL"
                )
            return values

        def decisions(offset: int, limit: int) -> tuple[Any, ...]:
            kwargs: dict[str, Any] = {
                "now": scan_cutoff,
                "limit": limit,
            }
            if subject_id is not None:
                kwargs["subject_id"] = subject_id
            if offset:
                kwargs["offset"] = offset
            return self._decisions.list_review_due(**kwargs)

        due_decisions = scan(
            ReviewItemSourceType.DECISION_REVIEW_DUE,
            page_limit=100,
            fetch_page=decisions,
        )
        projections.extend(
            projection
            for item in decision_review_attention_items(tuple(due_decisions))
            if (projection := review_item_projection(item)) is not None
        )

        if self._research_uow is None:
            receipts[ReviewItemSourceType.OPEN_QUESTION_DUE.value] = {
                "source": ReviewItemSourceType.OPEN_QUESTION_DUE.value,
                "status": "UNAVAILABLE",
                "row_count": 0,
                "pages": 0,
                "limit": 100,
                "max_rows": _REFRESH_MAX_ROWS,
                "started_at": self._clock.now().isoformat(),
                "completed_at": self._clock.now().isoformat(),
                "warnings": ("REVIEW_SOURCE_RESEARCH_UNAVAILABLE",),
                "warning_codes": ("REVIEW_SOURCE_RESEARCH_UNAVAILABLE",),
                "payload_digest": _payload_digest(()),
                "scan_passes": 0,
            }
            partial_failures.append("REVIEW_SOURCE_RESEARCH_UNAVAILABLE")
        else:
            research_uow = self._research_uow
            assert research_uow is not None

            def questions(offset: int, limit: int) -> tuple[Any, ...]:
                with research_uow() as uow:
                    kwargs: dict[str, Any] = {
                        "now": scan_cutoff,
                        "subject_id": subject_id,
                        "limit": limit,
                    }
                    if offset:
                        kwargs["offset"] = offset
                    return uow.questions.list_due(**kwargs)

            due_questions = scan(
                ReviewItemSourceType.OPEN_QUESTION_DUE,
                page_limit=100,
                fetch_page=questions,
            )
            projections.extend(
                self._question_projection(question) for question in due_questions
            )

        if subject_id is None:
            def observations(offset: int, limit: int) -> tuple[Any, ...]:
                kwargs: dict[str, Any] = {"limit": limit}
                if offset:
                    kwargs["offset"] = offset
                return self._external_note_reviews.pending_projections(**kwargs)

            pending_observations = scan(
                ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
                page_limit=100,
                fetch_page=observations,
            )
            projections.extend(pending_observations)

            def agents(offset: int, limit: int) -> tuple[Any, ...]:
                kwargs: dict[str, Any] = {"now": scan_cutoff, "limit": limit}
                if offset:
                    kwargs["offset"] = offset
                return self._agent_pending_actions.list_unresolved(**kwargs)

            unresolved_agents = scan(
                ReviewItemSourceType.AGENT_PENDING_ACTION,
                page_limit=100,
                fetch_page=agents,
            )

            def brokers(offset: int, limit: int) -> tuple[Any, ...]:
                return self._broker_orders.list_unresolved(limit=limit, offset=offset)

            unresolved_brokers = scan(
                ReviewItemSourceType.BROKER_ORDER_INTENT,
                page_limit=100,
                fetch_page=brokers,
            )
            projections.extend(
                projection
                for item in operational_attention_items(
                    agent_actions=tuple(unresolved_agents),
                    broker_intents=tuple(unresolved_brokers),
                )
                if (projection := review_item_projection(item)) is not None
            )
        else:
            # A subject-scoped refresh intentionally does not claim that global
            # operational sources were scanned.  Their old rows therefore stay
            # protected from source-disappearance closure.
            for source in (
                ReviewItemSourceType.OBSERVATION_REVIEW_DUE,
                ReviewItemSourceType.AGENT_PENDING_ACTION,
                ReviewItemSourceType.BROKER_ORDER_INTENT,
            ):
                receipts[source.value] = {
                    "source": source.value,
                    "status": "UNAVAILABLE",
                    "row_count": 0,
                    "pages": 0,
                    "limit": 100,
                    "max_rows": _REFRESH_MAX_ROWS,
                    "started_at": self._clock.now().isoformat(),
                    "completed_at": self._clock.now().isoformat(),
                    "warnings": ("REVIEW_SOURCE_SCOPED_OMISSION",),
                    "warning_codes": ("REVIEW_SOURCE_SCOPED_OMISSION",),
                    "payload_digest": _payload_digest(()),
                    "scan_passes": 0,
                }

        unique_projections: list[ReviewItemProjection] = []
        seen_projection_keys: set[str] = set()
        for projection in projections:
            if projection.source_key in seen_projection_keys:
                continue
            seen_projection_keys.add(projection.source_key)
            unique_projections.append(projection)
        batch = ReviewItemRefreshBatch(
            projections=tuple(unique_projections),
            observed_source_types=frozenset(observed),
            authoritative_source_refs=frozenset(authoritative_refs),
            fully_observed_source_types=frozenset(fully_observed),
            subject_scope=subject_id,
            partial_failures=tuple(dict.fromkeys(partial_failures)),
        )
        return ReviewItemRefreshScan(batch=batch, receipts=receipts)

    @staticmethod
    def _question_projection(question: Any) -> ReviewItemProjection:
        return ReviewItemProjection(
            source_key=f"open-question-due-{question.question_id}",
            source_type=ReviewItemSourceType.OPEN_QUESTION_DUE,
            source_ref=question.question_id,
            subject_id=question.subject_id,
            title="Research question is due",
            detail=question.text,
            severity=ReviewItemSeverity.ATTENTION,
            recommended_action="REVIEW_QUESTION",
            href=(
                f"/research?subject_id={question.subject_id}"
                "&section=thesis#research-section-questions"
            ),
            due_at=question.due_at,
        )

    def collect(
        self,
        agenda: dict[str, Any],
        retro: dict[str, Any],
        scorecards: dict[str, Any],
        subject_id: str | None = None,
    ) -> ReviewItemRefreshBatch:
        """Read bounded durable sources and return projections without writing.

        A source is marked observed only after its direct read succeeds.  A page
        containing exactly the bound is intentionally not fully observed because
        it may omit additional live source rows.  Global source failures remain
        silent for compatibility with the existing global Console projection;
        the scoped Decision read reports its failure so the endpoint can retain
        its existing ``partial_failures`` entry.
        """

        workflow = observe_workflow_reviews(
            agenda=agenda,
            retro=retro,
            scorecards=scorecards,
        )
        observed = set(workflow.observed_source_types)
        authoritative_refs = set(workflow.authoritative_source_refs)
        fully_observed: set[ReviewItemSourceType] = set()
        projections = list(workflow.projections)
        partial_failures: list[str] = []

        try:
            decision_kwargs: dict[str, Any] = {
                "now": self._clock.now(),
                "limit": self._SOURCE_LIMIT,
            }
            if subject_id is not None:
                decision_kwargs["subject_id"] = subject_id
            due_decisions = self._decisions.list_review_due(**decision_kwargs)
        except Exception:  # noqa: BLE001 - failed reads must not auto-resolve
            due_decisions = ()
            if subject_id is not None:
                partial_failures.append("decision_review_due")
        else:
            observed.add(ReviewItemSourceType.DECISION_REVIEW_DUE)
            projections.extend(
                projection
                for item in decision_review_attention_items(due_decisions)
                if (projection := review_item_projection(item)) is not None
            )
            if len(due_decisions) < self._SOURCE_LIMIT:
                fully_observed.add(ReviewItemSourceType.DECISION_REVIEW_DUE)

        if subject_id is None:
            try:
                observation_reviews = self._external_note_reviews.pending_projections(
                    limit=self._SOURCE_LIMIT
                )
            except Exception:  # noqa: BLE001 - absence must not resolve prior items
                observation_reviews = ()
            else:
                observed.add(ReviewItemSourceType.OBSERVATION_REVIEW_DUE)
                # ``pending_projections`` already returns the application DTO
                # used by ReviewItemService.  Keep it typed so this boundary does
                # not reconstruct a projection through a presentation dict.
                projections.extend(observation_reviews)
                if len(observation_reviews) < self._SOURCE_LIMIT:
                    fully_observed.add(ReviewItemSourceType.OBSERVATION_REVIEW_DUE)

            try:
                agent_actions = self._agent_pending_actions.list_unresolved(
                    now=self._clock.now(),
                    limit=self._SOURCE_LIMIT,
                )
            except Exception:  # noqa: BLE001 - absence must not resolve prior items
                agent_actions = ()
            else:
                observed.add(ReviewItemSourceType.AGENT_PENDING_ACTION)
                if len(agent_actions) < self._SOURCE_LIMIT:
                    fully_observed.add(ReviewItemSourceType.AGENT_PENDING_ACTION)

            try:
                broker_intents = self._broker_orders.list_unresolved(limit=self._SOURCE_LIMIT)
            except Exception:  # noqa: BLE001 - absence must not resolve prior items
                broker_intents = ()
            else:
                observed.add(ReviewItemSourceType.BROKER_ORDER_INTENT)
                if len(broker_intents) < self._SOURCE_LIMIT:
                    fully_observed.add(ReviewItemSourceType.BROKER_ORDER_INTENT)

            projections.extend(
                projection
                for item in operational_attention_items(
                    agent_actions=agent_actions,
                    broker_intents=broker_intents,
                )
                if (projection := review_item_projection(item)) is not None
            )
        else:
            projections = [item for item in projections if item.subject_id == subject_id]

        if self._research_uow is not None:
            try:
                with self._research_uow() as uow:
                    due_questions = uow.questions.list_due(
                        now=self._clock.now(),
                        subject_id=subject_id,
                        limit=self._SOURCE_LIMIT,
                    )
                question_projections = []
                for question in due_questions:
                    question_projections.append(
                        ReviewItemProjection(
                            source_key=f"open-question-due-{question.question_id}",
                            source_type=ReviewItemSourceType.OPEN_QUESTION_DUE,
                            source_ref=question.question_id,
                            subject_id=question.subject_id,
                            title="Research question is due",
                            detail=question.text,
                            severity=ReviewItemSeverity.ATTENTION,
                            recommended_action="REVIEW_QUESTION",
                            href=f"/research?subject_id={question.subject_id}&section=thesis#research-section-questions",
                            due_at=question.due_at,
                        )
                    )
                projections.extend(question_projections)
                observed.add(ReviewItemSourceType.OPEN_QUESTION_DUE)
                if len(due_questions) < self._SOURCE_LIMIT:
                    fully_observed.add(ReviewItemSourceType.OPEN_QUESTION_DUE)
            except Exception:  # noqa: BLE001 - failure must not close known question reminders
                partial_failures.append("open_question_due")

        return ReviewItemRefreshBatch(
            projections=tuple(projections),
            observed_source_types=frozenset(observed),
            authoritative_source_refs=frozenset(authoritative_refs),
            fully_observed_source_types=frozenset(fully_observed),
            subject_scope=subject_id,
            partial_failures=tuple(partial_failures),
        )

    def apply(self, batch: ReviewItemRefreshBatch) -> tuple[Any, ...]:
        """Apply one collected batch through the existing ReviewItem service."""

        return self._review_items.reconcile(
            batch.projections,
            observed_source_types=batch.observed_source_types,
            authoritative_source_refs=batch.authoritative_source_refs,
            fully_observed_source_types=batch.fully_observed_source_types,
            subject_scope=batch.subject_scope,
        )
