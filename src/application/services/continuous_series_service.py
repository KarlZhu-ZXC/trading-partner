"""Ruled continuous futures series application service.

Supports calendar / volume / open-interest roll rules with adjustment=none.
Never silently rewrites legacy ``future:US:*`` Yahoo continuous proxies.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime

from application.dto.cross_asset import (
    ContinuousContractMappingDTO,
    ContinuousSeriesDefinitionDTO,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.clock import Clock
from application.ports.futures_definition_repository import (
    FuturesDefinitionBatch,
    FuturesDefinitionRepository,
)
from application.ports.futures_reference_provider import FuturesReferenceProvider
from application.services.futures_contract_service import FuturesContractService
from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError, NoMarketData, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.cross_asset.cme_identity import (
    build_cme_continuous_symbol,
    is_legacy_us_continuous_proxy,
    parse_cme_continuous_code,
    require_not_legacy_us_proxy,
)
from domain.cross_asset.enums import ContinuousAdjustment, RollRule
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
)

_METHODOLOGY = "tp_continuous_v1"
_WARNINGS = (
    WarningInfo(
        code="CME_PUBLIC_REFERENCE_ONLY",
        message=(
            "CME free public facts are research reference only and do not "
            "replace CME MDP validation."
        ),
    ),
    WarningInfo(
        code="CONTINUOUS_FUTURES_ROLL_RISK",
        message="Continuous futures involve roll basis risk; bars are unadjusted.",
    ),
    WarningInfo(
        code="FUTURES_CONTRACT_NOT_SPOT",
        message="Exchange futures contracts are not OTC spot metals.",
    ),
)


@dataclass(frozen=True, slots=True)
class ContinuousSeriesResult:
    ok: bool
    data: ContinuousSeriesDefinitionDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None


@dataclass(frozen=True, slots=True)
class ContinuousMappingResult:
    ok: bool
    data: tuple[ContinuousContractMappingDTO, ...] | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None


class ContinuousSeriesService:
    def __init__(
        self,
        *,
        reference_provider: FuturesReferenceProvider,
        contract_service: FuturesContractService,
        repository: FuturesDefinitionRepository,
        clock: Clock,
    ) -> None:
        self._reference = reference_provider
        self._contracts = contract_service
        self._repository = repository
        self._clock = clock

    def ensure_series(
        self,
        product_key: str,
        *,
        roll_rule: RollRule,
        rank: int = 0,
        as_of: datetime | None = None,
    ) -> ContinuousSeriesResult:
        """Create or load a ruled continuous series definition (adjustment=none)."""
        as_of = require_aware_datetime(
            as_of if as_of is not None else self._clock.now(),
            field_name="as_of",
        )
        if not isinstance(roll_rule, RollRule):
            return ContinuousSeriesResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "roll_rule must be RollRule",
                    details={"field": "roll_rule"},
                ),
            )
        if type(rank) is not int or rank < 0:
            return ContinuousSeriesResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "rank must be a nonnegative int",
                    details={"field": "rank"},
                ),
            )
        if ":" not in product_key or not product_key.startswith("CME:"):
            return ContinuousSeriesResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "continuous series currently supports CME product keys only",
                    details={"product_key": product_key},
                ),
            )
        root = product_key.split(":", 1)[1]
        try:
            symbol = build_cme_continuous_symbol(root, roll_rule, rank)
        except TradingPartnerError as exc:
            return ContinuousSeriesResult(
                ok=False, data=None, warnings=(), error=exc
            )
        instrument_id = build_instrument_id(AssetType.FUTURE, Market.CME, symbol)
        require_not_legacy_us_proxy(instrument_id)

        cached = self._repository.get_continuous_series(instrument_id, as_of)
        if cached is not None:
            return ContinuousSeriesResult(
                ok=True,
                data=ContinuousSeriesDefinitionDTO.from_domain(cached),
                warnings=_WARNINGS,
                error=None,
            )

        # Product must exist (seed or cache).
        product = self._repository.get_product(product_key, as_of)
        if product is None:
            return ContinuousSeriesResult(
                ok=False,
                data=None,
                warnings=(),
                error=NoMarketData(
                    "product definition required before continuous series",
                    details={
                        "product_key": product_key,
                        "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                    },
                ),
            )

        series = ContinuousSeriesDefinition(
            instrument_id=instrument_id,
            product_id=product.product_id,
            roll_rule=roll_rule,
            rank=rank,
            adjustment=ContinuousAdjustment.NONE,
            provider_methodology_version=_METHODOLOGY,
            valid_from=product.valid_from,
            valid_to=None,
        )
        self._repository.save_definition_batch(
            FuturesDefinitionBatch(continuous_series=(series,))
        )
        return ContinuousSeriesResult(
            ok=True,
            data=ContinuousSeriesDefinitionDTO.from_domain(series),
            warnings=_WARNINGS,
            error=None,
        )

    async def resolve_mapping(
        self,
        continuous_instrument_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        as_of: datetime | None = None,
        persist: bool = True,
    ) -> ContinuousMappingResult:
        """Resolve continuous→specific contract mappings for a window."""
        if is_legacy_us_continuous_proxy(continuous_instrument_id):
            return ContinuousMappingResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "legacy future:US:* continuous proxy must not be rewritten",
                    details={
                        "instrument_id": continuous_instrument_id,
                        "code": "ROLL_MAPPING_UNAVAILABLE",
                    },
                ),
            )
        as_of = require_aware_datetime(
            as_of if as_of is not None else self._clock.now(),
            field_name="as_of",
        )
        start = require_aware_datetime(
            start if start is not None else as_of,
            field_name="start",
        )
        end = require_aware_datetime(
            end if end is not None else as_of,
            field_name="end",
        )
        if end < start:
            return ContinuousMappingResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "end must be >= start",
                    details={"field": "end"},
                ),
            )

        series = self._repository.get_continuous_series(
            continuous_instrument_id, as_of
        )
        if series is None:
            # Attempt to materialize from symbol grammar.
            try:
                _, _, symbol = continuous_instrument_id.split(":", 2)
                code = parse_cme_continuous_code(symbol)
            except (ValueError, TradingPartnerError) as exc:
                return ContinuousMappingResult(
                    ok=False,
                    data=None,
                    warnings=(),
                    error=NoMarketData(
                        "continuous series definition not found",
                        details={
                            "instrument_id": continuous_instrument_id,
                            "code": "ROLL_MAPPING_UNAVAILABLE",
                            "cause": type(exc).__name__,
                        },
                    ),
                )
            ensured = self.ensure_series(
                code.product_key,
                roll_rule=code.roll_rule,
                rank=code.rank,
                as_of=as_of,
            )
            if not ensured.ok or ensured.data is None:
                return ContinuousMappingResult(
                    ok=False,
                    data=None,
                    warnings=ensured.warnings,
                    error=ensured.error
                    or NoMarketData(
                        "continuous series definition unavailable",
                        details={"code": "ROLL_MAPPING_UNAVAILABLE"},
                    ),
                )
            series = ContinuousSeriesDefinition(
                instrument_id=ensured.data.instrument_id,
                product_id=ensured.data.product_id,
                roll_rule=ensured.data.roll_rule,
                rank=ensured.data.rank,
                adjustment=ensured.data.adjustment,
                provider_methodology_version=ensured.data.provider_methodology_version,
                valid_from=ensured.data.valid_from,
                valid_to=ensured.data.valid_to,
            )

        # Prefer durable mappings when present for the window.
        cached = self._repository.list_continuous_mappings(
            continuous_instrument_id, start=start, end=end
        )
        if cached:
            return ContinuousMappingResult(
                ok=True,
                data=tuple(
                    ContinuousContractMappingDTO.from_domain(item) for item in cached
                ),
                warnings=_WARNINGS,
                error=None,
            )

        try:
            success = await self._reference.resolve_continuous_mapping(
                series, start, end, as_of
            )
        except TradingPartnerError as exc:
            return ContinuousMappingResult(
                ok=False, data=None, warnings=_WARNINGS, error=exc
            )

        mappings = success.value
        if not mappings:
            return ContinuousMappingResult(
                ok=False,
                data=None,
                warnings=_WARNINGS,
                error=NoMarketData(
                    "no continuous mapping resolved",
                    details={"code": "ROLL_MAPPING_UNAVAILABLE"},
                ),
            )
        if persist:
            # Ensure specific contracts exist as FK targets when possible.
            # Repository requires contract rows; save mappings only if contracts
            # already cached. Otherwise return without persist.
            with contextlib.suppress(TradingPartnerError):
                self._repository.save_definition_batch(
                    FuturesDefinitionBatch(mappings=mappings)
                )

        return ContinuousMappingResult(
            ok=True,
            data=tuple(
                ContinuousContractMappingDTO.from_domain(item) for item in mappings
            ),
            warnings=_WARNINGS,
            error=None,
        )

    def mapping_at(
        self,
        continuous_instrument_id: str,
        as_of: datetime,
    ) -> ContinuousContractMapping | None:
        """Synchronous durable lookup of the mapping effective at as_of."""
        if is_legacy_us_continuous_proxy(continuous_instrument_id):
            return None
        require_aware_datetime(as_of, field_name="as_of")
        mappings = self._repository.list_continuous_mappings(
            continuous_instrument_id, start=as_of, end=as_of
        )
        return mappings[0] if mappings else None
