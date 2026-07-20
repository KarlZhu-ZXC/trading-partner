"""Pure native-currency industry/theme exposure enrichment."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal

from domain.portfolio.models import (
    AccountPosition,
    PortfolioClassification,
    PortfolioEnrichedExposure,
    PortfolioEnrichment,
)


class PortfolioEnrichmentCalculator:
    def calculate(
        self,
        positions: tuple[AccountPosition, ...],
        classifications: Mapping[str, PortfolioClassification],
    ) -> PortfolioEnrichment:
        totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        grouped: defaultdict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        missing_classification: set[str] = set()
        missing_valuation: set[str] = set()

        for position in positions:
            if position.market_value is None:
                missing_valuation.add(position.instrument_id)
                continue
            value = abs(position.market_value)
            totals[position.currency] += value
            classification = classifications.get(position.instrument_id)
            if classification is None or (
                classification.industry is None and not classification.themes
            ):
                missing_classification.add(position.instrument_id)
                continue
            if classification.industry is not None:
                grouped[("industry", classification.industry, position.currency)] += value
            for theme in classification.themes:
                grouped[("theme", theme, position.currency)] += value

        exposures = tuple(
            PortfolioEnrichedExposure(
                dimension,
                key,
                currency,
                value,
                value / totals[currency] if totals[currency] else Decimal(0),
            )
            for (dimension, key, currency), value in sorted(grouped.items())
        )
        return PortfolioEnrichment(
            exposures,
            tuple(sorted(missing_classification)),
            tuple(sorted(missing_valuation)),
        )
