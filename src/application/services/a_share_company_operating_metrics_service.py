"""Deterministic A-share company operating-metrics service (HOG-P0-001/002)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from application.dto.a_share import CompanyOperatingMetricsSnapshotDTO
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
)
from application.dto.provider_routing import ProviderSuccess, ToolDataPolicy
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import AShareCompanyOperatingMetricsProvider
from application.ports.category_provider import CategoryProvider
from application.services.a_share_market_structure_service import build_a_share_fingerprint
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType
from domain.a_share.models import CompanyOperatingMetricsSnapshot
from domain.common.enums import DataCategory, Market, ReliabilityLevel, VendorId
from domain.common.errors import DataContractError, TradingPartnerError
from domain.instruments.models import Instrument

_WARNING_MESSAGES = {
    "COMPANY_OPERATING_DOCUMENT_PARTIAL": (
        "One or more official disclosure documents failed download or parse; "
        "returned metrics come only from successfully parsed documents."
    ),
    "COMPANY_OPERATING_OBSERVATIONS_TRUNCATED": (
        "Observation rows exceeded the 200-row MCP bound; newest periods were retained."
    ),
    "COMPANY_OPERATING_UNAUDITED_SALES_BRIEF": (
        "Monthly operating / sales briefs are company-disclosed and typically unaudited."
    ),
    "COMPANY_OPERATING_ANNOUNCEMENT_SEARCH_PARTIAL": (
        "One or more bounded CNINFO announcement-title searches failed; returned "
        "documents come from successful searches only."
    ),
    "PUBLICATION_TIME_UNKNOWN_EXCLUDED": ("Records with unknown publication time excluded"),
}
_POLICY = ToolDataPolicy(
    tool_name="a_share_get_facts.company_operating_metrics",
    required_categories=(DataCategory.COMPANY_OPERATING_METRICS,),
    optional_categories=(),
    category_chain_overrides={
        DataCategory.COMPANY_OPERATING_METRICS: (VendorId.CNINFO,),
    },
)
_OPERATION = "a_share.company_operating_metrics.v1"


@dataclass(frozen=True, slots=True)
class AShareCompanyOperatingMetricsResult:
    ok: bool
    data: CompanyOperatingMetricsSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]


class AShareCompanyOperatingMetricsService:
    def __init__(self, router: ProviderRouter) -> None:
        self._router = router

    async def get_company_operating_metrics(
        self,
        instrument: Instrument,
        *,
        lookback_months: int,
        document_limit: int,
        metric_codes: tuple[str, ...],
        as_of: datetime,
    ) -> AShareCompanyOperatingMetricsResult:
        async def call(
            provider: CategoryProvider,
        ) -> ProviderSuccess[CompanyOperatingMetricsSnapshot]:
            if not isinstance(provider, AShareCompanyOperatingMetricsProvider):
                raise DataContractError(
                    "company-operating-metrics provider violates required protocol"
                )
            return await provider.get_company_operating_metrics(
                instrument,
                lookback_months=lookback_months,
                document_limit=document_limit,
                metric_codes=metric_codes,
                as_of=as_of,
            )

        routed = await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.COMPANY_OPERATING_METRICS,
            call=call,
            operation_name=_OPERATION,
            request_fingerprint=build_a_share_fingerprint(
                _OPERATION,
                instrument.instrument_id,
                {
                    "lookback_months": str(lookback_months),
                    "document_limit": str(document_limit),
                    "metric_codes": ",".join(metric_codes),
                },
                as_of,
            ),
            instrument=instrument,
            as_of=as_of,
            tool_policy=_POLICY,
            bypass_cache=True,
            cache_codec=None,
        )
        if not routed.ok or routed.value is None or routed.meta is None:
            return AShareCompanyOperatingMetricsResult(
                False,
                None,
                routed.warnings,
                routed.error or DataContractError("company-operating-metrics provider failed"),
                (),
            )
        success = ProviderSuccess(value=routed.value, meta=routed.meta)
        provenance = (
            component_provenance(
                AShareComponentType.COMPANY_OPERATING_METRICS,
                success.meta,
                success.value,
                empty_reliability=ReliabilityLevel.HIGH,
                empty_authoritative=True,
            ),
        )
        data = CompanyOperatingMetricsSnapshotDTO.from_domain(
            success.value,
            provenance=provenance_dtos(provenance),
            metric_codes=metric_codes,
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
        return AShareCompanyOperatingMetricsResult(True, data, warnings, None, provenance)
