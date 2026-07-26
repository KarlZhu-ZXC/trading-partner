"""Futures product/contract definition and EOD statistics application service.

Combines the definition repository (append-only cache) with CME public
reference/statistics providers. Never fabricates expiries or statistics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime

from application.dto.cross_asset import (
    FuturesContractDefinitionDTO,
    FuturesContractStatisticsDTO,
    FuturesProductDefinitionDTO,
)
from application.dto.provider_routing import ProviderSuccess
from application.dto.tool_envelope import WarningInfo
from application.ports.clock import Clock
from application.ports.futures_definition_repository import (
    FuturesDefinitionBatch,
    FuturesDefinitionRepository,
)
from application.ports.futures_reference_provider import FuturesReferenceProvider
from application.ports.futures_statistics_provider import FuturesStatisticsProvider
from application.ports.id_generator import IdGenerator
from domain.common.errors import DataContractError, NoMarketData, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.cross_asset.cme_identity import require_not_legacy_us_proxy
from domain.cross_asset.futures_models import (
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesProductDefinition,
)

_CME_SEED_WARNINGS = (
    WarningInfo(
        code="CME_PUBLIC_REFERENCE_ONLY",
        message=(
            "CME free public facts are research reference only and do not "
            "replace CME MDP validation."
        ),
    ),
    WarningInfo(
        code="FUTURES_CONTRACT_NOT_SPOT",
        message="Exchange futures contracts are not OTC spot metals.",
    ),
)
_DCE_SEED_WARNINGS = (
    WarningInfo(
        code="DCE_OFFICIAL_REFERENCE_ONLY",
        message=(
            "DCE free official EOD facts are research reference only and do not "
            "replace licensed exchange feeds."
        ),
    ),
    WarningInfo(
        code="DCE_OFFICIAL_EOD_ONLY",
        message="DCE Phase 3A-4 commits to official end-of-day facts only.",
    ),
    WarningInfo(
        code="FUTURES_CONTRACT_NOT_SPOT",
        message="Exchange futures contracts are not OTC spot metals.",
    ),
)
_SEED_WARNINGS = _CME_SEED_WARNINGS
_STATS_WARNINGS = _SEED_WARNINGS + (
    WarningInfo(
        code="OFFICIAL_SETTLEMENT_NOT_LAST_TRADE",
        message="Official settlement is distinct from last trade.",
    ),
)


def _seed_warnings_for(product_key: str) -> tuple[WarningInfo, ...]:
    if isinstance(product_key, str) and product_key.startswith("DCE:"):
        return _DCE_SEED_WARNINGS
    return _CME_SEED_WARNINGS


@dataclass(frozen=True, slots=True)
class FuturesProductResult:
    ok: bool
    data: FuturesProductDefinitionDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class FuturesContractListResult:
    ok: bool
    data: tuple[FuturesContractDefinitionDTO, ...] | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class FuturesStatisticsResult:
    ok: bool
    data: tuple[FuturesContractStatisticsDTO, ...] | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None


class FuturesContractService:
    def __init__(
        self,
        *,
        reference_provider: FuturesReferenceProvider,
        statistics_provider: FuturesStatisticsProvider,
        repository: FuturesDefinitionRepository,
        clock: Clock,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._reference = reference_provider
        self._statistics = statistics_provider
        self._repository = repository
        self._clock = clock
        self._ids = id_generator

    async def get_product(
        self,
        product_key: str,
        as_of: datetime | None = None,
        *,
        refresh: bool = False,
    ) -> FuturesProductResult:
        as_of = self._resolve_as_of(as_of)
        seed_warnings = _seed_warnings_for(product_key)
        if not refresh:
            cached = self._repository.get_product(product_key, as_of)
            if cached is not None:
                return FuturesProductResult(
                    ok=True,
                    data=FuturesProductDefinitionDTO.from_domain(cached),
                    warnings=seed_warnings,
                    error=None,
                    from_cache=True,
                )
        try:
            success = await self._reference.get_product_definition(product_key, as_of)
        except TradingPartnerError as exc:
            return FuturesProductResult(
                ok=False, data=None, warnings=(), error=exc, from_cache=False
            )
        product = self._ensure_product_version_id(success.value)
        self._repository.save_definition_batch(
            FuturesDefinitionBatch(products=(product,))
        )
        return FuturesProductResult(
            ok=True,
            data=FuturesProductDefinitionDTO.from_domain(product),
            warnings=self._warnings_from_meta(success) or seed_warnings,
            error=None,
            from_cache=False,
        )

    async def list_contracts(
        self,
        product_key: str,
        as_of: datetime | None = None,
        *,
        refresh: bool = False,
        include_expired: bool = False,
    ) -> FuturesContractListResult:
        as_of = self._resolve_as_of(as_of)
        seed_warnings = _seed_warnings_for(product_key)
        product_result = await self.get_product(product_key, as_of, refresh=refresh)
        if not product_result.ok or product_result.data is None:
            return FuturesContractListResult(
                ok=False,
                data=None,
                warnings=product_result.warnings,
                error=product_result.error
                or NoMarketData(
                    "product definition unavailable",
                    details={"code": "CONTRACT_DEFINITION_UNAVAILABLE"},
                ),
            )
        product_id = product_result.data.product_id
        if not refresh:
            cached = self._repository.list_contracts(product_id, as_of)
            if cached:
                filtered = self._filter_contracts(cached, include_expired=include_expired)
                return FuturesContractListResult(
                    ok=True,
                    data=tuple(
                        FuturesContractDefinitionDTO.from_domain(item)
                        for item in filtered
                    ),
                    warnings=seed_warnings
                    + (
                        WarningInfo(
                            code="FUTURES_CHAIN_UNAVAILABLE",
                            message="Contract chain served from durable cache.",
                        ),
                    )
                    if not filtered
                    else seed_warnings,
                    error=None,
                    from_cache=True,
                )
        try:
            success = await self._reference.list_contract_definitions(
                product_key, as_of
            )
        except TradingPartnerError as exc:
            # Fall back to durable cache on provider failure.
            cached = self._repository.list_contracts(product_id, as_of)
            if cached:
                filtered = self._filter_contracts(cached, include_expired=include_expired)
                return FuturesContractListResult(
                    ok=True,
                    data=tuple(
                        FuturesContractDefinitionDTO.from_domain(item)
                        for item in filtered
                    ),
                    warnings=seed_warnings
                    + (
                        WarningInfo(
                            code="FUTURES_CHAIN_UNAVAILABLE",
                            message=(
                                "Live contract chain unavailable; "
                                "returning durable cache."
                            ),
                        ),
                    ),
                    error=None,
                    from_cache=True,
                )
            return FuturesContractListResult(
                ok=False, data=None, warnings=(), error=exc, from_cache=False
            )

        contracts = tuple(
            self._ensure_contract_version_id(item) for item in success.value
        )
        if contracts:
            self._repository.save_definition_batch(
                FuturesDefinitionBatch(contracts=contracts)
            )
        filtered = self._filter_contracts(contracts, include_expired=include_expired)
        if not filtered:
            return FuturesContractListResult(
                ok=False,
                data=None,
                warnings=self._warnings_from_meta(success),
                error=NoMarketData(
                    "no contracts visible for product at as_of",
                    details={"code": "FUTURES_CHAIN_UNAVAILABLE"},
                ),
            )
        return FuturesContractListResult(
            ok=True,
            data=tuple(
                FuturesContractDefinitionDTO.from_domain(item) for item in filtered
            ),
            warnings=self._warnings_from_meta(success) or seed_warnings,
            error=None,
            from_cache=False,
        )

    async def get_statistics(
        self,
        instrument_ids: tuple[str, ...],
        trade_date: date,
        as_of: datetime | None = None,
        *,
        persist: bool = False,
    ) -> FuturesStatisticsResult:
        as_of = self._resolve_as_of(as_of)
        if not instrument_ids:
            return FuturesStatisticsResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "instrument_ids must be non-empty",
                    details={"field": "instrument_ids"},
                ),
            )
        for instrument_id in instrument_ids:
            require_not_legacy_us_proxy(instrument_id)
        try:
            success = await self._statistics.get_contract_statistics(
                instrument_ids, trade_date, as_of
            )
        except TradingPartnerError as exc:
            return FuturesStatisticsResult(ok=False, data=None, warnings=(), error=exc)
        if persist:
            self._repository.save_statistics(success.value)
        return FuturesStatisticsResult(
            ok=True,
            data=tuple(
                FuturesContractStatisticsDTO.from_domain(item) for item in success.value
            ),
            warnings=self._warnings_from_meta(success) or _STATS_WARNINGS,
            error=None,
        )

    def _resolve_as_of(self, as_of: datetime | None) -> datetime:
        if as_of is None:
            as_of = self._clock.now()
        return require_aware_datetime(as_of, field_name="as_of")

    def _ensure_product_version_id(
        self, product: FuturesProductDefinition
    ) -> FuturesProductDefinition:
        if product.version_id is not None:
            return product
        if self._ids is None:
            raise DataContractError(
                "product version_id missing and no IdGenerator configured",
                details={"product_id": product.product_id},
            )
        return FuturesProductDefinition(
            product_id=product.product_id,
            product_key=product.product_key,
            root=product.root,
            market=product.market,
            exchange=product.exchange,
            commodity=product.commodity,
            currency=product.currency,
            price_unit=product.price_unit,
            multiplier=product.multiplier,
            tick_size=product.tick_size,
            settlement_method=product.settlement_method,
            session_calendar_id=product.session_calendar_id,
            source=product.source,
            valid_from=product.valid_from,
            definition_as_of=product.definition_as_of,
            version_id=self._ids.new(EntityIdPrefix.FUTURES_PRODUCT_VERSION),
            version=product.version,
            valid_to=product.valid_to,
        )

    def _ensure_contract_version_id(
        self, contract: FuturesContractDefinition
    ) -> FuturesContractDefinition:
        if contract.version_id is not None:
            return contract
        if self._ids is None:
            # Deterministic content-addressed version id for cache writes when no
            # IdGenerator is injected (backend unit tests / lightweight wiring).
            digest = hashlib.sha256(
                f"{contract.instrument_id}|{contract.version}|"
                f"{contract.definition_as_of.isoformat()}|{contract.source}".encode()
            ).hexdigest()
            token = (
                f"{digest[:8]}-{digest[8:12]}-7{digest[13:16]}-"
                f"8{digest[17:20]}-{digest[20:32]}"
            )
            version_id = f"futures_contract_version_{token}"
        else:
            version_id = self._ids.new(EntityIdPrefix.FUTURES_CONTRACT_VERSION)
        return FuturesContractDefinition(
            instrument_id=contract.instrument_id,
            product_id=contract.product_id,
            contract_month=contract.contract_month,
            status=contract.status,
            definition_as_of=contract.definition_as_of,
            version_id=version_id,
            version=contract.version,
            listed_at=contract.listed_at,
            first_trade_at=contract.first_trade_at,
            last_trade_at=contract.last_trade_at,
            expiration_at=contract.expiration_at,
            first_notice_at=contract.first_notice_at,
            delivery_start=contract.delivery_start,
            delivery_end=contract.delivery_end,
            settlement_at=contract.settlement_at,
            source=contract.source,
        )

    @staticmethod
    def _filter_contracts(
        contracts: tuple[FuturesContractDefinition, ...],
        *,
        include_expired: bool,
    ) -> tuple[FuturesContractDefinition, ...]:
        if include_expired:
            return contracts
        from domain.cross_asset.enums import ContractLifecycleStatus

        return tuple(
            item
            for item in contracts
            if item.status is not ContractLifecycleStatus.EXPIRED
        )

    @staticmethod
    def _warnings_from_meta(
        success: ProviderSuccess[object],
    ) -> tuple[WarningInfo, ...]:
        messages = {
            "CME_PUBLIC_REFERENCE_ONLY": (
                "CME free public facts are research reference only and do not "
                "replace CME MDP validation."
            ),
            "DCE_OFFICIAL_REFERENCE_ONLY": (
                "DCE free official EOD facts are research reference only and do "
                "not replace licensed exchange feeds."
            ),
            "DCE_OFFICIAL_EOD_ONLY": (
                "DCE Phase 3A-4 commits to official end-of-day facts only."
            ),
            "FUTURES_CONTRACT_NOT_SPOT": (
                "Exchange futures contracts are not OTC spot metals."
            ),
            "OFFICIAL_SETTLEMENT_NOT_LAST_TRADE": (
                "Official settlement is distinct from last trade."
            ),
            "CONTINUOUS_FUTURES_ROLL_RISK": (
                "Continuous futures involve roll basis risk."
            ),
            "BEST_EFFORT_PUBLIC_FEED_NO_SLA": (
                "Public feed has no contractual SLA."
            ),
            "FUTURES_CHAIN_UNAVAILABLE": "Futures contract chain is incomplete.",
        }
        return tuple(
            WarningInfo(code=code, message=messages.get(code, code))
            for code in success.meta.warnings
        )


# Silence unused import for type checkers that only see statistics in annotations.
_ = FuturesContractStatistics
