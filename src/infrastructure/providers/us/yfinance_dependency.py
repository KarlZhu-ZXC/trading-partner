"""Resolve Yahoo error classes only at an explicit SDK operation boundary."""

from domain.common.errors import ProviderNotConfigured


def yfinance_errors(operation: str) -> tuple[type[Exception], type[Exception]]:
    try:
        from yfinance.exceptions import YFRateLimitError, YFTickerMissingError
    except ModuleNotFoundError as error:
        if error.name not in {"yfinance", "yfinance.exceptions"}:
            raise
        raise ProviderNotConfigured(
            "Yahoo SDK operations require trading-partner[market-data]",
            details={"vendor": "yfinance", "extra": "market-data", "operation": operation},
        ) from None
    return YFRateLimitError, YFTickerMissingError
