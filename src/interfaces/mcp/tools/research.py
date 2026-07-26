"""Compact research-state operation adapters."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.research_context import ResearchContextBuildInput
from bootstrap import ApplicationContainer
from domain.common.actor import ActorContext
from interfaces.mcp.schemas import (
    InvestmentCaseArchiveInput,
    InvestmentCaseCreateInput,
    InvestmentCaseGetInput,
    InvestmentCaseListInput,
    ResearchStateGetInput,
    ResearchStateUpdateInput,
    ThesisHistoryGetInput,
    ThesisRevisionConfirmInput,
    ThesisRevisionProposeInput,
)
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_research_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact research-state operation adapters."""

    # ---------------------------------------------------------- Phase 1B research
    def investment_case_create(
        case_type: str,
        title: str,
        summary: str,
        confirmed_by: str,
        idempotency_key: str,
        primary_instrument_id: str | None = None,
        topic_tags: list[str] | None = None,
        linked_case_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a user-confirmed research file (Investment Case).

        COMPANY and CATALYST files are anchored to an objective Instrument. The Case
        is the durable research file around it; creating one does not confirm a Thesis.
        """
        try:
            inp = InvestmentCaseCreateInput.model_validate(
                {
                    "case_type": case_type,
                    "title": title,
                    "summary": summary,
                    "primary_instrument_id": primary_instrument_id,
                    "topic_tags": tuple(topic_tags or ()),
                    "linked_case_ids": tuple(linked_case_ids or ()),
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.investment_case_service.create_case(
                case_type=inp.case_type,
                title=inp.title,
                summary=inp.summary,
                primary_instrument_id=inp.primary_instrument_id,
                topic_tags=inp.topic_tags,
                linked_case_ids=inp.linked_case_ids,
                confirmed_by=inp.confirmed_by,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def investment_case_query(
        case_id: str | None = None,
        case_type: str | None = None,
        status: str | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get one research file, or list research files with filters."""
        try:
            if case_id is not None:
                get_input = InvestmentCaseGetInput.model_validate({"case_id": case_id})
                return container.investment_case_service.get_case(get_input.case_id).model_dump(
                    mode="json"
                )
            list_input = InvestmentCaseListInput.model_validate(
                {
                    "case_type": case_type,
                    "status": status,
                    "primary_instrument_id": primary_instrument_id,
                    "topic_tag": topic_tag,
                    "include_archived": include_archived,
                    "limit": limit,
                    "offset": offset,
                }
            )
            envelope = container.investment_case_service.list_cases(
                case_type=list_input.case_type,
                status=list_input.status,
                primary_instrument_id=list_input.primary_instrument_id,
                topic_tag=list_input.topic_tag,
                include_archived=list_input.include_archived,
                limit=list_input.limit,
                offset=list_input.offset,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def investment_case_archive(
        case_id: str,
        archived_reason: str,
        reviewed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Archive a research file without deleting its linked Instrument."""
        try:
            inp = InvestmentCaseArchiveInput.model_validate(
                {
                    "case_id": case_id,
                    "archived_reason": archived_reason,
                    "reviewed_by": reviewed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.investment_case_service.archive_case(
                inp.case_id,
                archived_reason=inp.archived_reason,
                reviewed_by=inp.reviewed_by,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def research_state_get(
        case_id: str,
        include_archived_theses: bool = False,
        include_watchlist: bool = True,
    ) -> dict[str, Any]:
        """Return a research file's current judgments, assumptions, and open questions."""
        try:
            inp = ResearchStateGetInput.model_validate(
                {
                    "case_id": case_id,
                    "include_archived_theses": include_archived_theses,
                    "include_watchlist": include_watchlist,
                }
            )
            envelope = container.research_state_query_service.get_state(
                inp.case_id,
                include_archived_theses=inp.include_archived_theses,
                include_watchlist=inp.include_watchlist,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def research_state_update(
        payload: dict[str, Any],
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        case_id: str | None = None,
        confirmation_mode: str = "strict_review",
    ) -> dict[str, Any]:
        """Propose research-state or versioned Trade Plan changes for confirmation."""
        try:
            inp = ResearchStateUpdateInput.model_validate(
                {
                    "case_id": case_id,
                    "payload": payload,
                    "confirmation_mode": confirmation_mode,
                    "proposed_by": proposed_by,
                    "proposed_by_rationale": proposed_by_rationale,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.thesis_revision_service.propose_state_update(
                case_id=inp.case_id,
                payload=inp.payload,
                confirmation_mode=inp.confirmation_mode,
                proposed_by=inp.proposed_by,
                proposed_by_rationale=inp.proposed_by_rationale,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def thesis_revision_propose(
        case_id: str,
        payload: dict[str, Any],
        proposed_by: str,
        proposed_by_rationale: str,
        idempotency_key: str,
        thesis_id: str | None = None,
        confirmation_mode: str = "strict_review",
    ) -> dict[str, Any]:
        """Propose a revision to an investment judgment in a research file."""
        try:
            inp = ThesisRevisionProposeInput.model_validate(
                {
                    "case_id": case_id,
                    "thesis_id": thesis_id,
                    "payload": payload,
                    "confirmation_mode": confirmation_mode,
                    "proposed_by": proposed_by,
                    "proposed_by_rationale": proposed_by_rationale,
                    "idempotency_key": idempotency_key,
                }
            )
            envelope = container.thesis_revision_service.propose_revision(
                case_id=inp.case_id,
                thesis_id=inp.thesis_id,
                payload=inp.payload,
                confirmation_mode=inp.confirmation_mode,
                proposed_by=inp.proposed_by,
                proposed_by_rationale=inp.proposed_by_rationale,
                idempotency_key=inp.idempotency_key,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def thesis_revision_confirm(
        candidate_id: str,
        reviewed_by: Literal["user", "external_agent", "codex"],
        action: Literal["confirm", "reject", "withdraw"] = "confirm",
        submitted_via: Literal["direct", "codex_chat"] = "direct",
        authorization_note: str | None = None,
        review_note: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply an explicit review decision; Codex may relay an exact user chat approval."""
        try:
            inp = ThesisRevisionConfirmInput.model_validate(
                {
                    "candidate_id": candidate_id,
                    "action": action,
                    "reviewed_by": reviewed_by,
                    "submitted_via": submitted_via,
                    "authorization_note": authorization_note,
                    "review_note": review_note,
                    "rejection_reason": rejection_reason,
                }
            )
            actor_context = (
                ActorContext.codex_chat_authorized(
                    request_id=f"mcp:{inp.action}:{inp.candidate_id}",
                    authorization_note=inp.authorization_note or "",
                )
                if inp.submitted_via == "codex_chat"
                else None
            )
            if inp.action == "confirm":
                return container.thesis_revision_service.confirm_candidate(
                    inp.candidate_id,
                    reviewed_by=inp.reviewed_by,
                    review_note=inp.review_note,
                    actor_context=actor_context,
                ).model_dump(mode="json")
            if inp.action == "reject":
                assert inp.rejection_reason is not None
                return container.thesis_revision_service.reject_candidate(
                    inp.candidate_id,
                    reviewed_by=inp.reviewed_by,
                    rejection_reason=inp.rejection_reason,
                    actor_context=actor_context,
                ).model_dump(mode="json")
            assert inp.review_note is not None
            return container.thesis_revision_service.withdraw_candidate(
                inp.candidate_id,
                reviewed_by=inp.reviewed_by,
                review_note=inp.review_note,
                actor_context=actor_context,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def thesis_history_get(thesis_id: str) -> dict[str, Any]:
        """Return append-only history for one investment judgment (Thesis)."""
        try:
            inp = ThesisHistoryGetInput.model_validate({"thesis_id": thesis_id})
            envelope = container.thesis_revision_service.get_revision_history(inp.thesis_id)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def research_context_build(
        case_id: str | None = None,
        instrument_id: str | None = None,
        since: datetime | None = None,
        token_budget: int = 4_000,
    ) -> dict[str, Any]:
        """Build one current durable research context package for a fresh thread."""
        try:
            inp = ResearchContextBuildInput.model_validate(
                {
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "since": since,
                    "token_budget": token_budget,
                }
            )
            return container.research_context_builder.build(inp).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        investment_case_create=investment_case_create,
        investment_case_query=investment_case_query,
        investment_case_archive=investment_case_archive,
        research_state_get=research_state_get,
        research_state_update=research_state_update,
        thesis_revision_propose=thesis_revision_propose,
        thesis_revision_confirm=thesis_revision_confirm,
        thesis_history_get=thesis_history_get,
        research_context_build=research_context_build,
    )
