"""Phase 1D D6a: CriticalityPolicy defaults and tool overrides."""

from __future__ import annotations

import pytest

from application.dto.provider_routing import ToolDataPolicy
from application.services.criticality_policy import CriticalityPolicy
from domain.common.enums import DataCategory, DataCriticality
from domain.common.errors import DataContractError

# Design Phase 1D §4.3 + Phase 1E §15.3 exact default table.
EXPECTED_DEFAULTS: dict[DataCategory, DataCriticality] = {
    DataCategory.MARKET_QUOTE: DataCriticality.CORE,
    DataCategory.MARKET_OHLCV: DataCriticality.CORE,
    DataCategory.MARKET_SNAPSHOT: DataCriticality.CORE,
    DataCategory.MARKET_STRUCTURE: DataCriticality.CORE,
    DataCategory.FUNDAMENTALS: DataCriticality.CORE,
    DataCategory.FINANCIAL_STATEMENTS: DataCriticality.CORE,
    DataCategory.FILINGS: DataCriticality.CORE,
    DataCategory.ANNOUNCEMENTS: DataCriticality.CORE,
    DataCategory.ACCOUNT: DataCriticality.CORE,
    DataCategory.INSTRUMENT_MASTER: DataCriticality.CORE,
    DataCategory.CAPITAL: DataCriticality.OPTIONAL,
    DataCategory.LIMIT_UP: DataCriticality.OPTIONAL,
    DataCategory.OPTIONS: DataCriticality.OPTIONAL,
    DataCategory.NEWS: DataCriticality.OPTIONAL,
    DataCategory.MACRO: DataCriticality.OPTIONAL,
    DataCategory.SENTIMENT: DataCriticality.OPTIONAL,
    DataCategory.PREDICTION_MARKET: DataCriticality.OPTIONAL,
    DataCategory.MARKET_BREADTH: DataCriticality.OPTIONAL,
    DataCategory.COMMUNITY_HEAT: DataCriticality.OPTIONAL,
    DataCategory.RESEARCH_REPORTS: DataCriticality.OPTIONAL,
    DataCategory.INTERACTIVE_QA: DataCriticality.OPTIONAL,
    DataCategory.CORPORATE_ACTIONS: DataCriticality.OPTIONAL,
    DataCategory.INSIDER_ACTIVITY: DataCriticality.OPTIONAL,
    DataCategory.INDUSTRY_CYCLE: DataCriticality.OPTIONAL,
    DataCategory.COMPANY_OPERATING_METRICS: DataCriticality.OPTIONAL,
}


def test_default_table_covers_every_data_category() -> None:
    assert set(EXPECTED_DEFAULTS) == set(DataCategory)
    table = CriticalityPolicy.default_table()
    assert set(table) == set(DataCategory)
    for category, criticality in EXPECTED_DEFAULTS.items():
        assert table[category] is criticality


@pytest.mark.parametrize(
    ("category", "criticality"),
    sorted(EXPECTED_DEFAULTS.items(), key=lambda kv: kv[0].value),
)
def test_for_category_defaults_match_section_4_3(
    category: DataCategory, criticality: DataCriticality
) -> None:
    policy = CriticalityPolicy()
    assert policy.for_category(category, None) is criticality


def test_tool_required_overrides_to_core() -> None:
    policy = CriticalityPolicy()
    tool = ToolDataPolicy(
        tool_name="sentiment_tool",
        required_categories=(DataCategory.SENTIMENT,),
        optional_categories=(),
        category_chain_overrides={},
    )
    assert policy.for_category(DataCategory.SENTIMENT, tool) is DataCriticality.CORE
    # Undeclared categories still use defaults
    assert policy.for_category(DataCategory.NEWS, tool) is DataCriticality.OPTIONAL
    assert policy.for_category(DataCategory.MARKET_QUOTE, tool) is DataCriticality.CORE


def test_tool_optional_overrides_to_optional() -> None:
    policy = CriticalityPolicy()
    tool = ToolDataPolicy(
        tool_name="soft_fundamentals",
        required_categories=(),
        optional_categories=(DataCategory.FUNDAMENTALS,),
        category_chain_overrides={},
    )
    assert policy.for_category(DataCategory.FUNDAMENTALS, tool) is DataCriticality.OPTIONAL


def test_required_wins_over_default_optional_star_categories() -> None:
    """CAPITAL/LIMIT_UP/OPTIONS are OPTIONAL* by default; tool may elevate."""
    policy = CriticalityPolicy()
    tool = ToolDataPolicy(
        tool_name="limit_up_board",
        required_categories=(
            DataCategory.LIMIT_UP,
            DataCategory.CAPITAL,
            DataCategory.OPTIONS,
        ),
        optional_categories=(),
        category_chain_overrides={},
    )
    for cat in (
        DataCategory.LIMIT_UP,
        DataCategory.CAPITAL,
        DataCategory.OPTIONS,
    ):
        assert policy.for_category(cat, tool) is DataCriticality.CORE


def test_empty_chain_does_not_change_criticality() -> None:
    """Criticality must not depend on whether a vendor chain is empty."""
    policy = CriticalityPolicy()
    tool = ToolDataPolicy(
        tool_name="t",
        required_categories=(DataCategory.NEWS,),
        optional_categories=(),
        category_chain_overrides={DataCategory.NEWS: ()},
    )
    assert policy.for_category(DataCategory.NEWS, tool) is DataCriticality.CORE


def test_rejects_non_category() -> None:
    policy = CriticalityPolicy()
    with pytest.raises(DataContractError, match="DataCategory"):
        policy.for_category("news", None)  # type: ignore[arg-type]
