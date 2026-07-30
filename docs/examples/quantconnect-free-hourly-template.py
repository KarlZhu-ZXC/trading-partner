"""QuantConnect Free smoke template for Trading Partner historical validation."""

# ruff: noqa: F403, F405 -- AlgorithmImports is QuantConnect's supported bootstrap.

from AlgorithmImports import *


class TradingPartnerHourlyBaseline(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2008, 7, 1)
        self.set_end_date(2026, 7, 29)
        self.set_cash(100000)
        self._symbol = self.add_equity(
            "SPY",
            Resolution.HOUR,
            data_normalization_mode=DataNormalizationMode.SPLIT_ADJUSTED,
        ).symbol
        self.set_benchmark(self._symbol)
        self._fast = self.sma(self._symbol, 20, Resolution.HOUR)
        self._slow = self.sma(self._symbol, 100, Resolution.HOUR)
        self.set_warm_up(100, Resolution.HOUR)

    def on_data(self, data: Slice):
        if self.is_warming_up or not self._slow.is_ready:
            return
        target = 1.0 if self._fast.current.value > self._slow.current.value else 0.0
        self.set_holdings(self._symbol, target)
