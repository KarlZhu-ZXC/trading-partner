"""MCP-facing coordinator for the read-only Phase 2B Portfolio Risk Engine."""

from __future__ import annotations

from datetime import datetime

from application.dto.risk import (
    RiskCheckInput,
    RiskCheckResultDTO,
    RiskPolicyDTO,
    RiskPolicyUpdateInput,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services._router_envelope_support import exception_envelope
from application.services.risk_engine_service import RiskEngineService
from application.services.risk_policy_service import RiskPolicyService
from domain.common.enums import Freshness, SourceRole, VendorId
from domain.common.ids import EntityIdPrefix
from domain.portfolio.models import AccountSnapshot

_WARNING_MESSAGES = {
    "RISK_POLICY_DEFAULT_UNCONFIRMED": (
        "Risk limits are system defaults and have not been explicitly confirmed."
    ),
    "FX_CONVERSION_UNAVAILABLE": (
        "Cross-currency aggregation was not evaluated because no explicit FX facts exist."
    ),
    "PRICE_TIME_UNAVAILABLE": "At least one valued position has no price timestamp.",
    "ACCOUNT_NAV_UNAVAILABLE": "Account net asset value is unavailable.",
    "ACCOUNT_NAV_OR_CASH_UNAVAILABLE": "Cash ratio could not be evaluated.",
    "ACCOUNT_NAV_OR_MARGIN_UNAVAILABLE": "Margin ratio could not be evaluated.",
    "MISSING_MARKET_VALUE": "At least one position has no market value.",
    "NO_VALUED_POSITIONS": "No valued positions were available for concentration checks.",
}


class RiskToolCoordinator:
    def __init__(
        self,
        engine: RiskEngineService,
        policies: RiskPolicyService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._engine = engine
        self._policies = policies
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    def get_policy(self) -> ToolEnvelope[RiskPolicyDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            policy = self._policies.get_current()
            warnings = self._policy_warnings(policy.is_system_default)
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=policy.created_at,
                fetched_at=now,
                freshness=Freshness.FRESH,
                sources=(
                    SourceReference(
                        name="risk_policy_database",
                        role=SourceRole.PRIMARY,
                        retrieved_at=now,
                    ),
                ),
                data=RiskPolicyDTO.from_domain(policy),
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, now, exc)

    def update_policy(
        self, request: RiskPolicyUpdateInput
    ) -> ToolEnvelope[RiskPolicyDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        now = self._clock.now()
        try:
            policy = self._policies.update(request)
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=policy.created_at,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH,
                sources=(
                    SourceReference(
                        name="risk_policy_database",
                        role=SourceRole.PRIMARY,
                        retrieved_at=self._clock.now(),
                    ),
                ),
                data=RiskPolicyDTO.from_domain(policy),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, now, exc)

    async def check(
        self, request: RiskCheckInput
    ) -> ToolEnvelope[RiskCheckResultDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.as_of or self._clock.now()
        try:
            result, snapshots = await self._engine.check(
                request,
                effective_as_of=as_of,
            )
            warnings = tuple(
                WarningInfo(
                    code=code,
                    message=_WARNING_MESSAGES.get(
                        code, "A source or risk-evaluation data-quality warning applies."
                    ),
                    details={},
                )
                for code in result.data_quality_codes
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=result.as_of,
                fetched_at=max(item.fetched_at for item in snapshots),
                freshness=Freshness.UNKNOWN,
                sources=self._account_sources(snapshots),
                data=RiskCheckResultDTO.from_domain(result),
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    @staticmethod
    def _policy_warnings(is_system_default: bool) -> tuple[WarningInfo, ...]:
        if not is_system_default:
            return ()
        code = "RISK_POLICY_DEFAULT_UNCONFIRMED"
        return (WarningInfo(code=code, message=_WARNING_MESSAGES[code], details={}),)

    @staticmethod
    def _account_sources(
        snapshots: tuple[AccountSnapshot, ...],
    ) -> tuple[SourceReference, ...]:
        roles = {
            VendorId.SCHWAB: SourceRole.PRIMARY,
            VendorId.MOOMOO: SourceRole.PRIMARY,
            VendorId.MANUAL_CSV: SourceRole.SUPPLEMENTAL,
        }
        return tuple(
            SourceReference(
                name=vendor.value,
                role=roles.get(vendor, SourceRole.SUPPLEMENTAL),
                retrieved_at=max(
                    item.fetched_at for item in snapshots if item.provider is vendor
                ),
            )
            for vendor in dict.fromkeys(item.provider for item in snapshots)
        )

    def _failure[T](
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[T]:
        return exception_envelope(
            request_id=request_id,
            as_of=as_of,
            exc=exc,
            clock=self._clock,
            redactor=self._redactor,
        )
