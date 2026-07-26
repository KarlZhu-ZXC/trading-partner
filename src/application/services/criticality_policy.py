"""Core vs Optional criticality policy (Phase 1D D6a).

Defaults match design §4.3 exactly. ToolDataPolicy required/optional overrides
win for declared categories; undeclared categories fall back to the default
table. Criticality never depends on whether a vendor chain is empty.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from application.dto.provider_routing import ToolDataPolicy
from domain.common.enums import DataCategory, DataCriticality
from domain.common.errors import DataContractError

# Frozen default table — Phase 1D §4.3 + Phase 1E §15.3
# (every DataCategory must appear exactly once).
_DEFAULT_CRITICALITY: Mapping[DataCategory, DataCriticality] = MappingProxyType(
    {
        DataCategory.MARKET_QUOTE: DataCriticality.CORE,
        DataCategory.MARKET_OHLCV: DataCriticality.CORE,
        DataCategory.MARKET_SNAPSHOT: DataCriticality.CORE,
        DataCategory.MARKET_STRUCTURE: DataCriticality.CORE,
        DataCategory.MARKET_BREADTH: DataCriticality.OPTIONAL,
        DataCategory.COMMUNITY_HEAT: DataCriticality.OPTIONAL,
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
        DataCategory.RESEARCH_REPORTS: DataCriticality.OPTIONAL,
        DataCategory.INTERACTIVE_QA: DataCriticality.OPTIONAL,
        # OPTIONAL by default; full snapshot tool policy may elevate to CORE.
        DataCategory.CORPORATE_ACTIONS: DataCriticality.OPTIONAL,
        # Phase 1G: insider activity optional by default; tools may elevate.
        DataCategory.INSIDER_ACTIVITY: DataCriticality.OPTIONAL,
        DataCategory.INDUSTRY_CYCLE: DataCriticality.OPTIONAL,
        DataCategory.COMPANY_OPERATING_METRICS: DataCriticality.OPTIONAL,
        DataCategory.FUTURES_REFERENCE: DataCriticality.CORE,
        DataCategory.FUTURES_STATISTICS: DataCriticality.CORE,
    }
)


class CriticalityPolicy:
    """Resolve DataCriticality for a category, optionally under a tool policy."""

    def for_category(
        self,
        category: DataCategory,
        tool_policy: ToolDataPolicy | None,
    ) -> DataCriticality:
        if not isinstance(category, DataCategory):
            raise DataContractError(
                "category must be a DataCategory",
                details={"field": "category", "type": type(category).__name__},
            )
        if tool_policy is not None:
            if not isinstance(tool_policy, ToolDataPolicy):
                raise DataContractError(
                    "tool_policy must be ToolDataPolicy or None",
                    details={
                        "field": "tool_policy",
                        "type": type(tool_policy).__name__,
                    },
                )
            if category in tool_policy.required_categories:
                return DataCriticality.CORE
            if category in tool_policy.optional_categories:
                return DataCriticality.OPTIONAL
        try:
            return _DEFAULT_CRITICALITY[category]
        except KeyError as exc:  # pragma: no cover — enum completeness guard
            raise DataContractError(
                "no default criticality for category",
                details={"field": "category", "category": category.value},
            ) from exc

    @staticmethod
    def default_table() -> Mapping[DataCategory, DataCriticality]:
        """Read-only view of the §4.3 default criticality table."""
        return _DEFAULT_CRITICALITY
