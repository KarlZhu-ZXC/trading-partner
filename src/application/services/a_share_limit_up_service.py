"""A-share limit-up product aggregation service (Phase 1E E4b).

``AShareLimitUpService.get`` loads requested limit pools via ProviderRouter
(LIMIT_UP required; Eastmoney primary), optionally enriches ``reason_tags`` from
THS public limit-up pool without overwriting Eastmoney facts, and returns a
typed result wrapper.

Router ``result_validator`` rejects malicious adapter/cache payloads before DTO
conversion. Primary must be Eastmoney factual rows; THS enrichment is
LIMIT_UP-only editorial tags and never mutates factual summary counts.

E5 will wire public MCP tools; this module is not bootstrapped yet.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from application.dto.a_share import AShareLimitUpContextProductDTO, LimitUpContextDTO
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import AShareLimitUpProvider
from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import LIMIT_UP_POLICY
from application.services.component_settlement import settle_router_component
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType, LimitPoolType
from domain.a_share.models import LimitPoolEntry, LimitUpContext, LimitUpLadderRung
from domain.common.enums import DataCategory, Market, ReliabilityLevel, VendorId
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime

_SHANGHAI = ZoneInfo("Asia/Shanghai")

OP_LIMIT_CONTEXT = "a_share.limit_context.v1"
OP_LIMIT_REASON_TAGS = "a_share.limit_reason_tags.v1"

_DEFAULT_POOLS: tuple[LimitPoolType, ...] = tuple(LimitPoolType)
_POOL_ENUM_ORDER: tuple[LimitPoolType, ...] = tuple(LimitPoolType)
_POOL_INDEX: dict[LimitPoolType, int] = {p: i for i, p in enumerate(_POOL_ENUM_ORDER)}

# Optional THS enrichment only (does not replace Eastmoney facts).
_REASON_TAGS_POLICY = ToolDataPolicy(
    tool_name="a_share_get_limit_up_context.reason_tags",
    required_categories=(),
    optional_categories=(DataCategory.LIMIT_UP,),
    category_chain_overrides={DataCategory.LIMIT_UP: (VendorId.THS,)},
)

_PRIMARY_POLICY = ToolDataPolicy(
    tool_name=LIMIT_UP_POLICY.tool_name,
    required_categories=(DataCategory.LIMIT_UP,),
    optional_categories=(),
    category_chain_overrides={DataCategory.LIMIT_UP: (VendorId.EASTMONEY,)},
)

_ESTABLISHED_META_WARNING_CODES = frozenset(
    {
        "LOW_RELIABILITY_MARKET_SIGNAL",
        "PUBLICATION_TIME_UNKNOWN_EXCLUDED",
    }
)
_META_WARNING_MESSAGES: dict[str, str] = {
    "LOW_RELIABILITY_MARKET_SIGNAL": "Limit-up context carries low reliability",
    "PUBLICATION_TIME_UNKNOWN_EXCLUDED": (
        "Records with unknown publication time excluded"
    ),
}

_Role = Literal["primary", "enrichment"]


def _require_exact_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date (not datetime)",
            details={"field": field, "rule": "exact_date_type"},
        )
    return value


def _derive_ladder_and_max(
    limit_up_entries: tuple[LimitPoolEntry, ...],
) -> tuple[tuple[LimitUpLadderRung, ...], int | None]:
    by_count: dict[int, list[str]] = {}
    for entry in limit_up_entries:
        if entry.consecutive_limit_count is None:
            continue
        by_count.setdefault(entry.consecutive_limit_count, []).append(
            entry.instrument_id
        )
    if not by_count:
        return (), None
    rungs: list[LimitUpLadderRung] = []
    for count in sorted(by_count):
        ids = tuple(sorted(set(by_count[count])))
        rungs.append(
            LimitUpLadderRung(
                consecutive_limit_count=count,
                instrument_count=len(ids),
                instrument_ids=ids,
            )
        )
    return tuple(rungs), max(by_count)


@dataclass(frozen=True, slots=True)
class AShareLimitUpResult:
    """Typed result wrapper for limit-up product aggregation."""

    ok: bool
    data: AShareLimitUpContextProductDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(
            self.provenance,
            order=(
                AShareComponentType.LIMIT_CONTEXT,
                AShareComponentType.LIMIT_REASON_TAGS,
            ),
        )
        if type(self.ok) is not bool:
            raise DataContractError(
                "ok must be exact bool",
                details={"field": "ok", "rule": "type", "type": type(self.ok).__name__},
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
                raise DataContractError(
                    "AShareLimitUpResult ok=True requires data non-None",
                    details={"field": "data", "rule": "ok_true_data_required"},
                )
            if self.error is not None:
                raise DataContractError(
                    "AShareLimitUpResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            validate_data_provenance(self.data, self.provenance)
            if not self.provenance or (
                self.provenance[0].component is not AShareComponentType.LIMIT_CONTEXT
            ):
                raise DataContractError("successful limit-up result omits required provenance")
        else:
            if self.data is not None:
                raise DataContractError(
                    "AShareLimitUpResult ok=False requires data is None",
                    details={"field": "data", "rule": "ok_false_data_none"},
                )
            if self.error is None or not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "AShareLimitUpResult ok=False requires typed TradingPartnerError",
                    details={
                        "field": "error",
                        "rule": "ok_false_error_required",
                        "type": type(self.error).__name__,
                    },
                )


class AShareLimitUpService:
    """E4b product service: limit pools + optional THS reason-tag enrichment."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        calendar: AShareTradingCalendar,
        limit_context_codec: ProviderCacheCodec[LimitUpContext],
    ) -> None:
        if router is None or clock is None or calendar is None:
            raise DataContractError(
                "router, clock, and calendar are required",
                details={"field": "dependencies", "rule": "required"},
            )
        if limit_context_codec is None or not hasattr(limit_context_codec, "codec_id"):
            raise DataContractError(
                "limit_context_codec must be a ProviderCacheCodec",
                details={"field": "limit_context_codec", "rule": "required"},
            )
        self._router = router
        self._clock = clock
        self._calendar = calendar
        self._limit_context_codec = limit_context_codec

    def _resolve_pools(
        self, pools: tuple[LimitPoolType, ...]
    ) -> tuple[LimitPoolType, ...]:
        if not isinstance(pools, tuple):
            raise DataContractError(
                "pools must be a tuple of LimitPoolType",
                details={"field": "pools", "rule": "type"},
            )
        if not pools:
            return _DEFAULT_POOLS
        seen: set[LimitPoolType] = set()
        # Normalize to frozen enum order for deterministic output (caller subset).
        requested: set[LimitPoolType] = set()
        for pool in pools:
            if not isinstance(pool, LimitPoolType):
                raise DataContractError(
                    "pools elements must be LimitPoolType",
                    details={"field": "pools", "rule": "type"},
                )
            if pool in seen:
                raise DataContractError(
                    "pools must not contain duplicates",
                    details={"field": "pools", "rule": "unique"},
                )
            seen.add(pool)
            requested.add(pool)
        return tuple(p for p in LimitPoolType if p in requested)

    async def get(
        self,
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...] = (),
        as_of: datetime,
    ) -> AShareLimitUpResult:
        trade_date = _require_exact_date(trade_date, field="trade_date")
        require_aware_datetime(as_of, field_name="as_of")
        # Sample clock once for composition decisions (no stepping-clock drift).
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        if not self._calendar.is_trading_day(trade_date):
            raise DataContractError(
                "trade_date must be an A-share trading day",
                details={"field": "trade_date", "rule": "trading_day"},
            )
        as_of_local = as_of.astimezone(_SHANGHAI).date()
        if trade_date > as_of_local:
            raise DataContractError(
                "trade_date must not be later than the Asia/Shanghai local date of as_of",
                details={
                    "field": "trade_date",
                    "rule": "trade_date_not_after_as_of_local",
                },
            )
        ordered_pools = self._resolve_pools(pools)
        warnings: list[WarningInfo] = []

        primary_fp = build_a_share_fingerprint(
            OP_LIMIT_CONTEXT,
            "market",
            {
                "pools": ",".join(p.value for p in ordered_pools),
                "trade_date": trade_date.isoformat(),
            },
            as_of,
        )
        enrich_fp = build_a_share_fingerprint(
            OP_LIMIT_REASON_TAGS,
            "market",
            {
                "pools": LimitPoolType.LIMIT_UP.value,
                "trade_date": trade_date.isoformat(),
            },
            as_of,
        )

        async with asyncio.TaskGroup() as tg:
            primary_task = tg.create_task(
                settle_router_component(self._fetch_limit_context(
                    trade_date=trade_date,
                    pools=ordered_pools,
                    as_of=as_of,
                    tool_policy=_PRIMARY_POLICY,
                    fingerprint=primary_fp,
                    codec=self._limit_context_codec,
                    role="primary",
                ))
            )
            # Optional enrichment only when LIMIT_UP is in the request set.
            enrich_task: asyncio.Task[RouterExecutionResult[Any]] | None = None
            if LimitPoolType.LIMIT_UP in ordered_pools:
                enrich_task = tg.create_task(
                    settle_router_component(self._fetch_limit_context(
                        trade_date=trade_date,
                        pools=(LimitPoolType.LIMIT_UP,),
                        as_of=as_of,
                        tool_policy=_REASON_TAGS_POLICY,
                        fingerprint=enrich_fp,
                        codec=self._limit_context_codec,
                        role="enrichment",
                    ))
                )

        primary = primary_task.result()
        enrich = enrich_task.result() if enrich_task is not None else None
        provenance_items: list[AShareComponentProvenance] = []
        if primary.ok and primary.value is not None and primary.meta is not None:
            provenance_items.append(
                component_provenance(
                    AShareComponentType.LIMIT_CONTEXT,
                    primary.meta,
                    primary.value.entries,
                )
            )
        if (
            enrich is not None
            and enrich.ok
            and enrich.value is not None
            and enrich.meta is not None
        ):
            provenance_items.append(
                component_provenance(
                    AShareComponentType.LIMIT_REASON_TAGS,
                    enrich.meta,
                    enrich.value.entries,
                )
            )
        provenance = tuple(provenance_items)
        self._merge_warnings(warnings, primary)
        if enrich is not None:
            self._merge_warnings(warnings, enrich)
        if not primary.ok or primary.value is None:
            return AShareLimitUpResult(
                ok=False,
                data=None,
                warnings=tuple(warnings),
                error=primary.error
                or DataContractError(
                    "required limit-up context failed",
                    details={"rule": "required", "operation": OP_LIMIT_CONTEXT},
                ),
                provenance=provenance,
            )

        context: LimitUpContext = primary.value
        if not isinstance(context, LimitUpContext):
            return AShareLimitUpResult(
                ok=False,
                data=None,
                warnings=tuple(warnings),
                error=DataContractError(
                    "limit-up provider returned invalid value type",
                    details={"rule": "type"},
                ),
                provenance=provenance,
            )

        if enrich_task is not None:
            assert enrich is not None
            if enrich.ok and isinstance(enrich.value, LimitUpContext):
                # Merge fills empty reason_tags only; factual summary unchanged.
                context = self._merge_reason_tags(context, enrich.value)
            else:
                if not any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in warnings):
                    warnings.append(
                        WarningInfo(
                            code="PARTIAL_A_SHARE_SNAPSHOT",
                            message="Optional limit-up reason-tag enrichment failed",
                            details={"component": "reason_tags"},
                        )
                    )

        dto = AShareLimitUpContextProductDTO(
            trade_date=trade_date,
            as_of=as_of,
            pools=ordered_pools,
            context=LimitUpContextDTO.from_domain(context),
            provenance=provenance_dtos(provenance),
        )
        return AShareLimitUpResult(
            ok=True,
            data=dto,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

    def _merge_reason_tags(
        self, primary: LimitUpContext, enrichment: LimitUpContext
    ) -> LimitUpContext:
        """Fill empty reason_tags from THS; never overwrite Eastmoney facts."""
        tags_by_id: dict[str, tuple[str, ...]] = {}
        for entry in enrichment.entries:
            if entry.pool_type is not LimitPoolType.LIMIT_UP:
                continue
            if entry.reason_tags:
                tags_by_id[entry.instrument_id] = entry.reason_tags
        if not tags_by_id:
            return primary
        merged: list[LimitPoolEntry] = []
        for entry in primary.entries:
            if (
                entry.pool_type is LimitPoolType.LIMIT_UP
                and not entry.reason_tags
                and entry.instrument_id in tags_by_id
            ):
                merged.append(
                    replace(entry, reason_tags=tags_by_id[entry.instrument_id])
                )
            else:
                merged.append(entry)
        # Preserve factual summary / ladder / rates from primary exclusively.
        return replace(primary, entries=tuple(merged))

    async def _fetch_limit_context(
        self,
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...],
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
        codec: ProviderCacheCodec[LimitUpContext],
        role: _Role,
    ) -> RouterExecutionResult[Any]:
        async def _call(adapter: CategoryProvider) -> ProviderSuccess[LimitUpContext]:
            if not isinstance(adapter, AShareLimitUpProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={"category": DataCategory.LIMIT_UP.value},
                )
            return await adapter.get_limit_pools(
                trade_date=trade_date, pools=pools, as_of=as_of
            )

        def _validator(success: ProviderSuccess[LimitUpContext]) -> None:
            if role == "primary":
                self._validate_primary_limit_context(
                    success, trade_date=trade_date, pools=pools
                )
            else:
                self._validate_enrichment_limit_context(
                    success, trade_date=trade_date
                )

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.LIMIT_UP,
            call=_call,
            operation_name=OP_LIMIT_CONTEXT
            if tool_policy.required_categories
            else OP_LIMIT_REASON_TAGS,
            request_fingerprint=fingerprint,
            instrument=None,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=codec,
            result_validator=_validator,
        )

    # --- strict validators (Router result_validator) --------------------------

    def _require_success(
        self, success: object, *, expected_category: DataCategory
    ) -> ProviderSuccess[object]:
        if type(success) is not ProviderSuccess:
            raise DataContractError(
                "provider call must return exact ProviderSuccess",
                details={
                    "field": "result",
                    "rule": "type",
                    "type": type(success).__name__,
                },
            )
        if success.meta.category is not expected_category:
            raise DataContractError(
                "meta.category must match expected category",
                details={
                    "field": "meta.category",
                    "rule": "category",
                    "expected": expected_category.value,
                },
            )
        return success

    def _validate_primary_limit_context(
        self,
        success: ProviderSuccess[LimitUpContext],
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...],
    ) -> None:
        self._require_success(success, expected_category=DataCategory.LIMIT_UP)
        # Response-meta provenance: even empty pools must claim Eastmoney.
        if success.meta.vendor is not VendorId.EASTMONEY:
            raise DataContractError(
                "primary limit meta.vendor must be EASTMONEY",
                details={
                    "field": "meta.vendor",
                    "rule": "primary_meta_vendor",
                    "expected": VendorId.EASTMONEY.value,
                },
            )
        context = success.value
        if type(context) is not LimitUpContext:
            raise DataContractError(
                "success.value must be exact LimitUpContext",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(context).__name__,
                },
            )
        if context.trade_date != trade_date:
            raise DataContractError(
                "limit context trade_date must match request",
                details={"field": "trade_date", "rule": "trade_date"},
            )
        # Fail-closed: verified prior-day→today identity join is not implemented.
        if context.promotion_rate is not None:
            raise DataContractError(
                "promotion_rate must be None until verified prior-day identity join",
                details={
                    "field": "promotion_rate",
                    "rule": "promotion_rate_unavailable",
                },
            )
        if not isinstance(context.entries, tuple):
            raise DataContractError(
                "entries must be a tuple",
                details={"field": "entries", "rule": "type"},
            )
        requested = frozenset(pools)
        seen: set[tuple[LimitPoolType, str]] = set()
        prev_key: tuple[int, str] | None = None
        counts: dict[LimitPoolType, int] = {p: 0 for p in LimitPoolType}
        for idx, entry in enumerate(context.entries):
            if type(entry) is not LimitPoolEntry:
                raise DataContractError(
                    "entries elements must be exact LimitPoolEntry",
                    details={"field": "entries", "index": idx, "rule": "type"},
                )
            if entry.pool_type not in requested:
                raise DataContractError(
                    "entries must only include requested pools",
                    details={
                        "field": "pool_type",
                        "index": idx,
                        "rule": "requested_pool_only",
                        "pool": entry.pool_type.value,
                    },
                )
            if entry.trade_date != trade_date:
                raise DataContractError(
                    "entry trade_date must match request",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "trade_date",
                    },
                )
            if entry.source_vendor is not VendorId.EASTMONEY:
                raise DataContractError(
                    "primary limit entries must be Eastmoney factual rows",
                    details={
                        "field": "source_vendor",
                        "index": idx,
                        "rule": "primary_vendor",
                    },
                )
            if entry.reliability is not ReliabilityLevel.MEDIUM:
                raise DataContractError(
                    "primary limit entries must use MEDIUM reliability",
                    details={
                        "field": "reliability",
                        "index": idx,
                        "rule": "primary_reliability",
                    },
                )
            key = (entry.pool_type, entry.instrument_id)
            if key in seen:
                raise DataContractError(
                    "entries must be unique by pool_type+instrument_id",
                    details={"field": "entries", "index": idx, "rule": "unique"},
                )
            seen.add(key)
            order_key = (_POOL_INDEX[entry.pool_type], entry.instrument_id)
            if prev_key is not None and order_key < prev_key:
                raise DataContractError(
                    "entries must be sorted by pool enum order then instrument_id",
                    details={"field": "entries", "index": idx, "rule": "sorted"},
                )
            prev_key = order_key
            counts[entry.pool_type] += 1

        if context.limit_up_count != counts[LimitPoolType.LIMIT_UP]:
            raise DataContractError(
                "limit_up_count must exactly match LIMIT_UP entries",
                details={"field": "limit_up_count", "rule": "summary_count"},
            )
        if context.limit_down_count != counts[LimitPoolType.LIMIT_DOWN]:
            raise DataContractError(
                "limit_down_count must exactly match LIMIT_DOWN entries",
                details={"field": "limit_down_count", "rule": "summary_count"},
            )
        if context.broken_limit_count != counts[LimitPoolType.BROKEN_LIMIT]:
            raise DataContractError(
                "broken_limit_count must exactly match BROKEN_LIMIT entries",
                details={"field": "broken_limit_count", "rule": "summary_count"},
            )

        expected_broken_rate: Decimal | None = None
        if (
            LimitPoolType.LIMIT_UP in requested
            and LimitPoolType.BROKEN_LIMIT in requested
        ):
            denom = context.limit_up_count + context.broken_limit_count
            if denom > 0:
                expected_broken_rate = (
                    Decimal(context.broken_limit_count) / Decimal(denom)
                ).quantize(Decimal("0.0001"))
        if context.broken_rate != expected_broken_rate:
            raise DataContractError(
                "broken_rate must exactly match derived LIMIT_UP/BROKEN counts",
                details={"field": "broken_rate", "rule": "broken_rate"},
            )

        limit_up_entries = tuple(
            e for e in context.entries if e.pool_type is LimitPoolType.LIMIT_UP
        )
        expected_ladder, expected_max = _derive_ladder_and_max(limit_up_entries)
        if context.ladder != expected_ladder:
            raise DataContractError(
                "ladder must be exactly derived from emitted LIMIT_UP entries",
                details={"field": "ladder", "rule": "ladder_derived"},
            )
        if context.max_consecutive_count != expected_max:
            raise DataContractError(
                "max_consecutive_count must be exactly derived from LIMIT_UP entries",
                details={
                    "field": "max_consecutive_count",
                    "rule": "max_consecutive_derived",
                },
            )

    def _validate_enrichment_limit_context(
        self,
        success: ProviderSuccess[LimitUpContext],
        *,
        trade_date: date,
    ) -> None:
        self._require_success(success, expected_category=DataCategory.LIMIT_UP)
        # Response-meta provenance: even empty enrichment must claim THS.
        if success.meta.vendor is not VendorId.THS:
            raise DataContractError(
                "enrichment limit meta.vendor must be THS",
                details={
                    "field": "meta.vendor",
                    "rule": "enrichment_meta_vendor",
                    "expected": VendorId.THS.value,
                },
            )
        context = success.value
        if type(context) is not LimitUpContext:
            raise DataContractError(
                "success.value must be exact LimitUpContext",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(context).__name__,
                },
            )
        if context.trade_date != trade_date:
            raise DataContractError(
                "enrichment trade_date must match request",
                details={"field": "trade_date", "rule": "trade_date"},
            )
        # Fail-closed: verified prior-day→today identity join is not implemented.
        if context.promotion_rate is not None:
            raise DataContractError(
                "promotion_rate must be None until verified prior-day identity join",
                details={
                    "field": "promotion_rate",
                    "rule": "promotion_rate_unavailable",
                },
            )
        if not isinstance(context.entries, tuple):
            raise DataContractError(
                "entries must be a tuple",
                details={"field": "entries", "rule": "type"},
            )
        seen: set[str] = set()
        prev_id: str | None = None
        for idx, entry in enumerate(context.entries):
            if type(entry) is not LimitPoolEntry:
                raise DataContractError(
                    "entries elements must be exact LimitPoolEntry",
                    details={"field": "entries", "index": idx, "rule": "type"},
                )
            if entry.pool_type is not LimitPoolType.LIMIT_UP:
                raise DataContractError(
                    "THS enrichment must be LIMIT_UP-only editorial rows",
                    details={
                        "field": "pool_type",
                        "index": idx,
                        "rule": "enrichment_limit_up_only",
                    },
                )
            if entry.trade_date != trade_date:
                raise DataContractError(
                    "entry trade_date must match request",
                    details={
                        "field": "trade_date",
                        "index": idx,
                        "rule": "trade_date",
                    },
                )
            if entry.source_vendor is not VendorId.THS:
                raise DataContractError(
                    "enrichment entries must be THS vendor",
                    details={
                        "field": "source_vendor",
                        "index": idx,
                        "rule": "enrichment_vendor",
                    },
                )
            if entry.reliability is not ReliabilityLevel.LOW:
                raise DataContractError(
                    "enrichment entries must use LOW reliability",
                    details={
                        "field": "reliability",
                        "index": idx,
                        "rule": "enrichment_reliability",
                    },
                )
            if entry.instrument_id in seen:
                raise DataContractError(
                    "enrichment entries must be unique by instrument_id",
                    details={"field": "entries", "index": idx, "rule": "unique"},
                )
            seen.add(entry.instrument_id)
            if prev_id is not None and entry.instrument_id < prev_id:
                raise DataContractError(
                    "enrichment entries must be sorted by instrument_id",
                    details={"field": "entries", "index": idx, "rule": "sorted"},
                )
            prev_id = entry.instrument_id

        # Enrichment must not present factual multi-pool summaries.
        if context.limit_up_count != len(context.entries):
            raise DataContractError(
                "enrichment limit_up_count must match LIMIT_UP editorial rows",
                details={"field": "limit_up_count", "rule": "summary_count"},
            )
        if context.limit_down_count != 0 or context.broken_limit_count != 0:
            raise DataContractError(
                "enrichment must not claim limit_down/broken factual counts",
                details={"field": "summary", "rule": "enrichment_no_factual_summary"},
            )
        if context.broken_rate is not None:
            raise DataContractError(
                "enrichment must not claim broken_rate",
                details={"field": "broken_rate", "rule": "enrichment_no_factual_summary"},
            )
        if context.ladder != () or context.max_consecutive_count is not None:
            raise DataContractError(
                "enrichment must not claim ladder/max_consecutive factuals",
                details={"field": "ladder", "rule": "enrichment_no_factual_summary"},
            )

    @staticmethod
    def _merge_warnings(
        warnings: list[WarningInfo], result: RouterExecutionResult[object]
    ) -> None:
        for w in result.warnings:
            if w not in warnings:
                warnings.append(w)
        if result.meta is not None:
            for code in result.meta.warnings:
                if code not in _ESTABLISHED_META_WARNING_CODES:
                    continue
                if any(x.code == code for x in warnings):
                    continue
                warnings.append(
                    WarningInfo(
                        code=code,
                        message=_META_WARNING_MESSAGES[code],
                        details={},
                    )
                )
