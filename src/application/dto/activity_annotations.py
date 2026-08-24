"""Closed application contracts for Phase 4B activity annotations."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.account_transactions import AccountTransactionDTO
from application.dto.market import DecimalWire
from application.dto.review_item import ReviewItemDTO
from domain.common.enums import VendorId
from domain.portfolio.enums import ActivityAnnotationStatus, TradeCycleClassification
from domain.portfolio.models import ActivityAnnotation


class _DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, from_attributes=True
    )


class ActivityAnnotationAppendInput(_DTO):
    provider: VendorId
    account_ref: str = Field(min_length=1, max_length=128)
    provider_transaction_id: str = Field(min_length=1, max_length=256)
    status: ActivityAnnotationStatus = Field(
        validation_alias=AliasChoices("status", "link_status")
    )
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    trade_plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    trade_plan_version: int | None = Field(default=None, ge=1)
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, min_length=1, max_length=2_000)
    classification: TradeCycleClassification | None = None
    order_intent_id: str | None = Field(default=None, min_length=1, max_length=128)
    actor: str = Field(min_length=1, max_length=128)
    authorization_note: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator("account_ref", "provider_transaction_id", "actor", "authorization_note")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must be non-blank")
        return value

    @model_validator(mode="after")
    def plan_pair(self) -> ActivityAnnotationAppendInput:
        if (self.trade_plan_id is None) != (self.trade_plan_version is None):
            raise ValueError("trade_plan_id and trade_plan_version must be provided together")
        if self.status is ActivityAnnotationStatus.LINKED_DECISION_PLAN and (
            self.decision_id is None and self.trade_plan_id is None
        ):
            raise ValueError("LINKED_DECISION_PLAN requires a decision or Trade Plan")
        if self.status is not ActivityAnnotationStatus.LINKED_DECISION_PLAN and (
            self.decision_id is not None or self.trade_plan_id is not None
        ):
            raise ValueError("only LINKED_DECISION_PLAN accepts research links")
        return self


class ActivityAnnotationDTO(_DTO):
    annotation_id: str
    provider: VendorId
    account_ref: str
    provider_transaction_id: str
    version: int
    status: ActivityAnnotationStatus
    decision_id: str | None = None
    trade_plan_id: str | None = None
    trade_plan_version: int | None = None
    subject_id: str | None = None
    note: str | None = None
    classification: TradeCycleClassification | None = None
    order_intent_id: str | None = None
    actor: str
    authorization_note: str
    idempotency_key: str
    created_at: datetime

    @classmethod
    def from_domain(cls, value: ActivityAnnotation) -> ActivityAnnotationDTO:
        return cls.model_validate(value)

    @property
    def link_status(self) -> ActivityAnnotationStatus:
        return self.status


class UnlinkedActivityDTO(_DTO):
    source_key: str
    transaction: AccountTransactionDTO
    review_item: ReviewItemDTO | None = None

    @property
    def provider(self) -> VendorId:
        return self.transaction.provider

    @property
    def account_ref(self) -> str:
        return self.transaction.account_ref

    @property
    def provider_transaction_id(self) -> str:
        return self.transaction.provider_transaction_id

    @property
    def instrument_id(self) -> str | None:
        return self.transaction.instrument_id

    @property
    def occurred_at(self) -> datetime:
        return self.transaction.occurred_at

    @property
    def quantity(self) -> DecimalWire | None:
        return self.transaction.quantity

    @property
    def price(self) -> DecimalWire | None:
        return self.transaction.price


class UnlinkedActivityListDTO(_DTO):
    activities: tuple[UnlinkedActivityDTO, ...]
    has_more: bool = False
    observed_complete: bool = True
    limitation_codes: tuple[str, ...] = ()


# Common alternate spelling for callers that treat the result as an inbox.
UnlinkedActivityInboxDTO = UnlinkedActivityListDTO
