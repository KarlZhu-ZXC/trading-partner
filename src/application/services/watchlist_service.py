"""Watchlist propose-only writes and read queries."""

from __future__ import annotations

from datetime import datetime

from application.dto.research import (
    CandidateRevisionDTO,
    WatchlistCandidatePayload,
    WatchlistItemDTO,
    WatchlistListDTO,
)
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    candidate_to_dto,
    envelope_failure,
    envelope_success,
    propose_candidate,
)
from domain.common.enums import (
    CandidateKind,
    CandidateStatus,
    ConfirmationMode,
    Market,
    WatchlistItemStatus,
)
from domain.common.errors import InputValidationError
from domain.common.ids import EntityIdPrefix


class WatchlistService:
    """Watchlist formal rows land only after candidate confirm (PROPOSED on write APIs)."""

    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._redactor = secret_redactor

    def add_item(
        self,
        *,
        market: Market,
        symbol: str,
        display_name: str,
        thesis_hint: str,
        triggers: tuple[str, ...],
        case_id: str | None,
        expires_at: datetime | None,
        created_by: str,
        proposed_by_rationale: str = "Propose watchlist item",
        idempotency_key: str,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        """Propose a watchlist create candidate (PROPOSED only)."""
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            payload = WatchlistCandidatePayload(
                kind="watchlist_item",
                action="create",
                market=market,
                symbol=symbol.strip(),
                display_name=display_name.strip(),
                thesis_hint=thesis_hint.strip(),
                triggers=triggers,
                case_id=case_id,
                expires_at=expires_at,
            )
            with self._uow_factory() as uow:
                if case_id is not None:
                    uow.cases.get(case_id)
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.WATCHLIST_ITEM,
                    case_id=case_id,
                    thesis_id=None,
                    target_revision_no=None,
                    payload_model=payload,
                    confirmation_mode=confirmation_mode,
                    proposed_by=created_by,
                    proposed_by_rationale=proposed_by_rationale,
                    idempotency_key=idempotency_key,
                    status=CandidateStatus.PROPOSED,
                )
                if not is_dup:
                    uow.audit.append(
                        "phase1b.candidate.proposed",
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": CandidateKind.WATCHLIST_ITEM.value,
                            "case_id": case_id,
                            "proposed_by": created_by,
                        },
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(candidate),
                    warnings=(warn,) if warn is not None else (),
                    degraded=warn is not None,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def list_items(
        self,
        *,
        market: Market | None = None,
        status: WatchlistItemStatus | None = None,
        case_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ToolEnvelope[WatchlistListDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                items = uow.watchlist.list(
                    market=market,
                    status=status,
                    case_id=case_id,
                    limit=limit,
                    offset=offset,
                )
                data = WatchlistListDTO(items=WatchlistItemDTO.from_domain_list(items))
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def get_item(self, item_id: str) -> ToolEnvelope[WatchlistItemDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            with self._uow_factory() as uow:
                item = uow.watchlist.get(item_id)
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=WatchlistItemDTO.from_domain(item),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def update_status(
        self,
        item_id: str,
        *,
        new_status: WatchlistItemStatus,
        triggered_reason: str | None,
        promoted_to_case_id: str | None,
        reviewed_by: str,
        proposed_by_rationale: str = "Propose watchlist status update",
        idempotency_key: str,
        confirmation_mode: ConfirmationMode = ConfirmationMode.NORMAL,
        expires_at: datetime | None = None,
    ) -> ToolEnvelope[CandidateRevisionDTO]:
        """Propose a watchlist status update candidate (PROPOSED only)."""
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            if new_status == WatchlistItemStatus.PROMOTED_TO_CASE and not promoted_to_case_id:
                raise InputValidationError(
                    "PROMOTED_TO_CASE requires promoted_to_case_id",
                    details={"item_id": item_id},
                )
            if new_status == WatchlistItemStatus.TRIGGERED and (
                triggered_reason is None or not triggered_reason.strip()
            ):
                raise InputValidationError(
                    "TRIGGERED requires triggered_reason",
                    details={"item_id": item_id},
                )
            payload = WatchlistCandidatePayload(
                kind="watchlist_item",
                action="update_status",
                item_id=item_id,
                new_status=new_status,
                promoted_to_case_id=promoted_to_case_id,
                triggered_reason=triggered_reason,
                expires_at=expires_at,
            )
            with self._uow_factory() as uow:
                item = uow.watchlist.get(item_id)
                candidate, is_dup, warn = propose_candidate(
                    uow=uow,
                    clock=self._clock,
                    id_generator=self._id_generator,
                    kind=CandidateKind.WATCHLIST_ITEM,
                    case_id=item.case_id,
                    thesis_id=None,
                    target_revision_no=None,
                    payload_model=payload,
                    confirmation_mode=confirmation_mode,
                    proposed_by=reviewed_by,
                    proposed_by_rationale=proposed_by_rationale,
                    idempotency_key=idempotency_key,
                    status=CandidateStatus.PROPOSED,
                )
                if not is_dup:
                    uow.audit.append(
                        "phase1b.candidate.proposed",
                        {
                            "candidate_id": candidate.candidate_id,
                            "kind": CandidateKind.WATCHLIST_ITEM.value,
                            "item_id": item_id,
                            "new_status": new_status.value
                            if hasattr(new_status, "value")
                            else str(new_status),
                        },
                        request_id=request_id,
                    )
                    uow.commit()
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=candidate_to_dto(candidate),
                    warnings=(warn,) if warn is not None else (),
                    degraded=warn is not None,
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )
