"""Compact research-memory operation adapters."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from application.dto.account_transactions import TradeCycleQueryInput
from application.dto.activity_annotations import ActivityAnnotationAppendInput
from application.dto.behavior_review import BehaviorReviewRunInput
from application.dto.catalyst_agenda import AgendaMutationInput, AgendaQueryInput
from application.dto.research_memory import ResearchSearchQuery
from application.dto.tool_envelope import ToolEnvelope
from application.dto.trade_cycle_overrides import TradeCycleOverrideAppendInput
from bootstrap import ApplicationContainer
from domain.common.actor import (
    CURRENT_CHAT_SUBMISSION_VALUES,
    ActorContext,
)
from domain.common.enums import DecisionScenario, Freshness
from domain.common.ids import EntityIdPrefix
from interfaces.mcp.schemas import (
    DecisionRecordAppendInput,
    JournalAppendInput,
    ResearchReportGetInput,
    ResearchSearchInput,
    ResearchTimelineGetInput,
)
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_research_memory_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact research-memory operation adapters."""

    # ---------------------------------------------------------- Phase 1C research memory
    def research_search(
        text: str | None = None,
        case_id: str | None = None,
        thesis_id: str | None = None,
        instrument_id: str | None = None,
        entity_types: list[str] | None = None,
        evidence_types: list[str] | None = None,
        journal_entry_types: list[str] | None = None,
        stances: list[str] | None = None,
        topic_tags: list[str] | None = None,
        visible_from: datetime | None = None,
        visible_to: datetime | None = None,
        as_of: datetime | None = None,
        include_superseded: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Full-text + structured research-memory search (no Evidence create)."""
        try:
            inp = ResearchSearchInput.model_validate(
                {
                    "text": text,
                    "case_id": case_id,
                    "thesis_id": thesis_id,
                    "instrument_id": instrument_id,
                    "entity_types": tuple(entity_types or ()),
                    "evidence_types": tuple(evidence_types or ()),
                    "journal_entry_types": tuple(journal_entry_types or ()),
                    "stances": tuple(stances or ()),
                    "topic_tags": tuple(topic_tags or ()),
                    "visible_from": visible_from,
                    "visible_to": visible_to,
                    "as_of": as_of,
                    "include_superseded": include_superseded,
                    "limit": limit,
                    "offset": offset,
                }
            )
            query = ResearchSearchQuery(
                text=inp.text,
                subject_id=inp.case_id,
                thesis_id=inp.thesis_id,
                instrument_id=inp.instrument_id,
                entity_types=inp.entity_types,
                evidence_types=inp.evidence_types,
                journal_entry_types=inp.journal_entry_types,
                stances=inp.stances,
                topic_tags=inp.topic_tags,
                visible_from=inp.visible_from,
                visible_to=inp.visible_to,
                as_of=inp.as_of,
                include_superseded=inp.include_superseded,
                limit=inp.limit,
                offset=inp.offset,
            )
            envelope = container.services.research_search.search(query)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def research_report_get(report_id: str) -> dict[str, Any]:
        """Fetch one immutable ResearchReport by id."""
        try:
            inp = ResearchReportGetInput.model_validate({"report_id": report_id})
            envelope = container.services.research_archive.get_report(inp.report_id)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def research_timeline_get(
        case_id: str,
        entity_types: list[str] | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Unified Research Subject (标的) timeline projection."""
        try:
            inp = ResearchTimelineGetInput.model_validate(
                {
                    "case_id": case_id,
                    "entity_types": tuple(entity_types or ()),
                    "occurred_from": occurred_from,
                    "occurred_to": occurred_to,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = container.services.research_timeline.get_timeline(
                subject_id=inp.case_id,
                entity_types=inp.entity_types,
                occurred_from=inp.occurred_from,
                occurred_to=inp.occurred_to,
                as_of=inp.as_of,
                limit=inp.limit,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def catalyst_agenda_get(
        agenda_item_id: str | None = None,
        include_history: bool = False,
        as_of: datetime | None = None,
        window_days: int = 30,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read the durable Catalyst Agenda without refreshing Providers or accounts."""
        try:
            normalized_filters = dict(filters or {})
            if "case_ids" in normalized_filters:
                if "subject_ids" in normalized_filters:
                    raise ValueError("filters cannot contain both case_ids and subject_ids")
                normalized_filters["subject_ids"] = normalized_filters.pop("case_ids")
            request = AgendaQueryInput.model_validate(
                {
                    "agenda_item_id": agenda_item_id,
                    "include_history": include_history,
                    "as_of": as_of,
                    "window_days": window_days,
                    "filters": normalized_filters,
                    "limit": limit,
                    "offset": offset,
                }
            )
            return container.services.catalyst_agenda.query(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def journal_append(
        entry_type: str,
        title: str,
        body_markdown: str,
        authored_by: str,
        confirmed_by: str,
        idempotency_key: str,
        case_id: str | None = None,
        instrument_ids: list[str] | None = None,
        topic_tags: list[str] | None = None,
        related_entity_type: str | None = None,
        related_entity_id: str | None = None,
        supersedes_journal_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a user-confirmed journal entry (never auto-writes chat)."""
        try:
            inp = JournalAppendInput.model_validate(
                {
                    "case_id": case_id,
                    "entry_type": entry_type,
                    "title": title,
                    "body_markdown": body_markdown,
                    "authored_by": authored_by,
                    "confirmed_by": confirmed_by,
                    "instrument_ids": tuple(instrument_ids or ()),
                    "topic_tags": tuple(topic_tags or ()),
                    "related_entity_type": related_entity_type,
                    "related_entity_id": related_entity_id,
                    "supersedes_journal_id": supersedes_journal_id,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.services.journal.append(
                subject_id=inp.case_id,
                entry_type=inp.entry_type,
                title=inp.title,
                body_markdown=inp.body_markdown,
                authored_by=inp.authored_by,
                confirmed_by=inp.confirmed_by,
                instrument_ids=inp.instrument_ids,
                topic_tags=inp.topic_tags,
                related_entity_type=inp.related_entity_type,
                related_entity_id=inp.related_entity_id,
                supersedes_journal_id=inp.supersedes_journal_id,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def decision_record_append(
        case_id: str,
        decision_type: str,
        title: str,
        rationale: str,
        decided_at: datetime,
        decided_by: str,
        idempotency_key: str,
        confirmation_mode: str = "strict_review",
        primary_instrument_id: str | None = None,
        thesis_revision_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        report_ids: list[str] | None = None,
        supersedes_decision_id: str | None = None,
        position_context_snapshot_id: str | None = None,
        strategy_code: str | None = None,
        strategy_version: str | None = None,
        scenario: DecisionScenario | None = None,
        trade_plan_id: str | None = None,
        trade_plan_version: int | None = None,
        review_due_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a research/position intent DecisionRecord (no order/fill writes)."""
        try:
            inp = DecisionRecordAppendInput.model_validate(
                {
                    "case_id": case_id,
                    "decision_type": decision_type,
                    "title": title,
                    "rationale": rationale,
                    "decided_at": decided_at,
                    "decided_by": decided_by,
                    "confirmation_mode": confirmation_mode,
                    "primary_instrument_id": primary_instrument_id,
                    "thesis_revision_ids": tuple(thesis_revision_ids or ()),
                    "evidence_ids": tuple(evidence_ids or ()),
                    "report_ids": tuple(report_ids or ()),
                    "supersedes_decision_id": supersedes_decision_id,
                    "position_context_snapshot_id": position_context_snapshot_id,
                    "idempotency_key": idempotency_key,
                    "strategy_code": strategy_code,
                    "strategy_version": strategy_version,
                    "scenario": scenario,
                    "trade_plan_id": trade_plan_id,
                    "trade_plan_version": trade_plan_version,
                    "review_due_at": review_due_at,
                }
            )
            envelope = container.services.decisions.append(
                subject_id=inp.case_id,
                decision_type=inp.decision_type,
                title=inp.title,
                rationale=inp.rationale,
                decided_at=inp.decided_at,
                decided_by=inp.decided_by,
                confirmation_mode=inp.confirmation_mode,
                primary_instrument_id=inp.primary_instrument_id,
                thesis_revision_ids=inp.thesis_revision_ids,
                evidence_ids=inp.evidence_ids,
                report_ids=inp.report_ids,
                supersedes_decision_id=inp.supersedes_decision_id,
                position_context_snapshot_id=inp.position_context_snapshot_id,
                idempotency_key=inp.idempotency_key,
                strategy_code=inp.strategy_code,
                strategy_version=inp.strategy_version,
                scenario=inp.scenario,
                trade_plan_id=inp.trade_plan_id,
                trade_plan_version=inp.trade_plan_version,
                review_due_at=inp.review_due_at,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def catalyst_agenda_manage(
        action: str,
        confirmed_by: str,
        authorization_note: str,
        idempotency_key: str,
        payload: dict[str, Any],
        agenda_item_id: str | None = None,
        expected_version: int | None = None,
        submitted_via: str = "direct",
    ) -> dict[str, Any]:
        """Create/revise/cancel an Agenda item or link its durable outcome facts."""
        try:
            normalized_payload = dict(payload)
            if "case_id" in normalized_payload:
                if "subject_id" in normalized_payload:
                    raise ValueError("payload cannot contain both case_id and subject_id")
                normalized_payload["subject_id"] = normalized_payload.pop("case_id")
            request = AgendaMutationInput.model_validate(
                {
                    "action": action,
                    "agenda_item_id": agenda_item_id,
                    "expected_version": expected_version,
                    "confirmed_by": confirmed_by,
                    "authorization_note": authorization_note,
                    "idempotency_key": idempotency_key,
                    "payload": normalized_payload,
                }
            )
            if submitted_via not in {"direct", *CURRENT_CHAT_SUBMISSION_VALUES}:
                raise ValueError("submitted_via must be direct, mcp_chat, or codex_chat")
            actor_context = (
                ActorContext.current_chat_authorized(
                    request_id=f"mcp:agenda:{request.action.value}:{request.idempotency_key}",
                    authorization_note=request.authorization_note,
                    submitted_via=submitted_via,
                )
                if submitted_via in CURRENT_CHAT_SUBMISSION_VALUES
                else None
            )
            return container.services.catalyst_agenda.manage(
                request,
                actor_context=actor_context,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def activity_annotation_append(
        provider: str,
        account_ref: str,
        provider_transaction_id: str,
        status: str,
        confirmed_by: str,
        authorization_note: str,
        idempotency_key: str,
        classification: str | None = None,
        order_intent_id: str | None = None,
        expected_version: int | None = None,
        case_id: str | None = None,
        decision_id: str | None = None,
        trade_plan_id: str | None = None,
        trade_plan_version: int | None = None,
    ) -> dict[str, Any]:
        """Append an exact Broker-activity link or truthful manual classification."""

        try:
            if confirmed_by not in {"user", "external_agent"}:
                raise ValueError("confirmed_by must be user or external_agent")
            request = ActivityAnnotationAppendInput.model_validate(
                {
                    "provider": provider,
                    "account_ref": account_ref,
                    "provider_transaction_id": provider_transaction_id,
                    "status": status,
                    "classification": classification,
                    "order_intent_id": order_intent_id,
                    "decision_id": decision_id,
                    "trade_plan_id": trade_plan_id,
                    "trade_plan_version": trade_plan_version,
                    "subject_id": case_id,
                    "actor": confirmed_by,
                    "authorization_note": authorization_note,
                    "idempotency_key": idempotency_key,
                    "expected_version": expected_version,
                }
            )
            value = container.services.activity_annotations.append_revision(request)
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH,
                sources=(),
                data=value,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def trade_cycle_override_append(
        root_cycle_id: str,
        operation: str,
        cycle_ids: tuple[str, ...],
        confirmed_by: str,
        authorization_note: str,
        idempotency_key: str,
        expected_version: int | None = None,
        activity_ids: tuple[str, ...] = (),
        split_groups: tuple[tuple[str, ...], ...] = (),
        target_cycle_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Append a user-confirmed split/merge/relink Cycle projection revision."""

        try:
            if confirmed_by not in {"user", "external_agent"}:
                raise ValueError("confirmed_by must be user or external_agent")
            request = TradeCycleOverrideAppendInput.model_validate(
                {
                    "root_cycle_id": root_cycle_id,
                    "operation": operation,
                    "cycle_ids": cycle_ids,
                    "activity_ids": activity_ids,
                    "split_groups": split_groups,
                    "target_cycle_id": target_cycle_id,
                    "note": note,
                    "actor": confirmed_by,
                    "authorization_note": authorization_note,
                    "idempotency_key": idempotency_key,
                    "expected_version": expected_version,
                }
            )
            projection = (
                container.services.account_transactions.project_trade_cycles_for_override(
                    TradeCycleQueryInput(limit=500)
                )
            )
            value = container.services.trade_cycle_overrides.append_revision(
                request, projection=projection
            )
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH,
                sources=(),
                data=value,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def behavior_review_run(
        period_kind: str,
        period_start: datetime,
        period_end: datetime,
        idempotency_key: str,
        strategy_code: str | None = None,
        strategy_version: str | None = None,
        horizon: str | None = None,
        instrument_ids: tuple[str, ...] = (),
        currency: str | None = None,
        cycle_ids: tuple[str, ...] = (),
        decision_ids: tuple[str, ...] = (),
        retro_run_ids: tuple[str, ...] = (),
        retro_review_ids: tuple[str, ...] = (),
        review_item_source_keys: tuple[str, ...] = (),
        subject_ids: tuple[str, ...] = (),
        action_items: tuple[dict[str, Any], ...] = (),
        source_read_complete: bool = True,
        source_error_code: str | None = None,
    ) -> dict[str, Any]:
        """Persist one deterministic period/cohort action recurrence review."""

        try:
            request = BehaviorReviewRunInput.model_validate(locals())
            value = container.services.behavior_reviews.run(request)
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.FRESH,
                sources=(),
                data=value,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        research_search=research_search,
        research_report_get=research_report_get,
        research_timeline_get=research_timeline_get,
        catalyst_agenda_get=catalyst_agenda_get,
        journal_append=journal_append,
        decision_record_append=decision_record_append,
        catalyst_agenda_manage=catalyst_agenda_manage,
        activity_annotation_append=activity_annotation_append,
        trade_cycle_override_append=trade_cycle_override_append,
        behavior_review_run=behavior_review_run,
    )
