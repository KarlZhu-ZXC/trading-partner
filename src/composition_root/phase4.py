"""Compose the reuse-first Phase 4 application collaborators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from application.ports.account_transaction_provider import AccountTransactionProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.activity_annotation_service import ActivityAnnotationService
from application.services.behavior_review_service import BehaviorReviewService
from application.services.daily_equity_materialization_service import (
    DailyEquityMaterializationService,
)
from application.services.review_item_service import ReviewItemService
from application.services.trade_cycle_override_service import TradeCycleOverrideService
from domain.common.enums import VendorId
from infrastructure.composition.persistence import PersistenceInfrastructure


@dataclass(frozen=True, slots=True)
class Phase4ServiceBundle:
    account_transactions: AccountTransactionCoordinator
    activity_annotations: ActivityAnnotationService
    trade_cycle_overrides: TradeCycleOverrideService
    behavior_reviews: BehaviorReviewService
    daily_equity: DailyEquityMaterializationService


def build_phase4_services(
    *,
    providers: Mapping[VendorId, AccountTransactionProvider],
    persistence: PersistenceInfrastructure,
    clock: Clock,
    id_generator: IdGenerator,
    secret_redactor: SecretRedactor,
    review_items: ReviewItemService,
) -> Phase4ServiceBundle:
    return Phase4ServiceBundle(
        account_transactions=AccountTransactionCoordinator(
            providers,
            persistence.account_transactions,
            persistence.account_snapshots,
            clock,
            id_generator,
            secret_redactor,
            persistence.research_uow_factory,
            persistence.activity_annotations,
            persistence.trade_cycle_overrides,
            persistence.daily_equity,
        ),
        activity_annotations=ActivityAnnotationService(
            transactions=persistence.account_transactions,
            annotations=persistence.activity_annotations,
            research_uow_factory=persistence.research_uow_factory,
            clock=clock,
            id_generator=id_generator,
            review_items=review_items,
            broker_orders=persistence.broker_orders,
        ),
        trade_cycle_overrides=TradeCycleOverrideService(
            persistence.trade_cycle_overrides, clock, id_generator
        ),
        behavior_reviews=BehaviorReviewService(
            persistence.behavior_reviews, clock, id_generator
        ),
        daily_equity=DailyEquityMaterializationService(
            persistence.daily_equity,
            activation_repository=persistence.journal_activation,
            clock=clock,
        ),
    )


__all__ = ["Phase4ServiceBundle", "build_phase4_services"]
