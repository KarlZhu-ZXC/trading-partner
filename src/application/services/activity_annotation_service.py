"""Phase 4B Unlinked Activity projection and append-only annotations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from hashlib import sha256

from application.dto.account_transactions import AccountTransactionDTO
from application.dto.activity_annotations import (
    ActivityAnnotationAppendInput,
    ActivityAnnotationDTO,
    UnlinkedActivityDTO,
    UnlinkedActivityListDTO,
)
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.activity_annotation_repository import ActivityAnnotationRepository
from application.ports.broker_order_repository import BrokerOrderRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.review_item_service import ReviewItemService
from domain.common.enums import VendorId
from domain.common.errors import (
    InputValidationError,
    InvalidResearchLink,
    ResearchMemoryNotFound,
)
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.portfolio.enums import AccountTransactionKind, ActivityAnnotationStatus
from domain.portfolio.models import AccountTransaction, ActivityAnnotation

ResearchUowFactory = Callable[[], ResearchUnitOfWork]


def unlinked_activity_source_key(
    provider: VendorId | str,
    account_ref: str,
    provider_transaction_id: str,
) -> str:
    """Return the stable projection key for one exact provider activity."""

    provider_value = provider.value if isinstance(provider, VendorId) else str(provider)
    raw = f"UNLINKED_ACTIVITY:{provider_value}:{account_ref}:{provider_transaction_id}"
    if len(raw) <= 300:
        return raw
    digest = sha256(
        f"{provider_value}|{account_ref}|{provider_transaction_id}".encode()
    ).hexdigest()
    return f"UNLINKED_ACTIVITY:{digest}"


class ActivityAnnotationService:
    """Durable read/append boundary for activity links and classifications.

    Account facts are read through the existing normalized transaction port;
    research references are checked through the existing Research UoW.  The
    service never updates either fact source and all annotation revisions are
    appended by the dedicated repository.
    """

    def __init__(
        self,
        transactions: AccountTransactionRepository,
        annotations: ActivityAnnotationRepository,
        research_uow_factory: ResearchUowFactory | None,
        clock: Clock,
        id_generator: IdGenerator,
        review_items: ReviewItemService | None = None,
        broker_orders: BrokerOrderRepository | None = None,
    ) -> None:
        self._transactions = transactions
        self._annotations = annotations
        self._research_uow_factory = research_uow_factory
        self._clock = clock
        self._ids = id_generator
        self._review_items = review_items
        self._broker_orders = broker_orders

    def list_unlinked(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        account_refs: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> UnlinkedActivityListDTO:
        """List TRADE activities without an annotation, without queue materialization.

        The extra read row is intentional: reaching the requested limit means
        the source is only partially observed, so the reconciler cannot infer
        that a previously open item disappeared.
        """

        self._validate_filters(providers, account_refs, start, end, limit)
        raw = self._transactions.list(
            providers=providers,
            start=start,
            end=end,
            limit=limit + 1,
        )
        has_more = len(raw) > limit
        selected = raw[:limit]
        trades = tuple(item for item in selected if item.kind is AccountTransactionKind.TRADE)
        latest = self._annotations.list_latest(
            providers=providers,
            account_refs=account_refs,
            limit=None,
        )
        annotated_keys = {item.transaction_key for item in latest}
        unlinked = tuple(item for item in trades if item.transaction_key not in annotated_keys)

        activities = tuple(
            UnlinkedActivityDTO(
                source_key=unlinked_activity_source_key(
                    item.provider,
                    item.account_ref,
                    item.provider_transaction_id,
                ),
                transaction=AccountTransactionDTO.from_domain(item),
                review_item=None,
            )
            for item in unlinked
        )
        return UnlinkedActivityListDTO(
            activities=activities,
            has_more=has_more,
            observed_complete=not has_more,
            limitation_codes=(
                ("UNLINKED_ACTIVITY_LIMIT_REACHED",) if has_more else ()
            ),
        )

    # Naming used by scheduled/materialization callers.
    sync_unlinked = list_unlinked
    unlinked = list_unlinked

    def list_annotations(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        account_refs: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[ActivityAnnotationDTO, ...]:
        values = self._annotations.list_latest(
            providers=providers,
            account_refs=account_refs,
            limit=limit,
        )
        return tuple(ActivityAnnotationDTO.from_domain(item) for item in values)

    list = list_annotations

    def append_revision(
        self,
        request: ActivityAnnotationAppendInput | None = None,
        **values: object,
    ) -> ActivityAnnotationDTO:
        """Append one exact activity link/classification revision.

        ``request`` is accepted for Console-style callers while keyword values
        keep the service convenient for direct application tests.
        """

        if request is None:
            request = ActivityAnnotationAppendInput.model_validate(values)
        elif values:
            raise InputValidationError("request and keyword annotation values cannot be mixed")

        transaction = self._find_transaction(
            provider=request.provider,
            account_ref=request.account_ref,
            provider_transaction_id=request.provider_transaction_id,
        )
        if request.order_intent_id is not None:
            if self._broker_orders is None:
                raise InvalidResearchLink("Broker order link repository is unavailable")
            order = self._broker_orders.get(request.order_intent_id)
            if order is None:
                raise InvalidResearchLink("referenced Broker order intent does not exist")
            if order.account_ref != transaction.account_ref:
                raise InvalidResearchLink("Broker order intent belongs to another account")
            if order.instrument_id != transaction.instrument_id:
                raise InvalidResearchLink("Broker order intent belongs to another Instrument")
        subject_id = self._validate_research_links(request)
        latest = self._annotations.get_latest(
            provider=request.provider,
            account_ref=request.account_ref,
            provider_transaction_id=request.provider_transaction_id,
        )
        current_version = latest.version if latest is not None else 0
        annotation = ActivityAnnotation(
            annotation_id=self._ids.new(EntityIdPrefix.ACTIVITY_ANNOTATION),
            provider=request.provider,
            account_ref=request.account_ref,
            provider_transaction_id=request.provider_transaction_id,
            version=current_version + 1,
            status=request.status,
            decision_id=request.decision_id,
            trade_plan_id=request.trade_plan_id,
            trade_plan_version=request.trade_plan_version,
            subject_id=subject_id,
            note=request.note,
            classification=request.classification,
            order_intent_id=request.order_intent_id,
            actor=request.actor,
            authorization_note=request.authorization_note,
            idempotency_key=request.idempotency_key,
            created_at=self._clock.now(),
        )
        stored = self._annotations.append(
            annotation,
            expected_version=request.expected_version,
        )
        # ``transaction`` is intentionally touched above only to validate the
        # exact natural key; no field from it is copied into the annotation.
        _ = transaction
        return ActivityAnnotationDTO.from_domain(stored)

    append = append_revision

    def list_revisions(
        self,
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> tuple[ActivityAnnotationDTO, ...]:
        values = self._annotations.list_revisions(
            provider=provider,
            account_ref=account_ref,
            provider_transaction_id=provider_transaction_id,
        )
        return tuple(ActivityAnnotationDTO.from_domain(item) for item in values)

    def _validate_filters(
        self,
        providers: tuple[VendorId, ...],
        account_refs: tuple[str, ...],
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> None:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise InputValidationError("limit must be an int in [1, 500]")
        if len(providers) != len(set(providers)):
            raise InputValidationError("providers must be unique")
        if len(account_refs) != len(set(account_refs)):
            raise InputValidationError("account_refs must be unique")
        if any(not value.strip() for value in account_refs):
            raise InputValidationError("account_refs must be non-blank")
        if start is not None:
            require_aware_datetime(start, field_name="start")
        if end is not None:
            require_aware_datetime(end, field_name="end")
        if start is not None and end is not None and start > end:
            raise InputValidationError("start must be <= end")

    def _find_transaction(
        self,
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> AccountTransaction:
        values = self._transactions.list(
            providers=(provider,),
            start=None,
            end=None,
            limit=None,
        )
        for item in values:
            if (
                item.provider is provider
                and item.account_ref == account_ref
                and item.provider_transaction_id == provider_transaction_id
            ):
                return item
        raise InputValidationError(
            "account transaction does not exist",
            details={
                "provider": provider.value,
                "account_ref": account_ref,
                "provider_transaction_id": provider_transaction_id,
            },
        )

    def _validate_research_links(self, request: ActivityAnnotationAppendInput) -> str | None:
        if request.status is not ActivityAnnotationStatus.LINKED_DECISION_PLAN:
            # A caller may optionally keep a non-trading activity under a
            # Research Subject; only Decision/Plan references require the
            # stronger same-subject validation below.
            return request.subject_id
        if self._research_uow_factory is None:
            raise InvalidResearchLink("research links require a Research Unit of Work")

        subject_ids: list[str] = []
        with self._research_uow_factory() as uow:
            if request.decision_id is not None:
                try:
                    decision = uow.decisions.get(request.decision_id)
                except ResearchMemoryNotFound as exc:
                    raise InvalidResearchLink(
                        "referenced Decision does not exist",
                        details={"decision_id": request.decision_id},
                    ) from exc
                subject_ids.append(decision.subject_id)

            if request.trade_plan_id is not None and request.trade_plan_version is not None:
                plan = uow.trade_plans.get_version(
                    request.trade_plan_id,
                    request.trade_plan_version,
                )
                if plan is None:
                    raise InvalidResearchLink(
                        "referenced Trade Plan version does not exist",
                        details={
                            "trade_plan_id": request.trade_plan_id,
                            "trade_plan_version": request.trade_plan_version,
                        },
                    )
                subject_ids.append(plan.subject_id)

        if len(set(subject_ids)) > 1:
            raise InvalidResearchLink(
                "Decision and Trade Plan must belong to the same Research Subject"
            )
        subject_id = subject_ids[0] if subject_ids else request.subject_id
        if subject_id is None:
            raise InvalidResearchLink("linked activity annotation requires subject_id")
        if request.subject_id is not None and request.subject_id != subject_id:
            raise InvalidResearchLink(
                "Decision or Trade Plan does not belong to the supplied Research Subject",
                details={"subject_id": request.subject_id, "linked_subject_id": subject_id},
            )
        return subject_id
