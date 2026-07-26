"""Futures term-structure (curve) application service.

Builds same-as_of, same-price-basis curve snapshots sorted by actual expiration.
Reports completeness, far-near spread, and deterministic curve shape. Never
mixes price bases or invents missing nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from application.dto.cross_asset import FuturesCurveSnapshotDTO
from application.dto.tool_envelope import WarningInfo
from application.ports.clock import Clock
from application.services.futures_contract_service import FuturesContractService
from domain.common.errors import DataContractError, NoMarketData, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.cross_asset.basis_service import classify_curve_shape
from domain.cross_asset.enums import (
    ContractLifecycleStatus,
    CurveCompleteness,
    CurveShape,
    PriceBasis,
)
from domain.cross_asset.futures_models import (
    FuturesCurveContractPoint,
    FuturesCurveSnapshot,
)

_CME_DEFAULT_WARNINGS = (
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
_DCE_DEFAULT_WARNINGS = (
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
_DEFAULT_WARNINGS = _CME_DEFAULT_WARNINGS


def _default_warnings_for(product_key: str) -> tuple[WarningInfo, ...]:
    if isinstance(product_key, str) and product_key.startswith("DCE:"):
        return _DCE_DEFAULT_WARNINGS
    return _CME_DEFAULT_WARNINGS


@dataclass(frozen=True, slots=True)
class FuturesCurveResult:
    ok: bool
    data: FuturesCurveSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None


class FuturesCurveService:
    def __init__(
        self,
        *,
        contract_service: FuturesContractService,
        clock: Clock,
    ) -> None:
        self._contracts = contract_service
        self._clock = clock

    async def build_curve(
        self,
        product_key: str,
        *,
        price_basis: PriceBasis,
        as_of: datetime | None = None,
        contract_limit: int = 6,
        trade_date: date | None = None,
        include_expired: bool = False,
    ) -> FuturesCurveResult:
        as_of = require_aware_datetime(
            as_of if as_of is not None else self._clock.now(),
            field_name="as_of",
        )
        if not isinstance(price_basis, PriceBasis):
            return FuturesCurveResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "price_basis must be PriceBasis",
                    details={"field": "price_basis"},
                ),
            )
        if type(contract_limit) is not int or contract_limit < 1:
            return FuturesCurveResult(
                ok=False,
                data=None,
                warnings=(),
                error=DataContractError(
                    "contract_limit must be a positive int",
                    details={"field": "contract_limit"},
                ),
            )

        default_warnings = _default_warnings_for(product_key)
        product_result = await self._contracts.get_product(product_key, as_of)
        if not product_result.ok or product_result.data is None:
            return FuturesCurveResult(
                ok=False,
                data=None,
                warnings=product_result.warnings,
                error=product_result.error
                or NoMarketData(
                    "product definition unavailable",
                    details={"code": "CONTRACT_DEFINITION_UNAVAILABLE"},
                ),
            )

        chain = await self._contracts.list_contracts(
            product_key,
            as_of,
            include_expired=include_expired,
        )
        if not chain.ok or chain.data is None:
            return FuturesCurveResult(
                ok=False,
                data=None,
                warnings=chain.warnings or default_warnings,
                error=chain.error
                or NoMarketData(
                    "futures contract chain unavailable",
                    details={"code": "FUTURES_CHAIN_UNAVAILABLE"},
                ),
            )

        # Candidate contracts ordered by expiration then instrument_id.
        candidates = sorted(
            chain.data,
            key=lambda c: (
                c.expiration_at.isoformat()
                if c.expiration_at is not None
                else c.contract_month,
                c.instrument_id,
            ),
        )
        if not include_expired:
            candidates = [
                c
                for c in candidates
                if c.status is not ContractLifecycleStatus.EXPIRED
            ]
        expected = candidates[:contract_limit]
        if not expected:
            snapshot = FuturesCurveSnapshot(
                product_id=product_result.data.product_id,
                as_of=as_of,
                price_basis=price_basis,
                contracts=(),
                curve_shape=CurveShape.NOT_EVALUATED,
                completeness=CurveCompleteness.EMPTY,
                front_next_spread=None,
            )
            return FuturesCurveResult(
                ok=True,
                data=FuturesCurveSnapshotDTO.from_domain(snapshot),
                warnings=default_warnings
                + (
                    WarningInfo(
                        code="FUTURES_CHAIN_UNAVAILABLE",
                        message="No active contracts available for curve.",
                    ),
                ),
                error=None,
            )

        stats_by_id: dict[str, object] = {}
        warnings = list(default_warnings)
        if price_basis is PriceBasis.SETTLEMENT:
            day = trade_date if trade_date is not None else as_of.date()
            stats_result = await self._contracts.get_statistics(
                tuple(item.instrument_id for item in expected),
                day,
                as_of,
            )
            if not stats_result.ok or stats_result.data is None:
                return FuturesCurveResult(
                    ok=False,
                    data=None,
                    warnings=tuple(warnings) + stats_result.warnings,
                    error=stats_result.error
                    or NoMarketData(
                        "settlement statistics unavailable for curve",
                        details={"code": "FUTURES_CHAIN_UNAVAILABLE"},
                    ),
                )
            stats_by_id = {item.instrument_id: item for item in stats_result.data}
            warnings.extend(stats_result.warnings)
            # Avoid duplicate warning codes while preserving order.
            seen: set[str] = set()
            deduped: list[WarningInfo] = []
            for warning in warnings:
                if warning.code in seen:
                    continue
                seen.add(warning.code)
                deduped.append(warning)
            warnings = deduped
        else:
            # last/mid require external quote injection in a later slice; do not
            # fabricate from settlement.
            return FuturesCurveResult(
                ok=False,
                data=None,
                warnings=tuple(warnings),
                error=DataContractError(
                    "curve price_basis last/mid requires quote provider wiring",
                    details={
                        "price_basis": price_basis.value,
                        "code": "FUTURES_CHAIN_UNAVAILABLE",
                    },
                ),
            )

        points: list[FuturesCurveContractPoint] = []
        missing = 0
        for item in expected:
            stat = stats_by_id.get(item.instrument_id)
            price: Decimal | None = None
            open_interest: Decimal | None = None
            session_volume: Decimal | None = None
            if stat is not None:
                price = getattr(stat, "settlement", None)
                open_interest = getattr(stat, "open_interest", None)
                session_volume = getattr(stat, "session_volume", None)
            if price is None:
                missing += 1
                continue
            points.append(
                FuturesCurveContractPoint(
                    instrument_id=item.instrument_id,
                    contract_month=item.contract_month,
                    expiration_at=item.expiration_at,
                    price=price,
                    open_interest=open_interest,
                    session_volume=session_volume,
                )
            )

        points_tuple = tuple(
            sorted(
                points,
                key=lambda p: (
                    p.expiration_at.isoformat()
                    if p.expiration_at is not None
                    else p.contract_month,
                    p.instrument_id,
                ),
            )
        )
        if not points_tuple:
            completeness = CurveCompleteness.EMPTY
            shape = CurveShape.NOT_EVALUATED
            spread = None
        else:
            if missing == 0 and len(points_tuple) == len(expected):
                completeness = CurveCompleteness.COMPLETE
            else:
                completeness = CurveCompleteness.PARTIAL
            shape = classify_curve_shape(points_tuple)
            if len(points_tuple) >= 2:
                spread = points_tuple[1].price - points_tuple[0].price
            else:
                spread = None

        snapshot = FuturesCurveSnapshot(
            product_id=product_result.data.product_id,
            as_of=as_of,
            price_basis=price_basis,
            contracts=points_tuple,
            curve_shape=shape,
            completeness=completeness,
            front_next_spread=spread,
        )
        if completeness is CurveCompleteness.PARTIAL:
            warnings.append(
                WarningInfo(
                    code="FUTURES_CHAIN_UNAVAILABLE",
                    message="Curve is partial: some contract nodes lacked prices.",
                )
            )
        return FuturesCurveResult(
            ok=True,
            data=FuturesCurveSnapshotDTO.from_domain(snapshot),
            warnings=tuple(warnings),
            error=None,
        )
