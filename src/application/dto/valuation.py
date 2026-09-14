"""Closed valuation input contracts. No caller-supplied facts enter calculation."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from application.dto.market import DecimalWire
from domain.valuation.models import ValuationAssumptions


class ValuationAssumptionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    normalization_factor: DecimalWire = Field(gt=0, le=5)
    normalization_step: DecimalWire = Field(ge=0, le=5)
    pe_multiple: DecimalWire = Field(gt=0, le=100)
    pe_step: DecimalWire = Field(ge=0, le=100)
    business_model: Literal["operating_company", "bank", "insurance", "reit", "other"]
    rationale: str = Field(min_length=1, max_length=2000)

    def to_domain(self) -> ValuationAssumptions:
        return ValuationAssumptions(
            Decimal(self.normalization_factor),
            Decimal(self.normalization_step),
            Decimal(self.pe_multiple),
            Decimal(self.pe_step),
            self.business_model,
            self.rationale,
        )


class ValuationCalculateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_token: str = Field(min_length=1, max_length=128)
    assumptions: ValuationAssumptionsInput


class ValuationSaveInput(ValuationCalculateInput):
    supersedes_version_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    authorization_note: str = Field(min_length=1, max_length=1000)
    confirmed: Literal[True]
