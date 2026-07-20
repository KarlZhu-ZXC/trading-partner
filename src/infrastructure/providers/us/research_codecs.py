"""Explicit Phase 1G provider-cache codecs built on the shared US envelope codec.

The value schema is owned by closed Pydantic DTOs; domain constructors remain
the final validation boundary. No pickle, reflection, ``default=str``, or float
numeric payloads are accepted.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from pydantic import ValidationError

from application.dto.us_research import (
    USCompanyProfileDTO,
    USCorporateActionDTO,
    USFilingDTO,
    USFinancialStatementsDTO,
    USFundamentalMetricsDTO,
    USFundamentalSnapshotDTO,
    USInsiderTransactionDTO,
    USStatementPeriodDTO,
)
from domain.common.enums import DataCategory
from domain.common.errors import DataContractError
from domain.us_research.models import (
    USCompanyProfile,
    USCorporateAction,
    USFiling,
    USFilingSection,
    USFinancialStatements,
    USFundamentalMetrics,
    USFundamentalSnapshot,
    USInsiderTransaction,
    USStatementPeriod,
)
from infrastructure.providers.us.codecs import USProviderCacheCodec

CODEC_US_FILINGS: Final[str] = "us.filings.v1"
CODEC_US_INSIDER_ACTIVITY: Final[str] = "us.insider_activity.v1"
CODEC_US_FUNDAMENTAL_SNAPSHOT: Final[str] = "us.fundamental_snapshot.v1"
CODEC_US_FINANCIAL_STATEMENTS: Final[str] = "us.financial_statements.v1"
CODEC_US_CORPORATE_ACTIONS: Final[str] = "us.corporate_actions.v1"


def _contract(message: str, *, field: str, rule: str) -> DataContractError:
    return DataContractError(message, details={"field": field, "rule": rule})


def _reject_float_tree(value: object, *, field: str) -> None:
    if isinstance(value, float):
        raise _contract(
            "cache numeric values must use canonical decimal strings",
            field=field,
            rule="no_float",
        )
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_float_tree(item, field=f"{field}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_float_tree(item, field=f"{field}.{key}")


def _require_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _contract("cache value must be an array", field="value", rule="type")
    _reject_float_tree(value, field="value")
    return value


def _require_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _contract("cache value must be an object", field="value", rule="type")
    _reject_float_tree(value, field="value")
    return value


def _encode_filings(value: tuple[USFiling, ...]) -> list[object]:
    if not isinstance(value, tuple) or any(not isinstance(item, USFiling) for item in value):
        raise _contract(
            "filings cache value must be tuple[USFiling, ...]",
            field="value",
            rule="type",
        )
    return [USFilingDTO.from_domain(item).model_dump(mode="json") for item in value]


def _filing_from_dto(dto: USFilingDTO) -> USFiling:
    return USFiling(
        instrument_id=dto.instrument_id,
        accession=dto.accession,
        form=dto.form,
        is_amendment=dto.is_amendment,
        filed_date=dto.filed_date,
        accepted_at=dto.accepted_at,
        period_of_report=dto.period_of_report,
        primary_document=dto.primary_document,
        url=dto.url,
        items=dto.items,
        sections=tuple(
            USFilingSection(
                section_name=section.section_name,
                document_url=section.document_url,
                text=section.text,
                algorithm_version=section.algorithm_version,
            )
            for section in dto.sections
        ),
    )


def _decode_filings(value: object) -> tuple[USFiling, ...]:
    try:
        return tuple(
            _filing_from_dto(USFilingDTO.model_validate(item)) for item in _require_list(value)
        )
    except DataContractError:
        raise
    except ValidationError:
        raise _contract(
            "filings cache value failed schema validation",
            field="value",
            rule="value_schema",
        ) from None


def _encode_insiders(value: tuple[USInsiderTransaction, ...]) -> list[object]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, USInsiderTransaction) for item in value
    ):
        raise _contract(
            "insider cache value must be tuple[USInsiderTransaction, ...]",
            field="value",
            rule="type",
        )
    return [USInsiderTransactionDTO.from_domain(item).model_dump(mode="json") for item in value]


def _insider_from_dto(dto: USInsiderTransactionDTO) -> USInsiderTransaction:
    decimal_fields = (dto.shares, dto.price, dto.post_transaction_shares)
    if any(value is not None and type(value) is not Decimal for value in decimal_fields):
        raise _contract(
            "insider numeric values must decode to Decimal",
            field="value",
            rule="decimal_type",
        )
    return USInsiderTransaction(
        instrument_id=dto.instrument_id,
        owner_name=dto.owner_name,
        relationship=dto.relationship,
        transaction_date=dto.transaction_date,
        filed_at=dto.filed_at,
        accepted_at=dto.accepted_at,
        transaction_code=dto.transaction_code,
        acquired_disposed=dto.acquired_disposed,
        shares=dto.shares,
        price=dto.price,
        post_transaction_shares=dto.post_transaction_shares,
        is_direct=dto.is_direct,
        rule_10b5_1=dto.rule_10b5_1,
    )


def _decode_insiders(value: object) -> tuple[USInsiderTransaction, ...]:
    try:
        return tuple(
            _insider_from_dto(USInsiderTransactionDTO.model_validate(item))
            for item in _require_list(value)
        )
    except DataContractError:
        raise
    except ValidationError:
        raise _contract(
            "insider cache value failed schema validation",
            field="value",
            rule="value_schema",
        ) from None


def _encode_fundamental_snapshot(value: USFundamentalSnapshot) -> dict[str, object]:
    if not isinstance(value, USFundamentalSnapshot):
        raise _contract(
            "fundamental snapshot cache value must be USFundamentalSnapshot",
            field="value",
            rule="type",
        )
    return USFundamentalSnapshotDTO.from_domain(value).model_dump(mode="json")


def _profile_from_dto(dto: USCompanyProfileDTO | None) -> USCompanyProfile | None:
    if dto is None:
        return None
    return USCompanyProfile(
        instrument_id=dto.instrument_id,
        legal_name=dto.legal_name,
        description=dto.description,
        sector=dto.sector,
        industry=dto.industry,
        country=dto.country,
        website=dto.website,
        employees=dto.employees,
        market_cap=dto.market_cap,
    )


def _metrics_from_dto(dto: USFundamentalMetricsDTO | None) -> USFundamentalMetrics | None:
    if dto is None:
        return None
    return USFundamentalMetrics(
        trailing_pe=dto.trailing_pe,
        forward_pe=dto.forward_pe,
        peg_ratio=dto.peg_ratio,
        price_to_book=dto.price_to_book,
        price_to_sales=dto.price_to_sales,
        enterprise_to_ebitda=dto.enterprise_to_ebitda,
        dividend_yield=dto.dividend_yield,
        beta=dto.beta,
        eps_ttm=dto.eps_ttm,
        eps_forward=dto.eps_forward,
        book_value_per_share=dto.book_value_per_share,
        revenue_per_share=dto.revenue_per_share,
        revenue=dto.revenue,
        gross_profit=dto.gross_profit,
        ebitda=dto.ebitda,
        net_income=dto.net_income,
        profit_margin=dto.profit_margin,
        operating_margin=dto.operating_margin,
        roe=dto.roe,
        roa=dto.roa,
        debt_to_equity=dto.debt_to_equity,
        current_ratio=dto.current_ratio,
        revenue_growth=dto.revenue_growth,
        eps_growth=dto.eps_growth,
        estimate_revision=dto.estimate_revision,
        share_count=dto.share_count,
        stock_based_compensation=dto.stock_based_compensation,
        capital_expenditure=dto.capital_expenditure,
        free_cash_flow=dto.free_cash_flow,
        net_cash_or_debt=dto.net_cash_or_debt,
        period_end=dto.period_end,
        filed_at=dto.filed_at,
        basis=dto.basis,
    )


def _action_from_dto(dto: USCorporateActionDTO) -> USCorporateAction:
    return USCorporateAction(
        instrument_id=dto.instrument_id,
        action_type=dto.action_type,
        effective_date=dto.effective_date,
        declared_date=dto.declared_date,
        paid_date=dto.paid_date,
        amount=dto.amount,
        ratio=dto.ratio,
        currency=dto.currency,
        shares=dto.shares,
        description=dto.description,
    )


def _snapshot_from_dto(dto: USFundamentalSnapshotDTO) -> USFundamentalSnapshot:
    return USFundamentalSnapshot(
        instrument_id=dto.instrument_id,
        as_of=dto.as_of,
        profile=_profile_from_dto(dto.profile),
        metrics=_metrics_from_dto(dto.metrics),
        corporate_actions=tuple(_action_from_dto(a) for a in dto.corporate_actions),
        degraded=dto.degraded,
        warning_codes=dto.warning_codes,
        reported_metrics=_metrics_from_dto(dto.reported_metrics),
    )


def _decode_fundamental_snapshot(value: object) -> USFundamentalSnapshot:
    try:
        return _snapshot_from_dto(USFundamentalSnapshotDTO.model_validate(_require_object(value)))
    except DataContractError:
        raise
    except ValidationError:
        raise _contract(
            "fundamental snapshot cache value failed schema validation",
            field="value",
            rule="value_schema",
        ) from None


def _encode_financial_statements(value: USFinancialStatements) -> dict[str, object]:
    if not isinstance(value, USFinancialStatements):
        raise _contract(
            "financial statements cache value must be USFinancialStatements",
            field="value",
            rule="type",
        )
    return USFinancialStatementsDTO.from_domain(value).model_dump(mode="json")


def _period_from_dto(dto: USStatementPeriodDTO) -> USStatementPeriod:
    items: list[tuple[str, Decimal | None]] = []
    for pair in dto.line_items:
        key, amount = pair[0], pair[1]
        if amount is not None and type(amount) is not Decimal:
            raise _contract(
                "statement line amounts must decode to Decimal",
                field="value",
                rule="decimal_type",
            )
        items.append((key, amount))
    return USStatementPeriod(
        statement_type=dto.statement_type,
        frequency=dto.frequency,
        fiscal_year=dto.fiscal_year,
        fiscal_period=dto.fiscal_period,
        period_end=dto.period_end,
        filed_at=dto.filed_at,
        currency=dto.currency,
        line_items=tuple(items),
    )


def _statements_from_dto(dto: USFinancialStatementsDTO) -> USFinancialStatements:
    return USFinancialStatements(
        instrument_id=dto.instrument_id,
        as_of=dto.as_of,
        frequency=dto.frequency,
        income=tuple(_period_from_dto(p) for p in dto.income),
        balance_sheet=tuple(_period_from_dto(p) for p in dto.balance_sheet),
        cash_flow=tuple(_period_from_dto(p) for p in dto.cash_flow),
    )


def _decode_financial_statements(value: object) -> USFinancialStatements:
    try:
        return _statements_from_dto(USFinancialStatementsDTO.model_validate(_require_object(value)))
    except DataContractError:
        raise
    except ValidationError:
        raise _contract(
            "financial statements cache value failed schema validation",
            field="value",
            rule="value_schema",
        ) from None


def us_filings_codec() -> USProviderCacheCodec[tuple[USFiling, ...]]:
    return USProviderCacheCodec(
        CODEC_US_FILINGS,
        _encode_filings,
        _decode_filings,
        expected_category=DataCategory.FILINGS,
    )


def us_insider_activity_codec() -> USProviderCacheCodec[tuple[USInsiderTransaction, ...]]:
    return USProviderCacheCodec(
        CODEC_US_INSIDER_ACTIVITY,
        _encode_insiders,
        _decode_insiders,
        expected_category=DataCategory.INSIDER_ACTIVITY,
    )


def us_fundamental_snapshot_codec() -> USProviderCacheCodec[USFundamentalSnapshot]:
    return USProviderCacheCodec(
        CODEC_US_FUNDAMENTAL_SNAPSHOT,
        _encode_fundamental_snapshot,
        _decode_fundamental_snapshot,
        expected_category=DataCategory.FUNDAMENTALS,
    )


def us_financial_statements_codec() -> USProviderCacheCodec[USFinancialStatements]:
    return USProviderCacheCodec(
        CODEC_US_FINANCIAL_STATEMENTS,
        _encode_financial_statements,
        _decode_financial_statements,
        expected_category=DataCategory.FINANCIAL_STATEMENTS,
    )


def us_corporate_actions_codec() -> USProviderCacheCodec[tuple[USCorporateAction, ...]]:
    return USProviderCacheCodec(
        CODEC_US_CORPORATE_ACTIONS,
        lambda value: [
            USCorporateActionDTO.from_domain(item).model_dump(mode="json") for item in value
        ],
        lambda value: tuple(
            _action_from_dto(USCorporateActionDTO.model_validate(item))
            for item in _require_list(value)
        ),
        expected_category=DataCategory.CORPORATE_ACTIONS,
    )
