from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from application.dto.valuation import ValuationAssumptionsInput, ValuationCalculateInput
from application.services.valuation_service import ValuationService
from domain.common.errors import DataContractError
from domain.valuation.models import calculate_eps_multiple

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def assumptions(**changes):
    return ValuationAssumptionsInput(
        **dict(
            normalization_factor="1",
            normalization_step="0.2",
            pe_multiple="20",
            pe_step="5",
            business_model="operating_company",
            rationale="Synthetic normalized earnings assumption",
        )
        | changes
    )


def test_exact_reproducible_per_share_sensitivity_without_enterprise_value():
    with localcontext() as context:
        context.prec = 6
        result = calculate_eps_multiple(Decimal("2.5"), assumptions().to_domain())
    assert result == calculate_eps_multiple(Decimal("2.5"), assumptions().to_domain())
    assert result["per_share_value"] == "50.0000"
    assert result["sensitivity"][0]["per_share_value"] == "30.0000"
    assert result["sensitivity"][-1]["per_share_value"] == "75.0000"
    assert len(result["sensitivity"]) == 9
    assert result["enterprise_value"] is None and result["aggregate_equity_value"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"business_model": "bank"},
        {"business_model": "insurance"},
        {"business_model": "reit"},
        {"pe_step": "20"},
        {"normalization_step": "1"},
        {"rationale": " "},
        {"pe_multiple": "NaN"},
        {"normalization_factor": "Infinity"},
    ],
)
def test_unsupported_or_invalid_assumptions_fail(changes):
    with pytest.raises((ValidationError, DataContractError)):
        assumptions(**changes).to_domain()


def test_missing_assumptions_and_client_facts_rejected():
    with pytest.raises(ValidationError):
        ValuationAssumptionsInput(business_model="operating_company")
    with pytest.raises(ValidationError):
        ValuationCalculateInput(source_token="x", assumptions=assumptions(), eps_diluted="999")
    with pytest.raises(DataContractError):
        calculate_eps_multiple(Decimal("-1"), assumptions().to_domain())


def synthetic_service(**period_changes):
    period = NS(
        **(
            dict(
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                filed_at=NOW - timedelta(days=150),
                accession="0000000000-26-000001",
                filing_form="10-K",
                currency="USD",
                line_items=(("eps_diluted", "2.5"),),
            )
            | period_changes
        )
    )
    result = NS(
        ok=True,
        data=NS(instrument_id="equity:US:SYNTH", income=[period], balance_sheet=[]),
        sources=[NS(name="sec_edgar")],
        request_id="req_synthetic",
        degraded=False,
        warnings=[],
    )
    calls = []

    async def get(request):
        calls.append(request)
        return result

    clock = NS(now=lambda: NOW)
    uow = NS(subjects=NS(get=lambda _: NS(primary_instrument_id="equity:US:SYNTH")))
    service = ValuationService(
        lambda: nullcontext(uow), NS(get_fundamental_statements=get), None, clock
    )
    return service, result, calls, clock


@pytest.mark.asyncio
async def test_server_source_scope_expiry_and_no_calculation_network():
    service, result, calls, clock = synthetic_service()
    source = await service.prepare("s1")
    request = ValuationCalculateInput(
        source_token=source["source_token"], assumptions=assumptions()
    )
    assert service.calculate("s1", request)["per_share_value"] == "50.0000"
    assert len(calls) == 1
    with pytest.raises(DataContractError):
        service.calculate("s2", request)
    clock.now = lambda: NOW + timedelta(hours=2)
    with pytest.raises(DataContractError):
        service.calculate("s1", request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"filed_at": NOW + timedelta(days=1)},
        {"period_start": date(2025, 10, 1)},
        {"currency": "JPY"},
        {"accession": None},
        {"line_items": ()},
        {"line_items": (("eps_diluted", "-1"),)},
    ],
)
async def test_source_gaps_do_not_autofill(changes):
    service, *_ = synthetic_service(**changes)
    with pytest.raises(DataContractError):
        await service.prepare("s1")


@pytest.mark.asyncio
async def test_current_only_fallback_is_not_a_sec_fact():
    service, result, *_ = synthetic_service()
    result.sources = [NS(name="yfinance")]
    with pytest.raises(DataContractError):
        await service.prepare("s1")
