"""Pure domain basis and curve-shape helpers (Phase 3A).

Basis is only computed when currency/unit, observation lag, delivery/basis
disclosures, and real observations all pass the comparability gate. Curve shape
is a mechanical classification of adjacent far-near spreads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.cross_asset.enums import BasisComparability, CurveShape, PriceBasis
from domain.cross_asset.futures_models import FuturesCurveContractPoint
from domain.cross_asset.spot_models import SpotObservation

BASIS_FORMULA_VERSION = "tp_basis_v1"


def _require_str(value: object, *, field: str, max_len: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    text = value.strip()
    if len(text) > max_len:
        raise DataContractError(
            f"{field} exceeds max length",
            details={"field": field, "max": max_len},
        )
    return text


def _require_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, float):
        raise DataContractError(
            f"{field} must not be float; use Decimal",
            details={"field": field, "rule": "no_float"},
        )
    if type(value) is not Decimal or not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field},
        )
    return value


@dataclass(frozen=True, slots=True)
class BasisLeg:
    """One disclosed observation used as a basis leg."""

    instrument_id: str
    price: Decimal
    currency: str
    unit: str
    observed_at: datetime
    price_basis: PriceBasis
    delivery_location: str | None = None

    def __post_init__(self) -> None:
        text = _require_str(self.instrument_id, field="instrument_id", max_len=128)
        parse_instrument_id(text)
        price = _require_decimal(self.price, field="price")
        if price < 0:
            raise DataContractError(
                "price must be nonnegative",
                details={"field": "price"},
            )
        _require_str(self.currency, field="currency", max_len=16)
        _require_str(self.unit, field="unit", max_len=64)
        require_aware_datetime(self.observed_at, field_name="observed_at")
        if not isinstance(self.price_basis, PriceBasis):
            raise DataContractError("price_basis must be PriceBasis")
        if self.delivery_location is not None:
            _require_str(self.delivery_location, field="delivery_location", max_len=128)


@dataclass(frozen=True, slots=True)
class BasisSnapshot:
    """Computed or blocked basis between two disclosed legs."""

    left_leg: BasisLeg
    right_leg: BasisLeg
    normalized_unit: str | None
    observation_lag_seconds: int
    absolute_spread: Decimal | None
    percentage_spread: Decimal | None
    comparability: BasisComparability
    formula_version: str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.left_leg, BasisLeg) or not isinstance(
            self.right_leg, BasisLeg
        ):
            raise DataContractError("left_leg and right_leg must be BasisLeg")
        if type(self.observation_lag_seconds) is not int or self.observation_lag_seconds < 0:
            raise DataContractError(
                "observation_lag_seconds must be a nonnegative int",
                details={"observation_lag_seconds": self.observation_lag_seconds},
            )
        if not isinstance(self.comparability, BasisComparability):
            raise DataContractError("comparability must be BasisComparability")
        version = _require_str(self.formula_version, field="formula_version", max_len=32)
        if version != BASIS_FORMULA_VERSION:
            raise DataContractError(
                "formula_version must be tp_basis_v1",
                details={"formula_version": version},
            )
        if not isinstance(self.reason_codes, tuple):
            raise DataContractError("reason_codes must be a tuple")
        for idx, code in enumerate(self.reason_codes):
            if not isinstance(code, str) or not code.strip():
                raise DataContractError(
                    "reason_codes items must be non-blank strings",
                    details={"field": f"reason_codes[{idx}]"},
                )
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise DataContractError("reason_codes must be unique")

        if self.comparability is BasisComparability.NOT_COMPARABLE:
            if self.absolute_spread is not None or self.percentage_spread is not None:
                raise DataContractError(
                    "NOT_COMPARABLE basis cannot include spreads",
                    details={"comparability": self.comparability.value},
                )
            if self.normalized_unit is not None:
                raise DataContractError(
                    "NOT_COMPARABLE basis cannot set normalized_unit",
                    details={"field": "normalized_unit"},
                )
            if not self.reason_codes:
                raise DataContractError(
                    "NOT_COMPARABLE basis requires reason_codes",
                    details={"field": "reason_codes"},
                )
            return

        if self.normalized_unit is None:
            raise DataContractError(
                "comparable basis requires normalized_unit",
                details={"field": "normalized_unit"},
            )
        _require_str(self.normalized_unit, field="normalized_unit", max_len=64)
        if self.absolute_spread is None:
            raise DataContractError(
                "comparable basis requires absolute_spread",
                details={"field": "absolute_spread"},
            )
        _require_decimal(self.absolute_spread, field="absolute_spread")
        if self.percentage_spread is not None:
            _require_decimal(self.percentage_spread, field="percentage_spread")


def classify_curve_shape(
    contracts: tuple[FuturesCurveContractPoint, ...],
) -> CurveShape:
    """Classify term structure from adjacent far-near spreads."""
    if len(contracts) < 2:
        return CurveShape.NOT_EVALUATED
    spreads = tuple(
        contracts[idx + 1].price - contracts[idx].price
        for idx in range(len(contracts) - 1)
    )
    if all(spread >= 0 for spread in spreads):
        return CurveShape.CONTANGO
    if all(spread <= 0 for spread in spreads):
        return CurveShape.BACKWARDATION
    return CurveShape.MIXED


def _leg_from_spot(observation: SpotObservation, *, price_basis: PriceBasis) -> BasisLeg:
    if observation.quote_at is None:
        raise DataContractError(
            "spot observation requires quote_at for basis",
            details={"field": "quote_at", "code": "BASIS_NOT_COMPARABLE"},
        )
    price: Decimal | None
    if price_basis is PriceBasis.MID:
        price = observation.mid
        if price is None and observation.bid is not None and observation.ask is not None:
            price = (observation.bid + observation.ask) / Decimal("2")
    elif price_basis is PriceBasis.LAST:
        price = observation.last
    else:
        raise DataContractError(
            "spot basis supports last or mid price_basis only",
            details={"price_basis": price_basis.value},
        )
    if price is None:
        raise DataContractError(
            "spot observation lacks the requested price basis",
            details={"price_basis": price_basis.value},
        )
    return BasisLeg(
        instrument_id=observation.instrument_id,
        price=price,
        currency=observation.currency,
        unit=observation.unit,
        observed_at=observation.quote_at,
        price_basis=price_basis,
        delivery_location=observation.delivery_location,
    )


def evaluate_basis_comparability(
    left: BasisLeg,
    right: BasisLeg,
    *,
    max_observation_lag_seconds: int,
    indicative_only: bool = False,
) -> tuple[BasisComparability, tuple[str, ...]]:
    """Return comparability and stable reason codes for a pair of legs."""
    if type(max_observation_lag_seconds) is not int or max_observation_lag_seconds < 0:
        raise DataContractError(
            "max_observation_lag_seconds must be a nonnegative int",
            details={"max_observation_lag_seconds": max_observation_lag_seconds},
        )
    reasons: list[str] = []
    if left.currency != right.currency:
        reasons.append("CURRENCY_MISMATCH")
    if left.unit != right.unit:
        reasons.append("UNIT_MISMATCH")
    lag = abs(int((left.observed_at - right.observed_at).total_seconds()))
    if lag > max_observation_lag_seconds:
        reasons.append("OBSERVATION_LAG_EXCEEDED")
    if left.price_basis is not right.price_basis:
        reasons.append("PRICE_BASIS_MISMATCH")
    if reasons:
        return BasisComparability.NOT_COMPARABLE, tuple(reasons)
    if indicative_only:
        return BasisComparability.INDICATIVE_ONLY, ("INDICATIVE_DELIVERY_OR_GRADE",)
    return BasisComparability.COMPARABLE, ()


def build_basis_snapshot(
    left: BasisLeg,
    right: BasisLeg,
    *,
    max_observation_lag_seconds: int,
    indicative_only: bool = False,
) -> BasisSnapshot:
    """Build a basis snapshot or a typed NOT_COMPARABLE result."""
    comparability, reasons = evaluate_basis_comparability(
        left,
        right,
        max_observation_lag_seconds=max_observation_lag_seconds,
        indicative_only=indicative_only,
    )
    lag = abs(int((left.observed_at - right.observed_at).total_seconds()))
    if comparability is BasisComparability.NOT_COMPARABLE:
        return BasisSnapshot(
            left_leg=left,
            right_leg=right,
            normalized_unit=None,
            observation_lag_seconds=lag,
            absolute_spread=None,
            percentage_spread=None,
            comparability=comparability,
            formula_version=BASIS_FORMULA_VERSION,
            reason_codes=reasons,
        )
    absolute = left.price - right.price
    percentage: Decimal | None
    if right.price == 0:
        percentage = None
        extra = ("ZERO_RIGHT_LEG_PRICE",)
        reason_tuple = tuple(dict.fromkeys((*reasons, *extra)))
    else:
        percentage = (absolute / right.price) * Decimal("100")
        reason_tuple = reasons
    return BasisSnapshot(
        left_leg=left,
        right_leg=right,
        normalized_unit=left.unit,
        observation_lag_seconds=lag,
        absolute_spread=absolute,
        percentage_spread=percentage,
        comparability=comparability,
        formula_version=BASIS_FORMULA_VERSION,
        reason_codes=reason_tuple,
    )


def build_basis_snapshot_from_spot_and_leg(
    spot: SpotObservation,
    right: BasisLeg,
    *,
    spot_price_basis: PriceBasis,
    max_observation_lag_seconds: int,
    indicative_only: bool = False,
) -> BasisSnapshot:
    """Convenience path for spot vs futures-style right leg."""
    left = _leg_from_spot(spot, price_basis=spot_price_basis)
    return build_basis_snapshot(
        left,
        right,
        max_observation_lag_seconds=max_observation_lag_seconds,
        indicative_only=indicative_only,
    )
