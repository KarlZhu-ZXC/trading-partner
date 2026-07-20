"""External research report search product service (Phase 1E E3).

Not to be confused with internal ``research_search`` (Phase 1C memory FTS).
Does not archive reports into ResearchReport/Evidence.

Input contract (fail before Router/network):
- ``text``: ``None`` or ``str`` max 500; blank whitespace is absent
- ``industry_code``: ``None`` or ``str`` max 64 (domain industry_code); blank absent
- ``instrument``: ``None`` or ``Instrument`` with market ``A_SHARE`` and asset type
  allowed by ``A_SHARE_TOOL_ASSET_SUPPORT["reports"]`` (``OPTION`` rejected)
- ``published_from`` / ``published_to``: exact ``date`` (not ``datetime``)
- ``include_consensus``: exact ``bool``
- ``limit`` / ``offset``: exact non-bool ints with range rules
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

from application.dto.a_share import (
    AnalystReportItemDTO,
    ConsensusEstimateDTO,
    ResearchReportSearchDTO,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import AShareResearchProvider
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.secret_redactor import SecretRedactor
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import (
    A_SHARE_TOOL_ASSET_SUPPORT,
    REPORTS_POLICY,
)
from application.services.component_settlement import settle_router_component
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType
from domain.a_share.models import AnalystReportItem, ConsensusEstimate
from domain.common.enums import DataCategory, Market
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument

OP_REPORTS = "a_share.reports.v1"
OP_CONSENSUS = "a_share.consensus.v1"

_WS_RE = re.compile(r"\s+")
# Align with domain industry_code max (IndustryPerformanceRow) and free-text cap.
_REPORT_TEXT_MAX = 500
_INDUSTRY_CODE_MAX = 64


@dataclass(frozen=True, slots=True)
class ResearchReportSearchResult:
    ok: bool
    data: ResearchReportSearchDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise DataContractError(
                "ok must be exact bool",
                details={"field": "ok", "rule": "type", "type": type(self.ok).__name__},
            )
        validate_provenance_tuple(
            self.provenance,
            order=(AShareComponentType.REPORTS, AShareComponentType.CONSENSUS),
        )
        if not isinstance(self.warnings, tuple):
            raise DataContractError(
                "warnings must be a tuple of WarningInfo",
                details={
                    "field": "warnings",
                    "rule": "type",
                    "type": type(self.warnings).__name__,
                },
            )
        for idx, warning in enumerate(self.warnings):
            if not isinstance(warning, WarningInfo):
                raise DataContractError(
                    "warnings elements must be WarningInfo",
                    details={"field": "warnings", "index": idx, "rule": "type"},
                )
        if self.ok:
            if self.data is None:
                raise DataContractError("ok=True requires data non-None")
            if self.error is not None:
                raise DataContractError(
                    "ResearchReportSearchResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            validate_data_provenance(self.data, self.provenance)
        else:
            if self.data is not None:
                raise DataContractError("ok=False requires data None")
            if self.error is None or not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "ResearchReportSearchResult ok=False requires typed TradingPartnerError",
                    details={
                        "field": "error",
                        "rule": "ok_false_error_required",
                        "type": type(self.error).__name__,
                    },
                )


def normalize_report_search_text(text: str) -> str:
    """Normalize search text before hashing (casefold + collapse whitespace)."""
    return _WS_RE.sub(" ", text.strip()).casefold()


def report_text_fingerprint_hash(text: str, *, redactor: SecretRedactor) -> str:
    """SHA-256 of redacted normalized text — never raw search text."""
    normalized = normalize_report_search_text(text)
    redacted = redactor.redact_text(normalized)
    return hashlib.sha256(redacted.encode("utf-8")).hexdigest()


class ResearchReportSearchService:
    """Router-backed external report search + optional consensus composition."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        secret_redactor: SecretRedactor,
        reports_codec: ProviderCacheCodec[tuple[AnalystReportItem, ...]],
        consensus_codec: ProviderCacheCodec[tuple[ConsensusEstimate, ...]],
    ) -> None:
        if router is None or clock is None or secret_redactor is None:
            raise DataContractError(
                "router, clock, and secret_redactor are required",
                details={"field": "dependencies", "rule": "required"},
            )
        for name, codec in (
            ("reports_codec", reports_codec),
            ("consensus_codec", consensus_codec),
        ):
            if codec is None or not hasattr(codec, "codec_id"):
                raise DataContractError(
                    f"{name} must be a ProviderCacheCodec",
                    details={"field": name, "rule": "required"},
                )
        self._router = router
        self._clock = clock
        self._redactor = secret_redactor
        self._reports_codec = reports_codec
        self._consensus_codec = consensus_codec

    def _validate_inputs(
        self,
        *,
        text: object,
        instrument: object,
        industry_code: object,
        published_from: object,
        published_to: object,
        include_consensus: object,
        limit: object,
        offset: object,
    ) -> tuple[
        str | None,
        Instrument | None,
        str | None,
        date | None,
        date | None,
        bool,
        int,
        int,
    ]:
        if text is not None and not isinstance(text, str):
            raise DataContractError(
                "text must be str or None",
                details={"field": "text", "rule": "type"},
            )
        effective_text: str | None = None
        if isinstance(text, str):
            if len(text) > _REPORT_TEXT_MAX:
                raise DataContractError(
                    f"text must be at most {_REPORT_TEXT_MAX} characters",
                    details={"field": "text", "rule": "max_length"},
                )
            if text.strip():
                effective_text = text.strip()

        if industry_code is not None and not isinstance(industry_code, str):
            raise DataContractError(
                "industry_code must be str or None",
                details={"field": "industry_code", "rule": "type"},
            )
        effective_industry: str | None = None
        if isinstance(industry_code, str):
            stripped = industry_code.strip()
            if stripped:
                if len(stripped) > _INDUSTRY_CODE_MAX:
                    raise DataContractError(
                        f"industry_code must be at most {_INDUSTRY_CODE_MAX} characters",
                        details={"field": "industry_code", "rule": "max_length"},
                    )
                effective_industry = stripped

        if instrument is not None and not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument or None",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument is not None:
            if instrument.market is not Market.A_SHARE:
                raise DataContractError(
                    "instrument market must be A_SHARE",
                    details={"field": "instrument", "rule": "market"},
                )
            support = A_SHARE_TOOL_ASSET_SUPPORT["reports"].get(instrument.asset_type)
            if support == "reject" or support is None:
                raise DataContractError(
                    "asset type not supported for reports",
                    details={
                        "field": "instrument",
                        "rule": "asset_support",
                        "asset_type": instrument.asset_type.value,
                    },
                )

        if published_from is not None and type(published_from) is not date:
            raise DataContractError(
                "published_from must be exact date or None",
                details={
                    "field": "published_from",
                    "rule": "type",
                    "type": type(published_from).__name__,
                },
            )
        if published_to is not None and type(published_to) is not date:
            raise DataContractError(
                "published_to must be exact date or None",
                details={
                    "field": "published_to",
                    "rule": "type",
                    "type": type(published_to).__name__,
                },
            )
        if (
            published_from is not None
            and published_to is not None
            and published_to < published_from
        ):
            raise DataContractError(
                "published_to must be >= published_from",
                details={"field": "published_to", "rule": "range_order"},
            )

        if type(include_consensus) is not bool:
            raise DataContractError(
                "include_consensus must be exact bool",
                details={
                    "field": "include_consensus",
                    "rule": "type",
                    "type": type(include_consensus).__name__,
                },
            )

        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise DataContractError(
                "offset must be a nonnegative int",
                details={"field": "offset", "rule": "nonnegative"},
            )

        if effective_text is None and instrument is None and effective_industry is None:
            raise DataContractError(
                "at least one of text, instrument, or industry_code is required",
                details={"field": "filters", "rule": "required"},
            )

        return (
            effective_text,
            instrument,
            effective_industry,
            published_from,
            published_to,
            include_consensus,
            limit,
            offset,
        )

    def _validate_reports(
        self,
        success: ProviderSuccess[tuple[AnalystReportItem, ...]],
        *,
        as_of: datetime,
        published_from: date | None,
        published_to: date | None,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.RESEARCH_REPORTS:
            raise DataContractError(
                "reports meta.category must be RESEARCH_REPORTS",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of AnalystReportItem",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        seen_keys: set[str] = set()
        prev_sort: tuple[float, str] | None = None
        for idx, item in enumerate(success.value):
            if not isinstance(item, AnalystReportItem):
                raise DataContractError(
                    "report elements must be AnalystReportItem",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if item.report_key in seen_keys:
                raise DataContractError(
                    "duplicate report_key in report search results",
                    details={
                        "field": "report_key",
                        "index": idx,
                        "rule": "unique",
                    },
                )
            seen_keys.add(item.report_key)
            if item.published_at > as_of:
                raise DataContractError(
                    "report published_at must be <= as_of",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            if published_from is not None and item.published_at.date() < published_from:
                raise DataContractError(
                    "report published_at before published_from",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "range",
                    },
                )
            if published_to is not None and item.published_at.date() > published_to:
                raise DataContractError(
                    "report published_at after published_to",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "range",
                    },
                )
            sort_key = (-item.published_at.timestamp(), item.report_key)
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "reports must be sorted published_at desc, report_key asc",
                    details={
                        "field": "published_at",
                        "index": idx,
                        "rule": "sorted",
                    },
                )
            prev_sort = sort_key

    def _validate_consensus(
        self,
        success: ProviderSuccess[tuple[ConsensusEstimate, ...]],
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.RESEARCH_REPORTS:
            raise DataContractError(
                "consensus meta.category must be RESEARCH_REPORTS",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of ConsensusEstimate",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        seen: set[tuple[int, str]] = set()
        prev_sort: tuple[int, str] | None = None
        for idx, item in enumerate(success.value):
            if not isinstance(item, ConsensusEstimate):
                raise DataContractError(
                    "consensus elements must be ConsensusEstimate",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            identity = (item.fiscal_year, item.metric)
            if identity in seen:
                raise DataContractError(
                    "consensus (fiscal_year, metric) must be unique",
                    details={
                        "field": "identity",
                        "index": idx,
                        "rule": "unique",
                    },
                )
            seen.add(identity)
            sort_key = (item.fiscal_year, item.metric)
            if prev_sort is not None and sort_key < prev_sort:
                raise DataContractError(
                    "consensus must be sorted by fiscal_year asc, metric asc",
                    details={
                        "field": "order",
                        "index": idx,
                        "rule": "sorted",
                    },
                )
            prev_sort = sort_key

    async def search(
        self,
        *,
        text: str | None = None,
        instrument: Instrument | None = None,
        industry_code: str | None = None,
        published_from: date | None = None,
        published_to: date | None = None,
        include_consensus: bool = True,
        as_of: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ResearchReportSearchResult:
        (
            effective_text,
            instrument,
            effective_industry,
            published_from,
            published_to,
            include_consensus,
            limit,
            offset,
        ) = self._validate_inputs(
            text=text,
            instrument=instrument,
            industry_code=industry_code,
            published_from=published_from,
            published_to=published_to,
            include_consensus=include_consensus,
            limit=limit,
            offset=offset,
        )

        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        effective_as_of = as_of if as_of is not None else now
        require_aware_datetime(effective_as_of, field_name="as_of")
        if effective_as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )

        params: dict[str, str] = {
            "limit": str(limit),
            "offset": str(offset),
        }
        if effective_text is not None:
            params["text_sha256"] = report_text_fingerprint_hash(
                effective_text, redactor=self._redactor
            )
        if effective_industry is not None:
            params["industry_code"] = effective_industry
        if published_from is not None:
            params["published_from"] = published_from.isoformat()
        if published_to is not None:
            params["published_to"] = published_to.isoformat()
        instrument_key = (
            instrument.instrument_id if instrument is not None else "market"
        )
        fingerprint = build_a_share_fingerprint(
            OP_REPORTS, instrument_key, params, effective_as_of
        )
        # Safety: raw text and secrets must never appear in fingerprint.
        if effective_text is not None and effective_text in fingerprint:
            raise DataContractError(
                "fingerprint must not contain raw search text",
                details={"field": "fingerprint", "rule": "no_raw_text"},
            )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[AnalystReportItem, ...]]:
            if not isinstance(adapter, AShareResearchProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.RESEARCH_REPORTS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.search_reports(
                text=effective_text,
                instrument=instrument,
                industry_code=effective_industry,
                published_from=published_from,
                published_to=published_to,
                limit=limit,
                offset=offset,
                as_of=effective_as_of,
            )

        def _validator(
            success: ProviderSuccess[tuple[AnalystReportItem, ...]],
        ) -> None:
            self._validate_reports(
                success,
                as_of=effective_as_of,
                published_from=published_from,
                published_to=published_to,
            )

        async with asyncio.TaskGroup() as task_group:
            report_task = task_group.create_task(
                settle_router_component(
                    self._router.execute(
                        market=Market.A_SHARE,
                        category=DataCategory.RESEARCH_REPORTS,
                        call=_call,
                        operation_name=OP_REPORTS,
                        request_fingerprint=fingerprint,
                        instrument=instrument,
                        as_of=effective_as_of,
                        tool_policy=REPORTS_POLICY,
                        bypass_cache=False,
                        cache_codec=self._reports_codec,
                        result_validator=_validator,
                    )
                )
            )
            consensus_task: (
                asyncio.Task[RouterExecutionResult[tuple[ConsensusEstimate, ...]]] | None
            ) = None
            if include_consensus and instrument is not None:
                consensus_task = task_group.create_task(
                    settle_router_component(
                        self._fetch_consensus(instrument, effective_as_of)
                    )
                )

        report_res = report_task.result()
        consensus_res = consensus_task.result() if consensus_task is not None else None

        warnings: list[WarningInfo] = []
        self._merge_router_warnings(warnings, report_res)

        # OPTIONAL category: empty/failure is not hard failure.
        # Adapter/cache must already satisfy deterministic order; do not re-sort
        # over a failed uniqueness/order contract that the validator rejected.
        reports: tuple[AnalystReportItem, ...] = ()
        if report_res.ok and report_res.value is not None:
            reports = report_res.value

        consensus: tuple[ConsensusEstimate, ...] = ()
        # Deterministic composition: consensus is scheduled only when eligible.
        if consensus_res is not None:
            if consensus_res.ok and consensus_res.value is not None:
                consensus = consensus_res.value
            self._merge_router_warnings(warnings, consensus_res)

        provenance_items: list[AShareComponentProvenance] = []
        if report_res.ok and report_res.value is not None and report_res.meta is not None:
            provenance_items.append(
                component_provenance(
                    AShareComponentType.REPORTS, report_res.meta, report_res.value
                )
            )
        if (
            include_consensus
            and instrument is not None
            and consensus_res is not None
            and consensus_res.ok
            and consensus_res.value is not None
            and consensus_res.meta is not None
        ):
            provenance_items.append(
                component_provenance(
                    AShareComponentType.CONSENSUS,
                    consensus_res.meta,
                    consensus_res.value,
                )
            )
        provenance = tuple(provenance_items)
        dto = ResearchReportSearchDTO(
            instrument_id=instrument.instrument_id if instrument is not None else None,
            industry_code=effective_industry,
            published_from=published_from,
            published_to=published_to,
            include_consensus=include_consensus,
            limit=limit,
            offset=offset,
            reports=tuple(AnalystReportItemDTO.from_domain(r) for r in reports),
            consensus=tuple(ConsensusEstimateDTO.from_domain(c) for c in consensus),
            provenance=provenance_dtos(provenance),
        )
        return ResearchReportSearchResult(
            ok=True,
            data=dto,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

    @staticmethod
    def _merge_router_warnings(
        warnings: list[WarningInfo],
        result: RouterExecutionResult[object],
    ) -> None:
        """Merge router result.warnings and elevate recognized meta.warnings once.

        Only ``PUBLICATION_TIME_UNKNOWN_EXCLUDED`` is elevated from meta (same
        contract as report-path / snapshot merge). Dedupes by WarningInfo
        identity for result.warnings and by code for the elevated publication
        warning.
        """
        for w in result.warnings:
            if w not in warnings:
                warnings.append(w)
        if result.meta is not None:
            for code in result.meta.warnings:
                if code == "PUBLICATION_TIME_UNKNOWN_EXCLUDED" and not any(
                    x.code == code for x in warnings
                ):
                    warnings.append(
                        WarningInfo(
                            code=code,
                            message="Records with unknown publication time excluded",
                            details={},
                        )
                    )

    async def _fetch_consensus(
        self, instrument: Instrument, as_of: datetime
    ) -> RouterExecutionResult[tuple[ConsensusEstimate, ...]]:
        fingerprint = build_a_share_fingerprint(
            OP_CONSENSUS, instrument.instrument_id, {}, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[ConsensusEstimate, ...]]:
            if not isinstance(adapter, AShareResearchProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.RESEARCH_REPORTS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_consensus(instrument, as_of=as_of)

        def _validator(
            success: ProviderSuccess[tuple[ConsensusEstimate, ...]],
        ) -> None:
            self._validate_consensus(success)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.RESEARCH_REPORTS,
            call=_call,
            operation_name=OP_CONSENSUS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=REPORTS_POLICY,
            bypass_cache=False,
            cache_codec=self._consensus_codec,
            result_validator=_validator,
        )
