"""Pure Decimal descriptive correlation and beta for portfolio review."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, localcontext

from domain.common.errors import InputValidationError
from domain.portfolio.models import PortfolioRiskMetric

MIN_ALIGNED_RETURNS = 20


class PortfolioRiskCalculator:
    def calculate(
        self,
        *,
        instrument_id: str,
        benchmark_instrument_id: str,
        instrument_closes: Mapping[date, Decimal],
        benchmark_closes: Mapping[date, Decimal],
    ) -> PortfolioRiskMetric:
        instrument_returns = self._returns(instrument_closes)
        benchmark_returns = self._returns(benchmark_closes)
        aligned_dates = sorted(instrument_returns.keys() & benchmark_returns.keys())
        count = len(aligned_dates)
        if count < MIN_ALIGNED_RETURNS:
            return PortfolioRiskMetric(
                instrument_id,
                benchmark_instrument_id,
                count,
                None,
                None,
                f"requires at least {MIN_ALIGNED_RETURNS} aligned daily returns",
            )

        with localcontext() as context:
            context.prec = 50
            xs = [instrument_returns[item] for item in aligned_dates]
            ys = [benchmark_returns[item] for item in aligned_dates]
            mean_x = sum(xs, Decimal(0)) / Decimal(count)
            mean_y = sum(ys, Decimal(0)) / Decimal(count)
            divisor = Decimal(count - 1)
            covariance = sum(
                ((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)),
                Decimal(0),
            ) / divisor
            variance_x = sum(((x - mean_x) ** 2 for x in xs), Decimal(0)) / divisor
            variance_y = sum(((y - mean_y) ** 2 for y in ys), Decimal(0)) / divisor
            if variance_x == 0 or variance_y == 0:
                return PortfolioRiskMetric(
                    instrument_id,
                    benchmark_instrument_id,
                    count,
                    None,
                    None,
                    "return variance is zero",
                )
            correlation = covariance / (variance_x * variance_y).sqrt()
            beta = covariance / variance_y
            return PortfolioRiskMetric(
                instrument_id,
                benchmark_instrument_id,
                count,
                +correlation,
                +beta,
                None,
            )

    @staticmethod
    def _returns(closes: Mapping[date, Decimal]) -> dict[date, Decimal]:
        ordered = sorted(closes.items())
        for _, value in ordered:
            if type(value) is not Decimal or not value.is_finite() or value <= 0:
                raise InputValidationError("close prices must be positive finite Decimals")
        return {
            current_date: (current / previous) - Decimal(1)
            for (previous_date, previous), (current_date, current) in zip(
                ordered, ordered[1:], strict=False
            )
            if current_date > previous_date
        }
