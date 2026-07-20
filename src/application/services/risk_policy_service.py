"""Append-only risk-policy query and confirmed version update service."""

from __future__ import annotations

from application.dto.risk import RiskPolicyUpdateInput
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.risk_policy_repository import RiskPolicyRepository
from domain.common.errors import (
    IdempotencyConflict,
    RiskPolicyNotFound,
    RiskPolicyVersionConflict,
)
from domain.common.ids import EntityIdPrefix
from domain.risk.enums import RiskConfirmer
from domain.risk.models import RiskPolicy


class RiskPolicyService:
    def __init__(
        self,
        repository: RiskPolicyRepository,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = id_generator

    def get_current(self) -> RiskPolicy:
        policy = self._repository.get_current()
        if policy is None:
            raise RiskPolicyNotFound(
                "No risk policy exists; apply the Phase 2B migration",
                details={"required_migration": "0012_phase2b_risk_engine"},
            )
        return policy

    def update(self, request: RiskPolicyUpdateInput) -> RiskPolicy:
        existing = self._repository.get_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            if self._matches(existing, request):
                return existing
            raise IdempotencyConflict(
                "idempotency_key already belongs to a different risk policy update",
                details={"field": "idempotency_key"},
            )

        current = self.get_current()
        if current.version != request.expected_version:
            raise RiskPolicyVersionConflict(
                "expected_version does not match the current risk policy",
                details={
                    "expected_version": request.expected_version,
                    "current_version": current.version,
                },
            )
        policy = RiskPolicy(
            policy_id=self._ids.new(EntityIdPrefix.RISK_POLICY),
            version=current.version + 1,
            single_position_max_percent=request.single_position_max_percent,
            gross_exposure_max_percent=request.gross_exposure_max_percent,
            minimum_cash_percent=request.minimum_cash_percent,
            margin_usage_max_percent=request.margin_usage_max_percent,
            max_account_age_seconds=request.max_account_age_seconds,
            max_price_age_seconds=request.max_price_age_seconds,
            is_system_default=False,
            confirmed_by=RiskConfirmer(request.confirmed_by),
            created_at=self._clock.now(),
            idempotency_key=request.idempotency_key,
            schema_version=1,
        )
        return self._repository.append(policy)

    @staticmethod
    def _matches(policy: RiskPolicy, request: RiskPolicyUpdateInput) -> bool:
        requested_by = RiskConfirmer(request.confirmed_by)
        return (
            policy.single_position_max_percent
            == request.single_position_max_percent
            and policy.gross_exposure_max_percent
            == request.gross_exposure_max_percent
            and policy.minimum_cash_percent == request.minimum_cash_percent
            and policy.margin_usage_max_percent == request.margin_usage_max_percent
            and policy.max_account_age_seconds == request.max_account_age_seconds
            and policy.max_price_age_seconds == request.max_price_age_seconds
            and policy.confirmed_by == requested_by
            and policy.version == request.expected_version + 1
        )
