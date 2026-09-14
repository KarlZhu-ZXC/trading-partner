// Synthetic chart data only. Never import owner positions, notes, or drawings here.
export function syntheticTechnicalChartScene() {
  const start = Date.UTC(2025, 0, 2, 21);
  const bars = Array.from({ length: 260 }, (_, index) => {
    const close = 100 + index * 0.12 + Math.sin(index / 9) * 5;
    const open = close + (index % 3 === 0 ? 0.8 : -0.45);
    return {
      timestamp: new Date(start + index * 86_400_000).toISOString(),
      open: open.toFixed(4),
      high: (Math.max(open, close) + 1.2).toFixed(4),
      low: (Math.min(open, close) - 1.2).toFixed(4),
      close: close.toFixed(4),
      volume: String(1_000_000 + index * 1_000),
    };
  });
  return {
    instrument_id: "equity:US:TTWO",
    bars_interval: "1d" as const,
    bars,
    price_basis: "split_and_dividend_adjusted_daily_close",
    timeframes: [{
      interval: "1d" as const,
      trend_state: "uptrend",
      smart_money: {
        trend: "bullish",
        atr_200_ready: true,
        limitations: [],
        structure_events: [{
          scope: "swing" as const,
          event: "bos" as const,
          direction: "bullish" as const,
          price: bars[180].high,
          occurred_at: bars[210].timestamp,
          broken_swing_at: bars[180].timestamp,
        }],
        order_blocks: [{
          kind: "order_block" as const,
          scope: "swing" as const,
          direction: "bullish" as const,
          low: bars[190].low,
          high: bars[190].high,
          created_at: bars[190].timestamp,
          confirmed_at: bars[210].timestamp,
          status: "active",
        }],
        fair_value_gaps: [],
        liquidity_levels: [],
        value_zones: [],
      },
    }],
  };
}
