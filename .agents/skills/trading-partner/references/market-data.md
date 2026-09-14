# Canonical market-data boundary

Treat the MCP response as canonical, never the Provider payload:

`Provider payload → Provider adapter → domain model → application DTO → MCP envelope`

- Provider aliases such as `regularMarketPrice`, `prev_close_price`, `f60`, or
  `overnight_price` must not leak into client-facing field names.
- For quotes, use canonical `instrument_id`, `quote_at`, `session`,
  `display_price`, `price_basis`, `previous_close`, and `previous_close_basis`.
  Optional OHLC/volume/bid/ask fields remain null when the source cannot establish them.
- Provider differences belong in `sources`, delay/freshness, and warning/error codes;
  they must not change the meaning of canonical fields.
- `display_price` is the usable displayed observation. Read `price_basis` before
  describing it as last, midpoint, settlement, bid, or ask.
- Equity/ETF/index `previous_close_basis=previous_completed_regular_session_close`
  follows the actual returned `quote_at + session`. Call it
  前收（前一已完成常规交易时段收盘）, never 昨收 or the previous arbitrary candle.
- Futures `previous_close_basis=previous_completed_daily_bar_close`; do not call it
  a regular-session close or settlement.

For near-current US equity/ETF quotes, preserve pre/post/overnight session labels.
During Sunday–Thursday 20:00–04:00 America/New_York, a true overnight observation
requires Moomoo OpenD `market_state=OVERNIGHT` plus the exact instrument's dedicated
overnight field. Otherwise retain the latest-known fallback with
`OVERNIGHT_QUOTE_UNAVAILABLE`; never relabel it as overnight.

OTC `XAUUSD`/`XAGUSD` and rolling CFDs are broker observations, not licensed spot
benchmarks or exchange futures. A midpoint is not a trade. Weekend proxy sources
must retain their token/perpetual/CFD identity and basis warnings.

Technical snapshots use `tp_technical_v3`. Its `smart_money` member is deterministic
`tp_smc_v1`: confirmed internal/swing structure, BOS/CHoCH, Order Blocks, FVGs,
EQH/EQL liquidity, and value zones. Preserve each swing's occurrence and confirmation
time, each zone's status, source-bar basis, and `historically_validated=false`. The
published LuxAlgo defaults are the calculation compatibility target on identical
OHLC; Provider bars and TradingView rendering may still differ. Never present it as
direct evidence of institutional orders or an automatic trade signal.
If `atr_200_ready=false`, disclose that Order Block volatility filtering and
EQH/EQL coverage are incomplete even though price structure and FVGs remain usable.
