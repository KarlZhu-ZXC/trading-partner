"""Deterministic A-share industry-cycle facts service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from application.dto.a_share import IndustryCycleSnapshotDTO
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
)
from application.dto.provider_routing import ProviderSuccess, ToolDataPolicy
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import AShareIndustryCycleProvider
from application.ports.category_provider import CategoryProvider
from application.ports.industry_metric_repository import IndustryMetricRepository
from application.services.a_share_market_structure_service import build_a_share_fingerprint
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType
from domain.a_share.models import IndustryCycleSnapshot, IndustryMetricObservation
from domain.common.enums import DataCategory, Market, ReliabilityLevel, VendorId
from domain.common.errors import DataContractError, TradingPartnerError

_WARNING_MESSAGES = {
    "HOG_CYCLE_MONTHLY_PRICE_FREQUENCY": (
        "National price and feed observations are monthly, not real-time."
    ),
    "HOG_CYCLE_CAPACITY_PERIODIC": (
        "National capacity publications may be quarterly, half-year, or annual; "
        "each observation discloses its period basis."
    ),
    "HOG_CYCLE_NOT_COMPANY_COST": (
        "National feed and price observations are not company-specific production costs."
    ),
    "HOG_CYCLE_FUTURES_CURVE_UNAVAILABLE": (
        "Live-hog futures term structure is not included in this snapshot."
    ),
    "HOG_CYCLE_HISTORY_PARTIAL": (
        "The official online archive did not yield every requested monthly observation; "
        "returned periods are explicit and missing months were not interpolated."
    ),
}
_POLICY = ToolDataPolicy(
    tool_name="a_share_get_facts.industry_cycle",
    required_categories=(DataCategory.INDUSTRY_CYCLE,),
    optional_categories=(),
    category_chain_overrides={DataCategory.INDUSTRY_CYCLE: (VendorId.NAHS,)},
)
_OPERATION = "a_share.industry_cycle.v1"


@dataclass(frozen=True, slots=True)
class AShareIndustryCycleResult:
    ok: bool
    data: IndustryCycleSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]


class AShareIndustryCycleService:
    def __init__(
        self,
        router: ProviderRouter,
        repository: IndustryMetricRepository | None = None,
    ) -> None:
        self._router = router
        self._repository = repository

    async def get_hog_cycle(
        self,
        *,
        lookback_months: int,
        as_of: datetime,
        view: str = "compact",
        metric_codes: tuple[str, ...] = (),
        offset: int = 0,
        limit: int = 50,
    ) -> AShareIndustryCycleResult:
        # Provider/repository retain the full requested lookback; view/metric_codes/
        # offset/limit only bound the MCP payload after durable history is resolved.
        async def call(provider: CategoryProvider) -> ProviderSuccess[IndustryCycleSnapshot]:
            if not isinstance(provider, AShareIndustryCycleProvider):
                raise DataContractError("industry-cycle provider violates required protocol")
            return await provider.get_hog_cycle(
                lookback_months=lookback_months,
                as_of=as_of,
            )

        routed = await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.INDUSTRY_CYCLE,
            call=call,
            operation_name=_OPERATION,
            request_fingerprint=build_a_share_fingerprint(
                _OPERATION,
                "hog",
                {"lookback_months": str(lookback_months)},
                as_of,
            ),
            instrument=None,
            as_of=as_of,
            tool_policy=_POLICY,
            bypass_cache=True,
            cache_codec=None,
        )
        if not routed.ok or routed.value is None or routed.meta is None:
            return AShareIndustryCycleResult(
                False,
                None,
                routed.warnings,
                routed.error or DataContractError("industry-cycle provider failed"),
                (),
            )
        success = ProviderSuccess(value=routed.value, meta=routed.meta)
        if self._repository is not None:
            self._repository.upsert(
                cycle=success.value.cycle,
                dataset_code=success.value.dataset_code,
                observations=success.value.observations,
                ingested_at=success.meta.fetched_at,
            )
            stored = self._repository.list_visible(
                cycle=success.value.cycle,
                as_of=as_of,
            )
            monthly_periods = sorted(
                {item.period_end for item in stored if item.frequency.value == "monthly"}
            )[-lookback_months:]
            selected_months = frozenset(monthly_periods)
            latest_periodic: dict[str, IndustryMetricObservation] = {}
            for item in stored:
                if item.frequency.value != "monthly":
                    latest_periodic[item.metric_code] = item
            observations = tuple(
                sorted(
                    (
                        item
                        for item in stored
                        if (
                            item.frequency.value == "monthly"
                            and item.period_end in selected_months
                        )
                        or latest_periodic.get(item.metric_code) is item
                    ),
                    key=lambda item: (item.period_end, item.metric_code),
                )
            )
            if observations:
                success = ProviderSuccess(
                    value=IndustryCycleSnapshot(
                        cycle=success.value.cycle,
                        dataset_code=success.value.dataset_code,
                        as_of=success.value.as_of,
                        observations=observations,
                        missing_components=success.value.missing_components,
                    ),
                    meta=success.meta,
                )
        provenance = (
            component_provenance(
                AShareComponentType.INDUSTRY_CYCLE,
                success.meta,
                success.value,
                empty_reliability=ReliabilityLevel.HIGH,
                empty_authoritative=True,
            ),
        )
        shaped_view: Literal["compact", "series"] = (
            "series" if view == "series" else "compact"
        )
        data = IndustryCycleSnapshotDTO.from_domain(
            success.value,
            provenance=provenance_dtos(provenance),
            view=shaped_view,
            metric_codes=metric_codes,
            offset=offset,
            limit=limit,
        )
        validate_data_provenance(data, provenance)
        domain_warnings = tuple(
            WarningInfo(code=code, message=_WARNING_MESSAGES[code], details={})
            for code in success.meta.warnings
            if code in _WARNING_MESSAGES
        )
        warnings = tuple(
            {item.code: item for item in (*routed.warnings, *domain_warnings)}.values()
        )
        return AShareIndustryCycleResult(True, data, warnings, None, provenance)
