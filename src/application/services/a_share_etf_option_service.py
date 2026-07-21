"""A-share ETF option product service (Phase 1E E4c).

``AShareEtfOptionService.get`` routes a single required OPTIONS call through
``ProviderRouter`` with ``OPTIONS_POLICY``, explicit option snapshot codec,
canonical secret-free fingerprint, and a strict ``result_validator``.

The service is bootstrapped behind ``a_share_get_facts(operation="etf_option")``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from application.dto.a_share import EtfOptionSnapshotDTO
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import ProviderSuccess, ToolDataPolicy
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import AShareOptionProvider
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import OPTIONS_POLICY
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType, OptionType
from domain.a_share.models import EtfOptionQuote, EtfOptionSnapshot, OptionGreeks
from domain.common.enums import AssetType, DataCategory, Market, VendorId
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.instruments.models import Instrument

OP_OPTION_SNAPSHOT = "a_share.option_snapshot.v1"

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_OPTION_INSTRUMENT_RE = re.compile(r"^option:A_SHARE:\d+$")

_ESTABLISHED_META_WARNING_CODES = frozenset(
    {
        "LOW_RELIABILITY_MARKET_SIGNAL",
    }
)
_META_WARNING_MESSAGES: dict[str, str] = {
    "LOW_RELIABILITY_MARKET_SIGNAL": "Option snapshot carries low reliability",
}


@dataclass(frozen=True, slots=True)
class AShareEtfOptionResult:
    """Typed result wrapper for ETF option product aggregation."""

    ok: bool
    data: EtfOptionSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(
            self.provenance, order=(AShareComponentType.OPTION_SNAPSHOT,)
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
                    "AShareEtfOptionResult ok=True requires data non-None",
                    details={"field": "data", "rule": "ok_true_data_required"},
                )
            if self.error is not None:
                raise DataContractError(
                    "AShareEtfOptionResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            validate_data_provenance(self.data, self.provenance)
            if tuple(item.component for item in self.provenance) != (
                AShareComponentType.OPTION_SNAPSHOT,
            ):
                raise DataContractError("successful option result requires option provenance")
        else:
            if self.data is not None:
                raise DataContractError(
                    "AShareEtfOptionResult ok=False requires data is None",
                    details={"field": "data", "rule": "ok_false_data_none"},
                )
            if self.error is None or not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "AShareEtfOptionResult ok=False requires typed TradingPartnerError",
                    details={
                        "field": "error",
                        "rule": "ok_false_error_required",
                        "type": type(self.error).__name__,
                    },
                )


class AShareEtfOptionService:
    """E4c product service: required OPTIONS snapshot via ProviderRouter."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        option_snapshot_codec: ProviderCacheCodec[EtfOptionSnapshot],
        tool_policy: ToolDataPolicy = OPTIONS_POLICY,
    ) -> None:
        if router is None or clock is None:
            raise DataContractError(
                "router and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        if option_snapshot_codec is None or not hasattr(
            option_snapshot_codec, "codec_id"
        ):
            raise DataContractError(
                "option_snapshot_codec must be a ProviderCacheCodec",
                details={"field": "option_snapshot_codec", "rule": "required"},
            )
        if not isinstance(tool_policy, ToolDataPolicy):
            raise DataContractError(
                "tool_policy must be ToolDataPolicy",
                details={"field": "tool_policy", "rule": "type"},
            )
        self._require_options_required_policy(tool_policy)
        self._router = router
        self._clock = clock
        self._codec = option_snapshot_codec
        self._tool_policy = tool_policy

    @staticmethod
    def _require_options_required_policy(tool_policy: ToolDataPolicy) -> None:
        if DataCategory.OPTIONS not in tool_policy.required_categories:
            raise DataContractError(
                "OPTIONS must be a required category on option tool policy",
                details={"field": "tool_policy", "rule": "options_required"},
            )
        if DataCategory.OPTIONS in tool_policy.optional_categories:
            raise DataContractError(
                "OPTIONS must not be optional on option tool policy",
                details={"field": "tool_policy", "rule": "options_not_optional"},
            )

    async def get(
        self,
        underlying: Instrument,
        *,
        expiry: date | None = None,
        strike_center: Decimal | None = None,
        strike_count_each_side: int = 5,
        as_of: datetime,
    ) -> AShareEtfOptionResult:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        self._require_underlying(underlying)
        if expiry is not None and type(expiry) is not date:
            raise DataContractError(
                "expiry must be a date (not datetime)",
                details={"field": "expiry", "rule": "exact_date_type"},
            )
        if strike_center is not None:
            if type(strike_center) is not Decimal:
                raise DataContractError(
                    "strike_center must be Decimal or None",
                    details={"field": "strike_center", "rule": "decimal_type"},
                )
            if not strike_center.is_finite() or strike_center <= 0:
                raise DataContractError(
                    "strike_center must be a finite Decimal > 0",
                    details={"field": "strike_center", "rule": "positive_finite"},
                )
        if (
            type(strike_count_each_side) is not int
            or isinstance(strike_count_each_side, bool)
            or strike_count_each_side < 0
            or strike_count_each_side > 20
        ):
            raise DataContractError(
                "strike_count_each_side must be an exact int in 0..20",
                details={"field": "strike_count_each_side", "rule": "range"},
            )

        params = {
            "expiry": "" if expiry is None else expiry.isoformat(),
            "strike_center": "" if strike_center is None else format(strike_center, "f"),
            "strike_count_each_side": str(strike_count_each_side),
        }
        fingerprint = build_a_share_fingerprint(
            OP_OPTION_SNAPSHOT,
            underlying.instrument_id,
            params,
            as_of,
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[EtfOptionSnapshot]:
            if not isinstance(adapter, AShareOptionProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.OPTIONS.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_option_snapshot(
                underlying,
                expiry=expiry,
                strike_center=strike_center,
                strike_count_each_side=strike_count_each_side,
                as_of=as_of,
            )

        def _validator(success: ProviderSuccess[EtfOptionSnapshot]) -> None:
            self._validate_option_snapshot(
                success,
                underlying=underlying,
                expiry=expiry,
                as_of=as_of,
            )

        result = await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.OPTIONS,
            call=_call,
            operation_name=OP_OPTION_SNAPSHOT,
            request_fingerprint=fingerprint,
            instrument=underlying,
            as_of=as_of,
            tool_policy=self._tool_policy,
            bypass_cache=False,
            cache_codec=self._codec,
            result_validator=_validator,
        )

        warnings = self._collect_warnings(result.warnings, result.meta)
        provenance = (
            (
                component_provenance(
                    AShareComponentType.OPTION_SNAPSHOT,
                    result.meta,
                    result.value,
                ),
            )
            if result.ok and result.value is not None and result.meta is not None
            else ()
        )
        if not result.ok or result.value is None:
            return AShareEtfOptionResult(
                ok=False,
                data=None,
                warnings=warnings,
                error=result.error
                or DataContractError(
                    "required option snapshot failed",
                    details={"rule": "required", "operation": OP_OPTION_SNAPSHOT},
                ),
                provenance=provenance,
            )
        snapshot = result.value
        if type(snapshot) is not EtfOptionSnapshot:
            return AShareEtfOptionResult(
                ok=False,
                data=None,
                warnings=warnings,
                error=DataContractError(
                    "option provider returned invalid value type",
                    details={"rule": "type"},
                ),
                provenance=provenance,
            )
        return AShareEtfOptionResult(
            ok=True,
            data=EtfOptionSnapshotDTO.from_domain(
                snapshot, provenance=provenance_dtos(provenance)
            ),
            warnings=warnings,
            error=None,
            provenance=provenance,
        )

    @staticmethod
    def _require_underlying(underlying: Instrument) -> None:
        if not isinstance(underlying, Instrument):
            raise DataContractError(
                "underlying must be Instrument",
                details={"field": "underlying", "rule": "type"},
            )
        if underlying.market is not Market.A_SHARE:
            raise DataContractError(
                "underlying market must be A_SHARE",
                details={"field": "underlying", "rule": "market"},
            )
        if underlying.asset_type is not AssetType.ETF:
            raise DataContractError(
                "options underlying must be an ETF",
                details={"field": "underlying", "rule": "asset_type"},
            )

    def _validate_option_snapshot(
        self,
        success: object,
        *,
        underlying: Instrument,
        expiry: date | None,
        as_of: datetime,
    ) -> None:
        if type(success) is not ProviderSuccess:
            raise DataContractError(
                "provider call must return exact ProviderSuccess",
                details={
                    "field": "result",
                    "rule": "type",
                    "type": type(success).__name__,
                },
            )
        if success.meta.category is not DataCategory.OPTIONS:
            raise DataContractError(
                "meta.category must be OPTIONS",
                details={"field": "meta.category", "rule": "category"},
            )
        if success.meta.vendor is not VendorId.SINA:
            raise DataContractError(
                "option meta.vendor must be SINA",
                details={
                    "field": "meta.vendor",
                    "rule": "meta_vendor",
                    "expected": VendorId.SINA.value,
                },
            )
        if success.meta.as_of != as_of:
            raise DataContractError(
                "meta.as_of must equal request as_of exactly",
                details={"field": "meta.as_of", "rule": "meta_as_of"},
            )
        snapshot = success.value
        if type(snapshot) is not EtfOptionSnapshot:
            raise DataContractError(
                "success.value must be exact EtfOptionSnapshot",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(snapshot).__name__,
                },
            )
        if snapshot.underlying_instrument_id != underlying.instrument_id:
            raise DataContractError(
                "snapshot underlying must match request",
                details={"field": "underlying_instrument_id", "rule": "identity"},
            )
        if type(snapshot.expiry) is not date:
            raise DataContractError(
                "snapshot.expiry must be an exact date",
                details={"field": "expiry", "rule": "exact_date_type"},
            )
        as_of_local = as_of.astimezone(_SHANGHAI).date()
        if snapshot.expiry < as_of_local:
            raise DataContractError(
                "snapshot.expiry must be >= request Shanghai local date",
                details={"field": "expiry", "rule": "not_expired"},
            )
        if expiry is not None and snapshot.expiry != expiry:
            raise DataContractError(
                "snapshot expiry must match requested expiry",
                details={"field": "expiry", "rule": "expiry_match"},
            )
        if not snapshot.quotes:
            raise DataContractError(
                "option snapshot quotes must be non-empty",
                details={"field": "quotes", "rule": "non_empty"},
            )
        if len(snapshot.greeks) != len(snapshot.quotes):
            raise DataContractError(
                "option quotes and greeks must be one-to-one",
                details={"field": "greeks", "rule": "one_to_one"},
            )

        prev_key: tuple[Decimal, int, str] | None = None
        quote_ids: list[str] = []
        strike_sides: dict[Decimal, set[OptionType]] = {}
        quote_local_dates: set[date] = set()
        for idx, quote in enumerate(snapshot.quotes):
            if type(quote) is not EtfOptionQuote:
                raise DataContractError(
                    "quotes elements must be exact EtfOptionQuote",
                    details={"field": "quotes", "index": idx, "rule": "type"},
                )
            contract = quote.contract
            if contract.underlying_instrument_id != underlying.instrument_id:
                raise DataContractError(
                    "quote underlying must match request",
                    details={"field": "quotes", "index": idx, "rule": "underlying"},
                )
            if contract.expiry != snapshot.expiry:
                raise DataContractError(
                    "quote contract expiry must match snapshot expiry",
                    details={"field": "quotes", "index": idx, "rule": "expiry_match"},
                )
            if not _OPTION_INSTRUMENT_RE.fullmatch(contract.instrument_id):
                raise DataContractError(
                    "option instrument_id must be option:A_SHARE:<digits>",
                    details={"field": "instrument_id", "index": idx, "rule": "identity"},
                )
            try:
                asset, market, _symbol = parse_instrument_id(contract.instrument_id)
            except DataContractError:
                raise DataContractError(
                    "option instrument_id failed parse",
                    details={"field": "instrument_id", "index": idx, "rule": "identity"},
                ) from None
            if asset is not AssetType.OPTION or market is not Market.A_SHARE:
                raise DataContractError(
                    "option instrument_id must use OPTION/A_SHARE",
                    details={"field": "instrument_id", "index": idx, "rule": "identity"},
                )
            if contract.multiplier is not None:
                raise DataContractError(
                    "option multiplier must be exact None",
                    details={"field": "multiplier", "index": idx, "rule": "multiplier_none"},
                )
            if quote.quote_at > as_of:
                raise DataContractError(
                    "quote_at must be <= as_of",
                    details={
                        "field": "quote_at",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )
            quote_local_dates.add(quote.quote_at.astimezone(_SHANGHAI).date())
            if quote.last is not None and quote.last < 0:
                raise DataContractError(
                    "quote last must be nonnegative when present",
                    details={"field": "last", "index": idx, "rule": "nonnegative"},
                )
            side_set = strike_sides.setdefault(contract.strike, set())
            if contract.option_type in side_set:
                raise DataContractError(
                    "each strike must have exactly one CALL and one PUT",
                    details={"field": "quotes", "rule": "strike_sides"},
                )
            side_set.add(contract.option_type)
            sort_key = (
                contract.strike,
                0 if contract.option_type is OptionType.CALL else 1,
                contract.instrument_id,
            )
            if prev_key is not None and sort_key < prev_key:
                raise DataContractError(
                    "quotes must be sorted by strike asc, CALL before PUT, instrument_id",
                    details={"field": "quotes", "rule": "sorted"},
                )
            prev_key = sort_key
            quote_ids.append(contract.instrument_id)

        if len(set(quote_ids)) != len(quote_ids):
            raise DataContractError(
                "quotes must be unique by contract instrument_id",
                details={"field": "quotes", "rule": "unique"},
            )
        if len(quote_local_dates) != 1:
            raise DataContractError(
                "all quotes must share one local quote date",
                details={"field": "quote_at", "rule": "single_local_date"},
            )
        for strike, sides in strike_sides.items():
            if sides != {OptionType.CALL, OptionType.PUT}:
                raise DataContractError(
                    "each selected strike must have exactly one CALL and one PUT",
                    details={
                        "field": "quotes",
                        "rule": "strike_sides",
                        "strike": format(strike, "f"),
                    },
                )

        for idx, greek in enumerate(snapshot.greeks):
            if type(greek) is not OptionGreeks:
                raise DataContractError(
                    "greeks elements must be exact OptionGreeks",
                    details={"field": "greeks", "index": idx, "rule": "type"},
                )
            if greek.contract_instrument_id != quote_ids[idx]:
                raise DataContractError(
                    "greeks must join quotes one-to-one in order",
                    details={"field": "greeks", "index": idx, "rule": "join"},
                )
            if greek.source_provided is not True:
                raise DataContractError(
                    "source_provided must be True",
                    details={
                        "field": "source_provided",
                        "index": idx,
                        "rule": "source_provided_true",
                    },
                )
            if greek.as_of != as_of:
                raise DataContractError(
                    "greeks as_of must equal request as_of exactly",
                    details={
                        "field": "greeks.as_of",
                        "index": idx,
                        "rule": "as_of_exact",
                    },
                )
            self._validate_greek_ranges(greek, index=idx)

    @staticmethod
    def _validate_greek_ranges(greek: OptionGreeks, *, index: int) -> None:
        if greek.delta is not None and (
            not greek.delta.is_finite() or greek.delta < -1 or greek.delta > 1
        ):
            raise DataContractError(
                "delta must be in [-1, 1] when present",
                details={"field": "delta", "index": index, "rule": "delta_range"},
            )
        if greek.gamma is not None and (not greek.gamma.is_finite() or greek.gamma < 0):
            raise DataContractError(
                "gamma must be nonnegative when present",
                details={"field": "gamma", "index": index, "rule": "nonnegative"},
            )
        if greek.vega is not None and (not greek.vega.is_finite() or greek.vega < 0):
            raise DataContractError(
                "vega must be nonnegative when present",
                details={"field": "vega", "index": index, "rule": "nonnegative"},
            )
        if greek.implied_volatility is not None and (
            not greek.implied_volatility.is_finite() or greek.implied_volatility < 0
        ):
            raise DataContractError(
                "implied_volatility must be nonnegative when present",
                details={
                    "field": "implied_volatility",
                    "index": index,
                    "rule": "nonnegative",
                },
            )
        if greek.theoretical_value is not None and (
            not greek.theoretical_value.is_finite() or greek.theoretical_value < 0
        ):
            raise DataContractError(
                "theoretical_value must be nonnegative when present",
                details={
                    "field": "theoretical_value",
                    "index": index,
                    "rule": "nonnegative",
                },
            )
        if greek.theta is not None and not greek.theta.is_finite():
            raise DataContractError(
                "theta must be finite when present",
                details={"field": "theta", "index": index, "rule": "finite"},
            )

    @staticmethod
    def _collect_warnings(
        router_warnings: tuple[WarningInfo, ...],
        meta: Any,
    ) -> tuple[WarningInfo, ...]:
        warnings: list[WarningInfo] = list(router_warnings)
        if meta is not None:
            for code in getattr(meta, "warnings", ()) or ():
                if code not in _ESTABLISHED_META_WARNING_CODES:
                    continue
                if any(w.code == code for w in warnings):
                    continue
                warnings.append(
                    WarningInfo(
                        code=code,
                        message=_META_WARNING_MESSAGES.get(code, code),
                    )
                )
        return tuple(warnings)
