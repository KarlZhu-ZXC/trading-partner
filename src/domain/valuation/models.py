"""Normalized annual diluted-EPS multiple scenarios; never enterprise value."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from domain.common.errors import DataContractError

METHOD = "normalized_diluted_eps_pe_v1"


@dataclass(frozen=True)
class ValuationAssumptions:
    normalization_factor: Decimal
    normalization_step: Decimal
    pe_multiple: Decimal
    pe_step: Decimal
    business_model: str
    rationale: str

    def __post_init__(self) -> None:
        values = (
            self.normalization_factor,
            self.normalization_step,
            self.pe_multiple,
            self.pe_step,
        )
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise DataContractError("Assumptions must be finite decimals")
        if self.business_model != "operating_company":
            raise DataContractError("Only ordinary operating companies are supported")
        if not self.rationale.strip() or len(self.rationale) > 2000:
            raise DataContractError("Explain the user assumptions")
        if not 0 <= self.normalization_step < self.normalization_factor <= 5:
            raise DataContractError("Normalization and sensitivity must remain positive and <= 5")
        if self.normalization_factor + self.normalization_step > 5:
            raise DataContractError("Normalization sensitivity exceeds 5")
        if not 0 <= self.pe_step < self.pe_multiple <= 100:
            raise DataContractError("P/E and sensitivity must remain positive and <= 100")
        if self.pe_multiple + self.pe_step > 100:
            raise DataContractError("P/E sensitivity exceeds 100")


def calculate_eps_multiple(eps: Decimal, assumptions: ValuationAssumptions) -> dict[str, object]:
    if not isinstance(eps, Decimal) or not eps.is_finite() or not 0 < eps <= 1_000_000:
        raise DataContractError("Positive annual diluted EPS is required")
    with localcontext() as context:
        context.prec = 40

        def wire(value: Decimal) -> str:
            return format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN), "f")

        factors = [
            assumptions.normalization_factor + i * assumptions.normalization_step
            for i in (-1, 0, 1)
        ]
        multiples = [assumptions.pe_multiple + i * assumptions.pe_step for i in (-1, 0, 1)]
        return {
            "method": METHOD,
            "normalized_eps": wire(eps * assumptions.normalization_factor),
            "per_share_value": wire(
                eps * assumptions.normalization_factor * assumptions.pe_multiple
            ),
            "sensitivity": [
                {
                    "normalization_factor": str(f),
                    "pe_multiple": str(m),
                    "per_share_value": wire(eps * f * m),
                }
                for f in factors
                for m in multiples
            ],
            "value_basis": "USD per diluted share; as-reported annual EPS basis",
            "enterprise_value": None,
            "aggregate_equity_value": None,
        }
